"""T-nI4DBf: pause / resume / expiry of a cost-capped governed run. Fixed clocks, real files in tmp
dirs, a recording launcher (no process is ever spawned)."""

from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.models import RunState, TaskRunState, TrippedBreaker
from agent_orchestrator.schedules.governance import (
    MAX_USD,
    CostLimits,
    LimitsSpec,
    PeriodCostCap,
    QuotaSpec,
)
from agent_orchestrator.service import capped_resume as cr
from agent_orchestrator.service.fire_store import (
    FireGovernance,
    FireIntentRecord,
    FireIntentStore,
)
from agent_orchestrator.service.schedule_engine import REQUEST_TTL_SECONDS
from agent_orchestrator.service.usage_ledger import UsageLedger
from agent_orchestrator.ui.runs import RunNotFoundError

from .test_schedule_engine import T0, Env, binding

NOW = T0 + timedelta(minutes=10)
TTL = 3600
WEEK_MAX = 10.0
PER_RUN = 2.0


def limits(
    per_run: float | None = PER_RUN,
    week: float | None = WEEK_MAX,
    on_cap: str = "pause",
    ttl: int = TTL,
) -> LimitsSpec:
    return LimitsSpec(
        quotas=[],
        cost=CostLimits(
            per_run_usd=per_run,
            on_cap=on_cap,  # type: ignore[arg-type]
            pause_ttl_seconds=ttl,
            periods=[PeriodCostCap(period="week", max_usd=week)] if week is not None else [],
        ),
    )


class RunRepo:
    """Run repository over the real state.json files (what the retire hook reads)."""

    def __init__(self, root: str) -> None:
        self.root = root

    def load_state(self, run_id: str) -> RunState:
        f = Path(self.root) / ".orchestrator" / "runs" / run_id / "state.json"
        if not f.exists():
            raise RunNotFoundError(run_id)
        return RunState.model_validate_json(f.read_text())

    def exists(self, run_id: str) -> bool:
        return (Path(self.root) / ".orchestrator" / "runs" / run_id).exists()


class Launcher:
    """Implements ``launch`` (unused) and ``launch_capped_resume`` (records, can fail)."""

    def __init__(self, rig: Rig) -> None:
        self.rig = rig
        self.calls: list[tuple[str, float]] = []
        self.fail: Exception | None = None

    def launch(self, d: Any) -> Any:  # pragma: no cover - never fires in these tests
        raise AssertionError("no fresh launch expected")

    def launch_capped_resume(self, rec: FireIntentRecord, new_cap_usd: float) -> Any:
        if self.fail is not None:
            raise self.fail
        assert rec.run_id is not None
        self.calls.append((rec.run_id, new_cap_usd))
        self.rig.env.alive.add(rec.run_id)
        self.rig.env.statuses[rec.run_id] = "running"
        self.rig.write_state(rec.run_id, "running", cost=self.rig.cost(rec.run_id))
        return type("L", (), {"launch_id": f"resume-{len(self.calls)}", "pid": 4242})()


