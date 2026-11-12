"""Run one Harbor arm over a task list as one job per task, under a hard total spend limit.

`run.sh` caps spend per task (`--budget-usd`), so a multi-task job can cost up to
tasks x cap. This driver runs each task as its own `run.sh` job (job name
`<prefix>-<task>`), at most `--lanes` at a time, and starts the next task only while

    spent (finished trials) + in-flight x cap + cap <= --total-usd

so the total can never pass the limit even if every attempt hits its cap. Remaining tasks
are reported as skipped. A task whose job dir already has a finished trial is not rerun
(resume after an interruption).

With `--auth host` a task starts only while the host login has `--min-token-hours` left
(run.sh would refuse it and the task would be lost); otherwise the driver waits. The host
login is refreshed by Claude Code itself; `--refresh-host-login` lets the driver trigger
that (one tiny `claude -p` call on Haiku) once the token has expired and nothing is running.

Infra failures that cost nothing (no trial result, or every trial ended in a non-attempt
exception at $0: image pull rate limits, Docker start problems) are retried: the job dir is
moved to `<job>.infra-<n>` and the task requeued after `--retry-delay` seconds, at most
`--infra-retries` times. This also applies on resume. `--max-load L` holds new launches
while the host's 1-minute load average is above L (a shared host: Harbor's 30 s Docker
network-control probe fails under heavy load, and a busy host also skews wall times).

`--prune-solved-images DATASET_DIR`: after a task's job finishes with every trial solved,
remove its Docker image (task.toml `docker_image`), so a long calibration pool does not
fill the disk; failed tasks keep their image for the comparison.

    python3 benchmarks/harbor/budgeted.py --arm cc --model sonnet \
        --dataset datacurve/deep-swe-1-1 --tasks-file tasks.txt --cap-usd 8 \
        --total-usd 60 --lanes 2 --prefix p1c-deepswe-cc-sonnet
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN_SH = HERE / "run.sh"
AO_HARBOR_ROOT = Path(os.environ.get("AO_HARBOR_ROOT", "/var/tmp/ao-harbor"))
POLL_SECONDS = float(os.environ.get("AO_HARBOR_POLL_SECONDS", "15"))
CREDENTIALS = Path(
    os.environ.get("AO_HARBOR_HOST_CREDENTIALS", "~/.claude/.credentials.json")
).expanduser()
# A login this close to expiry (or past it) is refreshed by any Claude Code request.
REFRESH_BELOW_HOURS = 0.05
REFRESH_CMD = ["claude", "-p", "Reply with OK.", "--model", "haiku", "--max-turns", "1"]


def read_tasks(path: Path) -> list[str]:
    tasks = []
    for line in path.read_text().splitlines():
        name = line.split("#", 1)[0].strip()
        if name:
            tasks.append(name)
    return tasks


def job_trials(job: str) -> list[dict]:
    """Finished trial results (result.json) of one job."""
    job_dir = AO_HARBOR_ROOT / "jobs" / job
    out = []
    for result in sorted(job_dir.glob("*/result.json")):
        try:
            out.append(json.loads(result.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return out


def trial_cost(trial: dict) -> float:
    return float((trial.get("agent_result") or {}).get("cost_usd") or 0.0)


def host_token_hours(path: Path = CREDENTIALS) -> float:
    """Hours until the host Claude login's access token expires (-inf if unreadable)."""
    try:
        oauth = json.loads(path.read_text()).get("claudeAiOauth") or {}
        return (float(oauth["expiresAt"]) / 1000 - time.time()) / 3600
    except (OSError, ValueError, KeyError, TypeError):
        return float("-inf")


def task_image(dataset_dir: Path, task: str) -> str | None:
    """The task's prebuilt Docker image (task.toml [environment] docker_image), if any."""
    toml_path = dataset_dir / task / "task.toml"
    try:
        env = tomllib.loads(toml_path.read_text()).get("environment") or {}
    except (OSError, tomllib.TOMLDecodeError):
        return None
    image = env.get("docker_image")
    return image if isinstance(image, str) and image else None


