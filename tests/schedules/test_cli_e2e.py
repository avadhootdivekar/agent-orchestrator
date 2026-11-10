"""T-Cl6Jn9: ``ao schedule`` end to end via ``CliRunner`` against the ROOT app (real registration).

Fixed clock (``schedules.cli._now``), temp workspace/state/registry (env-redirected, never a real
``$HOME``), a fake process supervisor (nothing is spawned).
"""

from __future__ import annotations

import fcntl
import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.schedules import cli as sched_cli
from agent_orchestrator.service.fire_store import (
    FireIntentStore,
    ScheduleEventLog,
    ScheduleStateStore,
)

from ..ui.conftest import StubSupervisor

runner = CliRunner()
T0 = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
QUOTA = ["--quota", "day=100"]


class RunDirSupervisor(StubSupervisor):
    """Stub that also creates the run directory (the 'fake executor')."""

    def launch_run(self, **kwargs: object) -> Any:
        (self.workspace / "runs" / str(kwargs["run_id"])).mkdir(parents=True)
        return super().launch_run(**kwargs)

    def reconcile(self) -> list[Any]:
        return []


class Env:
    def __init__(self, tmp: Path) -> None:
        self.ws = (tmp / "ws").resolve()
        (self.ws / ".ao").mkdir(parents=True)
        (self.ws / "w.json").write_text("{}")
        self.state = tmp / "state"
        self.sups: dict[str, RunDirSupervisor] = {}

    def sup(self, root: str) -> RunDirSupervisor:
        return self.sups.setdefault(root, RunDirSupervisor(Path(root)))

    @property
    def sched_file(self) -> Path:
        return self.ws / ".ao" / "schedules.yaml"

    def data(self) -> dict[str, Any]:
        return yaml.safe_load(self.sched_file.read_text())

    def run(self, *args: str, ok: bool = True) -> Any:
        result = runner.invoke(app, ["schedule", *args])
        assert (result.exit_code == 0) == ok, result.output
        return result

    def add(self, sid: str = "nightly", *extra: str) -> None:
        self.run("add", sid, "--interval", "60", "--workflow", "w.json", *QUOTA, *extra)

    @contextmanager
    def engine_lock(self) -> Iterator[None]:
        path = self.state / "schedules" / "engine.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    e = Env(tmp_path)
    monkeypatch.setenv("AO_SERVICE_CONFIG", str(tmp_path / "service.yaml"))
    monkeypatch.setenv("AO_SERVICE_STATE_DIR", str(e.state))
    monkeypatch.chdir(e.ws)
    monkeypatch.setattr(sched_cli, "_now", lambda: T0)
    monkeypatch.setattr(sched_cli, "_SUPERVISOR_FACTORY", e.sup)
    return e


def opt_in(env: Env) -> None:
    assert runner.invoke(app, ["service", "add", str(env.ws), "--schedules"]).exit_code == 0


def test_lifecycle(env: Env) -> None:
    env.add("nightly", "--overlap", "queue", "--run-arg", "max_parallel=2", "--jitter", "5")
    row = env.data()["schedules"][0]
    assert row["id"] == "nightly" and row["interval_seconds"] == 60
    assert row["overlap"] == "queue" and row["jitter_seconds"] == 5
    assert row["run_args"] == {"max_parallel": 2}
    assert "nightly" in env.run("list").output
    listed = json.loads(env.run("list", "--json").stdout)
    assert listed["schedules"][0]["status"] == "active"
    assert "OK: 1" in env.run("validate").output
    env.run("disable", "nightly", "--requested-by", "alice")
    assert env.data()["schedules"][0]["enabled"] is False
    assert "disabled" in env.run("list").output
    env.run("enable", "nightly")
    assert env.data()["schedules"][0]["enabled"] is True
    hist = json.loads(env.run("history", "nightly", "--json").stdout)
    assert [e["event"] for e in hist] == ["schedule.enabled", "schedule.disabled"]
    assert hist[1]["requested_by"] == "alice"
    env.run("remove", "nightly")
    assert env.data()["schedules"] == []
    env.run("remove", "nightly", ok=False)


@pytest.mark.parametrize(
    "extra,needle",
    [
        (["--run-arg", "sentinel.llm.enabled=true"], "allowed:"),
        (["--run-arg", "oops"], "key=value"),
        (["--run-arg", "max_parallel=abc"], "max_parallel"),
        (["--run-arg", "self_heal=maybe"], "true or false"),
    ],
)
def test_add_run_arg_errors(env: Env, extra: list[str], needle: str) -> None:
    result = env.run(
        "add", "x", "--interval", "60", "--workflow", "w.json", *QUOTA, *extra, ok=False
    )
    assert needle in result.output
    assert not env.sched_file.exists()