class Rig:
    def __init__(self, tmp: Path, lim: LimitsSpec | None = None, **bkw: Any) -> None:
        self.tmp = tmp
        lim = lim or limits()
        self.env = Env(tmp, [binding("s", limits=lim, **bkw)])
        self.launcher = Launcher(self)
        self.env.engine = self.env.make_engine(
            launcher=self.launcher,
            run_repository_factory=lambda root: RunRepo(root),
            usage_ledger_factory=lambda fires: UsageLedger(
                fires,
                clock=self.env.clock,
                summary_cost_loader=lambda ws, run: self.summaries.get(run, 0.0),
                fs_now_ns=lambda: 10**19,
            ),
        )
        self.env.clock.now = NOW
        self.summaries: dict[str, float] = {}
        self._gen = 0
        self._costs: dict[str, float] = {}
        self.engine = self.env.engine
        self.fires: FireIntentStore = self.engine.fire_store
        self.ledger = self.engine.usage_ledger
        self.binding = self.env.binding("s")
        self.root = self.env.root
        self.drained: list[cr.ResumeOutcome] = []

    # -- fixtures ---------------------------------------------------------------------------
    def cost(self, run: str) -> float:
        return self._costs.get(run, 0.0)

    def write_state(self, run: str, status: str, cost: float, trip_at: str | None = None) -> None:
        self._costs[run] = cost
        trips = (
            [TrippedBreaker(id="builtin.cost_cap", condition="cost", action="pause", at=trip_at)]
            if trip_at
            else []
        )
        st = RunState(
            run_id=run, workflow_id="w", started_at=T0.isoformat(), updated_at=T0.isoformat(),
            status=status,  # type: ignore[arg-type]
            tasks={"t": TaskRunState(status="succeeded", cumulative_cost_usd=cost,
                                     actuals_available=True)},
            tripped_breakers=trips,
        )  # fmt: skip
        d = Path(self.root) / ".orchestrator" / "runs" / run
        d.mkdir(parents=True, exist_ok=True)
        f = d / "state.json"
        f.write_text(st.model_dump_json())
        self._gen += 1  # coarse fs timestamps repeat: force a distinct mtime per rewrite
        os.utime(f, ns=(self._gen * 10**9, self._gen * 10**9))
        self.env.statuses[run] = status
        self.ledger.invalidate(self.root)

    def paused(
        self,
        key: str = "k1",
        run: str = "r1",
        cost: float = 2.0,
        expires: Any = None,
        cap_state: str = "paused",
        on_cap: str = "pause",
        **gov: Any,
    ) -> FireIntentRecord:
        g = FireGovernance(
            tz="UTC", week_start="monday", reserved_usd=PER_RUN, run_cost_cap_usd=PER_RUN,
            on_cap=on_cap, pause_ttl_seconds=TTL, cost_observed_usd=cost,
            cost_confidence="actual", cap_state=cap_state,  # type: ignore[arg-type]
            paused_at=T0, pause_expires_at=expires or (T0 + timedelta(seconds=TTL)),
            **gov,
        )  # fmt: skip
        rec = FireIntentRecord(
            fire_key=key, workspace_root=self.root, schedule_id="s", scheduled_for=T0,
            created_at=T0, status="launched", run_id=run, launch_id="launch-0", governance=g,
        )  # fmt: skip
        self.fires.write(rec)
        self.write_state(run, "failed", cost, trip_at=T0.isoformat())
        return rec

    def check(self, add: float | None = None, run: str = "r1", **kw: Any) -> cr.ResumeCheck:
        return cr.check_resume(
            binding=kw.pop("binding", self.binding),
            rec=kw.pop("rec", self.fires.find_by_run_id(self.root, run)),
            schedule_id=kw.pop("schedule_id", "s"),
            add_usd=add,
            now=kw.pop("now", NOW),
            ledger=self.ledger,
        )

    def request(self, add: float | None = None, run: str = "r1", drain: bool = False, **kw: Any):  # type: ignore[no-untyped-def]
        return cr.request_capped_resume(
            self.root, kw.pop("schedule_id", "s"), run, add, "cli",
            state_dir=self.tmp / "state", binding=kw.pop("binding", self.binding),
            fires=self.fires, ledger=self.ledger, clock=lambda: self.env.clock.now,
            new_id=kw.pop("new_id", lambda: "req1"),
            drain=self.engine.drain_resume_requests if drain else None,
        )  # fmt: skip

    def retire(self, now: Any) -> None:
        """What the next evaluation does first: derive liveness and run the retire hook."""
        self.env.clock.now = now
        self.engine._live_runs()  # noqa: SLF001

    def events(self, name: str) -> list[dict[str, Any]]:
        return self.env.events(name)

    def gov(self, key: str = "k1") -> FireGovernance:
        rec = self.fires.get(key, self.root)
        assert rec is not None and rec.governance is not None
        return rec.governance


@pytest.fixture()
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


def refused(fn: Any, *a: Any, **k: Any) -> cr.ResumeRefused:
    with pytest.raises(cr.ResumeRefused) as ei:
        fn(*a, **k)
    return ei.value


# -- AC1: validation order -----------------------------------------------------------------------


def test_unknown_schedule_then_foreign_or_missing_record(rig: Rig) -> None:
    rig.paused()
    assert refused(rig.check, binding=None).reason == "schedule_not_found"
    assert refused(rig.check, schedule_id="other").reason == "schedule_not_found"
    assert refused(rig.check, run="nope", rec=None).reason == "schedule_not_found"


@pytest.mark.parametrize(
    ("cap_state", "reason"),
    [
        ("aborted", "aborted"),
        ("expired", "expired"),
        ("none", "not_paused"),
        ("resumed", "not_paused"),
    ],
)
def test_cap_state_refusals(rig: Rig, cap_state: str, reason: str) -> None:
    rig.paused(cap_state=cap_state)
    assert refused(rig.check).reason == reason


