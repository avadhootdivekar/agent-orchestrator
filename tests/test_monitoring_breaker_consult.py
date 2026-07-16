"""Engine integration tests for Consult Point A (T-TdildW, epic E-XyfjuZ).

Covers TASK.md acceptance criteria:
1. All-hard trips at a boundary behave byte-identically to today (no consult at all).
2. All-recommend trips consult the Monitor once per breaker, in declared order; extend
   applies `apply_breaker_extension` and the run continues; a later re-trip past the
   extended threshold halts (RuleBasedMonitor's own "extend once then halt" policy).
3. A mixed hard+recommend trip at one boundary halts WITHOUT consulting at all.
4. `max_extensions_per_breaker` is enforced in engine code — a monitor double that always
   answers "extend" still gets forced to "halt" once the bound is hit, and is never even
   called once the bound is already exhausted.
5. `max_monitor_calls_per_run` exhaustion falls back to "halt" without calling the monitor;
   a `monitor.cap_exceeded` event is logged.
6. Resume: a persisted `monitor_decisions` count (from before the run stopped) is honoured
   by the bound check — `prepare_resume` does not reset it.
7. `state.tasks`/`injected_tasks` are byte-identical before/after a consult (no pollution),
   even when the consulted Monitor is an `AgentMonitor` that spawns a real executor call.
8. A monitor that raises falls back to the safe default (halt) rather than crashing the run.

Test technique: `evaluate_breakers`' count-based conditions (`task_failures`,
`consecutive_failures`) read ALL of `state.tasks.values()`, not just tasks belonging to the
current `WorkflowSpec` -- so a "phantom" already-failed entry seeded directly into the
initial `RunState` (never dispatched, not part of `workflow.tasks`) lets a real `orch.run()`
trip a breaker at a boundary where the JUST-DISPATCHED task itself SUCCEEDED. This matters
because a task that itself fails ends the run via a separate, pre-existing, unconditional
check (`if ts.status not in ("succeeded", "skipped"): ...`) regardless of any breaker --
using a phantom failure keeps that check out of the picture so the consult outcome is what
actually determines whether the run continues.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.base import Executor
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    AgentSpec,
    CircuitBreakerSpec,
    MonitorDecisionRecord,
    RepoRef,
    RepoSet,
    RunState,
    TaskContext,
    TaskResult,
    TaskRunState,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.monitoring import (
    AgentMonitor,
    BreakerTripSummary,
    BreakerVerdict,
    HealVerdict,
    Monitor,
    RuleBasedMonitor,
    TaskFailureSummary,
)
from agent_orchestrator.runstate import RunStateStore

_STARTED = "2026-01-01T00:00:00+00:00"


def _make_workspace(tmp_path: Path) -> tuple[LocalFsArtifactStore, RunStateStore]:
    store = LocalFsArtifactStore(str(tmp_path))
    rs_store = RunStateStore(str(tmp_path), store)
    return store, rs_store


def _fake_agents() -> dict:
    return {"ag": AgentSpec(executor="fake")}


def _fake_reposets(workspace: str) -> dict:
    return {
        "rs": RepoSet(
            workspace_root=workspace,
            repos=[RepoRef(id="core", path=".", role="primary")],
        )
    }


def _task(tid: str, depends_on: list[str] | None = None) -> TaskSpec:
    return TaskSpec(
        id=tid,
        agent="ag",
        instruction="specs/examples/instructions/design.md",
        depends_on=depends_on or [],
        outputs=[f"output/{tid}.txt"],
    )


def _two_task_workflow(circuit_breakers: list[CircuitBreakerSpec]) -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0",
        id="wf",
        repo_set="rs",
        tasks=[_task("a"), _task("b", depends_on=["a"])],
        circuit_breakers=circuit_breakers,
    )


def _initial_state_with_phantom_failures(n: int, **overrides: object) -> RunState:
    """A fresh RunState with *n* already-"failed" phantom task entries that never belong
    to any real WorkflowSpec task -- lets task_failures/consecutive_failures trip at the
    boundary after a REAL task succeeds (see module docstring)."""
    tasks = {
        f"phantom-{i}": TaskRunState(status="failed", ended_at=f"2026-01-01T00:00:{i:02d}+00:00")
        for i in range(n)
    }
    base: dict[str, object] = dict(
        run_id="wf-20260101T000000Z",
        workflow_id="wf",
        repo_set="rs",
        started_at=_STARTED,
        updated_at=_STARTED,
        tasks=tasks,
    )
    base.update(overrides)
    return RunState(**base)  # type: ignore[arg-type]


class _RaisesIfCalledMonitor(Monitor):
    """Proves a consult never happens (hard-mode / mixed-mode / bound-or-cap-exhausted)."""

    name = "raises-if-called"

    def decide_breaker_trip(self, trip: BreakerTripSummary, *, run_id: str) -> BreakerVerdict:
        raise AssertionError(
            f"decide_breaker_trip must not be called (breaker_id={trip.breaker_id})"
        )

    def decide_task_failure(self, summary: TaskFailureSummary, *, run_id: str) -> HealVerdict:
        raise AssertionError("decide_task_failure must not be called")


class _AlwaysExtendMonitor(Monitor):
    """Always answers extend -- used to prove the ENGINE's bound overrides monitor intent
    (a misbehaving/over-eager monitor must not be able to exceed max_extensions_per_breaker)."""

    name = "always-extend"

    def __init__(self) -> None:
        self.breaker_calls: list[str] = []

    def decide_breaker_trip(self, trip: BreakerTripSummary, *, run_id: str) -> BreakerVerdict:
        self.breaker_calls.append(trip.breaker_id)
        return BreakerVerdict(decision="extend", extend_by_seconds=None, reason="always extend")

    def decide_task_failure(self, summary: TaskFailureSummary, *, run_id: str) -> HealVerdict:
        raise AssertionError("not used in these tests")


class _RaisingMonitor(Monitor):
    """Simulates a buggy Monitor implementation that raises (NFR-2 fallback proof)."""

    name = "raising"

    def decide_breaker_trip(self, trip: BreakerTripSummary, *, run_id: str) -> BreakerVerdict:
        raise RuntimeError("simulated monitor bug")

    def decide_task_failure(self, summary: TaskFailureSummary, *, run_id: str) -> HealVerdict:
        raise AssertionError("not used in these tests")


# ---------------------------------------------------------------------------
# AC1: hard mode (including the pre-epic default) never consults
# ---------------------------------------------------------------------------


class TestHardModeNeverConsults:
    def test_default_mode_is_hard_and_behaves_byte_identically(self, tmp_path: Path) -> None:
        """No `mode` declared at all (every pre-epic workflow) -> hard -> halt exactly like
        today, and the monitor is never even constructed-and-called."""
        store, rs_store = _make_workspace(tmp_path)
        wf = _two_task_workflow(
            [
                CircuitBreakerSpec(
                    id="fails-cap", condition="task_failures", action="stop", threshold=1
                )
            ]
        )
        initial = _initial_state_with_phantom_failures(1)
        orch = Orchestrator(FakeExecutor(), store, rs_store, monitor=_RaisesIfCalledMonitor())

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=initial)

        assert state.status == "failed"
        assert [tb.id for tb in state.tripped_breakers] == ["fails-cap"]
        assert state.monitor_decisions == []
        # Task "a" itself succeeded; "b" never dispatched -- halted before the next
        # boundary (no TaskRunState entry at all was ever created for it, since this test
        # passes a manufactured run_state rather than letting new_run() pre-seed pending
        # entries for every workflow task).
        assert state.tasks["a"].status == "succeeded"
        assert "b" not in state.tasks

    def test_explicit_hard_mode_never_consults(self, tmp_path: Path) -> None:
        store, rs_store = _make_workspace(tmp_path)
        wf = _two_task_workflow(
            [
                CircuitBreakerSpec(
                    id="fails-cap",
                    condition="task_failures",
                    action="stop",
                    threshold=1,
                    mode="hard",
                )
            ]
        )
        initial = _initial_state_with_phantom_failures(1)
        orch = Orchestrator(FakeExecutor(), store, rs_store, monitor=_RaisesIfCalledMonitor())

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=initial)

        assert state.status == "failed"
        assert state.monitor_decisions == []


# ---------------------------------------------------------------------------
# AC2: all-recommend trips consult, extend, then halt on the next re-trip
# ---------------------------------------------------------------------------


class TestRecommendModeConsultsAndExtends:
    def test_rule_based_monitor_extends_once_and_run_continues(self, tmp_path: Path) -> None:
        store, rs_store = _make_workspace(tmp_path)
        wf = _two_task_workflow(
            [
                CircuitBreakerSpec(
                    id="fails-cap",
                    condition="task_failures",
                    action="stop",
                    threshold=1,
                    mode="recommend",
                )
            ]
        )
        initial = _initial_state_with_phantom_failures(1)
        orch = Orchestrator(FakeExecutor(), store, rs_store, monitor=RuleBasedMonitor())

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=initial)

        # Extended -> the run continued past the trip and succeeded.
        assert state.status == "succeeded"
        assert state.tasks["a"].status == "succeeded"
        assert state.tasks["b"].status == "succeeded"
        # Un-latched: the trip record for "fails-cap" was removed by apply_breaker_extension.
        assert state.tripped_breakers == []
        # breaker_overrides bumped by the ORIGINAL threshold (1 + 1 = 2), extend_by_same.
        assert state.breaker_overrides == {"fails-cap": 2.0}
        # Exactly one consult was recorded, decision "extend".
        assert len(state.monitor_decisions) == 1
        rec = state.monitor_decisions[0]
        assert rec.consult_point == "breaker_trip"
        assert rec.subject_id == "fails-cap"
        assert rec.decision == "extend"
        assert rec.monitor == "rules"

    def test_second_trip_past_extended_threshold_halts_via_bound(self, tmp_path: Path) -> None:
        """2 phantom failures + threshold=1 -> trips immediately on the FIRST real boundary
        already past the ORIGINAL threshold; RuleBasedMonitor extends once (this is its
        first-ever consult for this breaker id). The extended threshold (2) is immediately
        met too (2 phantom failures == 2), so the breaker trips again at the VERY NEXT
        boundary -- but max_extensions_per_breaker (default 1) is already exhausted by the
        first extension, so the engine's BOUND halts it WITHOUT even asking the monitor a
        second time (no new monitor_decisions entry) -- see
        `test_rule_based_monitor_itself_halts_on_second_ask_when_bound_allows_it` below for
        the complementary case where the bound is raised and RuleBasedMonitor's OWN
        "extend once then halt" policy is what's actually exercised."""
        store, rs_store = _make_workspace(tmp_path)
        wf = _two_task_workflow(
            [
                CircuitBreakerSpec(
                    id="fails-cap",
                    condition="task_failures",
                    action="stop",
                    threshold=1,
                    mode="recommend",
                )
            ]
        )
        initial = _initial_state_with_phantom_failures(2)
        orch = Orchestrator(FakeExecutor(), store, rs_store, monitor=RuleBasedMonitor())

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=initial)

        assert state.status == "failed"
        # Task "a" ran (first boundary extended); "b" ran too, but its OWN boundary re-trips
        # past the extended threshold (2 phantom failures >= 2) and halts before anything
        # after "b" (there is nothing after "b", but the run itself ends "failed").
        assert state.tasks["a"].status == "succeeded"
        assert state.tasks["b"].status == "succeeded"
        # Only ONE actual consult happened -- the second trip was bound-exhausted (no ask).
        assert len(state.monitor_decisions) == 1
        assert state.monitor_decisions[0].decision == "extend"
        assert [tb.id for tb in state.tripped_breakers] == ["fails-cap"]  # re-latched on halt

    def test_rule_based_monitor_itself_halts_on_second_ask_when_bound_allows_it(
        self, tmp_path: Path
    ) -> None:
        """Complementary to the bound test above: with max_extensions_per_breaker=2 (so the
        engine's bound does NOT prevent a second ask), RuleBasedMonitor is actually
        consulted twice for the SAME breaker id and its own "extend once then halt" policy
        is what produces the second "halt" (prior_extensions=1 on the second ask)."""
        store, rs_store = _make_workspace(tmp_path)
        wf = _two_task_workflow(
            [
                CircuitBreakerSpec(
                    id="fails-cap",
                    condition="task_failures",
                    action="stop",
                    threshold=1,
                    mode="recommend",
                )
            ]
        )
        initial = _initial_state_with_phantom_failures(2)
        orch = Orchestrator(
            FakeExecutor(),
            store,
            rs_store,
            monitor=RuleBasedMonitor(),
            max_extensions_per_breaker=2,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=initial)

        assert state.status == "failed"
        assert len(state.monitor_decisions) == 2
        assert [d.decision for d in state.monitor_decisions] == ["extend", "halt"]
        assert [tb.id for tb in state.tripped_breakers] == ["fails-cap"]  # re-latched on halt

    def test_extension_reuses_apply_breaker_extension_un_latch_semantics(
        self, tmp_path: Path
    ) -> None:
        """Cross-check against the E-3JTmVu mechanism directly: breaker_overrides and the
        un-latch are produced by the SAME apply_breaker_extension function the operator-driven
        `ao resume --extend-breaker` CLI path uses -- not a parallel, duplicated mechanism."""
        store, rs_store = _make_workspace(tmp_path)
        wf = _two_task_workflow(
            [
                CircuitBreakerSpec(
                    id="fails-cap",
                    condition="task_failures",
                    action="stop",
                    threshold=10,
                    mode="recommend",
                )
            ]
        )
        initial = _initial_state_with_phantom_failures(10)
        orch = Orchestrator(FakeExecutor(), store, rs_store, monitor=RuleBasedMonitor())

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=initial)

        assert state.status == "succeeded"
        assert state.breaker_overrides == {"fails-cap": 20.0}  # 10 (current) + 10 (original)