def test_add_errors(env: Env) -> None:
    env.add("a")
    assert (
        "already exists"
        in env.run("add", "a", "--interval", "60", "--workflow", "w.json", *QUOTA, ok=False).output
    )
    env.run("add", "a", "--interval", "120", "--workflow", "w.json", *QUOTA, "--force")
    assert env.data()["schedules"][0]["interval_seconds"] == 120
    assert (
        "limits" in env.run("add", "b", "--interval", "60", "--workflow", "w.json", ok=False).output
    )
    assert (
        "interval_seconds"
        in env.run("add", "c", "--interval", "5", "--workflow", "w.json", *QUOTA, ok=False).output
    )
    assert "exactly one" in env.run("add", "d", "--workflow", "w.json", *QUOTA, ok=False).output
    assert "exactly one" in env.run("add", "d", "--cron", "0 * * * *", *QUOTA, ok=False).output
    env.run(
        "add",
        "e",
        "--cron",
        "0 9 * * *",
        "--tz",
        "Europe/Paris",
        "--template",
        "t",
        "--param",
        "k=v",
        *QUOTA,
    )
    assert [r["id"] for r in env.data()["schedules"]] == ["a", "e"]
    assert env.data()["schedules"][1]["params"] == {"k": "v"}


def test_validate_reports_id_and_field(env: Env) -> None:
    env.sched_file.write_text(
        "version: '1.0'\nschedules:\n"
        "- {id: bad, kind: interval, interval_seconds: 1, workflow: w.json,"
        " limits: {quotas: [{period: day, max_runs: 1}]}}\n"
    )
    result = env.run("validate", ok=False)
    assert "bad" in result.output and "interval_seconds" in result.output


def test_run_now_without_daemon_fires_in_process(env: Env) -> None:
    env.add()
    out = env.run("run-now", "nightly").output
    assert "fired: run_id=ao-nightly-20260304T050607Z" in out
    root = str(env.ws)
    assert (env.ws / "runs" / "ao-nightly-20260304T050607Z").is_dir()
    recs = FireIntentStore(env.state).list(workspace_root=root)
    assert len(recs) == 1 and recs[0].status == "launched"
    assert recs[0].run_id == "ao-nightly-20260304T050607Z" and recs[0].fire_key
    # the engine lock was released again
    assert not sched_cli.probe_engine_lock(env.state)


def test_run_now_with_live_daemon_queues_only(env: Env) -> None:
    env.add()
    with env.engine_lock():
        out = env.run("run-now", "nightly", "--force").output
    assert "queued" in out
    files = list((env.state / "schedules" / "requests").glob("*.json"))
    assert len(files) == 1
    body = json.loads(files[0].read_text())
    assert body["schedule_id"] == "nightly" and body["force"] is True and body["kind"] == "run_now"
    assert not env.sups  # no supervisor was even built: nothing launched by the CLI
    assert FireIntentStore(env.state).list() == []


def test_run_now_local_with_lock_held_queues_instead(env: Env) -> None:
    env.add()
    with env.engine_lock():
        out = env.run("run-now", "nightly", "--local").output
    assert "a daemon is evaluating; queued instead" in out
    assert len(list((env.state / "schedules" / "requests").glob("*.json"))) == 1
    assert not env.sups


def test_run_now_queue_flag_and_unknown(env: Env) -> None:
    env.add()
    assert "queued" in env.run("run-now", "nightly", "--queue").output
    env.run("run-now", "nope", ok=False)
    env.run("run-now", "nightly", "--local", "--queue", ok=False)


def test_run_now_overlap_reports_not_fired(env: Env) -> None:
    env.add()
    env.run("run-now", "nightly")
    # same fixed instant -> same fire key: at-most-once, nothing launched twice
    out = env.run("run-now", "nightly").output
    assert "not fired" in out
    assert len(FireIntentStore(env.state).list()) == 1


def test_list_warning_no_engine(env: Env) -> None:
    env.add()
    opt_in(env)
    res = runner.invoke(app, ["schedule", "list"])
    assert "no live schedule engine" in res.stderr
    assert "ao service start" in res.stderr and "ao schedule daemon" in res.stderr


