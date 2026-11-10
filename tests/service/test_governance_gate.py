"""T-PpE5lf: pre-fire governance gate, reservation, retire hook. Fixed clocks, no real spawns.

Covers ``service/governance_gate.py`` plus the governance call sites in ``schedule_engine`` and
``schedule_launch`` (kept together so the AC-to-test mapping is one file).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from agent_orchestrator.models import TrippedBreaker
from agent_orchestrator.schedules.governance import (
    MAX_PROMPT_BYTES,
    CostLimits,
    LimitsSpec,
    PeriodCostCap,
    QuotaSpec,
)
from agent_orchestrator.schedules.windows import Period, Window, window_for
from agent_orchestrator.service import governance_gate as gg
from agent_orchestrator.service import schedule_engine as se
from agent_orchestrator.service.fire_store import (
    FireGovernance,
    FireIntentRecord,
    FireIntentStore,
    fire_key,
)
from agent_orchestrator.service.governance_gate import (
    FireRecordLedger,
    WindowUsage,
    gate,
    read_prompt,
)
from agent_orchestrator.service.schedule_engine import FireDecision, RunNowRequest
from agent_orchestrator.service.schedule_launch import ScheduledLauncher

from ..ui.conftest import write_workflow
from .test_schedule_engine import NOW1, T0, Env, binding, env_for
from .test_schedule_launch import Rig, fixed_clock, make_binding

UTC_TZ = ZoneInfo("UTC")
SLOW_CI_FACTOR = 6


def limits(
    quota_day: int | None = None,
    per_run: float | None = None,
    week: float | None = None,
    on_cap: str = "pause",
    ttl: int = 3600,
) -> LimitsSpec:
    cost = None
    if per_run is not None or week is not None:
        cost = CostLimits(
            per_run_usd=per_run,
            on_cap=on_cap,  # type: ignore[arg-type]
            pause_ttl_seconds=ttl,
            periods=[PeriodCostCap(period="week", max_usd=week)] if week is not None else [],
        )
    quotas = [QuotaSpec(period="day", max_runs=quota_day)] if quota_day is not None else []
    return LimitsSpec(quotas=quotas, cost=cost)


class FakeLedger:
    """Returns canned usage for every requested period."""

    def __init__(self, **by_period: WindowUsage) -> None:
        self.by_period = by_period
        self.invalidations = 0

    def usage(
        self, workspace_root: str, schedule_id: str, windows: Mapping[Period, Window]
    ) -> dict[Period, WindowUsage]:
        return {p: self.by_period.get(p, WindowUsage()) for p in windows}

    def invalidate(self, workspace_root: str, schedule_id: str) -> None:
        self.invalidations += 1


def run_gate(lim: LimitsSpec, **usage: WindowUsage) -> gg.GateResult:
    b = binding("g", limits=lim)
    return gate(
        b, lim, T0, ledger=FakeLedger(**usage), workspace_root="/ws", tz=UTC_TZ, week_start="monday"
    )


# -- AC3: gate semantics + boundaries ---------------------------------------------------------


def test_quotas_in_declared_order_first_failing_reported() -> None:
    lim = LimitsSpec(
        quotas=[QuotaSpec(period="week", max_runs=5), QuotaSpec(period="day", max_runs=1)]
    )
    g = run_gate(lim, week=WindowUsage(runs=5), day=WindowUsage(runs=1))
    assert (g.admit, g.reason, g.detail["period"]) == (False, "quota_exhausted", "week")
    assert g.detail["used"] == 5 and g.detail["max"] == 5 and "resets_at" in g.detail
    g = run_gate(lim, week=WindowUsage(runs=4), day=WindowUsage(runs=1))
    assert g.detail["period"] == "day"
    assert run_gate(lim, week=WindowUsage(runs=4), day=WindowUsage(runs=0)).admit


@pytest.mark.parametrize(
    ("spent", "admit"),
    [(2.99, True), (3.00, True), (3.01, False)],  # remaining 2.01 / 2.00 / 1.99 vs per_run 2
)
def test_per_run_boundary_skip_iff_remaining_below_per_run(spent: float, admit: bool) -> None:
    g = run_gate(limits(per_run=2, week=5), week=WindowUsage(runs=1, actual=spent))
    assert g.admit is admit
    if admit:
        assert g.reservation_usd == 2
    else:
        assert g.reason == "cost_cap_reached"
        assert g.detail["needed_usd"] == 2 and g.detail["period"] == "week"
        assert g.detail["remaining_usd"] == pytest.approx(5 - spent)


@pytest.mark.parametrize(
    ("held", "admit", "reservation"),
    [(4.89, True, 0.11), (4.90, True, 0.10), (4.91, False, None), (3.7654, True, 1.23)],
)
def test_period_only_reservation_rounds_down_and_min_boundary(
    held: float, admit: bool, reservation: float | None
) -> None:
    g = run_gate(limits(week=5), week=WindowUsage(reserved=held))
    assert g.admit is admit and g.reservation_usd == reservation
    if not admit:
        assert g.detail["needed_usd"] == 0.10


def test_actual_plus_reserved_both_count_and_no_cost_block_is_free() -> None:
    assert not run_gate(limits(per_run=2, week=5), week=WindowUsage(actual=1.5, reserved=2)).admit
    g = run_gate(limits(quota_day=3))
    assert g.admit and g.reservation_usd is None
    g = run_gate(LimitsSpec(quotas=[QuotaSpec(period="day", max_runs=1)], cost=CostLimits()))
    assert g.admit and g.reservation_usd is None  # on_cap-only cost block reserves nothing


# -- AC8: prompt ------------------------------------------------------------------------------


def _prompt_binding(tmp: Path, data: bytes | None, **kw: Any) -> Any:
    if data is not None:
        (tmp / "p.md").write_bytes(data)
    return binding("g", prompt_file="p.md", **kw)


def test_prompt_matrix(tmp_path: Path) -> None:
    root = str(tmp_path)
    assert isinstance(read_prompt(binding("g"), root), gg.PromptSnapshot)
    assert read_prompt(binding("g"), root).text is None  # type: ignore[union-attr]
    g = read_prompt(_prompt_binding(tmp_path, None), root)
    assert isinstance(g, gg.GateResult) and g.reason == "prompt_missing" and not g.admit
    g = read_prompt(_prompt_binding(tmp_path, b"x" * (MAX_PROMPT_BYTES + 1)), root)
    assert isinstance(g, gg.GateResult)
    assert (g.reason, g.detail["detail"]) == ("prompt_missing", "too_large")
    g = read_prompt(_prompt_binding(tmp_path, b"\xff\xfe\x00bad"), root)
    assert isinstance(g, gg.GateResult)
    assert (g.reason, g.detail["detail"]) == ("prompt_missing", "not_utf8")
    data = "héllo".encode()
    sha = hashlib.sha256(data).hexdigest()
    g = read_prompt(_prompt_binding(tmp_path, data, prompt_sha256="0" * 64), root)
    assert isinstance(g, gg.GateResult) and g.reason == "prompt_changed"
    snap = read_prompt(_prompt_binding(tmp_path, data, prompt_sha256=sha), root)
    assert isinstance(snap, gg.PromptSnapshot)
    assert (snap.text, snap.sha256, snap.bytes) == ("héllo", sha, len(data))


def test_prompt_symlink_escaping_root_is_refused(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (tmp_path / "secret.md").write_text("s")
    (ws / "p.md").symlink_to(tmp_path / "secret.md")
    g = read_prompt(binding("g", prompt_file="p.md"), str(ws))
    assert isinstance(g, gg.GateResult) and g.reason == "prompt_missing"
    assert g.detail["detail"] == "escapes_root"


# -- interim ledger: counting, windows, mtime cache -------------------------------------------


def _put(
    store: FireIntentStore,
    root: str,
    key: str,
    at: datetime,
    status: str = "launched",
    sid: str = "s",
    gov: FireGovernance | None = None,
    run_id: str | None = None,
) -> None:
    store.write(
        FireIntentRecord(
            fire_key=key,
            workspace_root=root,
            schedule_id=sid,
            scheduled_for=at,
            created_at=at,
            status=status,  # type: ignore[arg-type]
            run_id=run_id,
            governance=gov,
        )
    )


def test_ledger_counts_only_started_runs_in_window(tmp_path: Path) -> None:
    root = str((tmp_path / "ws").resolve())
    store = FireIntentStore(tmp_path / "st")
    gov = FireGovernance(reserved_usd=2.0, cost_observed_usd=0.5)
    _put(store, root, "a", T0, gov=gov)
    _put(store, root, "b", T0 + timedelta(hours=1), "suppressed")
    _put(store, root, "c", T0 + timedelta(hours=2), "launch_failed")
    _put(store, root, "d", T0 + timedelta(hours=3), "orphaned", gov=gov)
    _put(store, root, "e", T0 + timedelta(days=1), gov=gov)  # next day
    _put(store, root, "f", T0, sid="other")
    w = {"day": window_for(T0, "day", UTC_TZ, "monday")}
    u = FireRecordLedger(store).usage(root, "s", w)["day"]
    # a: 1 run, actual .5, held 1.5; d orphaned: 1 run, actual .5, held 0
    assert (u.runs, u.actual, u.reserved) == (2, 1.0, 1.5)


def test_ledger_mtime_cache_avoids_rescans_once_settled(tmp_path: Path) -> None:
    root = str((tmp_path / "ws").resolve())
    store = FireIntentStore(tmp_path / "st")
    _put(store, root, "a", T0)
    ledger = FireRecordLedger(store)
    w = {"day": window_for(T0, "day", UTC_TZ, "monday")}
    ledger.usage(root, "s", w)
    assert ledger.scans == 1
    ledger.usage(root, "s", w)  # dir mtime is "now": not yet trusted
    assert ledger.scans == 2
    d = store.directory(root)
    old = time.time() - 3600
    for p in [*d.glob("*.json"), d]:
        os.utime(p, (old, old))
    ledger.usage(root, "s", w)  # rescan, now settled
    scans = ledger.scans
    for _ in range(5):
        ledger.usage(root, "s", w)
    assert ledger.scans == scans
    ledger.invalidate(root, "s")
    ledger.usage(root, "s", w)
    assert ledger.scans == scans + 1


# -- engine rig -------------------------------------------------------------------------------


class GovRepo:
    """RunRepository stand-in whose ``load_state`` returns a rich-enough state object."""

    def __init__(self, env: Env) -> None:
        self.env = env

    def load_state(self, run_id: str) -> Any:
        if run_id not in self.env.statuses:
            raise se.RunNotFoundError(run_id)
        st = self.env.run_states.get(run_id)
        return st or SimpleNamespace(status=self.env.statuses[run_id])

    def exists(self, run_id: str) -> bool:
        return run_id in self.env.statuses


def gov_env(tmp_path: Path, lim: LimitsSpec, **bkw: Any) -> Env:
    env = env_for(tmp_path, [binding("s", limits=lim, **bkw)])
    env.run_states = {}  # type: ignore[attr-defined]
    env.engine._repos.clear()
    env.engine._repo_factory = lambda root: GovRepo(env)
    env.anchor()
    return env


def put_run(
    env: Env,
    key: str,
    at: datetime,
    run_id: str,
    *,
    reserved: float = 2.0,
    observed: float = 0.0,
    live: bool = True,
    status: str = "launched",
    on_cap: str = "pause",
    ttl: int = 3600,
    sid: str = "s",
    **gov_extra: Any,
) -> None:
    gov = FireGovernance(
        reserved_usd=reserved,
        run_cost_cap_usd=reserved,
        cost_observed_usd=observed,
        on_cap=on_cap,
        pause_ttl_seconds=ttl,
        **gov_extra,
    )
    _put(env.engine.fire_store, env.root, key, at, status, sid=sid, gov=gov, run_id=run_id)
    env.engine.state_store.record_fire(
        env.root, sid, scheduled_for=at, run_id=run_id, fire_at=at, fire_key=key
    )
    env.statuses[run_id] = "running"
    if live:
        env.alive.add(run_id)


def suppressed(env: Env, at: datetime = NOW1) -> FireIntentRecord | None:
    return env.engine.fire_store.get(fire_key(env.root, "s", at), env.root)


# -- AC1 order / AC2 skip record --------------------------------------------------------------


def test_overlap_blocked_over_quota_reports_overlap_not_quota(tmp_path: Path) -> None:
    env = gov_env(tmp_path, limits(quota_day=1), overlap="skip")
    put_run(env, "k0", T0, "r0", reserved=0)
    d = env.evaluate(now=NOW1)
    assert d.reason == "overlap_skip"
    rec = suppressed(env)
    assert rec is not None and rec.reason == "overlap"
    assert not env.events("schedule.governance.skipped")


def test_over_quota_and_concurrency_blocked_burns_as_quota_exhausted(tmp_path: Path) -> None:
    env = gov_env(tmp_path, limits(quota_day=1), overlap="allow", max_concurrent=1)
    put_run(env, "k0", T0, "r0", reserved=0)
    d = env.evaluate(now=NOW1)
    assert (d.fire, d.reason) == (False, "quota_exhausted")
    assert env.engine.fire_store.exists(fire_key(env.root, "s", NOW1), env.root)
    assert not env.events("schedule.skipped_concurrency")


def test_concurrency_blocked_within_quota_is_not_burned(tmp_path: Path) -> None:
    env = gov_env(tmp_path, limits(quota_day=5), overlap="allow", max_concurrent=1)
    put_run(env, "k0", T0, "r0", reserved=0)
    d = env.evaluate(now=NOW1)
    assert d.reason == "concurrency"
    assert not env.engine.fire_store.exists(fire_key(env.root, "s", NOW1), env.root)


def test_governance_skip_writes_skeleton_and_event(tmp_path: Path) -> None:
    lim = limits(quota_day=1)
    env = gov_env(tmp_path, lim)
    put_run(env, "k0", T0, "r0", reserved=0, live=False)
    d = env.evaluate(now=NOW1)
    assert d.reason == "quota_exhausted" and d.gate is not None and d.effective_limits == lim
    rec = suppressed(env)
    assert rec is not None and rec.status == "suppressed" and rec.reason == "quota_exhausted"
    g = rec.governance
    assert g is not None
    assert g.limits_digest == gg.digest_of(lim) and g.tz == "UTC" and g.week_start == "monday"
    assert g.detail["period"] == "day" and g.detail["used"] == 1 and g.detail["max"] == 1
    ev = env.events("schedule.governance.skipped")
    assert len(ev) == 1
    assert (ev[0]["reason"], ev[0]["period"], ev[0]["used"], ev[0]["max"]) == (
        "quota_exhausted", "day", 1, 1,
    )  # fmt: skip
    assert ev[0]["resets_at"] == g.detail["resets_at"]
    # the key is burned: the same instant is "already fired" next time
    assert env.engine.fire_store.exists(fire_key(env.root, "s", NOW1), env.root)


def test_admitted_decision_carries_limits_and_gate(tmp_path: Path) -> None:
    lim = limits(quota_day=3, per_run=2, week=50)
    env = gov_env(tmp_path, lim)
    d = env.evaluate(now=NOW1)
    assert d.fire and d.effective_limits == lim
    assert d.gate is not None and d.gate.admit and d.gate.reservation_usd == 2


# -- AC10 period cap mid-run + AC9 retire hook ------------------------------------------------


def test_period_cap_mid_run_then_settle_admits(tmp_path: Path) -> None:
    env = gov_env(tmp_path, limits(per_run=2, week=5), overlap="allow", max_concurrent=5)
    put_run(env, "k1", T0, "r1")
    put_run(env, "k2", T0, "r2")
    d = env.evaluate(now=NOW1)
    assert d.reason == "cost_cap_reached"
    assert d.gate is not None and d.gate.detail["remaining_usd"] == 1.0
    # r1 settles at $0.50: actual .5 + r2's 2 held -> 2.5 left >= 2
    env.run_states["r1"] = _state("succeeded", cost=0.5)
    env.end_run("r1")
    nxt = NOW1 + timedelta(seconds=60)
    env.clock.now = nxt
    env.mono.t += 20
    d = env.evaluate(now=nxt)
    assert d.fire, d.reason
    g = env.engine.fire_store.get("k1")
    assert g is not None and g.governance is not None
    assert g.governance.final and g.governance.cost_observed_usd == 0.5


def _state(status: str, cost: float = 0.0, trips: list[TrippedBreaker] | None = None) -> Any:
    task = SimpleNamespace(
        cumulative_input_tokens=0, cumulative_output_tokens=0,
        cumulative_cache_creation_input_tokens=0, cumulative_cache_read_input_tokens=0,
        cumulative_cost_usd=cost,
    )  # fmt: skip
    return SimpleNamespace(status=status, tripped_breakers=trips or [], tasks={"t": task})


def trip(at: datetime) -> TrippedBreaker:
    return TrippedBreaker(
        id="builtin.cost_cap", condition="cost", action="halt", at=at.isoformat(), detail={}
    )


def _retire(env: Env, run_id: str, state: Any) -> None:
    env.run_states[run_id] = state
    env.statuses[run_id] = state.status
    env.alive.discard(run_id)
    env.engine._live_runs()  # retire as a tick would


def test_retire_hook_pause_on_cost_cap_trip(tmp_path: Path) -> None:
    env = gov_env(tmp_path, limits(per_run=2, week=50, ttl=3600))
    put_run(env, "k1", T0, "r1")
    env.clock.now = NOW1
    _retire(env, "r1", _state("failed", cost=2.3, trips=[trip(NOW1)]))
    g = env.engine.fire_store.get("k1").governance  # type: ignore[union-attr]
    assert g is not None and g.cap_state == "paused" and not g.final
    assert g.paused_at == NOW1 and g.pause_expires_at == NOW1 + timedelta(seconds=3600)
    assert g.cost_observed_usd == 2.3
    ev = env.events("schedule.governance.run_cost_capped")
    assert len(ev) == 1 and ev[0]["overshoot_usd"] == pytest.approx(0.3)
    assert ev[0]["on_cap"] == "pause" and ev[0]["run_id"] == "r1"


def test_retire_hook_abort_is_final(tmp_path: Path) -> None:
    env = gov_env(tmp_path, limits(per_run=2, week=50, on_cap="abort"))
    put_run(env, "k1", T0, "r1", on_cap="abort")
    _retire(env, "r1", _state("failed", cost=2.0, trips=[trip(NOW1)]))
    g = env.engine.fire_store.get("k1").governance  # type: ignore[union-attr]
    assert g is not None and g.cap_state == "aborted" and g.final and g.pause_expires_at is None


def test_retire_hook_success_finalizes_without_cap(tmp_path: Path) -> None:
    env = gov_env(tmp_path, limits(per_run=2, week=50))
    put_run(env, "k1", T0, "r1")
    _retire(env, "r1", _state("succeeded", cost=0.7))
    g = env.engine.fire_store.get("k1").governance  # type: ignore[union-attr]
    assert g is not None and g.final and g.cap_state == "none" and g.cost_observed_usd == 0.7
    assert not env.events("schedule.governance.run_cost_capped")


def test_retire_hook_ignores_stale_trip_after_resume_and_honours_fresh_one(
    tmp_path: Path,
) -> None:
    resumed = T0 + timedelta(minutes=10)
    old_trip, new_trip = trip(T0 + timedelta(minutes=5)), trip(T0 + timedelta(minutes=20))
    resumes = [{"at": resumed.isoformat(), "by": "op", "add_usd": 1, "new_cap_usd": 3}]
    # AC16: trip -> resume -> later non-cap failure => NOT paused, no new pause_expires_at
    env = gov_env(tmp_path / "a", limits(per_run=2, week=50))
    put_run(env, "k1", T0, "r1", cap_state="resumed", resumes=resumes)
    _retire(env, "r1", _state("failed", cost=2.5, trips=[old_trip]))
    g = env.engine.fire_store.get("k1").governance  # type: ignore[union-attr]
    assert g is not None and g.cap_state != "paused" and g.pause_expires_at is None and g.final
    assert not env.events("schedule.governance.run_cost_capped")
    # a second cap trip after the resume => paused with a fresh TTL
    env = gov_env(tmp_path / "b", limits(per_run=2, week=50, ttl=600))
    put_run(env, "k1", T0, "r1", cap_state="resumed", resumes=resumes, ttl=600)
    env.clock.now = NOW1
    _retire(env, "r1", _state("failed", cost=3.0, trips=[old_trip, new_trip]))
    g = env.engine.fire_store.get("k1").governance  # type: ignore[union-attr]
    assert g is not None and g.cap_state == "paused"
    assert g.pause_expires_at == NOW1 + timedelta(seconds=600)


# -- AC11 / AC12 ------------------------------------------------------------------------------


def test_gate_exception_fails_closed_and_feeds_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    env = gov_env(tmp_path, limits(quota_day=5))

    def boom(*a: Any, **k: Any) -> Any:
        raise OSError("state dir unreadable")

    monkeypatch.setattr(se, "gate", boom)
    with caplog.at_level(logging.ERROR):
        fired = env.tick(NOW1)
    assert fired == []
    rec = suppressed(env)
    assert rec is not None and rec.reason == "governance_error"
    ev = env.events("schedule.governance.error")
    assert len(ev) == 1 and "unreadable" in ev[0]["detail"]
    assert any(
        r.levelno == logging.ERROR and "schedule.governance.error" in r.getMessage()
        for r in caplog.records
    )
    st = env.engine.state_store.get(env.root, "s")
    assert st is not None and st.consecutive_eval_errors == 1  # bump_eval_error path applied
    assert env.events("schedule.eval_failed")


def test_run_now_force_does_not_bypass_governance(tmp_path: Path) -> None:
    env = gov_env(tmp_path, limits(quota_day=1), overlap="skip")
    put_run(env, "k0", T0, "r0", reserved=0, live=False)
    req = RunNowRequest(env.root, "s", NOW1, force=True)
    d = env.evaluate(now=NOW1, run_now=req)
    assert (d.fire, d.reason) == (False, "quota_exhausted")


# -- AC15 queue drain gate --------------------------------------------------------------------


def test_queued_fire_over_quota_is_burned_in_original_window(tmp_path: Path) -> None:
    day1 = datetime(2026, 1, 1, 23, 59, tzinfo=UTC)
    drain = datetime(2026, 1, 2, 0, 5, tzinfo=UTC)  # drained after midnight
    env = gov_env(tmp_path, limits(quota_day=1), overlap="queue")
    put_run(env, "k0", day1 - timedelta(hours=1), "r0", reserved=0)  # day 1 quota spent
    env.engine.state_store.advance_anchor(env.root, "s", day1 - timedelta(seconds=60))
    assert env.tick(day1) == []  # overlap-queued
    key = fire_key(env.root, "s", day1)
    st = env.engine.state_store.get(env.root, "s")
    assert st is not None and st.queued_fire_key == key
    env.end_run("r0")
    assert env.tick(drain) == []
    rec = env.engine.fire_store.get(key, env.root)
    assert rec is not None and rec.status == "suppressed" and rec.reason == "quota_exhausted"
    assert rec.scheduled_for == day1  # original window, not the drain-time one
    st = env.engine.state_store.get(env.root, "s")
    assert st is not None and st.queued_fire_key is None
    # next day is a fresh window with a fresh quota: the schedule fires again
    assert env.tick(drain + timedelta(minutes=2))


def test_queued_fire_within_quota_drains(tmp_path: Path) -> None:
    env = gov_env(tmp_path, limits(quota_day=5), overlap="queue")
    env.make_active()
    assert env.tick(NOW1) == []
    env.end_run()
    fired = env.tick(NOW1 + timedelta(seconds=10))
    assert [d.reason for d in fired] == ["queue_drain"]
    assert fired[0].gate is not None and fired[0].effective_limits is not None


# -- AC13 tick budget -------------------------------------------------------------------------


def test_tick_over_200_governed_schedules_stays_in_budget(tmp_path: Path) -> None:
    n, per = 200, 30
    bs = [binding(f"s{i}", limits=limits(quota_day=10_000), max_concurrent=1000) for i in range(n)]
    env = env_for(
        tmp_path, bs, launcher=None, max_concurrent_global=1000
    )  # decide only: launching writes (and rescans)
    store = env.engine.fire_store
    d = store.directory(env.root)
    d.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        for j in range(per):
            at = T0 - timedelta(days=2, minutes=j)
            rec = FireIntentRecord(
                fire_key=f"{i:03d}{j:03d}",
                workspace_root=env.root,
                schedule_id=f"s{i}",
                scheduled_for=at,
                created_at=at,
                status="launched",
            )
            (d / f"{rec.fire_key}.json").write_text(rec.model_dump_json())
    old = time.time() - 7200
    for p in [*d.glob("*.json"), d]:
        os.utime(p, (old, old))
    for i in range(n):
        env.engine.state_store.advance_anchor(env.root, f"s{i}", T0)
    ledger = env.engine.usage_ledger
    assert isinstance(ledger, FireRecordLedger)
    t = time.perf_counter()
    fired = env.tick(NOW1)
    elapsed = time.perf_counter() - t
    assert len(fired) == n
    print(f"200 schedules x 30 records: {elapsed * 1000:.0f} ms, scans={ledger.scans}")
    assert ledger.scans == 1
    assert env.events("schedule.tick_budget_exceeded") == []
    # ~0.3 s measured without coverage; the cap leaves headroom for slow/instrumented CI while
    # still failing if the per-schedule rescan regresses (that costs ~15 s).
    assert elapsed < SLOW_CI_FACTOR * se.SCHEDULE_TICK_BUDGET_SECONDS


# -- launcher: AC4 / 5 / 6 / 7 / 8 / 17 -------------------------------------------------------


def gov_decision(rig: Rig, b: Any, at: datetime = T0) -> FireDecision:
    d = rig.decision(b, at)
    return FireDecision(
        d.fire, d.reason, d.workspace_root, d.schedule_id, d.fire_key, d.scheduled_for,
        d.effective_due, None, b, effective_limits=b.limits,
    )  # fmt: skip


def cap_renderer(options: dict[str, object]) -> list[str]:
    return ["--cost-cap-usd", str(options["cost_cap_usd"])] if "cost_cap_usd" in options else []


def with_cap_argv(rig: Rig) -> None:
    """StubSupervisor's argv is fixed; make it look like the real child argv."""
    inner = rig.sup.launch_run

    def launch_run(**kw: Any) -> Any:
        rec = inner(**kw)
        opts = kw.get("options") or {}
        if "cost_cap_usd" in opts:
            rec.argv = ["ao", "run", "--cost-cap-usd", str(opts["cost_cap_usd"])]
        return rec

    rig.sup.launch_run = launch_run  # type: ignore[method-assign]