# ---------------------------------------------------------------------------
# AC3: mixed hard+recommend trips at one boundary never consult
# ---------------------------------------------------------------------------


class TestMixedHardAndRecommendNeverConsults:
    def test_mixed_trip_at_same_boundary_halts_without_consulting(self, tmp_path: Path) -> None:
        store, rs_store = _make_workspace(tmp_path)
        # Both breakers use task_failures (a pure COUNT, order/recency-independent) so both
        # reliably trip together off the same phantom failure -- consecutive_failures would
        # NOT work here: it is a trailing streak by `ended_at` recency, and the just-
        # dispatched (succeeded) task "a" always has a real, later clock timestamp than the
        # manufactured phantom entries, which breaks any trailing-failure streak.
        wf = _two_task_workflow(
            [
                CircuitBreakerSpec(
                    id="fails-cap-hard",
                    condition="task_failures",
                    action="stop",
                    threshold=1,
                    mode="hard",
                ),
                CircuitBreakerSpec(
                    id="fails-cap-recommend",
                    condition="task_failures",
                    action="stop",
                    threshold=1,
                    mode="recommend",
                ),
            ]
        )
        initial = _initial_state_with_phantom_failures(1)
        orch = Orchestrator(FakeExecutor(), store, rs_store, monitor=_RaisesIfCalledMonitor())

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=initial)

        assert state.status == "failed"
        assert state.monitor_decisions == []
        assert {tb.id for tb in state.tripped_breakers} == {
            "fails-cap-hard",
            "fails-cap-recommend",
        }


