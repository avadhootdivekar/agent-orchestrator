"""benchmarks/harbor/budgeted.py: total limit, host-login wait, image pruning, timeout pass-through.

Driven end to end as a subprocess (its CLI is the outer boundary) with a fake run.sh that
writes a Harbor-shaped result.json and a fake `docker` on PATH.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BUDGETED = REPO_ROOT / "benchmarks" / "harbor" / "budgeted.py"

FAKE_RUN_SH = """#!/usr/bin/env bash
# Records argv, then writes one finished trial for the job (reward from FAKE_REWARD_<task>).
echo "$*" >> "$AO_HARBOR_ROOT/argv.log"
job="" task=""
while [ $# -gt 0 ]; do
  case "$1" in --job-name) job="$2"; shift 2 ;; --include) task="$2"; shift 2 ;; *) shift ;; esac
done
var="FAKE_REWARD_${task//-/_}"; reward="${!var:-1}"
mkdir -p "$AO_HARBOR_ROOT/jobs/$job/t1"
# FAKE_INFRA_<task>=N: the first N runs end in a $0 infra exception (e.g. an image pull limit).
ivar="FAKE_INFRA_${task//-/_}"; count="$AO_HARBOR_ROOT/runs-$task"
n=$(( $(cat "$count" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$count"
if [ "$n" -le "${!ivar:-0}" ]; then
  printf '{"agent_result":null,"exception_info":{"exception_type":"RuntimeError"}}' \
    > "$AO_HARBOR_ROOT/jobs/$job/t1/result.json"
  exit 1
fi
printf '{"agent_result":{"cost_usd":0.5},"verifier_result":{"rewards":{"reward":%s}}}' \
  "$reward" > "$AO_HARBOR_ROOT/jobs/$job/t1/result.json"
"""

FAKE_DOCKER = """#!/usr/bin/env bash
echo "$*" >> "$AO_HARBOR_ROOT/docker.log"
"""


def _setup(tmp_path: Path, tasks: list[str], token_hours: float) -> dict[str, str]:
    root = tmp_path / "root"
    (root / "jobs").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (("run.sh", FAKE_RUN_SH), ("docker", FAKE_DOCKER)):
        script = bin_dir / name
        script.write_text(body)
        script.chmod(0o755)
    (tmp_path / "tasks.txt").write_text("\n".join(tasks) + "\n")
    dataset = tmp_path / "dataset"
    for task in tasks:
        (dataset / task).mkdir(parents=True)
        (dataset / task / "task.toml").write_text(
            f'[environment]\ndocker_image = "registry.example/{task}:v1"\n'
        )
    _write_token(tmp_path / "credentials.json", token_hours)
    env = dict(os.environ)
    env.update(
        AO_HARBOR_ROOT=str(root),
        AO_HARBOR_POLL_SECONDS="0.05",
        AO_HARBOR_HOST_CREDENTIALS=str(tmp_path / "credentials.json"),
        PATH=f"{bin_dir}{os.pathsep}{env['PATH']}",
    )
    return env


def _write_token(path: Path, hours: float) -> None:
    expires_ms = (time.time() + hours * 3600) * 1000
    path.write_text(json.dumps({"claudeAiOauth": {"expiresAt": expires_ms}}))


def _cmd(tmp_path: Path, *extra: str) -> list[str]:
    return [
        sys.executable, str(BUDGETED), "--arm", "ao", "--model", "sonnet",
        "--dataset", "org/ds", "--tasks-file", str(tmp_path / "tasks.txt"),
        "--cap-usd", "8", "--total-usd", "100", "--prefix", "p",
        "--run-sh", str(tmp_path / "bin" / "run.sh"), *extra,
    ]  # fmt: skip


def test_timeout_multiplier_and_prune_only_solved(tmp_path: Path) -> None:
    env = _setup(tmp_path, ["solved-task", "failed-task"], token_hours=8)
    env["FAKE_REWARD_failed_task"] = "0"
    out = subprocess.run(
        _cmd(
            tmp_path,
            "--agent-timeout-mult",
            "1.5",
            "--prune-solved-images",
            str(tmp_path / "dataset"),
        ),  # fmt: skip
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    root = Path(env["AO_HARBOR_ROOT"])
    argv = (root / "argv.log").read_text().splitlines()
    assert len(argv) == 2
    assert all(line.endswith("-- --agent-timeout-multiplier 1.5") for line in argv)
    # Only the solved task's image is removed; the failed one is kept for the comparison.
    assert (root / "docker.log").read_text().splitlines() == ["rmi registry.example/solved-task:v1"]
    assert "[end] total $1.00" in out.stdout


def test_waits_for_host_login_instead_of_losing_the_task(tmp_path: Path) -> None:
    env = _setup(tmp_path, ["only-task"], token_hours=0.5)  # below --min-token-hours 2
    proc = subprocess.Popen(
        _cmd(tmp_path, "--auth", "host"), env=env, stdout=subprocess.PIPE, text=True
    )
    try:
        time.sleep(1.0)
        root = Path(env["AO_HARBOR_ROOT"])
        assert proc.poll() is None, "driver must wait, not give up"
        assert not (root / "argv.log").exists(), "no launch on a short-lived login"
        _write_token(tmp_path / "credentials.json", 8)  # the host login was refreshed
        stdout, _ = proc.communicate(timeout=30)
    finally:
        proc.kill()
    assert "[wait-login]" in stdout
    assert "[done] only-task" in stdout
    assert "[skipped-budget]" not in stdout


def test_total_limit_still_stops_before_a_cap_could_overshoot(tmp_path: Path) -> None:
    env = _setup(tmp_path, ["a", "b", "c"], token_hours=8)
    cmd = _cmd(tmp_path)
    cmd[cmd.index("--total-usd") + 1] = "8.6"  # after $1.00 spent, a third $8 cap would overshoot
    out = subprocess.run(cmd, env=env, capture_output=True, text=True, check=True, timeout=60)
    assert "[skipped-budget] c" in out.stdout


def test_free_infra_failures_are_archived_and_retried(tmp_path: Path) -> None:
    env = _setup(tmp_path, ["flaky", "steady"], token_hours=8)
    env["FAKE_INFRA_flaky"] = "2"
    cmd = _cmd(tmp_path, "--retry-delay", "0")
    out = subprocess.run(cmd, env=env, capture_output=True, text=True, check=True, timeout=60)
    jobs = Path(env["AO_HARBOR_ROOT"]) / "jobs"
    assert out.stdout.count("[infra-retry] flaky") == 2
    assert (jobs / "p-flaky.infra-1").is_dir() and (jobs / "p-flaky.infra-2").is_dir()
    assert "[end] total $1.00" in out.stdout  # the final flaky attempt + steady


def test_resume_archives_an_earlier_infra_failure(tmp_path: Path) -> None:
    env = _setup(tmp_path, ["t"], token_hours=8)
    old = Path(env["AO_HARBOR_ROOT"]) / "jobs" / "p-t" / "x"
    old.mkdir(parents=True)
    (old / "result.json").write_text(
        json.dumps({"exception_info": {"exception_type": "ValueError"}, "agent_result": None})
    )
    out = subprocess.run(
        _cmd(tmp_path), env=env, capture_output=True, text=True, check=True, timeout=60
    )
    assert "[infra-archive] t" in out.stdout and "[done] t" in out.stdout
    assert (Path(env["AO_HARBOR_ROOT"]) / "jobs" / "p-t.infra-1").is_dir()


def test_infra_retries_are_bounded(tmp_path: Path) -> None:
    env = _setup(tmp_path, ["dead"], token_hours=8)
    env["FAKE_INFRA_dead"] = "99"
    cmd = _cmd(tmp_path, "--retry-delay", "0", "--infra-retries", "1")
    out = subprocess.run(cmd, env=env, capture_output=True, text=True, check=True, timeout=60)
    assert out.stdout.count("[launch] dead") == 2


def test_high_load_holds_launches(tmp_path: Path) -> None:
    env = _setup(tmp_path, ["t"], token_hours=8)
    proc = subprocess.Popen(
        _cmd(tmp_path, "--max-load", "-1"), env=env, stdout=subprocess.PIPE, text=True
    )
    try:
        time.sleep(1.0)
        assert proc.poll() is None
        assert not (Path(env["AO_HARBOR_ROOT"]) / "argv.log").exists()
    finally:
        proc.kill()
        stdout, _ = proc.communicate(timeout=10)
    assert "[wait-load]" in stdout


def test_hold_while_command_holds_until_it_fails(tmp_path: Path) -> None:
    env = _setup(tmp_path, ["t"], token_hours=8)
    flag = tmp_path / "co-tenant-running"
    flag.touch()
    proc = subprocess.Popen(
        _cmd(tmp_path, "--hold-while", f"test -e {flag}"),
        env=env,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(1.0)
        assert proc.poll() is None, "driver must wait for the co-tenant"
        assert not (Path(env["AO_HARBOR_ROOT"]) / "argv.log").exists()
        flag.unlink()  # the co-tenant run finished
        stdout, _ = proc.communicate(timeout=30)
    finally:
        proc.kill()
    assert stdout.count("[wait-hold]") == 1
    assert "[done] t" in stdout


def _finished_trial(trial: Path, cost: float, reward: int) -> None:
    trial.mkdir()
    doc = {"agent_result": {"cost_usd": cost}, "verifier_result": {"rewards": {"reward": reward}}}
    (trial / "result.json").write_text(json.dumps(doc))


def test_restart_defers_in_flight_and_counts_late_results(tmp_path: Path) -> None:
    """A stopped driver left trials running; the new one neither duplicates nor reruns them."""
    env = _setup(tmp_path, ["running", "finished", "killed"], token_hours=8)
    root = Path(env["AO_HARBOR_ROOT"])
    for task in ("running", "finished", "killed"):
        (root / "jobs" / f"p-{task}").mkdir()  # job dirs without a result yet
    secrets = root / "secrets"
    secrets.mkdir()
    login = secrets / "p-running.env"
    login.touch()  # its run.sh is still running
    flag = tmp_path / "hold"
    flag.touch()
    cmd = _cmd(tmp_path, "--hold-while", f"test -e {flag}", "--retry-delay", "0.5")
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, text=True)
    try:
        time.sleep(0.5)
        # The orphaned "finished" trial writes its result while the driver is holding.
        done = root / "jobs" / "p-finished" / "t1"
        _finished_trial(done, cost=2.0, reward=0)
        flag.unlink()
        time.sleep(1.0)
        assert proc.poll() is None, "must still wait for the running orphan"
        # The orphan "running" finishes: result written, login file removed by its run.sh.
        late = root / "jobs" / "p-running" / "t1"
        _finished_trial(late, cost=1.5, reward=1)
        login.unlink()
        stdout, _ = proc.communicate(timeout=30)
    finally:
        proc.kill()
    launched = (root / "argv.log").read_text()
    assert "--include killed" in launched  # killed run (no result, no run.sh): archived, rerun
    assert "--include running" not in launched and "--include finished" not in launched
    assert (root / "jobs" / "p-killed.infra-1").is_dir()
    assert "[wait-inflight] running" in stdout
    assert "[end] total $4.00" in stdout  # 2.0 + 1.5 counted late + 0.5 for the rerun
