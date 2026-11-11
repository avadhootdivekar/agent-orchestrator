"""T-Te3Qw8: per-trigger end-to-end tests via ``ao schedule daemon --once`` (root ``CliRunner``).

Each test drives the *real* engine + stores through the CLI boundary against a temp workspace and
temp state dir (env-redirected: never a real ``$HOME``). Time enters only through
``schedules.cli._now`` (fixed ``T0``), file mtimes are pinned with ``os.utime``, the webhook
secret is an env var, and the process supervisor is the ``RunDirSupervisor`` stub (it creates the
run directory; nothing is spawned). Not covered here: a real agent CLI, real uvicorn, systemd,
the network (see the T-Te3Qw8 STATUS "not exercised" list).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.schedules import cli as sched_cli
from agent_orchestrator.service.fire_store import (
    FireIntentStore,
    ScheduleEventLog,
    ScheduleStateStore,
)
from agent_orchestrator.service.supervisor import _slug_for_root
from agent_orchestrator.service.webhook import (
    WebhookRequestQueue,
    WebhookTarget,
    build_webhook_app,
    compute_signature,
)

from .test_cli_e2e import QUOTA, T0, Env

runner = CliRunner()
SECRET = "e2e-secret-value-0123456789abcdef"
SECRET_ENV = "AO_E2E_WEBHOOK_SECRET"
EPOCH = datetime(2026, 3, 4, 5, 0, 0, tzinfo=UTC)  # pinned mtime of watched files (before T0)


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    e = Env(tmp_path)
    monkeypatch.setenv("AO_SERVICE_CONFIG", str(tmp_path / "service.yaml"))
    monkeypatch.setenv("AO_SERVICE_STATE_DIR", str(e.state))
    monkeypatch.setenv(SECRET_ENV, SECRET)
    monkeypatch.chdir(e.ws)
    monkeypatch.setattr(sched_cli, "_now", lambda: T0)
    monkeypatch.setattr(sched_cli, "_SUPERVISOR_FACTORY", e.sup)
    return e


def once(env: Env) -> tuple[int, str]:
    """One ``daemon --once`` pass: (decisions printed on stdout, stderr)."""
    res = runner.invoke(app, ["schedule", "daemon", "--once", "--workspace", str(env.ws)])
    assert res.exit_code == 0, res.output
    return int(res.stdout.strip()), res.stderr


def fires(env: Env) -> list[Any]:
    return FireIntentStore(env.state).list(workspace_root=str(env.ws))


def run_dirs(env: Env) -> list[str]:
    runs = env.ws / "runs"
    return sorted(p.name for p in runs.iterdir()) if runs.is_dir() else []


def due(env: Env, sid: str, seconds: int = 120) -> None:
    ScheduleStateStore(env.state).advance_anchor(env.ws, sid, T0 - timedelta(seconds=seconds))


def add_binding(env: Env, row: dict[str, Any]) -> None:
    """Append a raw binding (kinds the CLI cannot add, e.g. file_watch) to schedules.yaml."""
    data = yaml.safe_load(env.sched_file.read_text()) if env.sched_file.exists() else {}
    data = data or {"schedules": []}
    data["schedules"].append(
        {"workflow": "w.json", "limits": {"quotas": [{"period": "day", "max_runs": 100}]}, **row}
    )
    env.sched_file.write_text(yaml.safe_dump(data))


def pin_mtime(path: Path, when: datetime) -> None:
    os.utime(path, (when.timestamp(), when.timestamp()))


# -- cron ---------------------------------------------------------------------------------------


def test_cron_due_fires_once_then_not_again_until_next_slot(env: Env) -> None:
    env.run("add", "tick5", "--cron", "*/5 * * * *", "--workflow", "w.json", *QUOTA)
    due(env, "tick5", 600)  # last evaluated 10 minutes ago -> a slot has elapsed
    n, err = once(env)
    assert n == 1 and "reason=due" in err
    [rec] = fires(env)
    assert rec.status == "launched" and rec.run_id is not None
    assert rec.run_id.startswith("ao-tick5-")
    assert run_dirs(env) == [rec.run_id]
    # same clock: the slot is consumed (at-most-once), nothing else is due
    assert once(env)[0] == 0
    assert len(fires(env)) == 1 and len(run_dirs(env)) == 1


def test_cron_not_due_fires_nothing(env: Env) -> None:
    env.run("add", "later", "--cron", "0 0 1 1 *", "--workflow", "w.json", *QUOTA)
    due(env, "later", 10)
    assert once(env)[0] == 0
    assert fires(env) == [] and run_dirs(env) == []


# -- interval (the headline) --------------------------------------------------------------------


def test_interval_fires_a_run_with_the_constructed_id_then_zero(env: Env) -> None:
    env.add("nightly")
    due(env, "nightly")
    assert once(env)[0] == 1
    [rec] = fires(env)
    assert rec.status == "launched" and rec.fire_key
    # ao-<schedule_id>-<UTC ts>-<fire_key[:6]> (T-Lp4Wt6 / H1): the UTC-normalised scheduled_for
    assert rec.run_id is not None
    parts = rec.run_id.split("-")
    assert parts[:2] == ["ao", "nightly"] and parts[2].endswith("Z")
    assert parts[-1] == rec.fire_key[:6]
    assert (env.ws / "runs" / rec.run_id).is_dir()  # the run directory exists
    sup = env.sups[str(env.ws)]
    assert [c["run_id"] for c in sup.launch_calls] == [rec.run_id]  # via the explicit run_id= seam
    assert once(env)[0] == 0  # second --once immediately: zero fires
    assert len(fires(env)) == 1


def test_disabled_interval_never_fires(env: Env) -> None:
    env.add("nightly")
    env.run("disable", "nightly")
    due(env, "nightly")
    assert once(env)[0] == 0 and fires(env) == []


# -- file watch ---------------------------------------------------------------------------------


def _watch(env: Env, debounce: int = 30) -> Path:
    (env.ws / "inbox").mkdir()
    add_binding(
        env,
        {
            "id": "watcher",
            "kind": "file_watch",
            "watch": {"paths": ["inbox/*.md"], "debounce_seconds": debounce, "poll_seconds": 1},
        },
    )
    return env.ws / "inbox"


def test_file_watch_fires_once_per_change_and_ignores_orchestrator_dir(env: Env) -> None:
    inbox = _watch(env)
    f = inbox / "a.md"
    f.write_text("hello")
    pin_mtime(f, EPOCH)  # 67s before T0: already past the 30s debounce
    n, _ = once(env)
    assert n == 1
    [rec] = fires(env)
    assert rec.status == "launched" and rec.run_id and rec.run_id.startswith("ao-watcher-")
    assert len(run_dirs(env)) == 1
    # no change -> no fire
    assert once(env)[0] == 0 and len(fires(env)) == 1
    # self-trigger guard: writes under .orchestrator/ never fire
    orch = env.ws / ".orchestrator" / "runs"
    orch.mkdir(parents=True)
    (orch / "inbox.md").write_text("x")
    assert once(env)[0] == 0 and len(fires(env)) == 1


def test_file_watch_ignores_orchestrator_dir_even_when_the_glob_matches(env: Env) -> None:
    """w21-05 review: the guard check above uses ``inbox/*.md``, which can never match
    ``.orchestrator/runs/inbox.md`` -- it passed with the ignore set removed. Use a glob that
    does match so only the ``.orchestrator`` guard can keep it from firing."""
    add_binding(
        env,
        {
            "id": "watcher",
            "kind": "file_watch",
            "watch": {"paths": ["**/*.md"], "debounce_seconds": 30, "poll_seconds": 1},
        },
    )
    assert once(env)[0] == 0
    orch = env.ws / ".orchestrator" / "runs"
    orch.mkdir(parents=True)
    (orch / "x.md").write_text("x")
    pin_mtime(orch / "x.md", EPOCH)
    assert once(env)[0] == 0 and fires(env) == []
    (env.ws / "y.md").write_text("y")  # positive control: a matching file elsewhere does fire
    pin_mtime(env.ws / "y.md", EPOCH)
    assert once(env)[0] == 1