# ---------------------------------------------------------------------------
# AC4: max_extensions_per_breaker enforced in engine code
# ---------------------------------------------------------------------------


class TestMaxExtensionsPerBreakerBound:
    def test_bound_exhausted_forces_halt_without_calling_an_always_extend_monitor(
        self, tmp_path: Path
    ) -> None:
        store, rs_store = _make_workspace(tmp_path)
        wf = _two_task_workflow(
            [
                CircuitBreakerSpec(
                    id="fails-cap",
                    condition="task_failures",
                    action="stop",
                    threshold=1,
                    mode="recommend",
                )
            ]
        )
        # Pre-seed monitor_decisions as if the bound (default 1) was already used up by an
        # earlier consult in this same run session.
        initial = _initial_state_with_phantom_failures(
            1,
            monitor_decisions=[
                MonitorDecisionRecord(
                    at=_STARTED,
                    consult_point="breaker_trip",
                    subject_id="fails-cap",
                    decision="extend",
                    monitor="rules",
                )
            ],
        )
        always_extend = _AlwaysExtendMonitor()
        orch = Orchestrator(
            FakeExecutor(),
            store,
            rs_store,
            monitor=always_extend,
            max_extensions_per_breaker=1,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=initial)

        assert state.status == "failed"
        # The bound was checked BEFORE consulting -- the always-extend monitor was NEVER
        # called, proving the engine enforces the bound rather than trusting the monitor.
        assert always_extend.breaker_calls == []
        # No NEW monitor_decisions entry was added for this (skipped) consult.
        assert len(state.monitor_decisions) == 1