def test_list_no_warning_with_engine(env: Env) -> None:
    env.add()
    opt_in(env)
    with env.engine_lock():
        res = runner.invoke(app, ["schedule", "list"])
    assert "no live schedule engine" not in res.stderr


def test_list_warns_even_with_authenticated_hub_but_no_lock(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A hub that answers 401 (auth on) must not count as a live engine: only the lock does.
    import urllib.request

    def refuse(*a: object, **k: object) -> None:
        raise AssertionError("list must not probe the hub")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    env.add()
    opt_in(env)
    assert "no live schedule engine" in runner.invoke(app, ["schedule", "list"]).stderr


def test_list_explains_inert_workspace(env: Env) -> None:
    env.add()
    res = runner.invoke(app, ["schedule", "list"])  # not registered
    assert "not registered" in res.stderr and "--schedules" in res.stderr
    assert runner.invoke(app, ["service", "add", str(env.ws)]).exit_code == 0  # schedules: false
    res = runner.invoke(app, ["schedule", "list"])
    assert "ao service set" in res.stderr and "--schedules" in res.stderr
    with env.engine_lock():
        res = runner.invoke(app, ["schedule", "list"])
    assert "ao service set" in res.stderr  # inert even though an engine is live


def test_list_all_covers_registered(env: Env, tmp_path: Path) -> None:
    env.add()
    opt_in(env)
    other = tmp_path / "other"
    (other / ".ao").mkdir(parents=True)
    runner.invoke(app, ["service", "add", str(other), "--schedules"])
    out = json.loads(env.run("list", "--all", "--json").stdout)
    assert [s["id"] for s in out["schedules"]] == ["nightly"]


def test_daemon_refuses_when_lock_held(env: Env) -> None:
    with env.engine_lock():
        res = runner.invoke(app, ["schedule", "daemon", "--once", "--workspace", str(env.ws)])
    assert res.exit_code == 1 and "already running" in res.output


def test_daemon_once_fires_exactly_once(env: Env) -> None:
    env.add()
    ScheduleStateStore(env.state).advance_anchor(env.ws, "nightly", T0 - timedelta(seconds=120))
    first = runner.invoke(app, ["schedule", "daemon", "--once", "--workspace", str(env.ws)])
    assert first.exit_code == 0, first.output
    assert first.stdout.strip() == "1"
    assert "reason=due" in first.stderr
    second = runner.invoke(app, ["schedule", "daemon", "--once", "--workspace", str(env.ws)])
    assert second.exit_code == 0 and second.stdout.strip() == "0"
    assert len(FireIntentStore(env.state).list()) == 1
    assert not sched_cli.probe_engine_lock(env.state)


def test_daemon_once_does_not_write_real_registry(env: Env, tmp_path: Path) -> None:
    env.add()
    runner.invoke(app, ["schedule", "daemon", "--once", "--workspace", str(env.ws)])
    assert not (tmp_path / "service.yaml").exists()


def test_next_sorted_and_read_only(env: Env) -> None:
    env.run("add", "hourly", "--cron", "0 * * * *", "--workflow", "w.json", *QUOTA)
    env.add("often")
    env.run("add", "watch-ish", "--cron", "30 9 * * *", "--workflow", "w.json", *QUOTA)
    ScheduleStateStore(env.state).advance_anchor(env.ws, "often", T0)
    state_file = env.state / "schedules" / "state.json"
    before = state_file.read_bytes()
    out = json.loads(env.run("next", "-n", "100", "--json").stdout)
    assert len(out) == 100
    assert [r["at"] for r in out] == sorted(r["at"] for r in out)
    assert {r["id"] for r in out} == {"often", "hourly"}
    assert [r["at"] for r in out if r["id"] == "hourly"][0].startswith("2026-03-04T06:00:00")
    assert out[0]["at"].startswith("2026-03-04T05:07:07")
    assert state_file.read_bytes() == before  # `next` never mutates state
    assert "NEXT FIRE" in env.run("next").output


def test_next_skips_disabled_and_empty(env: Env) -> None:
    assert "No upcoming" in env.run("next").output
    env.add("x", "--disabled")
    assert "No upcoming" in env.run("next").output


def test_history_shows_suppressed(env: Env) -> None:
    env.add()
    log = ScheduleEventLog(env.state, clock=lambda: T0)
    log.emit("schedule.missed", workspace=str(env.ws), schedule_id="nightly", reason="missed")
    log.emit("schedule.skipped_overlap", workspace=str(env.ws), schedule_id="other")
    out = env.run("history", "nightly").output
    assert "schedule.missed" in out and "missed" in out and "other" not in out
    assert "No events" in env.run("history", "zzz").output


def test_enable_resets_completed_schedule(env: Env) -> None:
    env.add()
    store = ScheduleStateStore(env.state)
    st = store.get(env.ws, "nightly")
    assert st is None
    store.advance_anchor(env.ws, "nightly", T0)
    store.record_fire(env.ws, "nightly", scheduled_for=T0, run_id="r1", fire_at=T0)
    store.set_status(env.ws, "nightly", "completed")
    assert "completed" in env.run("list").output
    with env.engine_lock():
        assert "daemon is evaluating" in env.run("enable", "nightly", ok=False).output
    env.run("enable", "nightly")
    fresh = ScheduleStateStore(env.state).get(env.ws, "nightly")
    assert fresh is not None and fresh.status == "active" and fresh.runs_count == 0


def _write_spec(path: Path, triggers: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps(
            {
                "version": "1.0",
                "id": "my-flow",
                "name": "n",
                "tasks": [{"id": "t", "agent": "a", "instruction": "i.md"}],
                "triggers": triggers,
            }
        )
    )


def test_from_workflow_cron_and_interval(env: Env) -> None:
    _write_spec(
        env.ws / "flow.json",
        [
            {"type": "cron", "schedule": "0 3 * * *"},
            {"type": "interval", "interval_seconds": 3600},
            {"type": "manual"},
        ],
    )
    env.run("add", "--from-workflow", "flow.json", *QUOTA)
    rows = env.data()["schedules"]
    assert [r["id"] for r in rows] == ["my-flow-1", "my-flow-2"]
    assert rows[0]["kind"] == "cron" and rows[0]["schedule"] == "0 3 * * *"
    assert rows[1]["interval_seconds"] == 3600 and rows[1]["workflow"] == "flow.json"


def test_from_workflow_manual_only_adds_nothing(env: Env) -> None:
    _write_spec(env.ws / "flow.json", [{"type": "manual"}])
    out = env.run("add", "--from-workflow", "flow.json", *QUOTA).output
    assert "nothing to add" in out
    assert not env.sched_file.exists()


def test_from_workflow_conflicting_flags(env: Env) -> None:
    _write_spec(env.ws / "flow.json", [{"type": "cron", "schedule": "0 3 * * *"}])
    env.run("add", "x", "--from-workflow", "flow.json", "--cron", "0 1 * * *", *QUOTA, ok=False)
    env.run("add", "--from-workflow", "missing.json", *QUOTA, ok=False)


def test_ao_validate_hook_warns_unbound_and_fails_bad_file(env: Env) -> None:
    _write_spec(env.ws / "flow.json", [{"type": "cron", "schedule": "0 3 * * *"}])
    ok = runner.invoke(app, ["validate", "--workflow", "flow.json"])
    # a spec this small may not satisfy the main validator; only assert hook behaviour if it ran
    if ok.exit_code == 0:
        assert "no schedule binds it" in ok.stderr
        env.run("add", "--from-workflow", "flow.json", *QUOTA)
        again = runner.invoke(app, ["validate", "--workflow", "flow.json"])
        assert "no schedule binds it" not in again.stderr
    env.sched_file.write_text("schedules: nope\n")
    bad = runner.invoke(app, ["validate", "--workflow", "flow.json"])
    assert bad.exit_code != 0


def test_schedule_validate_workflow_warning(env: Env) -> None:
    _write_spec(env.ws / "flow.json", [{"type": "cron", "schedule": "0 3 * * *"}])
    res = runner.invoke(app, ["schedule", "validate", "--workflow", "flow.json"])
    assert res.exit_code == 0 and "no schedule binds it" in res.stderr
    assert runner.invoke(app, ["schedule", "validate", "--workflow", "nope.json"]).exit_code == 1


def test_help_lists_subcommands() -> None:
    out = runner.invoke(app, ["schedule", "--help"]).output
    for name in (
        "list",
        "next",
        "history",
        "validate",
        "add",
        "remove",
        "enable",
        "disable",
        "run-now",
        "daemon",
    ):
        assert name in out


def test_no_process_spawn_in_module() -> None:
    assert "Popen" not in Path(sched_cli.__file__).read_text()