def test_file_watch_within_debounce_defers_then_fires(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    inbox = _watch(env, debounce=300)
    f = inbox / "b.md"
    f.write_text("x")
    pin_mtime(f, T0 - timedelta(seconds=10))  # changed 10s ago, debounce 300s
    assert once(env)[0] == 0 and fires(env) == []
    monkeypatch.setattr(sched_cli, "_now", lambda: T0 + timedelta(seconds=400))  # advance clock
    assert once(env)[0] == 1
    assert len(fires(env)) == 1


# -- webhook (in-process listener -> separate --once engine) ------------------------------------


class _Hook:
    def __init__(self, env: Env, now: datetime) -> None:
        self.env, self.now = env, now
        self.slug = _slug_for_root(str(env.ws))
        queue = WebhookRequestQueue(env.state)
        self.client = TestClient(
            build_webhook_app(
                self._lookup, queue, clock=lambda: self.now, emit=lambda *a, **k: None
            ),
            base_url="http://localhost",
        )

    def _lookup(self, slug: str, sid: str) -> WebhookTarget | None:
        from agent_orchestrator.schedules.store import ScheduleStore

        if slug != self.slug:
            return None
        b = next((x for x in ScheduleStore(self.env.ws).load().schedules if x.id == sid), None)
        return WebhookTarget(str(self.env.ws), b) if b else None

    def post(self, body: bytes = b'{"n": 1}') -> Any:
        ts = str(int(self.now.timestamp()))
        return self.client.post(
            f"/hooks/{self.slug}/hook",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-AO-Timestamp": ts,
                "X-AO-Signature": compute_signature(SECRET.encode(), body, ts),
            },
        )