# ---------------------------------------------------------------------------
# AC5: max_monitor_calls_per_run cap enforced in engine code
# ---------------------------------------------------------------------------


class TestMaxMonitorCallsPerRunCap:
    def test_cap_exhausted_falls_back_to_halt_without_calling_the_monitor(
        self, tmp_path: Path, read_jsonl
    ) -> None:
        store, rs_store = _make_workspace(tmp_path)
        wf = _two_task_workflow(
            [
                CircuitBreakerSpec(
                    id="fails-cap",
                    condition="task_failures",
                    action="stop",
                    threshold=1,
                    mode="recommend",
                )
            ]
        )
        # Pre-fill the run-wide cap with unrelated consult records (a different breaker id,
        # so max_extensions_per_breaker for "fails-cap" itself is untouched -- only the
        # SHARED run-wide cap is exhausted).
        initial = _initial_state_with_phantom_failures(
            1,
            monitor_decisions=[
                MonitorDecisionRecord(
                    at=_STARTED,
                    consult_point="breaker_trip",
                    subject_id="other-breaker",
                    decision="halt",
                    monitor="rules",
                )
            ],
        )
        orch = Orchestrator(
            FakeExecutor(),
            store,
            rs_store,
            monitor=_RaisesIfCalledMonitor(),
            max_monitor_calls_per_run=1,
        )

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=initial)

        assert state.status == "failed"
        # No NEW monitor_decisions entry (the consult was skipped, not attempted).
        assert len(state.monitor_decisions) == 1

        log_path = tmp_path / ".orchestrator" / "runs" / state.run_id / "run.log"
        events = [r.get("event") for r in read_jsonl(log_path)]
        assert "monitor.cap_exceeded" in events