def test_expiry_boundary_is_exclusive_of_the_expiry_instant(rig: Rig) -> None:
    rig.paused()
    exp = T0 + timedelta(seconds=TTL)
    assert rig.check(now=exp - timedelta(seconds=1)).add_usd == PER_RUN
    assert refused(rig.check, now=exp).reason == "expired"


def test_expired_beats_a_bad_amount(rig: Rig) -> None:
    rig.paused(expires=T0 + timedelta(minutes=1))
    assert refused(rig.check, add=-5).reason == "expired"


@pytest.mark.parametrize("add", [0, -1.0, math.nan, math.inf, PER_RUN + 0.01])
def test_bad_amounts(rig: Rig, add: float) -> None:
    rig.paused()
    assert refused(rig.check, add=add).reason == "bad_amount"


def test_amount_at_max_add_is_accepted(rig: Rig) -> None:
    rig.paused()
    assert rig.check(add=PER_RUN).add_usd == PER_RUN


def test_no_headroom_names_the_window_and_remaining(rig: Rig) -> None:
    # week max 10, this run already spent 9.5 -> 0.5 left; asking for the 2.0 default
    rig.paused(cost=9.5)
    e = refused(rig.check)
    assert e.reason == "no_headroom" and e.remaining_usd == pytest.approx(0.5)
    assert "week window" in str(e) and "0.50" in str(e)


def test_headroom_exactly_equal_is_accepted(rig: Rig) -> None:
    rig.paused(cost=8.0)
    assert rig.check().add_usd == 2.0


def test_default_add_is_per_run_or_min_reservation(tmp_path: Path) -> None:
    r = Rig(tmp_path / "a", limits(per_run=None, week=10))
    r.paused(cost=1.0)
    assert r.check().add_usd == cr.MIN_RESERVATION_USD
    r2 = Rig(tmp_path / "b", limits(per_run=3.0, week=10))
    r2.paused(cost=1.0)
    assert r2.check().add_usd == 3.0


def test_without_per_run_the_ceiling_is_the_remaining_headroom(tmp_path: Path) -> None:
    r = Rig(tmp_path, limits(per_run=None, week=10))
    r.paused(cost=4.0)  # 6 left
    assert r.check(add=6.0).add_usd == 6.0
    # ceiling == remaining here, so over-asking is a headroom refusal that names what is left
    e = refused(r.check, add=6.5)
    assert e.reason == "no_headroom" and e.remaining_usd == pytest.approx(6.0)


def test_new_cap_uses_engine_metric_not_summary_cost(rig: Rig) -> None:
    """L3: cost_observed would include summary_cost_usd; the engine gate never counts it."""
    rig.paused(cost=2.0)
    rig.summaries["r1"] = 0.7
    chk = rig.check()
    assert chk.new_cap_usd == pytest.approx(2.0 + PER_RUN)  # NOT 2.7 + 2.0


def test_max_add_and_default_add_helpers(rig: Rig) -> None:
    assert cr.max_add(rig.binding, 1.0) == PER_RUN * cr.MAX_RESUME_FACTOR
    assert cr.default_add(rig.binding) == PER_RUN


# -- AC2: the drop request -----------------------------------------------------------------------


def test_request_writes_one_atomic_file_with_the_contract_fields(rig: Rig) -> None:
    rig.paused()
    req = rig.request(1.5)
    files = list((rig.tmp / "state" / "schedules" / "requests").glob("*.json"))
    assert [f.name for f in files] == ["req1.json"] and not list(files[0].parent.glob("*.tmp*"))
    body = json.loads(files[0].read_text())
    assert body == {
        "kind": "resume_capped", "workspace_root": rig.root, "schedule_id": "s", "run_id": "r1",
        "add_usd": 1.5, "new_cap_usd": 3.5, "requested_by": "cli",
        "requested_at": NOW.isoformat(), "request_id": "req1",
    }  # fmt: skip
    assert req.new_cap_usd == 3.5
    assert cr.ResumeCappedRequest.from_json(body) == req


def test_refused_request_writes_nothing(rig: Rig) -> None:
    rig.paused(cap_state="aborted")
    assert refused(rig.request).reason == "aborted"
    assert not (rig.tmp / "state" / "schedules" / "requests").exists()


