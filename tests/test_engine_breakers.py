"""Engine-level integration test for the circuit-breaker task-boundary hook (T-x8v4d3).

Proves `evaluate_breakers` actually fires inside `Orchestrator.run()` at a real task
boundary -- after a task settles and state is saved, before the next task dispatches --
using a synthetic `circuit_breakers` entry that points at a test-only stub condition
registered directly into `BREAKER_REGISTRY` (never one of the six MVP conditions; those
land in T-q5n7k2 and are not exercised here).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.breakers import BREAKER_REGISTRY, Breaker, BreakerContext, TripResult
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    AgentSpec,
    CircuitBreakerSpec,
    RepoRef,
    RepoSet,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore


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


def _task(
    tid: str, depends_on: list[str] | None = None, outputs: list[str] | None = None
) -> TaskSpec:
    return TaskSpec(
        id=tid,
        agent="ag",
        instruction="specs/examples/instructions/design.md",
        depends_on=depends_on or [],
        outputs=outputs or [],
    )


class _AlwaysTripBreaker(Breaker):
    """Test-only stub: trips on the first boundary it is ever asked to evaluate."""

    def __init__(self, condition: str) -> None:
        self.condition = condition
        self.evaluate_calls: list[str] = []

    def evaluate(self, spec: CircuitBreakerSpec, ctx: BreakerContext) -> TripResult | None:
        self.evaluate_calls.append(spec.id)
        return TripResult(detail={"boundary_call": len(self.evaluate_calls)})


@pytest.fixture
def stub_condition():
    """Register a test-only always-trip condition for the duration of one test."""
    condition = "test.engine_boundary_stub"
    breaker = _AlwaysTripBreaker(condition)
    BREAKER_REGISTRY[condition] = breaker
    yield condition, breaker
    BREAKER_REGISTRY.pop(condition, None)


class TestBreakerHookFiresAtTaskBoundary:
    def test_breaker_trips_after_first_task_and_halts_before_next_dispatch(
        self, tmp_path: Path, stub_condition
    ) -> None:
        condition, breaker = stub_condition
        tasks = [
            _task("a", outputs=["output/a.txt"]),
            _task("b", depends_on=["a"], outputs=["output/b.txt"]),
            _task("c", depends_on=["b"], outputs=["output/c.txt"]),
        ]
        wf = WorkflowSpec(
            version="1.0",
            id="wf",
            repo_set="rs",
            tasks=tasks,
            circuit_breakers=[
                CircuitBreakerSpec(id="stub-breaker", condition=condition, action="fail"),
            ],
        )
        store, rs_store = _make_workspace(tmp_path)
        executor = FakeExecutor()
        orch = Orchestrator(executor, store, rs_store)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        # The breaker owns the outcome: resumable "failed" (ADR-RC-004), not "cancelled"
        # or a bespoke status value.
        assert state.status == "failed"

        # Recorded exactly once, action preserved distinctly from RunState.status.
        assert len(state.tripped_breakers) == 1
        rec = state.tripped_breakers[0]
        assert rec.id == "stub-breaker"
        assert rec.condition == condition
        assert rec.action == "fail"

        # Task "a" settled (the boundary that tripped the breaker); "b"/"c" never
        # dispatched -- the hook halted the loop BEFORE the next dispatch.
        assert state.tasks["a"].status == "succeeded"
        assert state.tasks["b"].status == "pending"
        assert state.tasks["c"].status == "pending"

        # The breaker was asked to evaluate exactly once (boundary after task "a" only;
        # the loop halted before a second boundary could occur).
        assert breaker.evaluate_calls == ["stub-breaker"]

    def test_no_circuit_breakers_declared_is_a_byte_identical_no_op(self, tmp_path: Path) -> None:
        """Regression guard: workflows without circuit_breakers (the default / common
        case, and every workflow before this ticket) must behave exactly as before --
        no registry lookup, no tripped_breakers entries, normal success."""
        tasks = [
            _task("a", outputs=["output/a.txt"]),
            _task("b", depends_on=["a"], outputs=["output/b.txt"]),
        ]
        wf = WorkflowSpec(version="1.0", id="wf", repo_set="rs", tasks=tasks)
        store, rs_store = _make_workspace(tmp_path)
        executor = FakeExecutor()
        orch = Orchestrator(executor, store, rs_store)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        assert state.tripped_breakers == []
        assert state.tasks["a"].status == "succeeded"
        assert state.tasks["b"].status == "succeeded"
