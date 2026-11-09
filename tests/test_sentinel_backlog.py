"""T-Sn7Bkl: L1 backlog release, expiry, L2 early checkpoint + carry-over, FR-19 cap, NFR-13."""

from __future__ import annotations

import json
import random
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator import engine as engine_mod
from agent_orchestrator import graph_check, sentinel_backlog
from agent_orchestrator.breakers import BREAKER_REGISTRY, BreakerContext
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.models import (
    SPAWN_ORIGIN_SENTINEL,
    CircuitBreakerSpec,
    RunState,
    SentinelBandwidthSpec,
    SentinelSpec,
    SentinelState,
    SpawnRecord,
    TaskRunState,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.sentinel_settings import resolve_sentinel_settings
from tests.test_sentinel_tick import _run_dir, _setup
from tests.test_soft_barrier import INSTR, WF, Exec, T, manifest

P = 6


@pytest.fixture(autouse=True)
def _fast_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    real = engine_mod.wait

    def fast(fs: Any, timeout: float | None = None, return_when: Any = None) -> Any:
        return real(fs, timeout=0.02 if timeout is not None else None, return_when=return_when)

    monkeypatch.setattr(engine_mod, "wait", fast)


def _settings(**kw: Any) -> Any:
    return resolve_sentinel_settings(workflow=SentinelSpec(enabled=True, tick_seconds=1, **kw))


def _entry(slug: str, n: int, prio: int = 0, exp: int = 9, **task_kw: Any) -> dict[str, Any]:
    tid = f"s-{n:02d}-{slug}"
    return {
        "id": slug,
        "priority": prio,
        "expires_at_ckpt": exp,
        "kind": "implement",
        "work_item": f"A1/{slug}",
        "task": {"id": tid, "agent": "ag", "instruction": INSTR, **task_kw},
    }


def _publish(run_dir: Path, entries: list[Any], **extra: Any) -> None:
    ctl = run_dir / "control"
    ctl.mkdir(parents=True, exist_ok=True)
    body = {
        "schema": sentinel_backlog.BACKLOG_SCHEMA,
        "checkpoint_id": "ck-01",
        "entries": entries,
        **extra,
    }
    (ctl / "backlog.json").write_text(json.dumps(body))


def _sleep_hooks(secs: dict[str, float]) -> dict[str, Any]:
    def mk(d: float) -> Any:
        return lambda ex, ctx: time.sleep(d)

    return {tid: mk(d) for tid, d in secs.items()}


KID = {"ck-01": manifest({"id": "kid", "depends_on": ["ck-01"]})}


def _heavy_tail_run(tmp_path: Path, *, release: bool) -> tuple[RunState, Exec]:
    rng = random.Random(7)  # fixed seed: lognormal durations, scaled so the max is ~1.2 s
    raw = [rng.lognormvariate(0.0, 1.0) for _ in range(P)]
    scale = 1.2 / max(raw)
    durs = {f"u{i}": raw[i] * scale for i in range(P)}
    entries = [_entry(f"b{i}", i + 1, prio=i) for i in range(8)]
    durs.update({e["task"]["id"]: 0.05 for e in entries})
    store, rs, reposets, agents = _setup(tmp_path)
    wf = WF(
        [
            *[T(f"u{i}") for i in range(P)],
            T(
                "ck-01",
                emit_tasks=True,
                task_manifest_path="out/m.json",
                barrier="soft",
                depends_on=[f"u{i}" for i in range(P)],
            ),
        ]
    )
    state0 = rs.new_run(wf)
    _publish(_run_dir(rs, state0), entries, stage="explore")
    ex = Exec(_sleep_hooks(durs), emit=KID)
    bw = SentinelBandwidthSpec(backlog_release=release, early_checkpoint=False)
    orch = Orchestrator(ex, store, rs, max_parallel=P, sentinel=_settings(bandwidth=bw))
    return orch.run(wf, reposets, agents, run_state=state0), ex


def test_heavy_tail_idle_slot_seconds_reduced_at_least_half(tmp_path: Path) -> None:
    off, ex_off = _heavy_tail_run(tmp_path / "off", release=False)
    on, ex_on = _heavy_tail_run(tmp_path / "on", release=True)
    assert off.status == "succeeded" and on.status == "succeeded"
    assert not any(t.startswith("s-") for t in ex_off.order)  # disabled: nothing released
    released = sorted(t for t in ex_on.order if t.startswith("s-"))
    assert released == [f"s-{i:02d}-b{i - 1}" for i in range(1, 9)]

    def waste(s: RunState) -> float:
        assert s.metrics is not None
        return s.metrics.slot_seconds_idle_with_ready + s.metrics.slot_seconds_idle_backlog

    w_off, w_on = waste(off), waste(on)
    print(f"NFR-13 idle-slot-seconds-with-work-available: off={w_off:.3f} on={w_on:.3f}")
    assert w_off > 1.0, "fixture must actually have a heavy tail"
    assert w_on <= 0.5 * w_off
    assert on.sentinel is not None and on.sentinel.injected_count == 8
    assert all(on.spawned_by[t].origin == SPAWN_ORIGIN_SENTINEL for t in released)


def test_priority_order_and_entries_released_once(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    wf = WF([T("long"), T("short")])
    state0 = rs.new_run(wf)
    _publish(
        _run_dir(rs, state0),
        [_entry("lo", 1, prio=1), _entry("hi", 2, prio=9), _entry("mid", 3, prio=5)],
    )
    ex = Exec(_sleep_hooks({"long": 0.6}))
    orch = Orchestrator(ex, store, rs, max_parallel=2, sentinel=_settings())
    state = orch.run(wf, reposets, agents, run_state=state0)
    released = [t for t in ex.order if t.startswith("s-")]
    assert released[0] == "s-02-hi"  # higher priority first
    assert sorted(released) == ["s-01-lo", "s-02-hi", "s-03-mid"]
    assert state.sentinel is not None
    assert sorted(state.sentinel.backlog_released) == ["hi", "lo", "mid"]


def test_expired_entries_never_released(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    wf = WF(
        [
            T("ck-01", emit_tasks=True, task_manifest_path="out/m.json", barrier="soft"),
            T("long", depends_on=["ck-01"]),
        ]
    )
    state0 = rs.new_run(wf)
    _publish(
        _run_dir(rs, state0),
        [_entry("stale", 1, exp=1), _entry("fresh", 2, exp=2)],  # ck-01 already started => stale
    )
    ex = Exec(_sleep_hooks({"long": 0.4}), emit=KID)
    orch = Orchestrator(ex, store, rs, max_parallel=3, sentinel=_settings())
    state = orch.run(wf, reposets, agents, run_state=state0)
    assert "s-01-stale" not in ex.order
    assert "s-02-fresh" in ex.order
    assert state.sentinel is not None and state.sentinel.backlog_expired == ["stale"]


def test_invalid_entries_dropped_and_bad_id_rejected(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    wf = WF([T("long"), T("x")])
    state0 = rs.new_run(wf)
    bad = _entry("bad", 1)
    bad["task"]["id"] = "not-sentinel-id"
    dup = _entry("dup", 2, depends_on=["ghost"])  # unknown dependency: shared checker refuses
    junk: list[Any] = [bad, dup, {"id": "x"}, "nope", _entry("ok", 3)]
    run_dir = _run_dir(rs, state0)
    _publish(run_dir, junk)
    parsed = sentinel_backlog.read_backlog(run_dir / "control")
    assert parsed is not None and len(parsed.entries) == 3
    ex = Exec(_sleep_hooks({"long": 0.4}))
    orch = Orchestrator(ex, store, rs, max_parallel=3, sentinel=_settings())
    state = orch.run(wf, reposets, agents, run_state=state0)
    assert state.status == "succeeded"
    assert state.sentinel is not None
    assert state.sentinel.backlog_rejected == {"bad": "BK-ID", "dup": "GE-SPEC"}
    assert "s-03-ok" in ex.order


def test_malformed_backlog_file_is_ignored(tmp_path: Path) -> None:
    ctl = tmp_path / "control"
    ctl.mkdir()
    (ctl / "backlog.json").write_text("{not json")
    assert sentinel_backlog.read_backlog(ctl) is None
    (ctl / "backlog.json").write_text(json.dumps({"schema": "wrong", "entries": []}))
    assert sentinel_backlog.read_backlog(ctl) is None


def test_injection_cap_is_shared_and_enforced(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    wf = WF([T("long"), T("x")])
    state0 = rs.new_run(wf)
    _publish(_run_dir(rs, state0), [_entry(f"e{i}", i + 1, prio=9 - i) for i in range(4)])
    ex = Exec(_sleep_hooks({"long": 0.5}))
    orch = Orchestrator(ex, store, rs, max_parallel=3, sentinel=_settings(max_injected_tasks=2))
    state = orch.run(wf, reposets, agents, run_state=state0)
    assert state.sentinel is not None
    assert state.sentinel.injected_count == 2
    assert set(state.sentinel.backlog_rejected.values()) == {"GE-LIMIT"}


def test_injection_cap_default_formula() -> None:
    wf = WF([T("a")])
    assert sentinel_backlog.injection_cap(SentinelSpec(), 2, wf) == 20
    assert sentinel_backlog.injection_cap(SentinelSpec(), 10, wf) == 30
    wf.circuit_breakers.append(
        CircuitBreakerSpec(
            id="d", condition="run_wall_clock_seconds", threshold=7200, action="stop"
        )
    )
    assert sentinel_backlog.injection_cap(SentinelSpec(), 10, wf) == 60
    assert sentinel_backlog.injection_cap(SentinelSpec(max_injected_tasks=5), 10, wf) == 5


def _state_with(injected: list[tuple[str, bool]], sentinel_count: int | None) -> RunState:
    st = RunState(run_id="r", workflow_id="w", started_at="t", updated_at="t")
    for tid, is_sent in injected:
        st.injected_tasks.append(TaskSpec(id=tid, agent="ag", instruction=INSTR))
        st.tasks[tid] = TaskRunState()
        if is_sent:
            st.spawned_by[tid] = SpawnRecord(
                parent_task_id="sentinel-tick",
                parent_dispatch_cycle=0,
                origin=SPAWN_ORIGIN_SENTINEL,
                injected_at="t",
            )
    if sentinel_count is not None:
        st.sentinel = SentinelState(
            injected_count=sentinel_count, injected_ids=[t for t, is_s in injected if is_s]
        )
    return st


def _bctx(state: RunState, tmp_path: Path) -> BreakerContext:
    from agent_orchestrator.artifacts import LocalFsArtifactStore

    return BreakerContext(
        state=state,
        clock_epoch=0.0,
        store=LocalFsArtifactStore(str(tmp_path)),
        workflow=WF([T("a")]),
    )


def test_breakers_use_separate_counters(tmp_path: Path) -> None:
    st = _state_with([("a", False), ("s-01-x", True), ("s-02-y", True)], 2)
    tmpl = CircuitBreakerSpec(id="i", condition="injected_task_count", threshold=2, action="fail")
    sent = CircuitBreakerSpec(
        id="s", condition="sentinel_injected_task_count", threshold=2, action="fail"
    )
    ctx = _bctx(st, tmp_path)
    assert BREAKER_REGISTRY["injected_task_count"].evaluate(tmpl, ctx) is None  # 1 < 2
    trip = BREAKER_REGISTRY["sentinel_injected_task_count"].evaluate(sent, ctx)
    assert trip is not None and trip.detail["count"] == 2
    none = _bctx(_state_with([], None), tmp_path)
    assert BREAKER_REGISTRY["sentinel_injected_task_count"].evaluate(sent, none) is None


# ---- L2 early checkpoint -----------------------------------------------------------------
def _ck_wf(n: int) -> WorkflowSpec:
    return WF(
        [
            *[T(f"u{i}", outputs=[f"o/u{i}.json"]) for i in range(n)],
            T(
                "ck-01",
                emit_tasks=True,
                task_manifest_path="out/m.json",
                barrier="soft",
                depends_on=[f"u{i}" for i in range(n)],
                inputs=[f"o/u{i}.json" for i in range(n)],
            ),
        ]
    )


def test_early_checkpoint_runs_while_straggler_continues(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    wf = _ck_wf(4)
    stamps: dict[str, float] = {}

    def end_hook(tid: str, d: float) -> Any:
        def hook(ex: Exec, ctx: Any) -> None:
            time.sleep(d)
            stamps[f"{tid}:end"] = time.monotonic()

        return hook

    hooks = {f"u{i}": end_hook(f"u{i}", 0.02) for i in range(3)}
    hooks["u3"] = end_hook("u3", 1.0)
    hooks["ck-01"] = lambda ex, ctx: stamps.__setitem__("ck-01:start", time.monotonic())
    ex = Exec(hooks, emit=KID)
    bw = SentinelBandwidthSpec(
        early_ckpt_min_idle=2, early_ckpt_grace_seconds=0, early_ckpt_fraction=0.75
    )
    orch = Orchestrator(ex, store, rs, max_parallel=4, sentinel=_settings(bandwidth=bw))
    state = orch.run(wf, reposets, agents)
    assert state.status == "succeeded", state.tasks
    assert stamps["ck-01:start"] < stamps["u3:end"], "checkpoint must start while u3 still runs"
    assert ex.order.count("u3") == 1 and ex.order.count("ck-01") == 1  # never re-scheduled
    assert state.sentinel is not None
    recs = state.sentinel.early_checkpoints
    assert [(r.checkpoint_id, r.carry_over) for r in recs] == [("ck-01", ["u3"])]
    edit = [e for e in state.graph_edits if e.op == "set_edges" and e.task_id == "ck-01"][0]
    assert edit.depends_on == ["u0", "u1", "u2"] and "o/u3.json" not in (edit.inputs or [])
    assert edit.source == graph_check.PARENT_TICK


@pytest.mark.parametrize(
    "bw",
    [
        SentinelBandwidthSpec(
            early_ckpt_min_idle=1, early_ckpt_grace_seconds=0, early_ckpt_fraction=1.0
        ),
        SentinelBandwidthSpec(early_checkpoint=False, early_ckpt_grace_seconds=0),
    ],
    ids=["below-fraction", "disabled"],
)
def test_no_early_checkpoint_below_fraction_or_when_disabled(tmp_path: Path, bw: Any) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    ex = Exec({"u3": lambda e, c: time.sleep(0.4)}, emit=KID)
    orch = Orchestrator(ex, store, rs, max_parallel=4, sentinel=_settings(bandwidth=bw))
    state = orch.run(_ck_wf(4), reposets, agents)
    assert state.status == "succeeded"
    assert state.sentinel is not None and state.sentinel.early_checkpoints == []


def _bare_state() -> RunState:
    return RunState(run_id="r", workflow_id="w", started_at="t", updated_at="t")


def test_early_checkpoint_edit_replays_on_resume() -> None:
    wf, st = _ck_wf(3), _bare_state()
    plan = graph_check.plan_early_checkpoint(
        wf, st, {"checkpoint_id": "ck-01", "carry_over": ["u2"]}, actor="t", at="t", edit_id="e"
    )
    graph_check.commit_plan(wf, st, plan, parent="sentinel-tick", at="t")
    fresh = _ck_wf(3)
    graph_check.replay_edits(fresh, st)
    assert fresh.task("ck-01").depends_on == ["u0", "u1"]


def test_plan_early_checkpoint_rules() -> None:
    wf, st = _ck_wf(3), _bare_state()
    st.tasks["u0"] = TaskRunState(status="succeeded")

    def plan(**p: Any) -> Any:
        return graph_check.plan_early_checkpoint(wf, st, p, actor="a", at="t", edit_id="e")

    for bad in (
        {"checkpoint_id": "u1", "carry_over": ["u2"]},  # not a soft emitter
        {"checkpoint_id": "ck-01", "carry_over": ["u0"]},  # already settled
        {"checkpoint_id": "ck-01", "carry_over": ["u0", "u1", "u2"]},  # nothing would remain
        {"checkpoint_id": "ck-01", "carry_over": ["nope"]},  # not a predecessor
    ):
        with pytest.raises(graph_check.EditRejected) as e:
            plan(**bad)
        assert e.value.rule_id == graph_check.RULE_EARLY
    st.tasks["ck-01"] = TaskRunState(status="running")
    with pytest.raises(graph_check.EditRejected) as e:
        plan(checkpoint_id="ck-01", carry_over=["u2"])
    assert e.value.rule_id == graph_check.RULE_NOT_PENDING


def test_find_early_plan_at_most_one_in_flight() -> None:
    wf, st = _ck_wf(4), _bare_state()
    st.sentinel = SentinelState()
    for i in range(3):
        st.tasks[f"u{i}"] = TaskRunState(status="succeeded")
    st.tasks["u3"] = TaskRunState(status="running")
    spec = SentinelBandwidthSpec()
    plan = sentinel_backlog.find_early_plan(wf, st, spec, {"u3"})
    assert plan is not None and plan.carry_over == ("u3",)
    sentinel_backlog.apply_early(
        st, plan, lambda *a, **k: None, datetime(2026, 1, 1, tzinfo=UTC).isoformat()
    )
    assert sentinel_backlog.find_early_plan(wf, st, spec, {"u3"}) is None  # one in flight
    st.tasks["ck-01"] = TaskRunState(status="succeeded")
    assert sentinel_backlog.find_early_plan(_ck_wf(4), st, spec, {"u3"}) is None  # not pending