@pytest.mark.parametrize(
    "raw",
    [None, [], {"kind": "run_now"}, {"kind": "resume_capped"}, {"kind": "resume_capped",
     "workspace_root": "w", "schedule_id": "s", "run_id": "r", "add_usd": "x",
     "new_cap_usd": 1, "requested_at": NOW.isoformat(), "request_id": "i"},
     {"kind": "resume_capped", "workspace_root": "w", "schedule_id": "s", "run_id": "r",
      "add_usd": 1, "new_cap_usd": 1, "requested_at": "2026-01-01T00:00:00", "request_id": "i"}],
)  # fmt: skip
def test_malformed_request_bodies_parse_to_none(raw: Any) -> None:
    assert cr.ResumeCappedRequest.from_json(raw) is None


def test_in_process_path_drains_immediately(rig: Rig) -> None:
    rig.paused()
    rig.request(drain=True)
    assert rig.launcher.calls == [("r1", 4.0)]
    assert not list((rig.tmp / "state" / "schedules" / "requests").glob("*.json"))


def test_in_process_path_reraises_a_drain_refusal(rig: Rig) -> None:
    rig.paused()
    rig.env.make_active("s", key="other", run_id="rX")  # overlap: another run is live
    assert refused(rig.request, drain=True).reason == "overlap"
    assert rig.launcher.calls == []


# -- AC3/AC4: the engine drain -------------------------------------------------------------------


def test_drain_resumes_updates_record_marks_active_and_emits(rig: Rig) -> None:
    rig.paused()
    before = rig.ledger.usage(rig.root, "s", _week())
    rig.request(1.5)
    rig.env.clock.now = NOW + timedelta(seconds=30)
    [out] = rig.engine.drain_resume_requests()
    assert out.refused is None
    g = rig.gov()
    assert (g.cap_state, g.reserved_usd, g.run_cost_cap_usd, g.final) == (
        "resumed",
        3.5,
        3.5,
        False,
    )
    assert g.resumes == [
        {"at": (NOW + timedelta(seconds=30)).isoformat(), "by": "cli", "add_usd": 1.5,
         "new_cap_usd": 3.5, "request_id": "req1"}
    ]  # fmt: skip
    assert rig.launcher.calls == [("r1", 3.5)]
    st = rig.engine.state_store.get(rig.root, "s")
    assert st is not None and "k1" in st.active_fire_keys
    rec = rig.fires.get("k1", rig.root)
    assert rec is not None and rec.launch_id == "resume-1" and rec.pid == 4242
    [ev] = rig.events("schedule.governance.resumed")
    assert (ev["run_id"], ev["add_usd"], ev["new_cap_usd"], ev["by"]) == ("r1", 1.5, 3.5, "cli")
    # AC4: no quota slot consumed
    assert rig.ledger.usage(rig.root, "s", _week())["week"].runs == before["week"].runs == 1


def test_resumed_run_holds_its_new_cap_in_the_ledger(rig: Rig) -> None:
    rig.paused(cost=2.0)
    rig.request(1.5)
    rig.engine.drain_resume_requests()
    u = rig.ledger.usage(rig.root, "s", _week())["week"]
    assert u.actual_usd + u.reserved_usd == pytest.approx(3.5)


def test_drain_rechecks_state_under_the_lock(rig: Rig) -> None:
    """The record changed between request and drain (expired): the drain refuses."""
    rig.paused()
    rig.env.clock.now = T0 + timedelta(seconds=TTL - 60)
    rig.request()
    rig.env.clock.now = T0 + timedelta(seconds=TTL)  # expired by drain time (request age: 60 s)
    [out] = rig.engine.drain_resume_requests()
    assert out.refused is not None and out.refused.reason == "expired"
    assert rig.launcher.calls == [] and rig.gov().cap_state == "paused"
    [ev] = rig.events("schedule.governance.resume_refused")
    assert ev["reason"] == "expired"


def test_overlap_refuses_unless_allow(tmp_path: Path) -> None:
    r = Rig(tmp_path / "skip")
    r.paused()
    r.env.make_active("s", key="other", run_id="rX")
    r.request()
    [out] = r.engine.drain_resume_requests()
    assert out.refused is not None and out.refused.reason == "overlap"
    assert r.launcher.calls == [] and r.gov().cap_state == "paused"

    a = Rig(tmp_path / "allow", overlap="allow")
    a.paused()
    a.env.make_active("s", key="other", run_id="rX")
    a.request()
    [out2] = a.engine.drain_resume_requests()
    assert out2.refused is None and a.launcher.calls == [("r1", 4.0)]


