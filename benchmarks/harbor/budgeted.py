"""Run one Harbor arm over a task list as one job per task, under a hard total spend limit.

`run.sh` caps spend per task (`--budget-usd`), so a multi-task job can cost up to
tasks x cap. This driver runs each task as its own `run.sh` job (job name
`<prefix>-<task>`), at most `--lanes` at a time, and starts the next task only while

    spent (finished trials) + in-flight x cap + cap <= --total-usd

so the total can never pass the limit even if every attempt hits its cap. Remaining tasks
are reported as skipped. A task whose job dir already has a finished trial is not rerun
(resume after an interruption).

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
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUN_SH = HERE / "run.sh"
AO_HARBOR_ROOT = Path(os.environ.get("AO_HARBOR_ROOT", "/var/tmp/ao-harbor"))
POLL_SECONDS = int(os.environ.get("AO_HARBOR_POLL_SECONDS", "15"))


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
    ap.add_argument("--run-sh", type=Path, default=RUN_SH, help=argparse.SUPPRESS)  # tests
    args = ap.parse_args()

    tasks = read_tasks(args.tasks_file)
    logs = AO_HARBOR_ROOT / "pilot" / args.prefix
    logs.mkdir(parents=True, exist_ok=True)
    running: dict[str, subprocess.Popen[bytes]] = {}
    spent = 0.0
    pending = []
    for task in tasks:
        job = f"{args.prefix}-{task}"
        done = job_trials(job)
        if done:  # resume: count it, do not rerun
            spent += sum(trial_cost(t) for t in done)
            print(f"[skip-done] {task}: {summarize(job)}", flush=True)
        else:
            pending.append(task)
    print(f"[start] {len(pending)} to run, spent so far ${spent:.2f}", flush=True)

    while pending or running:
        for task, proc in list(running.items()):
            if proc.poll() is None:
                continue
            del running[task]
            job = f"{args.prefix}-{task}"
            spent += sum(trial_cost(t) for t in job_trials(job))
            print(
                f"[done] {task} exit={proc.returncode}: {summarize(job)} | total ${spent:.2f}",
                flush=True,
            )
        while pending and len(running) < args.lanes:
            if spent + (len(running) + 1) * args.cap_usd > args.total_usd:
                break
            task = pending.pop(0)
            job = f"{args.prefix}-{task}"
            cmd = [
                str(args.run_sh), "--arm", args.arm, "--model", args.model,
                "--dataset", args.dataset, "--include", task, "-n", "1",
                "--budget-usd", str(args.cap_usd), "--auth", args.auth,
                "--min-token-hours", args.min_token_hours, "--job-name", job,
            ]  # fmt: skip
            log = (logs / f"{task}.log").open("wb")
            running[task] = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)  # noqa: S603
            print(f"[launch] {task} (in flight {len(running)}, spent ${spent:.2f})", flush=True)
        if pending and not running:
            # Not even one more cap fits: stop.
            break
        time.sleep(POLL_SECONDS)

    for task in pending:
        print(f"[skipped-budget] {task}", flush=True)
    print(f"[end] total ${spent:.2f} of ${args.total_usd:.2f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
