"""T-Te3Qw8 AC8a (F9): ``run-now --local`` queues instead of firing while a REAL engine holds
``engine.lock`` (the sibling test in ``test_cli_e2e`` holds a bare flock; this one holds it via
``ScheduleEngine.start()``, the very path the service / ``ao schedule daemon`` takes)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.schedules import cli as sched_cli
from agent_orchestrator.service.fire_store import FireIntentStore
from agent_orchestrator.service.schedule_engine import ScheduleEngine, probe_engine_lock

from ._fixtures import pin_engine_monotonic
from .test_cli_e2e import Env

runner = CliRunner()
T0 = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)


@pytest.fixture()
def lenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    e = Env(tmp_path)
    monkeypatch.setenv("AO_SERVICE_CONFIG", str(tmp_path / "service.yaml"))
    monkeypatch.setenv("AO_SERVICE_STATE_DIR", str(e.state))
    monkeypatch.chdir(e.ws)
    monkeypatch.setattr(sched_cli, "now_utc", lambda: T0)
    monkeypatch.setattr(sched_cli, "_SUPERVISOR_FACTORY", e.sup)
    pin_engine_monotonic(monkeypatch)
    return e


def test_run_now_local_queues_while_real_engine_holds_lock_then_fires_after_release(
    lenv: Env, tmp_path: Path
) -> None:
    lenv.add()
    holder = ScheduleEngine(tmp_path / "service.yaml", state_dir=lenv.state)
    holder.start()
    try:
        assert probe_engine_lock(lenv.state)
        res = runner.invoke(app, ["schedule", "run-now", "nightly", "--local"])
        assert res.exit_code == 0, res.output
        assert "a daemon is evaluating; queued instead" in res.output
        files = list((lenv.state / "schedules" / "requests").glob("*.json"))
        assert len(files) == 1
        assert json.loads(files[0].read_text())["kind"] == "run_now"
        assert not lenv.sups  # the CLI launched nothing
        assert FireIntentStore(lenv.state).list() == []
    finally:
        holder.shutdown()
    # lock released: the same command now fires in-process (control for the assertion above)
    assert not probe_engine_lock(lenv.state)
    res = runner.invoke(app, ["schedule", "run-now", "nightly", "--local"])
    assert res.exit_code == 0 and "fired: run_id=" in res.output, res.output
    assert len(FireIntentStore(lenv.state).list()) == 1