def test_stale_request_is_discarded_with_request_expired(rig: Rig) -> None:
    rig.paused()
    rig.request()
    rig.env.clock.now = NOW + timedelta(seconds=REQUEST_TTL_SECONDS + 1)
    assert rig.engine.drain_resume_requests() == []
    assert rig.launcher.calls == []
    assert rig.events("schedule.request_expired")[0]["reason"] == "expired"
    assert not list((rig.tmp / "state" / "schedules" / "requests").glob("*.json"))


def test_invalid_request_file_is_discarded_run_now_files_are_left(rig: Rig) -> None:
    d = rig.tmp / "state" / "schedules" / "requests"
    d.mkdir(parents=True)
    (d / "bad.json").write_text(json.dumps({"kind": "resume_capped", "run_id": 3}))
    (d / "now.json").write_text(json.dumps({"kind": "run_now"}))
    assert rig.engine.drain_resume_requests() == []
    assert not (d / "bad.json").exists() and (d / "now.json").exists()
    assert rig.events("schedule.request_expired")[0]["reason"] == "invalid"


def test_launch_failure_rolls_the_record_back(rig: Rig) -> None:
    rig.paused()
    rig.request()
    rig.launcher.fail = RuntimeError("boom")
    [out] = rig.engine.drain_resume_requests()
    assert out.refused is not None and out.refused.reason == "launch_failed"
    g = rig.gov()
    assert (g.cap_state, g.reserved_usd, g.final, g.resumes) == ("paused", PER_RUN, False, [])
    st = rig.engine.state_store.get(rig.root, "s")
    assert st is None or "k1" not in st.active_fire_keys
    assert rig.events("schedule.governance.resumed") == []


def test_no_resume_capable_launcher_refuses_cleanly(rig: Rig) -> None:
    rig.paused()
    rig.request()
    rig.engine._launcher = None  # noqa: SLF001
    [out] = rig.engine.drain_resume_requests()
    assert out.refused is not None and out.refused.reason == "launch_failed"
    assert rig.gov().cap_state == "paused"


def test_tick_drains_requests_via_the_engine_loop(rig: Rig) -> None:
    rig.paused()
    rig.request()
    rig.env.tick(NOW + timedelta(seconds=5))
    assert rig.launcher.calls == [("r1", 4.0)]


# -- AC5: re-pause with a fresh TTL; two resumes stay inside the original window ------------------


def test_two_resumes_then_final_cap_stay_within_the_week_cap(rig: Rig) -> None:
    rig.paused(cost=2.0)
    t = NOW
    for n in (1, 2):
        rig.request(PER_RUN, new_id=lambda n=n: f"req{n}")  # type: ignore[call-arg]
        t += timedelta(minutes=1)
        rig.env.clock.now = t
        assert rig.engine.drain_resume_requests()[0].refused is None
        # the resumed run spends its top-up, caps again and dies
        spent = 2.0 + PER_RUN * n
        t += timedelta(minutes=5)
        rig.env.clock.now = t
        rig.env.alive.discard("r1")
        rig.write_state("r1", "failed", spent, trip_at=t.isoformat())
        rig.retire(t)  # retire hook re-classifies the run
        g = rig.gov()
        assert g.cap_state == "paused"
        assert g.paused_at == t and g.pause_expires_at == t + timedelta(seconds=TTL)  # fresh TTL
    assert len(rig.gov().resumes) == 2
    u = rig.ledger.usage(rig.root, "s", _week())["week"]
    assert u.actual_usd + u.reserved_usd == pytest.approx(6.0)
    assert u.actual_usd + u.reserved_usd <= WEEK_MAX
    # a third resume that would breach the week cap is refused with no_headroom
    rig.write_state("r1", "failed", 9.5, trip_at=t.isoformat())
    assert refused(rig.request, new_id=lambda: "req3").reason == "no_headroom"  # type: ignore[call-arg]