def all_solved(trials: list[dict]) -> bool:
    rewards = [
        ((t.get("verifier_result") or {}).get("rewards") or {}).get("reward") for t in trials
    ]
    return bool(rewards) and all(r == 1 for r in rewards)


# Exceptions that are a legitimate attempt (the trial counts), as in summarize.py.
ATTEMPT_EXCEPTIONS = frozenset({"AgentTimeoutError"})


def free_infra_failure(trials: list[dict]) -> bool:
    """True when the job produced nothing to count and spent nothing: no trial result at
    all, or every trial ended in a non-attempt exception without a reward at $0."""
    if not trials:
        return True
    for t in trials:
        exc = (t.get("exception_info") or {}).get("exception_type")
        reward = ((t.get("verifier_result") or {}).get("rewards") or {}).get("reward")
        if reward is not None or not exc or exc in ATTEMPT_EXCEPTIONS or trial_cost(t) > 0:
            return False
    return True


def archive_job(job: str) -> str | None:
    """Move a job dir aside as `<job>.infra-<n>`; returns the new name (None if absent)."""
    src = AO_HARBOR_ROOT / "jobs" / job
    if not src.exists():
        return None
    n = 1
    while (AO_HARBOR_ROOT / "jobs" / f"{job}.infra-{n}").exists():
        n += 1
    dst = src.with_name(f"{job}.infra-{n}")
    src.rename(dst)
    return dst.name


def summarize(job: str) -> str:
    parts = []
    for t in job_trials(job):
        rewards = (t.get("verifier_result") or {}).get("rewards") or {}
        exc = (t.get("exception_info") or {}).get("exception_type")
        parts.append(
            f"reward={rewards.get('reward')} f2p={rewards.get('f2p_passed')}/"
            f"{rewards.get('f2p_total')} p2p={rewards.get('p2p_passed')}/"
            f"{rewards.get('p2p_total')} cost=${trial_cost(t):.2f} exc={exc}"
        )
    return "; ".join(parts) or "no trial result"


