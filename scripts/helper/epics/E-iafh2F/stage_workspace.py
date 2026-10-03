"""Stage a dashboard workspace that looks like a live multi-task run (E-iafh2F verification).

Writes ``state.json`` + per-attempt ``transcript.jsonl`` / ``result.json`` exactly as the engine
lays them out (cycle 1: ``<run>/<task>/attempt-<n>/``), so the REAL ``ao ui`` server and the
REAL activity reader are exercised -- only the producer (an agent process) is replaced by files.

Usage: python stage_workspace.py <workspace_dir>
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.models import RunState, TaskRunState
from agent_orchestrator.runstate import RunStateStore

NOW = time.time()


def _ago(minutes: float) -> str:
    return (datetime.now(UTC) - timedelta(minutes=minutes)).isoformat()


MODELS = ["sonnet", "opus", "haiku"]
EFFORTS = ["low", "medium", "high", "xhigh"]
ACTIONS = [
    ("Bash", {"command": "pytest -q tests/test_engine.py"}),
    ("Edit", {"file_path": "src/agent_orchestrator/engine.py"}),
    ("Read", {"file_path": "docs-md/live-activity-and-tabs-hld.md"}),
    ("Grep", {"pattern": "def run_activity"}),
    ("Bash", {"command": "ruff check . && mypy src"}),
    ("Write", {"file_path": "tests/ui/test_activity.py"}),
    ("Glob", {"pattern": "ui/src/**/*.tsx"}),
]


def _msg(i: int, n: int, name: str, inp: dict) -> str:
    events = [
        {"type": "system", "subtype": "thinking_tokens", "estimated_tokens_delta": 120 + n},
        {
            "type": "assistant",
            "message": {
                "id": f"msg_{i}_{n}",
                "role": "assistant",
                "usage": {"input_tokens": 6, "output_tokens": 3},
                "content": [
                    {"type": "text", "text": "Working on the next step. " * (3 + n % 5)},
                    {"type": "tool_use", "id": f"toolu_{i}_{n}", "name": name, "input": inp},
                ],
            },
        },
        {
            "type": "user",
            "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]},
        },
    ]
    return "".join(json.dumps(e) + "\n" for e in events)


def _transcript(i: int, turns: int) -> str:
    out = json.dumps({"type": "system", "subtype": "init", "model": "claude-test"}) + "\n"
    for n in range(turns):
        name, inp = ACTIONS[(i + n) % len(ACTIONS)]
        out += _msg(i, n, name, inp)
    return out


def stage_run(ws: Path, run_id: str, running: int, settled: int, *, stuck: int = 1) -> None:
    tasks: dict[str, TaskRunState] = {}
    for i in range(settled):
        tasks[f"done-{i}"] = TaskRunState(
            status="succeeded",
            attempts=1,
            dispatch_cycle=1,
            started_at=_ago(30),
            ended_at=_ago(24),
            cumulative_input_tokens=1200 + 100 * i,
            cumulative_output_tokens=9000 + 500 * i,
            cumulative_cost_usd=0.4 + i / 10,
            model=MODELS[i % 3],
            effort=EFFORTS[i % 4],
        )
    for i in range(running):
        tasks[f"worker-{i}"] = TaskRunState(
            status="running",
            attempts=1,
            dispatch_cycle=1,
            started_at=_ago(5 + 2 * i),
            model=MODELS[i % 3],
            effort=EFFORTS[i % 4],
        )
    tasks["queued-0"] = TaskRunState()
    state = RunState(
        run_id=run_id,
        workflow_id="epic-runner",
        repo_set="main",
        started_at="2026-10-02T11:00:00+00:00",
        updated_at="2026-10-02T11:10:00+00:00",
        status="running",
        tasks=tasks,
    )
    store = LocalFsArtifactStore(str(ws))
    RunStateStore(str(ws), store).save(state)
    run_dir = ws / ".orchestrator" / "runs" / run_id
    for i in range(settled):
        d = run_dir / f"done-{i}" / "attempt-1"
        d.mkdir(parents=True, exist_ok=True)
        (d / "result.json").write_text(
            json.dumps(
                {
                    "type": "result",
                    "num_turns": 18 + 3 * i,
                    "total_cost_usd": 0.4,
                    "usage": {"input_tokens": 1200, "output_tokens": 9000},
                }
            )
        )
    for i in range(running):
        d = run_dir / f"worker-{i}" / "attempt-1"
        d.mkdir(parents=True, exist_ok=True)
        t = d / "transcript.jsonl"
        t.write_text(_transcript(i, 4 + 5 * i))
        if i < stuck:  # last write 7 minutes ago -> stuck hint
            old = NOW - 420
            os.utime(t, (old, old))


def main() -> None:
    ws = Path(sys.argv[1])
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "docs").mkdir(exist_ok=True)
    (ws / "docs" / "plan.md").write_text("# Plan\n\nStaged file for the file viewer.\n")
    (ws / "src").mkdir(exist_ok=True)
    (ws / "src" / "main.py").write_text("print('hello')\n")
    stage_run(ws, "epic-runner-20261002T110000Z", running=7, settled=2)
    stage_run(ws, "bench-20261002T105000Z", running=2, settled=1, stuck=0)
    stage_run(ws, "idle-20261002T100000Z", running=0, settled=3)


if __name__ == "__main__":
    main()