def test_stale_cap_trip_does_not_repause_a_later_non_cap_failure(rig: Rig) -> None:
    """M3 interplay: the trip predates the resume instant, so a plain failure is not a pause."""
    rig.paused()
    rig.request()
    rig.env.clock.now = NOW + timedelta(minutes=1)
    rig.engine.drain_resume_requests()
    rig.env.alive.discard("r1")
    rig.write_state("r1", "failed", 2.5, trip_at=T0.isoformat())  # old trip only
    rig.retire(NOW + timedelta(minutes=5))
    g = rig.gov()
    assert g.cap_state == "resumed" and g.final is True


# -- AC6/AC7: expiry -----------------------------------------------------------------------------


def test_expiry_sweep_boundaries_idempotence_and_state_json_untouched(rig: Rig) -> None:
    rig.paused()
    state_file = Path(rig.root) / ".orchestrator" / "runs" / "r1" / "state.json"
    before = state_file.read_bytes()
    exp = T0 + timedelta(seconds=TTL)
    assert cr.sweep_pause_expiry(rig.fires, rig.engine.event_log, exp - timedelta(seconds=1)) == []
    assert rig.gov().cap_state == "paused"
    assert cr.sweep_pause_expiry(rig.fires, rig.engine.event_log, exp) == ["k1"]
    g = rig.gov()
    assert g.cap_state == "expired" and g.final is True
    [ev] = rig.events("schedule.governance.pause_expired")
    assert ev["run_id"] == "r1"
    assert cr.sweep_pause_expiry(rig.fires, rig.engine.event_log, exp) == []  # idempotent
    assert len(rig.events("schedule.governance.pause_expired")) == 1
    assert state_file.read_bytes() == before


def test_expiry_event_is_a_warning(rig: Rig, caplog: pytest.LogCaptureFixture) -> None:
    rig.paused()
    with caplog.at_level(logging.WARNING):
        cr.sweep_pause_expiry(rig.fires, rig.engine.event_log, T0 + timedelta(seconds=TTL))
    rec = [r for r in caplog.records if "pause_expired" in r.getMessage()]
    assert rec and rec[0].levelno == logging.WARNING


def test_tick_sweeps_expiry(rig: Rig) -> None:
    rig.paused()
    rig.env.tick(T0 + timedelta(seconds=TTL))
    assert rig.gov().cap_state == "expired"


def test_sweep_ignores_non_paused_and_open_pauses(rig: Rig) -> None:
    rig.paused("a", "ra", cap_state="resumed")
    rig.paused("b", "rb", expires=T0 + timedelta(days=1))
    assert cr.sweep_pause_expiry(rig.fires, rig.engine.event_log, T0 + timedelta(hours=2)) == []


def test_abort_policy_refuses_immediately(tmp_path: Path) -> None:
    r = Rig(tmp_path, limits(on_cap="abort"))
    r.paused(cap_state="aborted", on_cap="abort", final=True)
    assert refused(r.request).reason == "aborted"


def test_ttl_zero_expires_on_first_sweep(tmp_path: Path) -> None:
    r = Rig(tmp_path, limits(ttl=0))
    r.paused(expires=T0)
    assert refused(r.request).reason == "expired"
    assert cr.sweep_pause_expiry(r.fires, r.engine.event_log, NOW) == ["k1"]


def test_retire_hook_applies_on_cap_and_ttl_end_to_end(tmp_path: Path) -> None:
    """The retire hook (existing) feeds this lifecycle: on_cap=pause -> paused, abort -> aborted."""
    for on_cap, want in (("pause", "paused"), ("abort", "aborted")):
        r = Rig(tmp_path / on_cap, limits(on_cap=on_cap))
        g = FireGovernance(
            tz="UTC", week_start="monday", reserved_usd=2.0, run_cost_cap_usd=2.0, on_cap=on_cap,
            pause_ttl_seconds=TTL,
        )  # fmt: skip
        r.fires.write(
            FireIntentRecord(
                fire_key="k",
                workspace_root=r.root,
                schedule_id="s",
                scheduled_for=T0,
                created_at=T0,
                status="launched",
                run_id="rr",
                governance=g,
            )  # fmt: skip
        )
        r.engine.state_store.record_fire(r.root, "s", scheduled_for=T0, run_id="rr",
                                         fire_at=T0, fire_key="k")  # fmt: skip
        r.write_state("rr", "failed", 2.1, trip_at=NOW.isoformat())
        r.env.tick(NOW + timedelta(minutes=1))
        got = r.gov("k")
        assert got.cap_state == want
        if want == "paused":
            assert got.pause_expires_at == NOW + timedelta(minutes=1, seconds=TTL)
        else:
            assert got.final is True