def _add_hook(env: Env) -> _Hook:
    env.run("add", "hook", "--webhook", "--secret-env", SECRET_ENV, "--workflow", "w.json", *QUOTA)
    return _Hook(env, T0)


def _requests(env: Env) -> list[Path]:
    d = env.state / "schedules" / "requests"
    return sorted(d.glob("*.json")) if d.is_dir() else []


def test_webhook_delivery_persisted_then_fired_by_separate_engine_replay_409(env: Env) -> None:
    hook = _add_hook(env)
    r = hook.post()
    assert r.status_code == 202
    [req] = _requests(env)  # fsync'd file, kind=webhook
    assert json.loads(req.read_text())["kind"] == "webhook"
    assert fires(env) == []  # the listener itself fires nothing
    # "crash" between the 202 and the next tick: nothing in memory, the file alone carries it
    n, _ = once(env)
    assert n == 1
    [rec] = fires(env)
    assert rec.status == "launched" and rec.run_id and rec.run_id.startswith("ao-hook-")
    assert len(run_dirs(env)) == 1
    # replayed identical delivery: 409, no second run
    assert hook.post().status_code == 409
    assert once(env)[0] == 0 and len(run_dirs(env)) == 1


def test_webhook_bad_signature_is_401_and_fires_nothing(env: Env) -> None:
    hook = _add_hook(env)
    ts = str(int(T0.timestamp()))
    r = hook.client.post(
        f"/hooks/{hook.slug}/hook",
        content=b"{}",
        headers={
            "Content-Type": "application/json",
            "X-AO-Timestamp": ts,
            "X-AO-Signature": "sha256=" + "0" * 64,
        },
    )
    assert r.status_code == 401
    assert _requests(env) == [] and once(env)[0] == 0 and run_dirs(env) == []


# -- until termination --------------------------------------------------------------------------


def test_until_max_runs_three_then_completed_then_enable_allows_more(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env.add("loop", "--max-runs", "3")
    now = T0
    for i in range(3):
        monkeypatch.setattr(sched_cli, "_now", lambda t=now: t)
        if i == 0:
            due(env, "loop")
        assert once(env)[0] == 1, f"fire {i + 1}"
        now += timedelta(seconds=120)
    assert len(fires(env)) == 3 and len(run_dirs(env)) == 3
    monkeypatch.setattr(sched_cli, "_now", lambda t=now: t)
    n, _ = once(env)
    assert len(fires(env)) == 3  # the 4th tick launched nothing
    assert n == 0
    listed = json.loads(env.run("list", "--json").stdout)
    assert listed["schedules"][0]["status"] == "completed"
    env.run("enable", "loop")
    monkeypatch.setattr(sched_cli, "_now", lambda t=now + timedelta(seconds=120): t)
    assert once(env)[0] == 1
    assert len(fires(env)) == 4


# -- daemon --once safety -----------------------------------------------------------------------


def test_daemon_once_with_no_schedules_file_is_a_clean_noop(env: Env) -> None:
    assert not env.sched_file.exists()
    assert once(env)[0] == 0
    assert fires(env) == [] and run_dirs(env) == []
    assert not sched_cli.probe_engine_lock(env.state)  # lock released


def test_daemon_once_unparseable_schedules_file_does_not_crash(env: Env) -> None:
    env.sched_file.write_text("schedules: [ {oops")
    n, _ = once(env)
    assert n == 0 and fires(env) == []
    # w21-05 review: the bad file is reported in the event log, not silently swallowed
    events = [e["event"] for e in ScheduleEventLog(env.state).read()]
    assert "schedule.file_invalid" in events