def main() -> int:
    ap = argparse.ArgumentParser(description="Budgeted per-task Harbor runner.")
    ap.add_argument("--arm", required=True, choices=("cc", "ao"))
    ap.add_argument("--model", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--tasks-file", type=Path, required=True)
    ap.add_argument("--cap-usd", type=float, required=True, help="per-task cap (run.sh)")
    ap.add_argument("--total-usd", type=float, required=True, help="hard total limit")
    ap.add_argument("--lanes", type=int, default=1)
    ap.add_argument("--prefix", required=True, help="job name prefix")
    ap.add_argument("--auth", default="host")
    ap.add_argument("--min-token-hours", default="2")
    ap.add_argument(
        "--refresh-host-login",
        action="store_true",
        help="trigger a host login refresh (tiny claude -p) once it has expired",
    )
    ap.add_argument("--agent-timeout-mult", help="harbor --agent-timeout-multiplier")
    ap.add_argument(
        "--prune-solved-images",
        type=Path,
        metavar="DATASET_DIR",
        help="docker rmi a task's image once every trial of it is solved",
    )
    ap.add_argument("--infra-retries", type=int, default=3)
    ap.add_argument("--retry-delay", type=float, default=300.0, help="seconds")
    ap.add_argument("--max-load", type=float, help="hold launches above this 1-min loadavg")
    ap.add_argument("--run-sh", type=Path, default=RUN_SH, help=argparse.SUPPRESS)  # tests
    args = ap.parse_args()

    tasks = read_tasks(args.tasks_file)
    logs = AO_HARBOR_ROOT / "pilot" / args.prefix
    logs.mkdir(parents=True, exist_ok=True)
    running: dict[str, subprocess.Popen[bytes]] = {}
    spent = 0.0
    pending = []
    retries: dict[str, int] = {}
    not_before: dict[str, float] = {}
    for task in tasks:
        job = f"{args.prefix}-{task}"
        done = job_trials(job)
        if done and free_infra_failure(done):  # resume: an infra failure is rerun
            print(f"[infra-archive] {task}: {summarize(job)} -> {archive_job(job)}", flush=True)
            pending.append(task)
        elif done:  # resume: count it, do not rerun
            spent += sum(trial_cost(t) for t in done)
            print(f"[skip-done] {task}: {summarize(job)}", flush=True)
        else:
            pending.append(task)
    print(f"[start] {len(pending)} to run, spent so far ${spent:.2f}", flush=True)
    waiting_logged = False
    load_logged = False

    while pending or running:
        for task, proc in list(running.items()):
            if proc.poll() is None:
                continue
            del running[task]
            job = f"{args.prefix}-{task}"
            trials = job_trials(job)
            spent += sum(trial_cost(t) for t in trials)
            print(
                f"[done] {task} exit={proc.returncode}: {summarize(job)} | total ${spent:.2f}",
                flush=True,
            )
            if free_infra_failure(trials) and retries.get(task, 0) < args.infra_retries:
                retries[task] = retries.get(task, 0) + 1
                not_before[task] = time.time() + args.retry_delay
                pending.append(task)
                print(
                    f"[infra-retry] {task} #{retries[task]} in {args.retry_delay:.0f}s "
                    f"(job moved to {archive_job(job)})",
                    flush=True,
                )
                continue
            if args.prune_solved_images and all_solved(trials):
                image = task_image(args.prune_solved_images, task)
                if image:
                    rc = subprocess.run(["docker", "rmi", image], capture_output=True).returncode  # noqa: S603, S607
                    print(f"[prune] {task}: docker rmi {image} exit={rc}", flush=True)
        token_short = args.auth == "host" and host_token_hours() < float(args.min_token_hours)
        if token_short and pending and not running and args.refresh_host_login:
            if host_token_hours() < REFRESH_BELOW_HOURS:
                rc = subprocess.run(REFRESH_CMD, capture_output=True).returncode  # noqa: S603
                print(f"[refresh-login] exit={rc} now {host_token_hours():.2f} h", flush=True)
        if token_short and pending and not waiting_logged:
            print(
                f"[wait-login] host token {host_token_hours():.2f} h < {args.min_token_hours} h",
                flush=True,
            )
        waiting_logged = token_short
        load = os.getloadavg()[0]
        load_high = args.max_load is not None and load > args.max_load
        if load_high and pending and not load_logged:
            print(f"[wait-load] 1-min load {load:.0f} > {args.max_load:g}", flush=True)
        load_logged = load_high
        now = time.time()
        while pending and len(running) < args.lanes and not token_short and not load_high:
            if spent + (len(running) + 1) * args.cap_usd > args.total_usd:
                break
            ready = [t for t in pending if not_before.get(t, 0) <= now]
            if not ready:
                break
            task = ready[0]
            pending.remove(task)
            job = f"{args.prefix}-{task}"
            cmd = [
                str(args.run_sh), "--arm", args.arm, "--model", args.model,
                "--dataset", args.dataset, "--include", task, "-n", "1",
                "--budget-usd", str(args.cap_usd), "--auth", args.auth,
                "--min-token-hours", args.min_token_hours, "--job-name", job,
            ]  # fmt: skip
            if args.agent_timeout_mult:
                cmd += ["--", "--agent-timeout-multiplier", args.agent_timeout_mult]
            log = (logs / f"{task}.log").open("wb")
            running[task] = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)  # noqa: S603
            print(f"[launch] {task} (in flight {len(running)}, spent ${spent:.2f})", flush=True)
        if pending and not running and spent + args.cap_usd > args.total_usd:
            # Not even one more cap fits: stop.
            break
        time.sleep(POLL_SECONDS)

    for task in pending:
        print(f"[skipped-budget] {task}", flush=True)
    print(f"[end] total ${spent:.2f} of ${args.total_usd:.2f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