# -- AC8: single launch authority ----------------------------------------------------------------


def test_only_the_resume_drain_calls_launch_capped_resume() -> None:
    src = Path(__file__).resolve().parents[2] / "src" / "agent_orchestrator"
    callers = {
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if re.search(r"\.launch_capped_resume\(", p.read_text())
    }
    # the only real call site: the drain in capped_resume.py (the engine reaches it through that).
    # The two Deferred launcher shims merely forward the call to the real launcher (B-1, w22-01).
    assert callers == {"service/capped_resume.py", "schedules/cli.py", "service/cli.py"}
    cli_like = [p for p in src.rglob("*.py") if "launch_resume(" in p.read_text()]
    # nothing in the schedule layer (cli/engine/schedules) calls launch_resume directly
    assert not [p for p in cli_like if "schedules/" in p.as_posix()]


# -- AC9: out-of-band resume after expiry is still charged ---------------------------------------


def test_out_of_band_resume_after_expiry_is_still_charged(rig: Rig) -> None:
    rig.paused(cost=2.0)
    cr.sweep_pause_expiry(rig.fires, rig.engine.event_log, T0 + timedelta(seconds=TTL))
    assert rig.gov().cap_state == "expired"
    before = rig.ledger.usage(rig.root, "s", _week())["week"].actual_usd
    # `ao resume --cost-cap-usd 99` outside governance: the run's state.json cost rises
    rig.write_state("r1", "running", 5.0)
    after = rig.ledger.usage(rig.root, "s", _week())["week"].actual_usd
    assert before == pytest.approx(2.0) and after == pytest.approx(5.0)


# -- AC12: governed-run lookup -------------------------------------------------------------------


def test_is_governed_run(rig: Rig) -> None:
    rig.paused()
    assert cr.is_governed_run(rig.root, "r1", fires=rig.fires)
    assert not cr.is_governed_run(rig.root, "nope", fires=rig.fires)
    rig.fires.write(
        FireIntentRecord(
            fire_key="plain",
            workspace_root=rig.root,
            schedule_id="s",
            scheduled_for=T0,
            created_at=T0,
            status="launched",
            run_id="rp",
        )  # fmt: skip
    )
    assert not cr.is_governed_run(rig.root, "rp", fires=rig.fires)  # no governance sub-object


def _week() -> Any:
    from zoneinfo import ZoneInfo

    from agent_orchestrator.schedules.windows import windows_for

    return windows_for(T0, ["week"], ZoneInfo("UTC"), "monday")


# -- AC11: launch inputs come from the ORIGINAL LaunchRecord ---------------------------------------


def _capped_launch_rig(tmp_path: Path) -> Any:
    from agent_orchestrator.service.schedule_launch import ScheduledLauncher  # noqa: F401

    from .test_governance_gate import cap_renderer, gov_decision, with_cap_argv
    from .test_schedule_launch import Rig as LaunchRig
    from .test_schedule_launch import make_binding

    rig = LaunchRig(tmp_path, options_renderer=cap_renderer)
    with_cap_argv(rig)
    inner = rig.sup.launch_resume

    def launch_resume(run_id: str, **kw: Any) -> Any:
        rec = inner(run_id, **kw)
        rec.argv = [
            "ao",
            "resume",
            "--cost-cap-usd",
            str((kw.get("options") or {})["cost_cap_usd"]),
        ]
        return rec

    rig.sup.launch_resume = launch_resume  # type: ignore[method-assign]
    rig.add_template()
    b = make_binding(
        workflow=None, template="mini", instance_id="nightly-{rand6}", limits=limits(per_run=2)
    )
    return rig, gov_decision(rig, b)


def test_template_instance_resume_reuses_the_instance_and_never_renders(tmp_path: Path) -> None:
    rig, d = _capped_launch_rig(tmp_path)
    rec = rig.launcher.launch(d)
    assert rec.status == "launched" and rec.launch_id is not None
    first_path = rig.sup.launch_calls[0]["workflow_path"]
    renders = rig.template_calls
    launched = rig.launcher.launch_capped_resume(rec, 3.5)
    call = rig.sup.launch_calls[-1]
    assert call["run_id"] == rec.run_id and call["workflow_path"] == first_path  # same instance dir
    assert call["options"] == {"cost_cap_usd": 3.5} and launched.argv[-1] == "3.5"
    assert rig.template_calls == renders  # no template discovery/instantiation on resume
    assert len(rig.sup.launch_calls) == 2