def test_reserve_writes_one_intended_with_governance_and_forwards_cap(tmp_path: Path) -> None:
    rig = Rig(tmp_path, options_renderer=cap_renderer)
    with_cap_argv(rig)
    write_workflow(Path(rig.root) / "w.json")
    data = "do it, ünïcode\n".encode()
    (Path(rig.root) / "p.md").write_bytes(data)
    b = make_binding(prompt_file="p.md", limits=limits(quota_day=5, per_run=2, week=10))
    writes: list[str] = []
    real_write = rig.launcher._fires.write
    rig.launcher._fires.write = lambda rec: (writes.append(rec.status), real_write(rec))[1]  # type: ignore[method-assign]
    seen: list[FireIntentRecord | None] = []
    real_launch = rig.sup.launch_run

    def spy(**kw: Any) -> Any:
        seen.append(rig.record(gov_decision(rig, b)))  # the record BEFORE spawn
        return real_launch(**kw)

    rig.sup.launch_run = spy  # type: ignore[method-assign]
    rec = rig.launcher.launch(gov_decision(rig, b))
    assert writes == ["intended"]  # AC17: exactly one intended write
    pre = seen[0]
    assert pre is not None and pre.status == "intended" and pre.governance is not None
    assert pre.governance.reserved_usd == 2 and pre.governance.run_cost_cap_usd == 2
    pm = pre.governance.prompt
    assert pm is not None and pm.path == "p.md" and pm.bytes == len(data)
    assert pm.sha256 == hashlib.sha256(data).hexdigest()  # AC8: sha equals sha256(file)
    call = rig.sup.launch_calls[0]
    assert call["options"]["cost_cap_usd"] == 2  # AC7
    assert call["prompt"].encode() == data  # AC8: bytes handed over == file bytes
    assert rec.status == "launched" and rec.governance is not None
    assert rec.governance.reserved_usd == 2
    assert rig.launcher._render(call["options"]) == ["--cost-cap-usd", "2.0"]
    assert (tmp_path / "state" / "schedules" / gg.GOVERNANCE_LOCK_NAME).exists()
    reserved = [
        e for e in rig.launcher._events.read() if e["event"] == "schedule.governance.reserved"
    ]
    assert len(reserved) == 1 and reserved[0]["reserved_usd"] == 2