# ---------------------------------------------------------------------------
# AC6: resume honours a persisted monitor_decisions count
# ---------------------------------------------------------------------------


class TestResumeHonoursPersistedMonitorState:
    def test_resumed_run_does_not_reset_the_extension_bound(self, tmp_path: Path) -> None:
        store, rs_store = _make_workspace(tmp_path)
        wf = _two_task_workflow(
            [
                CircuitBreakerSpec(
                    id="fails-cap",
                    condition="task_failures",
                    action="stop",
                    threshold=1,
                    mode="recommend",
                )
            ]
        )
        # Simulate a PRIOR session that already used up the extension bound and then
        # halted for an unrelated reason (status="failed", task "a" already succeeded).
        prior = _initial_state_with_phantom_failures(
            1,
            status="failed",
            monitor_decisions=[
                MonitorDecisionRecord(
                    at=_STARTED,
                    consult_point="breaker_trip",
                    subject_id="fails-cap",
                    decision="extend",
                    monitor="rules",
                )
            ],
        )
        prior.tasks["a"] = TaskRunState(
            status="succeeded", started_at=_STARTED, ended_at=_STARTED, outputs_present=True
        )
        (tmp_path / "output").mkdir(parents=True, exist_ok=True)
        (tmp_path / "output" / "a.txt").write_text("done")
        rs_store.save(prior)

        resumed = rs_store.load(prior.run_id)
        resumed = rs_store.prepare_resume(resumed, wf)

        assert resumed.monitor_decisions == prior.monitor_decisions  # untouched by resume

        orch = Orchestrator(FakeExecutor(), store, rs_store, monitor=_AlwaysExtendMonitor())
        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=resumed)

        # "a" is already succeeded (skipped); "b" dispatches and its boundary re-trips
        # "fails-cap" (1 phantom failure >= threshold 1) -- but the bound (default 1) was
        # already used up in the PRIOR session, so this halts without a second extension.
        assert state.status == "failed"
        assert state.tasks["b"].status == "succeeded"
        assert len(state.monitor_decisions) == 1  # no new entry added


