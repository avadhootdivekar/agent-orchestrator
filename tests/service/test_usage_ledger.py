"""T-Uz0Uol: the derived usage ledger (HLD §10). Fixed clocks, real files in tmp dirs."""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from agent_orchestrator.models import RunState, TaskRunState
from agent_orchestrator.schedules.windows import window_for, windows_for
from agent_orchestrator.service.fire_store import (
    FireGovernance,
    FireIntentRecord,
    FireIntentStore,
    readonly_fire_store,
)
from agent_orchestrator.service.usage_ledger import (
    EVENT_COST_UNKNOWN,
    STATE_PROBE_GRACE_SECONDS,
    RunProbe,
    UsageLedger,
    WindowUsage,
    probe_run,
    run_confidence,
)

T0 = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
FAR_FUTURE_NS = 10**19  # fs clock so the directory-mtime settle check never forces a rescan
UTC_TZ = ZoneInfo("UTC")


class Rig:
    def __init__(self, tmp: Path, now: datetime = NOW, **kw: Any) -> None:
        self.root = str(tmp / "ws")
        Path(self.root).mkdir(exist_ok=True)
        self.store = FireIntentStore(tmp / "state")
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.now = now
        self._gen = 0
        self.ledger = UsageLedger(
            self.store,
            clock=lambda: self.now,
            event_sink=lambda ev, **f: self.events.append((ev, f)),
            fs_now_ns=lambda: FAR_FUTURE_NS,
            **kw,
        )

    def fire(
        self,
        key: str,
        *,
        sf: datetime = T0,
        status: str = "launched",
        run: str | None = None,
        sid: str = "s",
        reserved: float | None = 2.0,
        **gov: Any,
    ) -> FireIntentRecord:
        g = FireGovernance(reserved_usd=reserved, run_cost_cap_usd=reserved, **gov)
        rec = FireIntentRecord(
            fire_key=key, workspace_root=self.root, schedule_id=sid, scheduled_for=sf,
            created_at=sf, status=status, run_id=run, governance=g,
        )  # type: ignore[arg-type]  # fmt: skip
        self.store.write(rec)
        self.ledger.note_written(rec)
        self.ledger.invalidate(self.root)
        return rec

    def state(
        self,
        run: str,
        status: str = "running",
        tasks: list[tuple[float, bool, int, str]] = (),  # type: ignore[assignment]
    ) -> None:
        """tasks: (cost, actuals_available, attempts_without_actuals, status)."""
        st = RunState(
            run_id=run, workflow_id="w", started_at=T0.isoformat(), updated_at=T0.isoformat(),
            status=status,  # type: ignore[arg-type]
            tasks={
                f"t{i}": TaskRunState(
                    status=s, cumulative_cost_usd=c, actuals_available=a,  # type: ignore[arg-type]
                    attempts_without_actuals=n,
                )
                for i, (c, a, n, s) in enumerate(tasks)
            },
        )  # fmt: skip
        d = Path(self.root) / ".orchestrator" / "runs" / run
        d.mkdir(parents=True, exist_ok=True)
        f = d / "state.json"
        f.write_text(st.model_dump_json(), encoding="utf-8")
        self._gen += 1  # coarse fs timestamps can repeat: force a distinct mtime per rewrite
        os.utime(f, ns=(self._gen * 10**9, self._gen * 10**9))

    def summary(self, run: str, cost: float) -> None:
        d = Path(self.root) / ".orchestrator" / "runs" / run / "summary"
        d.mkdir(parents=True, exist_ok=True)
        (d / "summary.json").write_text(json.dumps({"summary_cost_usd": cost}))

    def charge(self, rec: FireIntentRecord, **kw: Any):  # type: ignore[no-untyped-def]
        return self.ledger.charge(self.store.get(rec.fire_key, self.root), **kw)  # type: ignore[arg-type]


OK = (1.5, True, 0, "succeeded")


# -- AC2: one test per HLD §10.2 row ----------------------------------------------------------


