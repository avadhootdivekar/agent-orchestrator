"""Engine tests for the mid-run cost-cap gate (T-et7Edf).

Covers AC3 (pure, clock-free helper), AC4 (dispatch gate), AC5 (boundary gate, H1),
AC6 (non-latching re-arm), AC7 (monitor never consulted), AC8 (sentinel cap feed),
AC13 (cost-visibility fields), AC15 (FakeExecutor cost_usd) and AC16 (halt without re-entering
the run loop). Fixed/stepping clocks only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from agent_orchestrator.engine import Orchestrator, cost_cap_reached
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    BUILTIN_COST_CAP,
    RepoRef,
    RepoSet,
    RunState,
    TaskResult,
    TaskRunState,
    WorkflowSpec,
    compute_run_usage_totals,
)

AGENTS = {
    "ag": __import__("agent_orchestrator.models", fromlist=["AgentSpec"]).AgentSpec(executor="fake")
}
_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _reposets(workspace: Path) -> dict:
    return {
        "rs": RepoSet(
            workspace_root=str(workspace),
            repos=[RepoRef(id="core", path=".", role="primary")],
        )
    }


def _costs(**per_task: float) -> FakeExecutor:
    return FakeExecutor(token_outputs={t: {"cost_usd": c} for t, c in per_task.items()})


def _serial(n: int) -> list[dict]:
    return [
        {"id": f"t{i}", **({"depends_on": [f"t{i - 1}"]} if i > 1 else {})} for i in range(1, n + 1)
    ]


def _cap_trips(state: RunState) -> list:
    return [t for t in state.tripped_breakers if t.id == BUILTIN_COST_CAP]


def _state(cap: float | None, *costs: float) -> RunState:
    st = RunState(
        run_id="r",
        workflow_id="w",
        status="running",
        started_at=_T0.isoformat(),
        updated_at=_T0.isoformat(),
        cost_cap_usd=cap,
    )
    for i, c in enumerate(costs):
        st.tasks[f"t{i}"] = TaskRunState(status="succeeded", cumulative_cost_usd=c)
    return st


# -- AC3 ------------------------------------------------------------------------------------


def test_helper_none_without_cap() -> None:
    assert cost_cap_reached(_state(None, 99.0)) is None


def test_helper_below_at_above_cap() -> None:
    assert cost_cap_reached(_state(1.0, 0.4, 0.5)) is None
    assert cost_cap_reached(_state(1.0, 0.4, 0.6)) == {"cost_usd": 1.0, "cap_usd": 1.0}
    assert cost_cap_reached(_state(1.0, 2.0)) == {"cost_usd": 2.0, "cap_usd": 1.0}


def test_unset_cap_adds_no_clock_calls(workspace: Path, store, rs_store, monkeypatch) -> None:
    """Stepping clock: the gate with no cap consumes exactly as many ticks as no gate at all."""

    def run(gate_removed: bool) -> int:
        calls = [0]

        def clock() -> datetime:
            calls[0] += 1
            return _T0 + timedelta(seconds=calls[0])

        orch = Orchestrator(FakeExecutor(), store, rs_store, clock=clock)
        if gate_removed:
            monkeypatch.setattr(orch, "_trip_cost_cap", lambda *_a: False)
        orch.run(_wf(workspace, 2), _reposets(workspace), AGENTS)
        return calls[0]

    assert run(False) == run(True)


def _wf(workspace: Path, n: int, tasks: list[dict] | None = None) -> WorkflowSpec:
    instr = workspace / "specs" / "instructions"
    instr.mkdir(parents=True, exist_ok=True)
    from agent_orchestrator.models import TaskSpec, WorkflowDefaults

    specs = []
    for t in tasks or _serial(n):
        (instr / f"{t['id']}.md").write_text("x")
        specs.append(
            TaskSpec(
                id=t["id"],
                agent="ag",
                instruction=f"specs/instructions/{t['id']}.md",
                depends_on=t.get("depends_on", []),
                outputs=[],
                inputs=[],
            )
        )
    return WorkflowSpec(
        version="1.0", id="wf", repo_set="rs", tasks=specs, defaults=WorkflowDefaults()
    )


# -- AC15 / AC13 -----------------------------------------------------------------------------


def test_fake_cost_script_and_actuals_fields(workspace: Path, store, rs_store) -> None:
    orch = Orchestrator(_costs(t1=0.4, t2=0.4, t3=0.4), store, rs_store, clock=lambda: _T0)
    st = orch.run(_wf(workspace, 3), _reposets(workspace), AGENTS)
    assert st.status == "succeeded"
    assert round(compute_run_usage_totals(st).cost_usd, 6) == 1.2
    assert all(
        ts.actuals_available and ts.attempts_without_actuals == 0 for ts in st.tasks.values()
    )


def test_blind_attempt_counted_and_flag_sticky() -> None:
    ts = TaskRunState()
    Orchestrator._accumulate_actuals(ts, TaskResult(task_id="a", status="killed", attempts=1))
    assert (ts.actuals_available, ts.attempts_without_actuals, ts.cumulative_cost_usd) == (
        False,
        1,
        0.0,
    )
    seen = TaskResult(
        task_id="a", status="succeeded", attempts=1, actuals_available=True, cost_usd=0.2
    )
    Orchestrator._accumulate_actuals(ts, seen)
    Orchestrator._accumulate_actuals(ts, TaskResult(task_id="a", status="killed", attempts=1))
    assert ts.actuals_available is True
    assert ts.attempts_without_actuals == 2  # a later report never clears earlier blindness
    assert ts.cumulative_cost_usd == 0.2


def test_prepare_resume_carries_visibility_fields(workspace: Path, store, rs_store) -> None:
    wf = _wf(workspace, 1)
    st = _state(None)
    st.tasks["t1"] = TaskRunState(
        status="failed", actuals_available=True, attempts_without_actuals=2, cumulative_cost_usd=0.3
    )
    out = rs_store.prepare_resume(st, wf)
    ts = out.tasks["t1"]
    assert (ts.actuals_available, ts.attempts_without_actuals, ts.cumulative_cost_usd) == (
        True,
        2,
        0.3,
    )


# -- AC5 / AC4 / AC16 -------------------------------------------------------------------------


def test_one_task_crossing_cap_finishes_without_trip(workspace: Path, store, rs_store) -> None:
    orch = Orchestrator(_costs(t1=0.9), store, rs_store, clock=lambda: _T0, cost_cap_usd=0.5)
    st = orch.run(_wf(workspace, 1), _reposets(workspace), AGENTS)
    assert st.status == "succeeded"
    assert compute_run_usage_totals(st).cost_usd > 0.5
    assert _cap_trips(st) == []


def test_serial_two_tasks_halts_at_boundary_with_second_pending(
    workspace: Path, store, rs_store
) -> None:
    orch = Orchestrator(
        _costs(t1=0.6, t2=0.6), store, rs_store, clock=lambda: _T0, cost_cap_usd=0.5
    )
    st = orch.run(_wf(workspace, 2), _reposets(workspace), AGENTS)
    assert st.status == "failed"
    assert st.tasks["t1"].status == "succeeded"
    assert "t2" not in st.tasks or st.tasks["t2"].status == "pending"
    trips = _cap_trips(st)
    assert len(trips) == 1  # AC16: halted once, did not re-enter the loop
    assert (trips[0].condition, trips[0].action) == ("run_cost_usd", "pause")
    assert trips[0].detail == {"cost_usd": 0.6, "cap_usd": 0.5}
    # persisted
    assert rs_store.load(st.run_id).status == "failed"


def test_parallel_siblings_are_drained_not_killed(workspace: Path, store, rs_store) -> None:
    wf = _wf(
        workspace,
        0,
        [{"id": "a"}, {"id": "b"}, {"id": "c", "depends_on": ["a", "b"]}],
    )
    orch = Orchestrator(
        _costs(a=0.6, b=0.3, c=1.0),
        store,
        rs_store,
        clock=lambda: _T0,
        max_parallel=2,
        cost_cap_usd=0.5,
    )
    st = orch.run(wf, _reposets(workspace), AGENTS)
    assert st.status == "failed"
    assert st.tasks["a"].status == st.tasks["b"].status == "succeeded"
    assert round(compute_run_usage_totals(st).cost_usd, 6) == 0.9
    assert st.tasks.get("c") is None or st.tasks["c"].status == "pending"
    # the failure is the cap halt itself, not some other failure. Settle order of a/b is not fixed:
    # a first trips at 0.6 and again at 0.9 once b drains (repeat trips are by design, HLD L2:
    # the status trailer shows "x N"); b first trips once at 0.9. Either way the last one is 0.9.
    trips = _cap_trips(st)
    assert trips and all(t.detail["cap_usd"] == 0.5 for t in trips)
    assert max(t.detail["cost_usd"] for t in trips) == 0.9


def test_dispatch_gate_blocks_new_dispatch_when_cap_already_reached(
    workspace: Path, store, rs_store
) -> None:
    """Resume with spend >= cap: zero dispatches, a fresh trip (AC4/AC6 re-armed)."""
    wf = _wf(workspace, 2)
    ex = _costs(t1=0.6, t2=0.1)
    orch = Orchestrator(ex, store, rs_store, clock=lambda: _T0, cost_cap_usd=0.5)
    st = orch.run(wf, _reposets(workspace), AGENTS)
    assert len(_cap_trips(st)) == 1
    dispatched: list[str] = []
    ex2 = FakeExecutor()
    orig = ex2.execute
    ex2.execute = lambda ctx: (dispatched.append(ctx.task_id), orig(ctx))[1]  # type: ignore[method-assign]
    st2 = rs_store.prepare_resume(rs_store.load(st.run_id), wf)
    assert st2.cost_cap_usd == 0.5  # sticky, persisted in state
    out = Orchestrator(ex2, store, rs_store, clock=lambda: _T0).run(
        wf, _reposets(workspace), AGENTS, run_state=st2
    )
    assert dispatched == []
    assert out.status == "failed"
    assert len(_cap_trips(out)) == 2  # re-armed, not latched


def test_raised_cap_on_resume_completes(workspace: Path, store, rs_store) -> None:
    wf = _wf(workspace, 2)
    st = Orchestrator(
        _costs(t1=0.6, t2=0.1), store, rs_store, clock=lambda: _T0, cost_cap_usd=0.5
    ).run(wf, _reposets(workspace), AGENTS)
    st2 = rs_store.prepare_resume(rs_store.load(st.run_id), wf)
    out = Orchestrator(_costs(t2=0.1), store, rs_store, clock=lambda: _T0, cost_cap_usd=5.0).run(
        wf, _reposets(workspace), AGENTS, run_state=st2
    )
    assert out.status == "succeeded"
    assert out.cost_cap_usd == 5.0
    assert len(_cap_trips(out)) == 1


def test_gate_is_consulted_at_each_dispatch_and_boundary_with_work_left(
    workspace: Path, store, rs_store
) -> None:
    """Spy on the gate: one dispatch check per task plus one boundary check while work remains.
    (Injected-task and redispatch paths: the ``test_q4_*`` tests below.)"""
    calls: list[str] = []
    orch = Orchestrator(_costs(t1=0.1, t2=0.1), store, rs_store, clock=lambda: _T0, cost_cap_usd=9)
    orig = orch._trip_cost_cap
    orch._trip_cost_cap = lambda s, log: (calls.append("g"), orig(s, log))[1]  # type: ignore[method-assign]
    orch.run(_wf(workspace, 2), _reposets(workspace), AGENTS)
    # dispatch(t1), boundary after t1 (t2 left), dispatch(t2); no boundary after t2 (nothing left)
    assert len(calls) == 3


# -- Q-4: injected and redispatched tasks go through the dispatch gate (w23-06) ------------------

_MANIFEST = "output/task-manifest.json"


def _emitter_wf(workspace: Path) -> WorkflowSpec:
    """One static ``emit`` task that injects ``inj`` (depends on ``emit``)."""
    wf = _wf(workspace, 1, tasks=[{"id": "emit"}])
    wf.tasks[0].emit_tasks = True
    wf.tasks[0].task_manifest_path = _MANIFEST
    (workspace / "specs" / "instructions" / "inj.md").write_text("x")
    return wf


def _emit_executor(**costs: float) -> FakeExecutor:
    inj = {
        "id": "inj",
        "agent": "ag",
        "instruction": "specs/instructions/inj.md",
        "outputs": [],
        "depends_on": ["emit"],
    }
    return FakeExecutor(
        emit_payloads={"emit": {"tasks": [inj]}},
        token_outputs={t: {"cost_usd": c} for t, c in costs.items()},
    )


def _record_dispatches(ex: FakeExecutor) -> list[str]:
    seen: list[str] = []
    orig = ex.execute
    ex.execute = lambda ctx: (seen.append(ctx.task_id), orig(ctx))[1]  # type: ignore[method-assign]
    return seen


def test_q4_injected_task_is_held_by_the_gate_when_the_emitter_reaches_the_cap(
    workspace: Path, store, rs_store
) -> None:
    ex = _emit_executor(emit=0.6, inj=0.1)
    seen = _record_dispatches(ex)
    orch = Orchestrator(ex, store, rs_store, clock=lambda: _T0, cost_cap_usd=0.5)
    st = orch.run(_emitter_wf(workspace), _reposets(workspace), AGENTS)
    assert st.tasks["inj"].origin == "injected"  # it really was injected into the run...
    assert seen == ["emit"]  # ...and never dispatched: the gate halted before it
    assert st.tasks["inj"].status == "pending"
    assert st.status == "failed" and len(_cap_trips(st)) == 1


def test_q4_injected_task_dispatch_consults_the_gate_below_the_cap(
    workspace: Path, store, rs_store
) -> None:
    ex = _emit_executor(emit=0.1, inj=0.1)
    seen = _record_dispatches(ex)
    orch = Orchestrator(ex, store, rs_store, clock=lambda: _T0, cost_cap_usd=9)
    calls: list[int] = []
    orig = orch._trip_cost_cap
    orch._trip_cost_cap = lambda s, log: (calls.append(len(s.injected_tasks)), orig(s, log))[1]  # type: ignore[method-assign]
    st = orch.run(_emitter_wf(workspace), _reposets(workspace), AGENTS)
    assert st.status == "succeeded" and seen == ["emit", "inj"]
    # dispatch(emit) before injection (0 injected); boundary after emit (injected, work left) and
    # dispatch(inj) both see the injected task: the injected dispatch is gated like any other
    assert calls == [0, 1, 1]


def test_q4_resumed_run_redispatches_the_injected_task_through_the_gate(
    workspace: Path, store, rs_store
) -> None:
    """Resume re-dispatches a pending injected task: held at cap, runs once the cap is raised."""
    wf = _emitter_wf(workspace)
    st = Orchestrator(
        _emit_executor(emit=0.6), store, rs_store, clock=lambda: _T0, cost_cap_usd=0.5
    ).run(wf, _reposets(workspace), AGENTS)
    assert [t.id for t in st.injected_tasks] == ["inj"]
    # same (sticky) cap: the redispatch is refused by the gate (zero dispatches, fresh trip)
    ex = _emit_executor(inj=0.1)
    seen = _record_dispatches(ex)
    st2 = rs_store.prepare_resume(rs_store.load(st.run_id), wf)
    out = Orchestrator(ex, store, rs_store, clock=lambda: _T0).run(
        wf, _reposets(workspace), AGENTS, run_state=st2
    )
    assert seen == [] and len(_cap_trips(out)) == 2
    # raised cap: the injected task is dispatched exactly once and the run completes
    ex3 = _emit_executor(inj=0.1)
    seen3 = _record_dispatches(ex3)
    st3 = rs_store.prepare_resume(rs_store.load(st.run_id), wf)
    done = Orchestrator(ex3, store, rs_store, clock=lambda: _T0, cost_cap_usd=5.0).run(
        wf, _reposets(workspace), AGENTS, run_state=st3
    )
    assert seen3 == ["inj"] and done.status == "succeeded"


def test_q4_redispatch_of_a_failed_task_is_gated_on_resume(
    workspace: Path, store, rs_store
) -> None:
    """A failed task that resume resets to pending is a redispatch: the gate sees it too.

    Resume drops the failed task's own spend (only settled tasks keep theirs), so the gate
    judges the redispatch against t1's 0.3: refused under a 0.25 cap, dispatched under a 5.0 cap.
    """
    wf = _wf(workspace, 2)
    st = Orchestrator(
        FakeExecutor(behaviors={"t2": "fail"}, token_outputs={"t1": {"cost_usd": 0.3}}),
        store,
        rs_store,
        clock=lambda: _T0,
        cost_cap_usd=5.0,
    ).run(wf, _reposets(workspace), AGENTS)
    assert st.tasks["t1"].status == "succeeded" and st.tasks["t2"].status == "failed"
    assert _cap_trips(st) == []  # a plain failure, not a cap trip

    ex = _costs(t2=0.1)
    seen = _record_dispatches(ex)
    st2 = rs_store.prepare_resume(rs_store.load(st.run_id), wf)
    assert st2.tasks["t2"].status == "pending"
    out = Orchestrator(ex, store, rs_store, clock=lambda: _T0, cost_cap_usd=0.25).run(
        wf, _reposets(workspace), AGENTS, run_state=st2
    )
    assert seen == [] and len(_cap_trips(out)) == 1  # redispatch refused: settled 0.3 >= 0.25

    ex3 = _costs(t2=0.1)
    seen3 = _record_dispatches(ex3)
    st3 = rs_store.prepare_resume(rs_store.load(st.run_id), wf)
    done = Orchestrator(ex3, store, rs_store, clock=lambda: _T0, cost_cap_usd=5.0).run(
        wf, _reposets(workspace), AGENTS, run_state=st3
    )
    assert seen3 == ["t2"] and done.status == "succeeded"


# -- AC7 --------------------------------------------------------------------------------------


def test_cost_cap_trip_never_consults_monitor(workspace: Path, store, rs_store) -> None:
    orch = Orchestrator(
        _costs(t1=0.6, t2=0.6), store, rs_store, clock=lambda: _T0, cost_cap_usd=0.5
    )
    st = orch.run(_wf(workspace, 2), _reposets(workspace), AGENTS)
    assert st.monitor_decisions == []


# -- AC8 --------------------------------------------------------------------------------------


def test_sentinel_lane_cap_is_min_of_breakers_and_state_cap(
    workspace: Path, store, rs_store, monkeypatch
) -> None:
    import agent_orchestrator.engine as eng

    seen: dict = {}

    class _Lane:
        def __init__(self, **kw) -> None:
            seen.update(kw)

        def reconcile_on_start(self, *_a) -> bool:
            return False

    monkeypatch.setattr(eng, "GenerationLane", _Lane)
    monkeypatch.setattr(eng, "make_driver", lambda *a, **k: object())
    from types import SimpleNamespace

    from agent_orchestrator.models import SentinelLlmSpec

    orch = Orchestrator(FakeExecutor(), store, rs_store, clock=lambda: _T0)
    rt = SimpleNamespace(log=_NullLog(), clock=lambda: _T0, lane=None)
    wf = _wf(workspace, 1)
    br = SimpleNamespace(condition="run_cost_usd", threshold=3.0)
    wf_stub = SimpleNamespace(circuit_breakers=[br])
    llm = SentinelLlmSpec(enabled=True)
    for state_cap, expect in ((None, 3.0), (1.5, 1.5), (9.0, 3.0)):
        st = _state(state_cap)
        orch._start_llm_lane(rt, st, str(workspace), llm, wf_stub, None)  # type: ignore[arg-type]
        assert seen["run_cost_cap_usd"] == expect
    orch._start_llm_lane(rt, _state(2.0), str(workspace), llm, None, None)
    assert seen["run_cost_cap_usd"] == 2.0
    assert wf is not None


class _NullLog:
    def warning(self, *a, **k) -> None: ...
    def error(self, *a, **k) -> None: ...
    def info(self, *a, **k) -> None: ...


# -- T-lTW88y H-1 / W-1: retries inside one dispatched task ----------------------------------


class _AttemptExecutor:
    """Scripted per-attempt results: ``(status, cost_usd | None)``; ``None`` cost = blind."""

    def __init__(self, script: list[tuple[str, float | None]]) -> None:
        self.script = list(script)
        self.calls = 0

    def execute(self, ctx) -> TaskResult:
        status, cost = self.script[self.calls]
        self.calls += 1
        return TaskResult(
            task_id=ctx.task_id,
            status=status,  # type: ignore[arg-type]
            attempts=1,
            error=None if status == "succeeded" else "boom",
            actuals_available=cost is not None,
            cost_usd=cost,
        )


def _retry_wf(workspace: Path, max_attempts: int) -> WorkflowSpec:
    from agent_orchestrator.models import RetryPolicy

    wf = _wf(workspace, 1)
    wf.tasks[0].retries = RetryPolicy(max_attempts=max_attempts, backoff_seconds=0)
    return wf


def test_h1_retry_stops_when_run_cap_reached_between_attempts(
    workspace: Path, store, rs_store
) -> None:
    ex = _AttemptExecutor([("failed", 0.6)] * 5)
    orch = Orchestrator(ex, store, rs_store, clock=lambda: _T0, cost_cap_usd=0.5)  # type: ignore[arg-type]
    st = orch.run(_retry_wf(workspace, 5), _reposets(workspace), AGENTS)
    assert ex.calls == 1  # attempt 1 already crossed the cap: no paid attempts 2..5
    assert st.tasks["t1"].status == "failed"
    assert round(st.tasks["t1"].cumulative_cost_usd, 6) == 0.6


def test_h1_retry_continues_below_cap_and_stops_on_crossing(
    workspace: Path, store, rs_store
) -> None:
    ex = _AttemptExecutor([("failed", 0.2), ("failed", 0.2), ("failed", 0.2), ("succeeded", 0.2)])
    orch = Orchestrator(ex, store, rs_store, clock=lambda: _T0, cost_cap_usd=0.5)  # type: ignore[arg-type]
    orch.run(_retry_wf(workspace, 4), _reposets(workspace), AGENTS)
    assert ex.calls == 3  # 0.2, 0.4 < 0.5 continue; 0.6 >= 0.5 stops before the 4th


def test_h1_no_cap_retries_exhaust_as_before(workspace: Path, store, rs_store) -> None:
    ex = _AttemptExecutor([("failed", 5.0)] * 3)
    orch = Orchestrator(ex, store, rs_store, clock=lambda: _T0)  # type: ignore[arg-type]
    orch.run(_retry_wf(workspace, 3), _reposets(workspace), AGENTS)
    assert ex.calls == 3


def test_h1_cap_counts_settled_cost_of_other_tasks(workspace: Path) -> None:
    st = _state(1.0, 0.7)
    assert Orchestrator._retry_hits_cost_cap(st, 0.2) is False
    assert Orchestrator._retry_hits_cost_cap(st, 0.3) is True
    assert Orchestrator._retry_hits_cost_cap(_state(None, 9.0), 9.0) is False


def test_w1_blind_attempt_after_actuals_attempt_is_counted(
    workspace: Path, store, rs_store
) -> None:
    ex = _AttemptExecutor([("failed", 0.1), ("failed", None), ("failed", None)])
    orch = Orchestrator(ex, store, rs_store, clock=lambda: _T0)  # type: ignore[arg-type]
    st = orch.run(_retry_wf(workspace, 3), _reposets(workspace), AGENTS)
    ts = st.tasks["t1"]
    assert ts.actuals_available is True  # attempt 1 had actuals...
    assert ts.attempts_without_actuals == 2  # ...but attempts 2 and 3 are still counted blind


def test_w1_all_actuals_attempts_count_zero_blind(workspace: Path, store, rs_store) -> None:
    ex = _AttemptExecutor([("failed", 0.1), ("succeeded", 0.1)])
    orch = Orchestrator(ex, store, rs_store, clock=lambda: _T0)  # type: ignore[arg-type]
    st = orch.run(_retry_wf(workspace, 3), _reposets(workspace), AGENTS)
    assert st.tasks["t1"].attempts_without_actuals == 0


def test_n1_cancel_between_attempts_keeps_spent_cost(workspace: Path, store, rs_store) -> None:
    """T-lTW88y N-1: a cancel observed before attempt 2 must not drop attempt 1's paid cost."""
    ex = _AttemptExecutor([("failed", 0.3)] * 3)

    def cancel() -> bool:
        return ex.calls >= 1  # attempt 1 ran; cancelled before attempt 2

    orch = Orchestrator(ex, store, rs_store, clock=lambda: _T0, cancel_fn=cancel)  # type: ignore[arg-type]
    st = orch.run(_retry_wf(workspace, 3), _reposets(workspace), AGENTS)
    ts = st.tasks["t1"]
    assert ex.calls == 1
    assert round(ts.cumulative_cost_usd, 6) == 0.3
    assert ts.actuals_available is True
    assert ts.attempts_without_actuals == 0  # the one attempt that ran reported actuals
