"""Unit tier for ``schedules.usage_view`` (T-Se4Bk5 / w24-04): the branches the CLI e2e leaves cold.

Fixed clocks everywhere; real ``FireIntentStore`` / ``ScheduleEventLog`` on tmp dirs; a fake ledger
returns canned ``WindowUsage`` so no ledger arithmetic is under test here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agent_orchestrator.schedules.governance import (
    CostLimits,
    LimitsSpec,
    PeriodCostCap,
    QuotaSpec,
)
from agent_orchestrator.schedules.usage_view import (
    NO_VALUE,
    build_usage_doc,
    compact_limits,
    governance_events,
    governance_fields,
    limit_periods,
    paused_records,
    reason_with_period,
    render_usage,
)
from agent_orchestrator.service.fire_store import (
    FireGovernance,
    FireIntentRecord,
    FireIntentStore,
    ScheduleEventLog,
)
from agent_orchestrator.service.usage_ledger import WindowUsage

from ._fixtures import governed_binding

AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
GEN = AT + timedelta(minutes=1)


class FakeLedger:
    def __init__(self, usage: WindowUsage) -> None:
        self._usage = usage
        self.calls: list[tuple[str, str, list[str]]] = []

    def usage(self, workspace_root: str, schedule_id: str, windows: Any) -> dict[str, WindowUsage]:
        self.calls.append((workspace_root, schedule_id, list(windows)))
        return {p: self._usage for p in windows}

    def invalidate(self, workspace_root: str, schedule_id: str) -> None:  # pragma: no cover
        pass


def _rec(
    ws: str,
    key: str,
    schedule_id: str = "gov",
    *,
    gov: FireGovernance | None,
    minutes: int = 0,
) -> FireIntentRecord:
    t = AT + timedelta(minutes=minutes)
    return FireIntentRecord(
        fire_key=key,
        workspace_root=ws,
        schedule_id=schedule_id,
        scheduled_for=t,
        created_at=t,
        status="launched",
        run_id=f"run-{key}",
        governance=gov,
    )


def _paused(**kw: Any) -> FireGovernance:
    base: dict[str, Any] = {
        "cap_state": "paused",
        "cost_observed_usd": 3.0,
        "run_cost_cap_usd": 2.0,
    }
    base.update(kw)
    return FireGovernance(**base)


# -- limit_periods / compact_limits ----------------------------------------------------------


def test_limit_periods_none_is_empty() -> None:
    assert limit_periods(None) == []


def test_limit_periods_merges_quota_and_cost_sorted_day_week_month() -> None:
    limits = LimitsSpec(
        quotas=[QuotaSpec(period="month", max_runs=5)],
        cost=CostLimits(periods=[PeriodCostCap(period="day", max_usd=1.0)]),
    )
    assert limit_periods(limits) == ["day", "month"]


def test_compact_limits_none_and_empty_render_dash() -> None:
    assert compact_limits(None) == NO_VALUE
    assert compact_limits(LimitsSpec(quotas=[], cost=CostLimits(per_run_usd=None))) == NO_VALUE


def test_compact_limits_full_shape() -> None:
    limits = LimitsSpec(
        quotas=[QuotaSpec(period="day", max_runs=3), QuotaSpec(period="week", max_runs=10)],
        cost=CostLimits(per_run_usd=2.0, periods=[PeriodCostCap(period="week", max_usd=20.0)]),
    )
    assert compact_limits(limits) == "3/d 10/w $2/run $20/w"


# -- paused_records ---------------------------------------------------------------------------


def test_paused_records_filters_schedule_cap_state_and_orders_oldest_first(tmp_path: Path) -> None:
    ws = str((tmp_path / "ws").resolve())
    fires = FireIntentStore(tmp_path / "state")
    fires.write(_rec(ws, "late", gov=_paused(), minutes=10))
    fires.write(_rec(ws, "early", gov=_paused(), minutes=1))
    fires.write(_rec(ws, "other-sched", "other", gov=_paused(), minutes=2))
    fires.write(_rec(ws, "not-paused", gov=FireGovernance(cap_state="aborted"), minutes=3))
    fires.write(_rec(ws, "no-gov", gov=None, minutes=4))
    assert [r.fire_key for r in paused_records(fires, ws, "gov")] == ["early", "late"]


# -- governance_events / governance_fields / reason_with_period --------------------------------


def test_governance_events_filters_prefix_copies_detail_and_caps_at_limit(tmp_path: Path) -> None:
    ws = str((tmp_path / "ws").resolve())
    ticks = iter(AT + timedelta(seconds=i) for i in range(100))
    log = ScheduleEventLog(tmp_path / "state", clock=lambda: next(ticks))
    log.emit("schedule.fire.launched", workspace=ws, schedule_id="gov")  # not governance
    for i in range(7):
        log.emit(
            "schedule.governance.skipped",
            workspace=ws,
            schedule_id="gov",
            reason="quota_exhausted",
            period="day",
            used=i,
            max=3,
            extra_ignored="x",
        )
    log.emit("schedule.governance.skipped", workspace=ws, schedule_id="other", reason="r")
    rows = governance_events(log, ws, "gov")
    assert len(rows) == 5  # EVENT_TAIL
    assert [r["used"] for r in rows] == [6, 5, 4, 3, 2]  # newest first
    assert all(r["event"] == "schedule.governance.skipped" for r in rows)
    assert rows[0]["period"] == "day" and rows[0]["max"] == 3
    assert "extra_ignored" not in rows[0]
    assert len(governance_events(log, ws, "gov", limit=2)) == 2


def test_governance_fields_none_for_non_governance_and_filtered_for_governance() -> None:
    assert governance_fields({"event": "schedule.fire.launched", "reason": "x"}) is None
    assert governance_fields({}) is None
    ev = {"event": "schedule.governance.skipped", "reason": "r", "period": "day", "junk": 1}
    assert governance_fields(ev) == {"reason": "r", "period": "day"}


def test_reason_with_period_variants() -> None:
    assert reason_with_period({"reason": "quota_exhausted", "period": "day"}) == (
        "quota_exhausted (day)"
    )
    assert reason_with_period({"reason": "quota_exhausted"}) == "quota_exhausted"
    assert reason_with_period({"period": "day"}) == NO_VALUE  # no reason -> bare dash, no period
    assert reason_with_period({}) == NO_VALUE


# -- build_usage_doc ----------------------------------------------------------------------------


def test_build_usage_doc_without_limits_has_no_windows_and_skips_ledger(tmp_path: Path) -> None:
    ws = str((tmp_path / "ws").resolve())
    ledger = FakeLedger(WindowUsage())
    b = governed_binding(limits=None)
    doc = build_usage_doc(
        workspace=ws,
        at=AT,
        generated_at=GEN,
        bindings=[b],
        ledger=ledger,
        fires=FireIntentStore(tmp_path / "s"),
        events=ScheduleEventLog(tmp_path / "s"),
    )
    (s,) = doc["schedules"]
    assert s["windows"] == [] and s["limits_digest"] is None and s["limits"] == NO_VALUE
    assert s["per_run_usd"] is None and s["on_cap"] is None
    assert s["tz"] == "UTC" and s["week_start"] == "monday"  # defaults when binding has none
    assert ledger.calls == []
    assert doc["at"] == AT.isoformat() and doc["generated_at"] == GEN.isoformat()


def test_build_usage_doc_window_rows_paused_and_local_date(tmp_path: Path) -> None:
    ws = str((tmp_path / "ws").resolve())
    state = tmp_path / "state"
    fires = FireIntentStore(state)
    pause_at = AT - timedelta(hours=1)
    fires.write(
        _rec(
            ws,
            "p1",
            gov=_paused(paused_at=pause_at, pause_expires_at=AT + timedelta(hours=1)),
        )
    )
    fires.write(
        _rec(
            ws,
            "p2",
            gov=_paused(run_cost_cap_usd=None, cost_observed_usd=1.5),
            minutes=5,
        )
    )
    limits = LimitsSpec(
        quotas=[QuotaSpec(period="day", max_runs=3)],
        cost=CostLimits(
            per_run_usd=2.0, on_cap="abort", periods=[PeriodCostCap(period="week", max_usd=9.0)]
        ),
    )
    b = governed_binding(limits=limits, timezone="Asia/Kolkata", week_start="sunday")
    ledger = FakeLedger(
        WindowUsage(runs=2, actual_usd=1.25, reserved_usd=0.5, confidence="partial")
    )
    doc = build_usage_doc(
        workspace=ws,
        at=AT,
        generated_at=GEN,
        bindings=[b],
        ledger=ledger,
        fires=fires,
        events=ScheduleEventLog(state),
    )
    (s,) = doc["schedules"]
    assert s["tz"] == "Asia/Kolkata" and s["week_start"] == "sunday"
    assert s["per_run_usd"] == 2.0 and s["on_cap"] == "abort"
    assert s["limits_digest"]
    day, week = s["windows"]
    assert (day["period"], day["max_runs"], day["max_usd"]) == ("day", 3, None)
    assert (week["period"], week["max_runs"], week["max_usd"]) == ("week", None, 9.0)
    assert day["runs"] == 2 and day["confidence"] == "partial"
    assert day["start_local_date"] == "2026-11-11"  # 12:00Z == 17:30 IST, same date
    assert ledger.calls == [(ws, "gov", ["day", "week"])]
    p1, p2 = s["paused"]
    assert p1["overshoot_usd"] == 1.0 and p1["paused_at"] == pause_at.isoformat()
    assert p1["expires_at"] is not None
    assert p2["cap_usd"] is None and p2["overshoot_usd"] == 0.0  # no cap -> no overshoot
    assert p2["paused_at"] is None and p2["expires_at"] is None


# -- render_usage ----------------------------------------------------------------------------------


def _row(**kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "period": "day",
        "start_local_date": "2026-11-11",
        "resets_at": "2026-11-12T00:00:00+00:00",
        "runs": 1,
        "max_runs": 3,
        "actual_usd": 1.0,
        "reserved_usd": 0.5,
        "max_usd": 5.0,
        "confidence": "actual",
    }
    row.update(kw)
    return row


def _sched(**kw: Any) -> dict[str, Any]:
    s: dict[str, Any] = {
        "id": "gov",
        "tz": "UTC",
        "week_start": "monday",
        "limits_digest": None,
        "windows": [],
        "paused": [],
        "events": [],
    }
    s.update(kw)
    return s


def test_render_usage_header_digest_and_window_lines() -> None:
    out = render_usage(
        {
            "schedules": [
                _sched(
                    limits_digest="abcdef123456",
                    windows=[
                        _row(),
                        _row(period="week", max_runs=None, max_usd=None, confidence="unknown"),
                    ],
                )
            ]
        }
    )
    head, day, week = out.splitlines()
    assert head.startswith("gov  (UTC, week starts monday)") and head.endswith(
        "limits digest abcdef"
    )
    assert "runs 1/3" in day and "spend $1.00 (+$0.50 reserved) / $5.00" in day
    assert "(cost partial)" not in day
    assert day.endswith("resets 2026-11-12 00:00 UTC")
    assert "runs 1/-" in week and "/ -" in week and "(cost partial)" in week


def test_render_usage_no_digest_no_windows_is_just_the_header() -> None:
    assert render_usage({"schedules": [_sched()]}) == "gov  (UTC, week starts monday)"
    assert render_usage({"schedules": []}) == ""


def test_render_usage_paused_lines() -> None:
    paused = [
        {"run_id": "r1", "cost_usd": 3.0, "cap_usd": 2.0, "overshoot_usd": 1.0,
         "expires_at": "2026-11-11T13:00:00+00:00"},
        {"run_id": "r2", "cost_usd": 1.5, "cap_usd": None, "overshoot_usd": 0.0,
         "expires_at": None},
    ]  # fmt: skip
    lines = render_usage({"schedules": [_sched(paused=paused)]}).splitlines()
    assert (
        lines[1] == "  paused: r1  $3.00 of $2.00 (overshoot $1.00)  expires 2026-11-11 13:00 UTC"
    )
    assert lines[2] == "  paused: r2  $1.50 of -  expires -"


def test_render_usage_events_details_and_timezone() -> None:
    events = [
        {"ts": "2026-11-11T12:00:00+00:00", "event": "schedule.governance.skipped",
         "reason": "cost_exhausted", "period": "week", "remaining_usd": 0.5, "needed_usd": 2.0},
        {"ts": "2026-11-11T11:00:00+00:00", "event": "schedule.governance.skipped",
         "reason": "cost_exhausted", "remaining_usd": 0.5, "needed_usd": 2.0},
        {"ts": "2026-11-11T10:00:00+00:00", "event": "schedule.governance.skipped",
         "reason": "quota_exhausted", "period": "day", "used": 3, "max": 3},
        {"ts": "2026-11-11T09:00:00+00:00", "event": "schedule.governance.paused",
         "reason": None},
        {"ts": None, "event": "schedule.governance.x", "reason": "r"},
    ]  # fmt: skip
    out = render_usage({"schedules": [_sched(tz="Asia/Kolkata", events=events)]}).splitlines()
    assert out[1] == "  last events:"
    assert out[2] == (
        "    2026-11-11 17:30  skipped  cost_exhausted  week remaining $0.50 < needed $2.00"
    )
    assert out[3].endswith("cost_exhausted  remaining $0.50 < needed $2.00")  # no period prefix
    assert out[4] == "    2026-11-11 15:30  skipped  quota_exhausted  day 3/3"
    assert out[5] == "    2026-11-11 14:30  paused"  # empty reason/detail dropped
    assert out[6] == "    -  x  r"  # missing ts renders the dash