@pytest.mark.parametrize("status", ["suppressed", "launch_failed"])
def test_row_suppressed_and_launch_failed_cost_nothing(tmp_path: Path, status: str) -> None:
    r = Rig(tmp_path)
    c = r.charge(r.fire("a", status=status, run="r"))
    assert (c.runs, c.actual_usd, c.reserved_usd) == (0, 0.0, 0.0)


def test_row_intended_without_state_holds_reservation(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    c = r.charge(r.fire("a", status="intended", run="r"))
    assert (c.runs, c.actual_usd, c.reserved_usd, c.confidence) == (1, 0.0, 2.0, "pending")


def test_row_orphaned_without_run_dir_is_free_but_spends_the_slot(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    c = r.charge(r.fire("a", status="orphaned", run="r"))
    assert (c.runs, c.actual_usd, c.reserved_usd) == (1, 0.0, 0.0)


def test_row_orphaned_with_run_dir_is_observed(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.state("r", "succeeded", [OK])
    c = r.charge(r.fire("a", status="orphaned", run="r"))
    assert (c.runs, c.actual_usd, c.reserved_usd, c.confidence) == (1, 1.5, 0.0, "actual")


def test_row_running_is_observed_plus_reserved_left(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.state("r", "running", [(0.5, True, 0, "succeeded")])
    c = r.charge(r.fire("a", run="r"))
    assert (c.actual_usd, c.reserved_usd) == (0.5, 1.5)


def test_row_terminal_actual(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.state("r", "succeeded", [OK])
    c = r.charge(r.fire("a", run="r"))
    assert (c.actual_usd, c.reserved_usd, c.confidence, c.overshoot_usd) == (
        1.5,
        0.0,
        "actual",
        0.0,
    )


def test_row_terminal_overshoot_is_reported(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.state("r", "failed", [(2.5, True, 0, "failed")])
    assert r.charge(r.fire("a", run="r")).overshoot_usd == 0.5


def test_row_terminal_partial_open_window_charged_max_closed_observed(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.state("r", "failed", [(0.5, True, 0, "succeeded"), (0.0, False, 0, "failed")])
    rec = r.fire("a", run="r")
    open_ = r.charge(rec, window_open=True)
    closed = r.charge(rec, window_open=False)
    assert (open_.actual_usd, open_.confidence) == (2.0, "partial")
    assert (closed.actual_usd, closed.confidence) == (0.5, "partial")


def test_row_terminal_unknown_is_max_observed_reserved_even_when_closed(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.state("r", "succeeded", [(0.0, False, 0, "succeeded")])
    rec = r.fire("a", run="r")
    for open_ in (True, False):
        c = r.charge(rec, window_open=open_)
        assert (c.actual_usd, c.reserved_usd, c.confidence) == (2.0, 0.0, "unknown")


@pytest.mark.parametrize("cap_state", ["paused", "aborted", "expired"])
def test_row_capped_states_charge_observed_no_reservation(tmp_path: Path, cap_state: str) -> None:
    r = Rig(tmp_path)
    r.state("r", "failed", [(1.2, True, 0, "failed")])
    c = r.charge(r.fire("a", run="r", cap_state=cap_state))
    assert (c.actual_usd, c.reserved_usd) == (1.2, 0.0)


def test_g2_resumed_pending_child_keeps_topup_reserved(tmp_path: Path) -> None:
    """REVIEW G-2 probe: cap_state=resumed, reserved $4, state still `failed`, $2 spent."""
    r = Rig(tmp_path)
    r.state("r", "failed", [(2.0, True, 0, "failed")])
    c = r.charge(r.fire("a", run="r", reserved=4.0, cap_state="resumed"))
    assert (c.actual_usd, c.reserved_usd) == (2.0, 2.0)
    w = windows_for(T0, ["day"], ZoneInfo("UTC"), "monday")  # same shape through usage()
    assert r.ledger.usage(r.root, "s", w)["day"].reserved_usd == 2.0


def test_g2_resumed_then_final_releases_reservation(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.state("r", "succeeded", [(2.5, True, 0, "succeeded")])
    c = r.charge(r.fire("a", run="r", reserved=4.0, cap_state="resumed", final=True))
    assert (c.actual_usd, c.reserved_usd) == (2.5, 0.0)


def test_g2_resumed_running_unchanged(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.state("r", "running", [(2.0, True, 0, "succeeded")])
    c = r.charge(r.fire("a", run="r", reserved=4.0, cap_state="resumed"))
    assert (c.actual_usd, c.reserved_usd) == (2.0, 2.0)


# -- AC12 / AC10 -------------------------------------------------------------------------------


def test_ac12_killed_attempt_plus_reporting_task_is_partial_and_charged_max(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.state("r", "succeeded", [(0.4, True, 1, "succeeded")])  # a killed attempt reported nothing
    c = r.charge(r.fire("a", run="r"))
    assert c.confidence == "partial" and c.actual_usd == max(0.4, 2.0)


def test_ac12_running_partial_is_conservative(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.state("r", "running", [(0.4, True, 1, "succeeded")])
    c = r.charge(r.fire("a", run="r"))
    assert c.actual_usd + c.reserved_usd == 2.0 and c.confidence == "partial"


def _st(tasks: list[tuple[float, bool, int, str]], status: str = "succeeded") -> RunState:
    return RunState(
        run_id="r", workflow_id="w", started_at="x", updated_at="x", status=status,  # type: ignore[arg-type]
        tasks={
            f"t{i}": TaskRunState(status=s, actuals_available=a, attempts_without_actuals=n)  # type: ignore[arg-type]
            for i, (_, a, n, s) in enumerate(tasks)
        },
    )  # fmt: skip


@pytest.mark.parametrize(
    ("tasks", "status", "want"),
    [
        ([OK, OK], "succeeded", "actual"),
        ([OK, (0, False, 0, "succeeded")], "succeeded", "partial"),
        ([OK, (0, True, 1, "succeeded")], "succeeded", "partial"),
        ([(0, False, 0, "succeeded")], "succeeded", "unknown"),
        ([(0, False, 2, "failed")], "failed", "unknown"),
        ([], "failed", "unknown"),
        ([], "running", "pending"),
        ([OK, (0, False, 0, "pending")], "running", "actual"),  # unsettled tasks are ignored
        ([(0, True, 0, "not_taken"), OK], "succeeded", "actual"),
    ],
)
def test_ac10_confidence_from_persisted_fields(
    tasks: list[tuple[float, bool, int, str]], status: str, want: str
) -> None:
    assert run_confidence(_st(tasks, status)) == want


def test_ac10_blind_run_differs_from_zero_cost_reporting_run(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.state("blind", "succeeded", [(0.0, False, 0, "succeeded")])
    r.state("free", "succeeded", [(0.0, True, 0, "succeeded")])
    blind = r.charge(r.fire("a", run="blind"))
    free = r.charge(r.fire("b", run="free"))
    assert (blind.actual_usd, free.actual_usd) == (2.0, 0.0)


# -- AC3 / AC13 --------------------------------------------------------------------------------


def test_ac3_ac13_observed_is_engine_plus_summary_and_engine_metric_exposed(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.state("r", "succeeded", [OK])
    r.summary("r", 0.25)
    rec = r.fire("a", run="r")
    c = r.charge(rec)
    stored = r.store.get("a", r.root).governance.cost_observed_usd  # type: ignore[union-attr]
    assert c.actual_usd == stored == 1.75
    assert r.ledger.engine_cost_usd(rec) == 1.5
    assert stored - r.ledger.engine_cost_usd(rec) == pytest.approx(0.25)


@pytest.mark.parametrize(
    "body", [None, "{not json", '"str"', '{"summary_cost_usd": "x"}', '{"summary_cost_usd": -3}']
)
def test_ac3_missing_or_corrupt_summary_counts_zero(tmp_path: Path, body: str | None) -> None:
    r = Rig(tmp_path)
    r.state("r", "succeeded", [OK])
    if body is not None:
        d = Path(r.root) / ".orchestrator/runs/r/summary"
        d.mkdir(parents=True)
        (d / "summary.json").write_text(body)
    assert r.charge(r.fire("a", run="r")).actual_usd == 1.5


def test_ac13_engine_cost_zero_without_state(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    assert r.ledger.engine_cost_usd(r.fire("a", run="r")) == 0.0
    assert r.ledger.engine_cost_usd(r.fire("b", run=None)) == 0.0


# -- AC4: high-water mark ----------------------------------------------------------------------


def test_ac4_tamper_lower_cost_never_drops_charge_and_write_count(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.state("r", "running", [(1.0, True, 0, "succeeded")])
    rec = r.fire("a", run="r")
    assert r.charge(rec).actual_usd == 1.0
    assert r.ledger.writes == 1
    r.state("r", "running", [(0.1, True, 0, "succeeded")])  # tamper: lower
    assert r.charge(rec).actual_usd == 1.0
    r.charge(rec)
    assert r.ledger.writes == 1  # nothing rose, nothing rewritten
    stored = r.store.get("a", r.root).governance.cost_observed_usd  # type: ignore[union-attr]
    assert stored == 1.0


def test_ac4_small_rise_is_not_persisted_but_never_lost_in_process(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.state("r", "running", [(1.0, True, 0, "succeeded")])
    rec = r.fire("a", run="r")
    r.charge(rec)
    r.state("r", "running", [(1.005, True, 0, "succeeded")])
    assert r.charge(rec).actual_usd == 1.005 and r.ledger.writes == 1
    r.state("r", "running", [(1.001, True, 0, "succeeded")])
    assert r.charge(rec).actual_usd == 1.005  # in-process mark holds


def test_ac4_terminal_persists_even_for_small_rise(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.state("r", "running", [(1.0, True, 0, "succeeded")])
    rec = r.fire("a", run="r")
    r.charge(rec)
    r.state("r", "succeeded", [(1.004, True, 0, "succeeded")])
    r.charge(rec)
    assert r.ledger.writes == 2
    g = r.store.get("a", r.root).governance  # type: ignore[union-attr]
    assert g.cost_observed_usd == 1.004 and g.cost_confidence == "actual"  # type: ignore[union-attr]
    r.charge(rec)
    assert r.ledger.writes == 2  # unchanged terminal: idempotent


def test_ac4_update_governance_refuses_to_lower_mark(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.fire("a", run="r", cost_observed_usd=3.0)
    out = r.store.update_governance("a", cost_observed_usd=1.0, cap_state="paused")
    assert out.governance.cost_observed_usd == 3.0 and out.governance.cap_state == "paused"  # type: ignore[union-attr]
    with pytest.raises(KeyError):
        r.store.update_governance("nope", cap_state="paused")


def test_persist_failure_is_swallowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r = Rig(tmp_path)
    r.state("r", "running", [(1.0, True, 0, "succeeded")])
    rec = r.fire("a", run="r")

    def boom(*a: Any, **k: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(r.store, "update_governance", boom)
    assert r.charge(rec).actual_usd == 1.0 and r.ledger.writes == 0


# -- AC5/6/7: no double count, restart determinism, timezone ----------------------------------


def _day(sf: datetime = T0) -> dict[Any, Any]:
    return {"day": window_for(sf, "day", UTC_TZ, "monday")}


def test_ac5_resumes_do_not_double_count(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.state("r", "running", [(1.0, True, 0, "succeeded")])
    r.fire("a", run="r")
    for i in range(3):  # three resumes rewrite the same record
        r.store.update_governance(
            "a", resumes=[{"at": T0.isoformat(), "by": "op", "add_usd": 1.0}] * (i + 1)
        )
        r.ledger.invalidate(r.root)
    u = r.ledger.usage(r.root, "s", _day())["day"]
    assert u.runs == 1 and u.actual_usd == 1.0 and u.reserved_usd == 1.0


def test_ac6_restart_gives_identical_usage(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.state("r1", "running", [(0.7, True, 0, "succeeded")])
    r.state("r2", "succeeded", [OK])
    r.fire("a", run="r1")
    r.fire("b", run="r2", sf=T0 + timedelta(hours=1))
    r.fire("c", status="suppressed", sf=T0 + timedelta(hours=2))
    first = r.ledger.usage(r.root, "s", _day())
    fresh = Rig(tmp_path)
    assert fresh.ledger.usage(r.root, "s", _day()) == first
    assert first["day"].runs == 2


def test_ac7_timezone_change_rebuckets_and_conserves_totals(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    late = datetime(2026, 11, 10, 23, 30, tzinfo=UTC)
    r.state("r", "succeeded", [OK])
    r.fire("a", run="r", sf=late)
    berlin = ZoneInfo("Europe/Berlin")

    def day_usage(tz: ZoneInfo, at: datetime) -> WindowUsage:
        return r.ledger.usage(r.root, "s", {"day": window_for(at, "day", tz, "monday")})["day"]

    d10, d11 = datetime(2026, 11, 10, 12, tzinfo=UTC), datetime(2026, 11, 11, 12, tzinfo=UTC)
    assert (day_usage(UTC_TZ, d10).runs, day_usage(UTC_TZ, d11).runs) == (1, 0)
    assert (day_usage(berlin, d10).runs, day_usage(berlin, d11).runs) == (0, 1)
    assert day_usage(berlin, d11).actual_usd == 1.5


def test_other_schedules_and_out_of_window_fires_are_excluded(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.fire("a", status="intended")
    r.fire("b", sid="other", status="intended")
    r.fire("c", sf=T0 - timedelta(days=2), status="intended")
    u = r.ledger.usage(r.root, "s", _day())["day"]
    assert u.runs == 1 and u.reserved_usd == 2.0
    assert r.ledger.usage(r.root, "s", {}) == {}


def test_multiple_periods_and_worst_confidence(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.state("r1", "succeeded", [OK])
    r.state("r2", "succeeded", [(0.0, False, 0, "succeeded")])
    r.fire("a", run="r1")
    r.fire("b", run="r2", sf=T0 + timedelta(hours=1))
    w = windows_for(T0, ["day", "week"], UTC_TZ, "monday")
    u = r.ledger.usage(r.root, "s", w)
    assert u["day"] == u["week"] and u["day"].confidence == "unknown"
    assert (u["day"].runs, u["day"].actual_usd) == (2, 3.5)


# -- AC8: index + perf -------------------------------------------------------------------------


def test_note_written_upserts_into_built_index(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.fire("a", status="intended")
    r.ledger.usage(r.root, "s", _day())
    rec = FireIntentRecord(
        fire_key="b", workspace_root=r.root, schedule_id="s", scheduled_for=T0 + timedelta(hours=1),
        created_at=T0, status="intended", governance=FireGovernance(reserved_usd=1.0),
    )  # fmt: skip
    r.store.write(rec)
    r.ledger.note_written(rec)
    idx = r.ledger._index[r.ledger._key(r.root)]
    assert [k for _, k in idx.by_schedule["s"]] == ["a", "b"]  # inserted in order, no rescan
    assert r.ledger.usage(r.root, "s", _day())["day"].runs == 2
    other = UsageLedger(r.store)
    other.note_written(rec)  # before any build: a harmless no-op
    assert other._index == {}


def _rig_50k(tmp_path: Path) -> tuple[Rig, dict, int]:
    r = Rig(tmp_path)
    d = r.store.directory(r.root)
    d.mkdir(parents=True)
    month_start = datetime(2026, 11, 1, tzinfo=UTC)
    w = {"month": window_for(NOW, "month", UTC_TZ, "monday")}
    expected = 0
    for i in range(50_000):
        sf = month_start + timedelta(minutes=i % 40_000) - timedelta(days=(i % 7) * 40)
        sid = f"s{i % 100}"
        # s0's records are started runs (each counts 1), the rest suppressed: a fast path that
        # returned an empty or wrong window would fail the count, not just pass the timer
        rec = FireIntentRecord(
            fire_key=f"k{i}", workspace_root=r.root, schedule_id=sid, scheduled_for=sf,
            created_at=sf, status="launched" if sid == "s0" else "suppressed",
        )  # fmt: skip
        (d / f"k{i}.json").write_text(rec.model_dump_json())
        expected += sid == "s0" and w["month"].start <= sf < w["month"].end
    assert expected > 0
    return r, w, expected


def test_ac8_month_usage_over_50k_records_is_exact_and_warm_reads_do_not_rescan(
    tmp_path: Path,
) -> None:
    """Deterministic AC8 gate: exact count, one index build, warm reads add no scan."""
    r, w, expected = _rig_50k(tmp_path)
    assert r.ledger.usage(r.root, "s0", w)["month"].runs == expected  # cold: builds the index
    scans = r.ledger.scans
    assert scans >= 1
    assert r.ledger.usage(r.root, "s0", w)["month"].runs == expected
    assert r.ledger.scans == scans  # warm read served from the index


@pytest.mark.perf
def test_perf_ac8_month_usage_over_50k_records_is_fast(tmp_path: Path) -> None:
    """Wall-clock envelope (spec 50 ms, x10 CI margin); opt out with ``-m "not perf"`` (Q-5)."""
    r, w, expected = _rig_50k(tmp_path)
    assert r.ledger.usage(r.root, "s0", w)["month"].runs == expected  # warm: builds the index
    scans = r.ledger.scans
    t = time.perf_counter()
    got = r.ledger.usage(r.root, "s0", w)
    elapsed = time.perf_counter() - t
    assert got["month"].runs == expected and r.ledger.scans == scans  # counter gate first
    assert elapsed < 0.05 * 10, elapsed


# -- AC9: probe + grace ------------------------------------------------------------------------


def test_ac9_probe_caches_by_mtime_and_size_and_survives_corruption(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.state("r", "running", [OK])
    p = RunProbe()
    assert p(r.root, "r") is p(r.root, "r") and p.parses == 1
    r.state("r", "succeeded", [OK, OK])  # size + mtime change
    assert p(r.root, "r").status == "succeeded" and p.parses == 2  # type: ignore[union-attr]
    (Path(r.root) / ".orchestrator/runs/r/state.json").write_text("{corrupt")
    assert p(r.root, "r") is None
    assert p(r.root, "missing") is None


def test_probe_cache_is_bounded(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    p = RunProbe(max_entries=2)
    for i in range(4):
        r.state(f"r{i}", "running", [OK])
        p(r.root, f"r{i}")
    assert len(p._cache) == 2


def test_ac9_cost_unknown_warning_after_grace_once(tmp_path: Path) -> None:
    r = Rig(tmp_path, now=T0 + timedelta(seconds=STATE_PROBE_GRACE_SECONDS))
    rec = r.fire("a", status="launched", run="r")
    r.charge(rec)
    assert r.events == []  # exactly at the grace boundary: not yet
    r.now = T0 + timedelta(seconds=STATE_PROBE_GRACE_SECONDS + 1)
    r.charge(rec)
    r.charge(rec)
    assert [e for e, _ in r.events] == [EVENT_COST_UNKNOWN]
    assert r.events[0][1]["fire_key"] == "a"


def test_no_sink_means_no_warning_and_no_crash(tmp_path: Path) -> None:
    r = Rig(tmp_path, now=T0 + timedelta(days=1))
    r.ledger._sink = None
    assert r.charge(r.fire("a", status="launched", run="r")).reserved_usd == 2.0


def test_record_without_governance_counts_one_run(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    rec = FireIntentRecord(
        fire_key="x", workspace_root=r.root, schedule_id="s", scheduled_for=T0, created_at=T0,
        status="launched",
    )  # fmt: skip
    r.store.write(rec)
    c = r.ledger.charge(rec)
    assert (c.runs, c.actual_usd, c.reserved_usd) == (1, 0.0, 0.0)
    r.state("r", "running", [OK])
    rec2 = rec.model_copy(update={"run_id": "r", "fire_key": "y"})
    assert r.ledger.charge(rec2).actual_usd == 1.5  # no governance: nothing persisted, no crash


def test_final_record_without_state_is_not_held(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    c = r.charge(r.fire("a", run="r", final=True, cost_observed_usd=1.0, cost_confidence="unknown"))
    assert (c.actual_usd, c.reserved_usd, c.confidence) == (1.0, 0.0, "unknown")


# -- store additions ---------------------------------------------------------------------------


def test_store_find_by_run_id_and_iter_schedule(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    r.fire("a", run="r1")
    r.fire("b", run="r2", sf=T0 + timedelta(days=1))
    r.fire("c", run="r3", sid="o", sf=T0 + timedelta(days=2))
    assert r.store.find_by_run_id(r.root, "r2").fire_key == "b"  # type: ignore[union-attr]
    assert r.store.find_by_run_id(r.root, "zz") is None
    assert r.store.find_by_run_id(r.root + "-none", "r1") is None
    got = r.store.iter_schedule(r.root, "s", T0 + timedelta(hours=1))
    assert [x.fire_key for x in got] == ["b"]
    assert r.store.iter_schedule(r.root + "-none", "s", T0) == []


def test_window_bounds_start_inclusive_end_exclusive(tmp_path: Path) -> None:
    r = Rig(tmp_path)
    w = _day()["day"]
    r.fire("at_start", sf=w.start, status="intended")
    r.fire("before", sf=w.start - timedelta(seconds=1), status="intended")
    r.fire("at_end", sf=w.end, status="intended")
    r.fire("last", sf=w.end - timedelta(seconds=1), status="intended")
    assert r.ledger.usage(r.root, "s", {"day": w})["day"].runs == 2


# -- T-Se4Bk5 R-2: view callers never write fire records ---------------------------------------


def _view_rig(tmp_path: Path, **kw: Any) -> tuple[Rig, UsageLedger]:
    """A Rig whose engine-side store is writable, plus a ledger over the read-only view store."""
    r = Rig(tmp_path, **kw)
    view = UsageLedger(
        readonly_fire_store(tmp_path / "state"), clock=lambda: r.now,
        fs_now_ns=lambda: FAR_FUTURE_NS,
    )  # fmt: skip
    return r, view


def test_r2_view_ledger_never_rewrites_fire_records(tmp_path: Path) -> None:
    r, view = _view_rig(tmp_path)
    r.state("r", "running", [(1.0, True, 0, "succeeded")])
    rec = r.fire("a", run="r")
    path = r.store.directory(r.root) / f"{rec.fire_key}.json"
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    w = window_for(NOW, "day", UTC_TZ, "monday")
    u = view.usage(r.root, "s", {"day": w})["day"]
    assert u.actual_usd == 1.0  # the view still reports the observed cost (in memory)
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before  # ...but wrote nothing
    # the same call through the writable ledger does persist the mark (the bug's trigger)
    r.ledger.usage(r.root, "s", {"day": w})
    assert r.store.get("a", r.root).governance.cost_observed_usd == 1.0  # type: ignore[union-attr]


def test_r2_view_does_not_clobber_a_concurrent_engine_governance_write(tmp_path: Path) -> None:
    """The engine writes ``cap_state`` after the view read the record, before it would write back:
    a writable view would roll that write back (unlocked read-modify-write)."""
    r = Rig(tmp_path)
    r.state("r", "running", [(1.0, True, 0, "succeeded")])
    r.fire("a", run="r")
    engine_store = FireIntentStore(tmp_path / "state")

    def racing_loader(root: str, run_id: str) -> Any:
        engine_store.update_governance("a", cap_state="paused")  # lands mid-view
        return probe_run(root, run_id)

    view = UsageLedger(
        readonly_fire_store(tmp_path / "state"), clock=lambda: r.now,
        run_state_loader=racing_loader, fs_now_ns=lambda: FAR_FUTURE_NS,
    )  # fmt: skip
    view.usage(r.root, "s", {"day": window_for(NOW, "day", UTC_TZ, "monday")})
    g = r.store.get("a", r.root).governance  # type: ignore[union-attr]
    assert g.cap_state == "paused" and g.cost_observed_usd == 0.0  # engine's write intact
    assert view.writes == 1  # the in-memory mark was applied, nothing hit disk