def test_resume_without_original_launch_record_is_refused_not_guessed(tmp_path: Path) -> None:
    from agent_orchestrator.service.schedule_launch import LaunchResolutionError

    rig, d = _capped_launch_rig(tmp_path)
    rec = rig.launcher.launch(d)
    rig.sup.records.clear()
    with pytest.raises(LaunchResolutionError):
        rig.launcher.launch_capped_resume(rec, 3.0)
    assert len(rig.sup.launch_calls) == 1


def test_resume_fails_closed_when_the_cap_flag_would_be_dropped(tmp_path: Path) -> None:
    from agent_orchestrator.service.schedule_launch import CostCapNotForwardedError

    rig, d = _capped_launch_rig(tmp_path)
    rec = rig.launcher.launch(d)
    rig.launcher._render = lambda o: []  # type: ignore[method-assign]  # renderer drops the cap
    with pytest.raises(CostCapNotForwardedError):
        rig.launcher.launch_capped_resume(rec, 3.0)
    assert len(rig.sup.launch_calls) == 1  # nothing spawned


def test_post_spawn_argv_without_the_cap_cancels_the_resumed_child(tmp_path: Path) -> None:
    from agent_orchestrator.service.schedule_launch import CostCapNotForwardedError

    rig, d = _capped_launch_rig(tmp_path)
    rec = rig.launcher.launch(d)
    inner = rig.sup.launch_resume

    def bare(run_id: str, **kw: Any) -> Any:
        r = inner(run_id, **kw)
        r.argv = ["ao", "resume"]
        return r

    rig.sup.launch_resume = bare  # type: ignore[method-assign]
    with pytest.raises(CostCapNotForwardedError):
        rig.launcher.launch_capped_resume(rec, 3.0)
    assert rig.sup.cancelled == [rec.run_id]


# -- T-lTW88y H-2 audit: a top-up can never mint an unbounded cap (re-landed, w23-06) ------------


def test_max_add_is_bounded_without_per_run_or_period_cap(tmp_path: Path) -> None:
    """No per-run cap and no period cap (remaining == inf): the absolute MAX_USD bounds it."""
    lim = limits(per_run=None, week=None)
    lim.quotas = [QuotaSpec(period="day", max_runs=5)]  # D-2: some limit must be declared
    rig = Rig(tmp_path, lim=lim)
    assert cr.max_add(rig.binding, math.inf) == MAX_USD
    assert cr.max_add(rig.binding, 7.5) == 7.5  # finite headroom still wins when smaller
    assert cr.max_add(rig.binding, -1.0) == 0.0  # never negative


def test_max_add_with_per_run_ignores_headroom(rig: Rig) -> None:
    assert cr.max_add(rig.binding, math.inf) == PER_RUN * cr.MAX_RESUME_FACTOR


def _req_body(**over: Any) -> dict[str, Any]:
    body = {
        "kind": "resume_capped", "workspace_root": "w", "schedule_id": "s", "run_id": "r",
        "add_usd": 1.0, "new_cap_usd": 3.0, "requested_by": "cli",
        "requested_at": NOW.isoformat(), "request_id": "req-1",
    }  # fmt: skip
    return body | over


def test_from_json_accepts_a_well_formed_body() -> None:
    req = cr.ResumeCappedRequest.from_json(_req_body())
    assert req is not None and req.requested_by == "cli" and req.request_id == "req-1"


@pytest.mark.parametrize(
    "over",
    [
        {"requested_by": "x" * (cr.MAX_REQUEST_TEXT + 1)},  # unbounded free text
        {"request_id": "../../etc/passwd"},  # path-ish id
        {"request_id": ""},
        {"request_id": "a" * 65},
        {"add_usd": float("nan")},
        {"new_cap_usd": float("inf")},
        {"requested_at": "2026-01-01T00:00:00"},  # naive timestamp
        {"run_id": 7},  # non-string identifier
    ],
)
def test_from_json_rejects_unbounded_or_malformed_fields(over: dict[str, Any]) -> None:
    assert cr.ResumeCappedRequest.from_json(_req_body(**over)) is None
    # the text-bound edge itself is accepted
    ok = cr.ResumeCappedRequest.from_json(_req_body(requested_by="x" * cr.MAX_REQUEST_TEXT))
    assert ok is not None
