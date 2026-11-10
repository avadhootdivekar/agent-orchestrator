"""T-88wSLC: governance (quota / prompt / skip-record / history) end to end via ``CliRunner``.

Real ``ao`` root app, real ``ScheduleEngine`` + gate + ledger, fixed injected clock
(``schedules.cli._now``), temp workspace/state (never a real ``$HOME``), stub supervisor (nothing is
spawned). No sleeps. Scope: ``ao schedule usage`` / ``resume`` and the real renderer's
``--cost-cap-usd`` are not committed yet, so per-run cost-cap and pause/resume e2e cases are covered
at engine level (tests/test_engine_cost_cap.py, tests/service/test_governance_gate.py) and listed
UNFINISHED in the ticket.
"""

# ruff: noqa: F811

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.schedules import cli as sched_cli
from agent_orchestrator.service.fire_store import FireIntentStore, ScheduleStateStore

from .test_cli_e2e import Env, env  # noqa: F401  # ``env`` is the shared fixture

runner = CliRunner()
T0 = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)
PROMPT = "Summarise the nightly report.\nSecond line ✓\n"


def _tick(env: Env, monkeypatch: pytest.MonkeyPatch, at: datetime) -> int:
    monkeypatch.setattr(sched_cli, "_now", lambda: at)
    res = runner.invoke(app, ["schedule", "daemon", "--once", "--workspace", str(env.ws)])
    assert res.exit_code == 0, res.output
    return int(res.stdout.strip())


def _events(sid: str = "nightly") -> list[dict]:
    res = runner.invoke(app, ["schedule", "history", sid, "--json", "-n", "100"])
    assert res.exit_code == 0, res.output
    return json.loads(res.stdout)


def _arm(env: Env, at: datetime) -> None:
    ScheduleStateStore(env.state).advance_anchor(env.ws, "nightly", at - timedelta(seconds=60))


def test_add_with_no_limits_is_refused_and_writes_nothing(env: Env) -> None:
    res = env.run("add", "nightly", "--interval", "60", "--workflow", "w.json", ok=False)
    assert "limits" in res.output
    assert not env.sched_file.exists() or env.data()["schedules"] == []


def test_add_persists_quota_cost_and_prompt_pin(env: Env) -> None:
    (env.ws / "p.md").write_text(PROMPT)
    env.run(
        "add", "nightly", "--interval", "60", "--workflow", "w.json", "--quota", "day=2",
        "--per-run-usd", "1.00", "--prompt-file", "p.md",
    )  # fmt: skip
    row = env.data()["schedules"][0]
    assert row["limits"]["quotas"] == [{"period": "day", "max_runs": 2}]
    assert row["limits"]["cost"]["per_run_usd"] == 1.0
    assert row["prompt_file"] == "p.md"


def test_headline_quota_two_runs_then_third_tick_suppressed_quota_exhausted(
    env: Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (env.ws / "p.md").write_text(PROMPT)
    env.run(
        "add", "nightly", "--interval", "60", "--workflow", "w.json", "--quota", "day=2",
        "--prompt-file", "p.md", "--overlap", "allow",
    )  # fmt: skip
    _arm(env, T0)
    fired = [_tick(env, monkeypatch, T0 + timedelta(seconds=60 * i)) for i in range(3)]
    assert fired == [1, 1, 0]
    calls = env.sup(str(env.ws)).launch_calls
    assert len(calls) == 2
    assert all(c["prompt"] == PROMPT for c in calls)  # bytes pass verbatim
    assert len(list((env.ws / "runs").iterdir())) == 2
    assert any(e.get("reason") == "quota_exhausted" for e in _events())
    assert "quota_exhausted" in env.run("history", "nightly").output
    started = [r for r in FireIntentStore(env.state).list() if r.status != "suppressed"]
    assert len(started) == 2


@pytest.mark.parametrize(
    ("tz", "prior", "nxt", "admit"),
    [
        # Europe/Berlin (CET, UTC+1): local midnight is 23:00Z
        ("Europe/Berlin", datetime(2026, 1, 10, 12, 0, tzinfo=UTC),
         datetime(2026, 1, 10, 22, 59, tzinfo=UTC), False),
        ("Europe/Berlin", datetime(2026, 1, 10, 12, 0, tzinfo=UTC),
         datetime(2026, 1, 10, 23, 0, tzinfo=UTC), True),
        # America/New_York fall-back 2026-11-01: 01:30 EDT then 01:30 EST, same local day
        ("America/New_York", datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
         datetime(2026, 11, 1, 6, 30, tzinfo=UTC), False),
        ("America/New_York", datetime(2026, 11, 1, 5, 30, tzinfo=UTC),
         datetime(2026, 11, 2, 6, 30, tzinfo=UTC), True),
    ],
)  # fmt: skip
def test_day_quota_boundaries_at_cli_tier(
    env: Env, monkeypatch: pytest.MonkeyPatch,
    tz: str, prior: datetime, nxt: datetime, admit: bool,
) -> None:  # fmt: skip
    env.run(
        "add", "nightly", "--interval", "60", "--workflow", "w.json", "--quota", "day=1",
        "--overlap", "allow", "--tz", tz,
    )  # fmt: skip
    _arm(env, prior)
    assert _tick(env, monkeypatch, prior) == 1
    _arm(env, nxt)
    assert _tick(env, monkeypatch, nxt) == (1 if admit else 0)
    reasons = [e.get("reason") for e in _events()]
    assert ("quota_exhausted" in reasons) is (not admit)


def test_no_schedules_file_emits_no_governance_events(env: Env) -> None:
    assert not env.sched_file.exists()
    res = runner.invoke(app, ["schedule", "daemon", "--once", "--workspace", str(env.ws)])
    assert res.exit_code == 0 and res.stdout.strip() == "0"
    res = runner.invoke(app, ["schedule", "history", "--json"])
    events = json.loads(res.stdout) if res.stdout.strip() else []
    assert not [e for e in events if str(e.get("event", "")).startswith("schedule.governance")]


def test_each_tick_rebuilds_from_disk_so_quota_count_survives_restart(
    env: Env,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every ``daemon --once`` rebuilds engine, ledger and stores from disk: skips on ticks 3 and 4
    after two fires prove the persisted records alone carry the quota count."""
    env.run(
        "add", "nightly", "--interval", "60", "--workflow", "w.json", "--quota", "day=2",
        "--overlap", "allow",
    )  # fmt: skip
    _arm(env, T0)
    ticks = [_tick(env, monkeypatch, T0 + timedelta(seconds=60 * i)) for i in range(4)]
    assert ticks == [1, 1, 0, 0]
