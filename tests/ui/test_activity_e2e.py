"""E2E for live activity (E-iafh2F Phase 1): a REAL ``ao ui`` server process over real HTTP.

A running run is staged on disk exactly as the engine leaves it (``state.json`` + per-attempt
``transcript.jsonl``); the test then grows the transcript while the server is up and checks the
``/activity`` endpoint follows it, and that the runs list/detail carry the additive fields.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="dashboard needs the optional [ui] extra")
pytest.importorskip("uvicorn", reason="dashboard needs the optional [ui] extra")
httpx = pytest.importorskip("httpx", reason="dashboard e2e needs httpx")

from agent_orchestrator.models import TaskRunState  # noqa: E402

from .conftest import make_run_state, write_run  # noqa: E402
from .test_e2e_ui import SERVER_BOOT_TIMEOUT, _free_port, _wait_for  # noqa: E402

RUN_ID = "demo-20261002T110000Z"
RUNNING_TASKS = 5


def _line(msg_id: str, text: str = "working") -> str:
    msg = {"id": msg_id, "role": "assistant", "content": [{"type": "text", "text": text}]}
    return json.dumps({"type": "assistant", "message": msg}) + "\n"


@pytest.fixture()
def staged_server(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    ws = tmp_path / "ws"
    ws.mkdir()
    tasks = {
        f"worker-{i}": TaskRunState(
            status="running",
            attempts=1,
            dispatch_cycle=1,
            started_at="2026-10-02T11:00:00+00:00",
            model="sonnet",
            effort="high",
        )
        for i in range(RUNNING_TASKS)
    }
    tasks["done"] = TaskRunState(status="succeeded", attempts=1, dispatch_cycle=1)
    run_dir = write_run(ws, make_run_state(RUN_ID, status="running", tasks=tasks))
    for i in range(RUNNING_TASKS):
        d = run_dir / f"worker-{i}" / "attempt-1"
        d.mkdir(parents=True)
        (d / "transcript.jsonl").write_text(_line(f"m{i}a") + _line(f"m{i}b"))
    d = run_dir / "done" / "attempt-1"
    d.mkdir(parents=True)
    (d / "result.json").write_text(json.dumps({"type": "result", "num_turns": 12, "usage": {}}))

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agent_orchestrator.cli",
            "ui",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workspace",
            str(ws),
        ],
        cwd=str(ws),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        start_new_session=True,
    )
    if not _wait_for(
        lambda: httpx.get(f"{base}/api/health", timeout=2.0).status_code == 200, SERVER_BOOT_TIMEOUT
    ):
        proc.kill()
        pytest.fail("dashboard server did not start")
    try:
        yield base, ws
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), 15)
            proc.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired, OSError):
            proc.kill()
        if proc.stdout:
            proc.stdout.close()


def test_activity_follows_a_growing_transcript_over_real_http(staged_server) -> None:
    base, ws = staged_server
    first = httpx.get(f"{base}/api/runs/{RUN_ID}/activity", timeout=5).json()
    rows = first["tasks"]
    assert {t for t, r in rows.items() if r["status"] == "running"} == {
        f"worker-{i}" for i in range(RUNNING_TASKS)
    }
    assert rows["worker-0"]["turns"] == 2 and rows["worker-0"]["source"] == "transcript"
    assert rows["done"]["turns"] == 12 and rows["done"]["source"] == "result"

    transcript = (
        ws / ".orchestrator" / "runs" / RUN_ID / "worker-0" / "attempt-1" / "transcript.jsonl"
    )
    with transcript.open("a") as handle:
        handle.write(_line("m0c") + _line("m0d", "x" * 40))
    second = httpx.get(f"{base}/api/runs/{RUN_ID}/activity", timeout=5).json()["tasks"]
    assert second["worker-0"]["turns"] == 4
    assert second["worker-1"]["turns"] == 2

    listing = httpx.get(f"{base}/api/runs", timeout=5).json()
    assert len(listing[0]["running_tasks"]) == RUNNING_TASKS
    detail = httpx.get(f"{base}/api/runs/{RUN_ID}", timeout=5).json()
    assert {t["model"] for t in detail["tasks"] if t["status"] == "running"} == {"sonnet"}


def test_hash_deep_links_are_served_by_the_spa_fallback(staged_server) -> None:
    """Phase 2 premise: a hash never reaches the server, and any deep path serves index.html."""
    base, _ = staged_server
    for path in ("/", "/run", "/anything/deep"):
        response = httpx.get(f"{base}{path}", timeout=5)
        assert response.status_code == 200 and '<div id="root">' in response.text
    assert httpx.get(f"{base}/api/runs/nope/activity", timeout=5).status_code == 404