# ---------------------------------------------------------------------------
# AC7: a consult never pollutes state.tasks / injected_tasks
# ---------------------------------------------------------------------------


class _VerdictWritingExecutor(Executor):
    def __init__(self, verdict: dict) -> None:
        self._verdict = verdict

    def execute(self, ctx: TaskContext) -> TaskResult:
        import json

        Path(ctx.output_paths[0]).write_text(json.dumps(self._verdict))
        return TaskResult(task_id=ctx.task_id, status="succeeded", attempts=1, exit_code=0)


class TestConsultNeverPollutesRunState:
    def test_agent_monitor_consult_leaves_state_tasks_and_injected_tasks_untouched(
        self, tmp_path: Path
    ) -> None:
        store, rs_store = _make_workspace(tmp_path)
        wf = _two_task_workflow(
            [
                CircuitBreakerSpec(
                    id="fails-cap",
                    condition="task_failures",
                    action="stop",
                    threshold=1,
                    mode="recommend",
                )
            ]
        )
        initial = _initial_state_with_phantom_failures(1)
        before_task_keys = set(initial.tasks.keys())

        monitor_executor = _VerdictWritingExecutor(
            {"decision": "extend", "extend_by_seconds": None, "reason": "fine"}
        )
        monitor = AgentMonitor(
            AgentSpec(executor="fake"), monitor_executor, store, name="my-agent-monitor"
        )
        orch = Orchestrator(FakeExecutor(), store, rs_store, monitor=monitor)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=initial)

        assert state.status == "succeeded"
        # Only the two REAL workflow tasks ("a", "b") plus the pre-existing phantom entries
        # exist in state.tasks -- the AgentMonitor consult added NOTHING (no "monitor-*" key
        # leaked into state.tasks).
        assert set(state.tasks.keys()) == before_task_keys | {"a", "b"}
        assert state.injected_tasks == []


# ---------------------------------------------------------------------------
# AC8: a raising Monitor falls back to the safe default (NFR-2)
# ---------------------------------------------------------------------------


class TestMonitorRaisingFallsBackToSafeDefault:
    def test_raising_monitor_halts_like_the_safe_default_never_crashes_the_run(
        self, tmp_path: Path
    ) -> None:
        store, rs_store = _make_workspace(tmp_path)
        wf = _two_task_workflow(
            [
                CircuitBreakerSpec(
                    id="fails-cap",
                    condition="task_failures",
                    action="stop",
                    threshold=1,
                    mode="recommend",
                )
            ]
        )
        initial = _initial_state_with_phantom_failures(1)
        orch = Orchestrator(FakeExecutor(), store, rs_store, monitor=_RaisingMonitor())

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=initial)

        assert state.status == "failed"  # safe default is "halt" -> run fails, does not crash
        assert len(state.monitor_decisions) == 1
        assert state.monitor_decisions[0].decision == "halt"


# ---------------------------------------------------------------------------
# Defense-in-depth: _consult_breaker_trips rejects a hard-mode spec if ever reached
# (D1 -- "enforce in code, not convention"). Unreachable via the normal engine loop
# (the caller already filters for mode=="recommend"), so this calls the method directly.
# ---------------------------------------------------------------------------


class TestConsultBreakerTripsDefenseInDepth:
    def test_raises_if_called_with_a_hard_mode_spec(self, tmp_path: Path) -> None:
        store, rs_store = _make_workspace(tmp_path)
        orch = Orchestrator(FakeExecutor(), store, rs_store, monitor=_RaisesIfCalledMonitor())
        state = _initial_state_with_phantom_failures(0)
        hard_spec = CircuitBreakerSpec(
            id="b", condition="task_failures", action="stop", threshold=1, mode="hard"
        )
        run_log = logging.getLogger("test")

        with pytest.raises(AssertionError, match="recommend-mode"):
            orch._consult_breaker_trips([hard_spec], state, run_log)  # type: ignore[attr-defined]