def test_quota_only_binding_reserves_nothing_and_passes_no_cap(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    write_workflow(Path(rig.root) / "w.json")
    b = make_binding(limits=limits(quota_day=5))
    rec = rig.launcher.launch(gov_decision(rig, b))
    assert rec.status == "launched" and "cost_cap_usd" not in rig.sup.launch_calls[0]["options"]
    assert rec.governance is not None and rec.governance.reserved_usd is None


def test_dropped_cost_cap_fails_closed_before_spawn(tmp_path: Path) -> None:
    rig = Rig(tmp_path, options_renderer=lambda o: [])  # renderer drops cost_cap_usd
    write_workflow(Path(rig.root) / "w.json")
    b = make_binding(limits=limits(per_run=2, week=10))
    rec = rig.launcher.launch(gov_decision(rig, b))
    assert rig.sup.launch_calls == []
    assert rec.status == "launch_failed" and rec.reason == "cost_cap_not_forwarded"
    ev = [
        e for e in rig.launcher._events.read() if e["event"] == "schedule.governance.launch_unsafe"
    ]
    assert len(ev) == 1 and ev[0]["reason"] == "cost_cap_not_forwarded"


def test_launch_unsafe_event_level_is_error(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig = Rig(tmp_path, options_renderer=lambda o: [])
    write_workflow(Path(rig.root) / "w.json")
    with caplog.at_level(logging.DEBUG):
        rig.launcher.launch(gov_decision(rig, make_binding(limits=limits(per_run=2, week=10))))
    assert [
        r.levelno for r in caplog.records if "schedule.governance.launch_unsafe" in r.getMessage()
    ] == [logging.ERROR]


def test_post_spawn_argv_without_flag_cancels_child(tmp_path: Path) -> None:
    rig = Rig(tmp_path, options_renderer=cap_renderer)  # pre-check passes, stub argv has no flag
    write_workflow(Path(rig.root) / "w.json")
    rec = rig.launcher.launch(gov_decision(rig, make_binding(limits=limits(per_run=2, week=10))))
    assert rec.status == "launch_failed" and rec.reason == "cost_cap_not_forwarded"
    assert rig.sup.cancelled == ["launched-run"]  # the stub's run id for the spawned child


def test_real_renderer_does_not_yet_register_cost_cap_so_governed_cost_fire_fails_closed(
    tmp_path: Path,
) -> None:
    """UNFINISHED dependency T-et7Edf: until ``cost_cap_usd`` is a run-options registry Option the
    real renderer drops it, and the launcher correctly refuses to spawn an uncapped run."""
    rig = Rig(tmp_path)
    write_workflow(Path(rig.root) / "w.json")
    rec = rig.launcher.launch(gov_decision(rig, make_binding(limits=limits(per_run=2, week=10))))
    assert rig.sup.launch_calls == [] and rec.reason == "cost_cap_not_forwarded"


@pytest.mark.parametrize(
    ("data", "reason", "detail"),
    [
        (None, "prompt_missing", None),
        (b"x" * (MAX_PROMPT_BYTES + 1), "prompt_missing", "too_large"),
        (b"\xff\xfe", "prompt_missing", "not_utf8"),
    ],
)
def test_prompt_problems_burn_the_key_and_do_not_spawn(
    tmp_path: Path, data: bytes | None, reason: str, detail: str | None
) -> None:
    rig = Rig(tmp_path)
    write_workflow(Path(rig.root) / "w.json")
    if data is not None:
        (Path(rig.root) / "p.md").write_bytes(data)
    b = make_binding(prompt_file="p.md", limits=limits(quota_day=5))
    d = gov_decision(rig, b)
    rec = rig.launcher.launch(d)
    assert rig.sup.launch_calls == []
    assert rec.status == "suppressed" and rec.reason == reason
    assert rec.governance is not None and rec.governance.detail.get("detail") == detail
    assert rig.launcher._fires.exists(d.fire_key or "", rig.root)  # burned


def test_prompt_pin_mismatch_is_prompt_changed_and_match_fires(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    write_workflow(Path(rig.root) / "w.json")
    (Path(rig.root) / "p.md").write_text("hi")
    bad = make_binding(prompt_file="p.md", prompt_sha256="0" * 64, limits=limits(quota_day=5))
    rec = rig.launcher.launch(gov_decision(rig, bad))
    assert rec.status == "suppressed" and rec.reason == "prompt_changed"
    good = make_binding(
        prompt_file="p.md",
        prompt_sha256=hashlib.sha256(b"hi").hexdigest(),
        limits=limits(quota_day=5),
    )
    rec = rig.launcher.launch(gov_decision(rig, good, T0 + timedelta(minutes=1)))
    assert rec.status == "launched" and rig.sup.launch_calls[0]["prompt"] == "hi"


def test_natural_fire_and_run_now_in_one_tick_launch_exactly_once(tmp_path: Path) -> None:
    """AC5a: both decisions passed evaluate() (no record yet); the launcher serialises them."""
    rig = Rig(tmp_path)
    write_workflow(Path(rig.root) / "w.json")
    b = make_binding(limits=limits(quota_day=1))
    first = rig.launcher.launch(gov_decision(rig, b, T0))
    second = rig.launcher.launch(gov_decision(rig, b, T0 + timedelta(seconds=5)))
    assert first.status == "launched"
    assert second.status == "suppressed" and second.reason == "quota_exhausted"
    assert len(rig.sup.launch_calls) == 1
    ev = [e for e in rig.launcher._events.read() if e["event"] == "schedule.governance.skipped"]
    assert len(ev) == 1 and ev[0]["reason"] == "quota_exhausted" and ev[0]["max"] == 1


def test_concurrent_run_now_threads_launch_exactly_once(tmp_path: Path) -> None:
    """AC5b: independent launchers (as two processes would be) share only the flock file."""
    rig = Rig(tmp_path)
    write_workflow(Path(rig.root) / "w.json")
    b = make_binding(limits=limits(quota_day=1))
    launchers = [
        ScheduledLauncher(
            rig.state_dir,
            process_supervisor_factory=lambda root: rig.sup,
            project_config_loader=lambda root: None,
            clock=fixed_clock,
        )
        for _ in range(2)
    ]
    barrier = threading.Barrier(2)
    out: list[FireIntentRecord] = []

    def go(i: int) -> None:
        barrier.wait()
        out.append(launchers[i].launch(gov_decision(rig, b, T0 + timedelta(seconds=i))))

    threads = [threading.Thread(target=go, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r.status for r in out) == ["launched", "suppressed"]
    assert len(rig.sup.launch_calls) == 1


class _Crash(BaseException):
    pass


def test_crash_between_reserve_and_spawn_reconciles_to_orphaned(tmp_path: Path) -> None:
    def factory(root: str) -> Any:
        raise _Crash

    rig = Rig(tmp_path, process_supervisor_factory=factory, options_renderer=cap_renderer)
    write_workflow(Path(rig.root) / "w.json")
    b = make_binding(limits=limits(quota_day=5, per_run=2, week=10))
    d = gov_decision(rig, b)
    with pytest.raises(_Crash):
        rig.launcher.launch(d)
    store = FireIntentStore(rig.state_dir)
    rec = store.get(d.fire_key or "", rig.root)
    assert rec is not None and rec.status == "intended"
    assert rec.governance is not None and rec.governance.reserved_usd == 2
    orphans = store.reconcile_orphans(lambda pid: False, lambda root, rid: False)
    assert [o.status for o in orphans] == ["orphaned"]
    w = {"day": window_for(T0, "day", UTC_TZ, "monday")}
    u = FireRecordLedger(store).usage(rig.root, "nightly", w)["day"]
    assert (u.runs, u.actual, u.reserved) == (1, 0.0, 0.0)  # slot spent, $0, nothing held
    # and nothing relaunches it
    again = rig.launcher.launch(d)
    assert again.status == "orphaned" and rig.sup.launch_calls == []


def test_gate_failure_inside_launcher_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_orchestrator.service import schedule_launch as sl

    rig = Rig(tmp_path)
    write_workflow(Path(rig.root) / "w.json")

    def boom(**kw: Any) -> Any:
        raise OSError("lock dir unwritable")

    monkeypatch.setattr(sl, "check_and_reserve", boom)
    d = gov_decision(rig, make_binding(limits=limits(quota_day=5)))
    rec = rig.launcher.launch(d)
    assert rec.status == "suppressed" and rec.reason == "governance_error"
    assert rig.sup.launch_calls == []
    assert any(e["event"] == "schedule.governance.error" for e in rig.launcher._events.read())


def test_ungoverned_decision_keeps_plain_single_intended_write(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    write_workflow(Path(rig.root) / "w.json")
    rec = rig.launcher.launch(rig.decision(make_binding()))
    assert rec.status == "launched" and rec.governance is None


def test_fire_record_json_uses_schema_alias(tmp_path: Path) -> None:
    store = FireIntentStore(tmp_path)
    root = str(tmp_path / "ws")
    _put(store, root, "a", T0, gov=FireGovernance(reserved_usd=1.0))
    raw = json.loads(next(store.directory(root).glob("a.json")).read_text())
    assert raw["governance"]["schema"] == "ao.schedule.governance/v1"
    assert store.get("a", root).governance.reserved_usd == 1.0  # type: ignore[union-attr]
