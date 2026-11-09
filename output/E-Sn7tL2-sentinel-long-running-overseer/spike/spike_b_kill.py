"""T-Sn0Spk (b, part 2): cooperative kill via process group + file-growth liveness. THROWAWAY.
uv run python output/E-Sn7tL2-sentinel-long-running-overseer/spike/spike_b_kill.py
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from agent_orchestrator.executors.claude_cli import ClaudeCliExecutor
from agent_orchestrator.models import AgentSpec, TaskContext

HERE = Path(__file__).resolve().parent
STUB = str(HERE / "stub_claude.sh")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # zombie check
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")")[1].split()[0] != "Z"
    except OSError:
        return False


out: dict = {}
with tempfile.TemporaryDirectory(prefix="ao-spike-b-") as d:
    d = Path(d)
    # ---- K1: today's timeout path (proc.kill() on the direct child only) ----
    pidfile = d / "child1.pid"
    os.environ["STUB_CHILD_PIDFILE"] = str(pidfile)
    os.environ["STUB_SLEEP"] = "300"
    agent = AgentSpec(executor="claude_cli", command_template=[STUB])
    ctx = TaskContext(
        run_id="r",
        repo_paths={},
        task_id="k1",
        agent=agent,
        instruction_path="x.md",
        input_paths=[],
        output_paths=[],
        output_dir=str(d / "k1"),
        timeout_seconds=2,
        cwd=str(d),
    )
    t0 = time.monotonic()
    res = ClaudeCliExecutor().execute(ctx)
    time.sleep(0.3)
    child = int(pidfile.read_text())
    out["K1_today_timeout_kill"] = {
        "result_status": res.status,
        "elapsed_s": round(time.monotonic() - t0, 2),
        "grandchild_pid": child,
        "grandchild_still_alive_after_task_settled": alive(child),
        "note": "Popen has no start_new_session; proc.kill() hits only the direct child (claude_cli.py:634-655)",
    }
    if alive(child):
        os.kill(child, signal.SIGKILL)  # clean up our own leak

    # ---- K2: cooperative kill via process group (proposed) ----
    pidfile = d / "child2.pid"
    os.environ["STUB_CHILD_PIDFILE"] = str(pidfile)
    tf = open(d / "k2.out", "w")
    proc = subprocess.Popen(
        [STUB],
        stdout=tf,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(1.0)
    child = int(pidfile.read_text())
    pgid = os.getpgid(proc.pid)
    t0 = time.monotonic()
    os.killpg(pgid, signal.SIGTERM)
    try:
        proc.wait(timeout=5)
        escalated = False
    except subprocess.TimeoutExpired:
        os.killpg(pgid, signal.SIGKILL)
        proc.wait()
        escalated = True
    t_dead = time.monotonic() - t0
    time.sleep(0.2)
    out["K2_killpg_sigterm"] = {
        "pgid_equals_direct_pid": pgid == proc.pid,
        "direct_returncode": proc.returncode,
        "time_to_dead_s": round(t_dead, 3),
        "escalated_to_sigkill": escalated,
        "grandchild_alive_after": alive(child),
    }

    # ---- K3: SIGTERM-ignoring tree needs the SIGKILL rung ----
    pidfile = d / "child3.pid"
    os.environ["STUB_CHILD_PIDFILE"] = str(pidfile)
    sh = d / "stubborn.sh"
    sh.write_text(
        '#!/usr/bin/env bash\ntrap "" TERM\n( trap "" TERM; sleep 300 & echo $! > "$STUB_CHILD_PIDFILE"; wait ) &\nwhile true; do sleep 1; done\n'
    )
    sh.chmod(0o755)
    proc = subprocess.Popen(
        [str(sh)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
    )
    time.sleep(1.0)
    child = int(pidfile.read_text())
    pgid = os.getpgid(proc.pid)
    t0 = time.monotonic()
    os.killpg(pgid, signal.SIGTERM)
    try:
        proc.wait(timeout=3)
        escalated = False
    except subprocess.TimeoutExpired:
        os.killpg(pgid, signal.SIGKILL)
        proc.wait()
        escalated = True
    time.sleep(0.2)
    out["K3_sigterm_ignored_needs_sigkill"] = {
        "escalated_to_sigkill": escalated,
        "time_to_dead_s": round(time.monotonic() - t0, 2),
        "grandchild_alive_after": alive(child),
    }

    # ---- K4: file-growth liveness probe on a stub that emits 1 line/s for 3s, then goes silent ----
    p = d / "growth.out"

    def writer():
        with open(p, "w") as f:
            for _ in range(3):
                f.write('{"type":"assistant"}\n')
                f.flush()
                time.sleep(1)
            time.sleep(6)  # silent "tool call"
            f.write('{"type":"user"}\n')
            f.flush()

    th = threading.Thread(target=writer)
    th.start()
    last_size, last_change, samples = -1, time.monotonic(), []
    t_start = time.monotonic()
    while th.is_alive():
        sz = p.stat().st_size if p.exists() else 0
        if sz != last_size:
            if last_size >= 0:
                samples.append(round(time.monotonic() - last_change, 2))
            last_size, last_change = sz, time.monotonic()
        time.sleep(0.2)
    time.sleep(0.3)
    sz = p.stat().st_size
    if sz != last_size:
        samples.append(round(time.monotonic() - last_change, 2))
    out["K4_growth_probe"] = {
        "observed_gaps_between_size_changes_s": samples,
        "cost_per_probe": "one os.stat(); no read, no lock",
    }

print(json.dumps(out, indent=1))
