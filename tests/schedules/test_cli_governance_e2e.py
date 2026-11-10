"""T-rwj4nV: governance CLI (limit flags, set-limits, pin-prompt, usage, resume, list/next/history,
validate warnings) end to end via ``CliRunner`` on the ROOT app. Fixed clock; temp workspace, state
dir and registry (never ``$HOME``); fire records and events are written directly (no process)."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.schedules import cli as sched_cli
from agent_orchestrator.schedules.usage_view import USAGE_SCHEMA
from agent_orchestrator.service.fire_store import (
    FireGovernance,
    FireIntentRecord,
    FireIntentStore,
    ScheduleEventLog,
)

runner = CliRunner()
NOW = datetime(2026, 11, 10, 11, 0, tzinfo=UTC)  # 12:00 in Berlin (CET)
TZ = "Europe/Berlin"
LIMIT_FLAGS = [
    "--quota", "day=3", "--quota", "week=10", "--cost-per-run", "2", "--on-cap", "pause",
    "--pause-ttl", "259200", "--cost-cap", "week=20", "--week-start", "monday",
]  # fmt: skip
USAGE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["schema", "generated_at", "workspace", "schedules"],
    "properties": {
        "schema": {"const": USAGE_SCHEMA},
        "schedules": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "id",
                    "tz",
                    "week_start",
                    "limits_digest",
                    "windows",
                    "paused",
                    "events",
                ],
                "properties": {
                    "windows": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": [
                                "period",
                                "runs",
                                "actual_usd",
                                "reserved_usd",
                                "resets_at",
                            ],
                        },
                    },
                    "paused": {"type": "array"},
                    "events": {"type": "array"},
                },
            },
        },
    },
}


class Env:
    def __init__(self, tmp: Path) -> None:
        self.ws = (tmp / "ws").resolve()
        (self.ws / ".ao").mkdir(parents=True)
        (self.ws / "w.json").write_text("{}")
        self.state = tmp / "state"

    @property
    def sched_file(self) -> Path:
        return self.ws / ".ao" / "schedules.yaml"

    def data(self) -> dict[str, Any]:
        return yaml.safe_load(self.sched_file.read_text())

    def run(self, *args: str, code: int = 0) -> Any:
        result = runner.invoke(app, ["schedule", *args])
        assert result.exit_code == code, result.output
        return result

    def add(self, sid: str = "daily", *extra: str, code: int = 0) -> Any:
        return self.run(
            "add", sid, "--interval", "3600", "--workflow", "w.json", "--tz", TZ, *extra, code=code
        )

    @property
    def fires(self) -> FireIntentStore:
        return FireIntentStore(self.state)

    def fire(self, key: str, when: datetime, run: str | None = None, **gov: Any) -> None:
        g = FireGovernance(
            tz=TZ, week_start="monday", reserved_usd=gov.pop("reserved", 2.0),
            run_cost_cap_usd=2.0, cost_observed_usd=gov.pop("cost", 0.0),
            cost_confidence="actual", **gov,
        )  # fmt: skip
        self.fires.write(
            FireIntentRecord(
                fire_key=key, workspace_root=str(self.ws), schedule_id="daily", scheduled_for=when,
                created_at=when, status="launched", run_id=run or key, governance=g,
            )
        )  # fmt: skip

    def event(self, name: str, ts: datetime, **fields: Any) -> None:
        ScheduleEventLog(self.state, clock=lambda: ts).emit(
            name, workspace=str(self.ws), schedule_id="daily", **fields
        )

    def files_mtimes(self) -> dict[str, int]:
        return {
            str(p): p.stat().st_mtime_ns
            for base in (self.ws, self.state)
            if base.exists()
            for p in base.rglob("*")
            if p.is_file()
        }


@pytest.fixture()
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Env:
    e = Env(tmp_path)
    monkeypatch.setenv("AO_SERVICE_CONFIG", str(tmp_path / "service.yaml"))
    monkeypatch.setenv("AO_SERVICE_STATE_DIR", str(e.state))
    monkeypatch.chdir(e.ws)
    monkeypatch.setattr(sched_cli, "_now", lambda: NOW)
    return e


# -- AC1 / AC2: add -----------------------------------------------------------------------------


def test_add_writes_exact_limits_block(env: Env) -> None:
    env.add("daily", *LIMIT_FLAGS)
    row = env.data()["schedules"][0]
    assert row["limits"] == {
        "quotas": [{"period": "day", "max_runs": 3}, {"period": "week", "max_runs": 10}],
        "cost": {
            "per_run_usd": 2,
            "on_cap": "pause",
            "pause_ttl_seconds": 259200,
            "periods": [{"period": "week", "max_usd": 20}],
        },
    }
    assert row["week_start"] == "monday"


@pytest.mark.parametrize("bad", ["day", "day=0", "year=3", "day=x", "=3"])
def test_malformed_quota_exit_2_names_token(env: Env, bad: str) -> None:
    res = env.add("daily", "--quota", bad, code=2)
    assert repr(bad) in res.output
    assert not env.sched_file.exists()


def test_malformed_cost_cap_and_orphan_on_cap(env: Env) -> None:
    assert "'week=-1'" in env.add("daily", "--cost-cap", "week=-1", code=2).output
    assert "--on-cap" in env.add("daily", "--quota", "day=1", "--on-cap", "abort", code=2).output
    assert "'x'" in env.add("daily", "--quota", "day=1", "--on-cap", "x", code=2).output


def test_d2_no_limits_exit_2_nothing_written(env: Env) -> None:
    res = env.add("daily", "--prompt", "hello", code=2)
    for needle in (
        "every schedule must declare at least one limit",
        "--quota",
        "--cost-per-run",
        "--cost-cap",
    ):
        assert needle in res.output
    assert not env.sched_file.exists()
    assert not (env.ws / ".ao" / "schedule-prompts").exists()


def test_defaults_limits_satisfy_d2(env: Env) -> None:
    env.sched_file.write_text(
        "defaults: {limits: {quotas: [{period: day, max_runs: 5}]}}\nschedules: []\n"
    )
    env.add("daily")
    assert "limits" not in env.data()["schedules"][0]


# -- AC3 / AC4: prompt + set-limits -------------------------------------------------------------


def test_prompt_written_pinned_and_guarded(env: Env) -> None:
    env.add("daily", "--quota", "day=1", "--prompt", "do the thing", "--pin-prompt")
    path = env.ws / ".ao" / "schedule-prompts" / "daily.md"
    row = env.data()["schedules"][0]
    assert path.read_text() == "do the thing"
    assert row["prompt_file"] == ".ao/schedule-prompts/daily.md"
    assert row["prompt_sha256"] == hashlib.sha256(b"do the thing").hexdigest()
    # --force replaces both the binding and a differing prompt file
    env.add("daily", "--quota", "day=1", "--prompt", "other", "--force")
    assert path.read_text() == "other"


def test_prompt_existing_different_without_force_refused(env: Env) -> None:
    p = env.ws / ".ao" / "schedule-prompts"
    p.mkdir(parents=True)
    (p / "daily.md").write_text("old")
    assert (
        "already exists" in env.add("daily", "--quota", "day=1", "--prompt", "new", code=2).output
    )
    assert (p / "daily.md").read_text() == "old" and not env.sched_file.exists()


def test_prompt_and_prompt_file_exclusive(env: Env) -> None:
    res = env.add("daily", *["--quota", "day=1", "--prompt", "a", "--prompt-file", "p.md"], code=2)
    assert "mutually exclusive" in res.output


def test_pin_prompt_command(env: Env) -> None:
    (env.ws / "p.md").write_text("v1")
    env.add("daily", "--quota", "day=1", "--prompt-file", "p.md")
    env.add("bare", "--quota", "day=1")
    assert "no prompt_file" in env.run("pin-prompt", "bare", code=2).output
    env.run("pin-prompt", "daily")
    assert env.data()["schedules"][0]["prompt_sha256"] == hashlib.sha256(b"v1").hexdigest()
    (env.ws / "p.md").write_text("v2")
    env.run("pin-prompt", "daily")
    assert env.data()["schedules"][0]["prompt_sha256"] == hashlib.sha256(b"v2").hexdigest()


def test_set_limits_merges_and_clears(env: Env) -> None:
    env.add("daily", *LIMIT_FLAGS)
    before = env.data()["schedules"][0]
    env.run("set-limits", "daily", "--quota", "day=5", "--cost-cap", "month=50")
    row = env.data()["schedules"][0]
    assert {q["period"]: q["max_runs"] for q in row["limits"]["quotas"]} == {"day": 5, "week": 10}
    assert {c["period"]: c["max_usd"] for c in row["limits"]["cost"]["periods"]} == {
        "week": 20,
        "month": 50,
    }
    untouched = {k: v for k, v in before.items() if k != "limits"}
    assert {k: row[k] for k in untouched} == untouched  # only limits (and defaults) change
    env.run("set-limits", "daily", "--clear-quotas")
    assert "quotas" not in env.data()["schedules"][0]["limits"]
    res = env.run("set-limits", "daily", "--clear-cost", code=2)  # would leave no limit (D-2)
    assert "at least one limit" in res.output
    assert "cost" in env.data()["schedules"][0]["limits"]  # unchanged
    assert "nothing to change" in env.run("set-limits", "daily", code=2).output
    assert "no such schedule" in env.run("set-limits", "zzz", "--quota", "day=1", code=1).output


# -- AC5: usage ---------------------------------------------------------------------------------

GOLDEN = """\
daily  (Europe/Berlin, week starts monday)                  limits digest {digest}
  day   2026-11-10  runs 2/3   spend $3.10 (+$2.00 reserved) / -       resets 2026-11-11 00:00 CET
  week  2026-11-09  runs 4/10  spend $9.52 (+$2.00 reserved) / $20.00  resets 2026-11-16 00:00 CET
  paused: run-p  $2.07 of $2.00 (overshoot $0.07)  expires 2026-11-13 09:04 CET
  last events:
    2026-11-10 12:00  skipped  cost_cap_reached  week remaining $0.55 < needed $2.00"""


def seed_usage(env: Env) -> None:
    env.add("daily", *LIMIT_FLAGS)
    day = datetime(2026, 11, 10, 6, 0, tzinfo=UTC)
    # day window: 2 runs, $3.10 actual; 2.00 of the second still reserved (running, no state)
    env.fire("f1", day, cost=3.10, reserved=2.0, final=True)
    env.fire("f2", day + timedelta(hours=2), cost=0.0, reserved=2.0)
    # earlier this week: 2 more runs, $4.35 actual
    env.fire("f3", datetime(2026, 11, 9, 9, 0, tzinfo=UTC), cost=4.35, final=True)
    env.fire(
        "run-p", datetime(2026, 11, 9, 7, 0, tzinfo=UTC), run="run-p", cost=2.07, final=True,
        cap_state="paused", paused_at=NOW - timedelta(hours=1),
        pause_expires_at=datetime(2026, 11, 13, 8, 4, tzinfo=UTC),
    )  # fmt: skip
    env.event(
        "schedule.governance.skipped", NOW, reason="cost_cap_reached", period="week",
        remaining_usd=0.55, needed_usd=2.0,
    )  # fmt: skip


def test_usage_text_golden_and_json_schema(env: Env) -> None:
    seed_usage(env)
    out = env.run("usage", "daily").output
    digest = json.loads(env.run("usage", "daily", "--json").output)["schedules"][0]["limits_digest"]
    assert out.rstrip("\n").splitlines()[0].endswith(f"limits digest {digest[:6]}")
    assert out.rstrip("\n") == GOLDEN.format(digest=digest[:6])


def test_usage_json_validates_and_at_is_read_only(env: Env) -> None:
    seed_usage(env)
    doc = json.loads(env.run("usage", "--json").output)
    jsonschema.validate(doc, USAGE_JSON_SCHEMA)
    assert doc["schedules"][0]["paused"][0]["overshoot_usd"] == pytest.approx(0.07)
    before = env.files_mtimes()
    res = env.run("usage", "daily", "--at", "2026-11-17T09:00:00+00:00", "--json")
    future = json.loads(res.output)["schedules"][0]
    assert [w["runs"] for w in future["windows"]] == [0, 0]  # next day / next week: empty
    assert env.files_mtimes() == before
    assert "--at" in env.run("usage", "--at", "tomorrow", code=2).output
    assert "no such schedule" in env.run("usage", "nope", code=1).output


# -- AC6: resume --------------------------------------------------------------------------------


def paused(env: Env, expires: datetime, run: str = "run-p", **gov: Any) -> None:
    env.add("daily", *LIMIT_FLAGS)
    gov = {"cap_state": "paused", **gov}
    env.fire(
        "kp", datetime(2026, 11, 10, 7, 0, tzinfo=UTC), run=run, cost=2.0, final=True,
        paused_at=NOW - timedelta(hours=1), pause_expires_at=expires, **gov,
    )  # fmt: skip


def test_resume_queue_success_prints_new_cap(env: Env) -> None:
    paused(env, NOW + timedelta(hours=5))
    res = env.run("resume", "daily", "--run", "run-p", "--add-usd", "1.5", "--queue")
    assert "new cap $1.50" in res.output  # no run state.json -> engine cost 0 + 1.5
    reqs = list((env.state / "schedules" / "requests").glob("*.json"))
    assert len(reqs) == 1
    body = json.loads(reqs[0].read_text())
    assert body["kind"] == "resume_capped" and body["add_usd"] == 1.5


def test_resume_refused_expired_exit_3(env: Env) -> None:
    paused(env, NOW - timedelta(minutes=1))
    res = env.run("resume", "daily", "--run", "run-p", "--queue", code=3)
    assert (
        "refused (expired)" in res.output and "expired at 2026-11-10T10:59:00+00:00" in res.output
    )
    assert not (env.state / "schedules" / "requests").exists()


def test_resume_refused_no_headroom_exit_3(env: Env) -> None:
    paused(env, NOW + timedelta(hours=5))
    env.fire("big", datetime(2026, 11, 10, 8, 0, tzinfo=UTC), cost=17.45, final=True)  # 20-2-17.45
    res = env.run("resume", "daily", "--run", "run-p", "--queue", code=3)
    assert "refused (no_headroom)" in res.output and "remaining $0.55" in res.output


def test_resume_refused_not_paused_and_unknown(env: Env) -> None:
    paused(env, NOW + timedelta(hours=5), run="run-x", cap_state="none")  # type: ignore[arg-type]
    assert "not_paused" in env.run("resume", "daily", "--run", "run-x", "--queue", code=3).output
    assert (
        "schedule_not_found"
        in env.run("resume", "daily", "--run", "nope", "--queue", code=3).output
    )
    assert (
        "mutually exclusive"
        in env.run("resume", "daily", "--run", "x", "--local", "--queue", code=2).output
    )


# -- AC7 / AC8: list, history, next --------------------------------------------------------------


def test_list_columns_and_json_governance(env: Env) -> None:
    seed_usage(env)
    out = env.run("list").output
    assert "LIMITS" in out and "TODAY" in out and "PAUSED" in out
    assert "3/d 10/w $2/run $20/w" in out
    gov = json.loads(env.run("list", "--json").stdout)["schedules"][0]["governance"]
    assert gov == {
        "limits": "3/d 10/w $2/run $20/w",
        "today": {"period": "day", "used": 2, "max": 3},
        "paused": 1,
    }


def test_history_renders_reason_with_period_and_json_governance(env: Env) -> None:
    env.add("daily", *LIMIT_FLAGS)
    env.event(
        "schedule.governance.skipped", NOW, reason="quota_exhausted", period="day", used=3, max=3
    )
    out = env.run("history", "daily").output
    assert "quota_exhausted (day)" in out
    ev = json.loads(env.run("history", "daily", "--json").output)[0]
    assert ev["governance"]["period"] == "day" and ev["governance"]["used"] == 3


def test_next_annotates_would_skip_without_writing(env: Env) -> None:
    env.add("daily", "--quota", "day=2")
    env.fire("a", datetime(2026, 11, 10, 8, 0, tzinfo=UTC), cost=1.0, final=True)
    env.fire("b", datetime(2026, 11, 10, 9, 0, tzinfo=UTC), cost=1.0, final=True)
    before = env.files_mtimes()
    out = env.run("next", "-n", "15").output
    assert "would skip: quota_exhausted (resets 2026-11-10T23:00:00+00:00)" in out
    rows = json.loads(env.run("next", "-n", "15", "--json").stdout)
    assert rows[0]["would_skip"]["reason"] == "quota_exhausted"
    assert "would_skip" not in rows[-1]  # a later day has headroom
    assert env.files_mtimes() == before


# -- AC11: validate warnings --------------------------------------------------------------------


def test_validate_warns_overlap_allow_without_per_run(env: Env) -> None:
    env.add("daily", "--quota", "day=1", "--overlap", "allow")
    assert "overlap: allow without limits.cost.per_run_usd" in env.run("validate").output
    env.run("set-limits", "daily", "--cost-per-run", "2")
    assert "overlap: allow" not in env.run("validate").output


def test_validate_warns_sentinel_budget_ge_per_run(env: Env) -> None:
    (env.ws / "instr.md").write_text("do it")
    wf = {
        "version": "1.0",
        "id": "w",
        "tasks": [{"id": "a", "agent": "worker", "instruction": "instr.md", "outputs": ["o.txt"]}],
        "sentinel": {"enabled": True, "llm": {"budget_usd": 3}},
    }
    (env.ws / "w.json").write_text(json.dumps(wf))
    env.add("daily", "--cost-per-run", "2")
    res = env.run("validate")
    assert "sentinel.llm.budget_usd (3) >= per_run_usd (2)" in res.output
    env.run("set-limits", "daily", "--cost-per-run", "5")
    assert "sentinel.llm.budget_usd" not in env.run("validate").output


def test_no_test_touches_home(env: Env) -> None:
    assert str(Path.home()) not in str(env.state) and os.environ["AO_SERVICE_STATE_DIR"] == str(
        env.state
    )
