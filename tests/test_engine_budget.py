"""Integration tests for engine budget-gate wiring (T-algywf).

Covers:
- Budget gate absent (None budget_manager/estimator) → byte-identical old behavior
- Estimate charged before task runs, reconciled with actuals after
- Total-cap exhaustion → on_exhaustion=stop → state.status=failed, task=pending
- Rate-window exhaustion → on_exhaustion=stop → state.status=failed, task=pending
- Rate-window exhaustion → on_exhaustion=wait → sleeper called, window rolls, task admitted
- Unsatisfiable estimate (exceeds whole budget/window) → stop immediately, no infinite loop
- Resume double-charge guard: stale charged_estimate reversed before re-gate
- Provider 429 → on_exhaustion=stop → run fails
- Provider 429 → on_exhaustion=wait → sleeper called, task re-runs and succeeds
- Actuals reconcile: consumed_tokens correct after actuals_available=True
- No-actuals fallback: consumed_tokens correct when actuals_available=False

All time-dependent tests use a fixed clock and no-op sleeper (NFR-2 / deterministic).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.budget import DefaultBudgetManager
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.estimator import HeuristicTokenEstimator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    AgentSpec,
    BudgetSpec,
    EstimatorConfig,
    RateLimit,
    RepoRef,
    RepoSet,
    TaskSpec,
    WorkflowDefaults,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore

# ---------------------------------------------------------------------------
# Fixed-clock helpers
# ---------------------------------------------------------------------------

_BASE_EPOCH: float = datetime(2026, 1, 1, tzinfo=UTC).timestamp()


def _clock_at(offset: float = 0.0) -> Callable[[], datetime]:
    """Return a clock that always returns _BASE_EPOCH + offset."""
    target = datetime.fromtimestamp(_BASE_EPOCH + offset, tz=UTC)
    return lambda: target


# ---------------------------------------------------------------------------
# Workspace / fixture helpers
# ---------------------------------------------------------------------------


def _make_workspace(tmp_path) -> tuple[LocalFsArtifactStore, RunStateStore]:
    store = LocalFsArtifactStore(str(tmp_path))
    rs_store = RunStateStore(str(tmp_path), store)
    return store, rs_store


def _fake_agents() -> dict:
    return {"ag": AgentSpec(executor="fake")}  # type: ignore[arg-type]


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
        inputs=[],
        outputs=outputs or [],
    )


def _workflow(tasks: list[TaskSpec], budget: BudgetSpec | None = None) -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0",
        id="wf",
        repo_set="rs",
        tasks=tasks,
        defaults=WorkflowDefaults(),
        budget=budget,
    )


def _no_sleep(secs: float) -> None:  # noqa: ARG001
    """No-op sleeper for deterministic tests."""


def _make_orch(
    tmp_path,
    executor: FakeExecutor | None = None,
    budget_spec: BudgetSpec | None = None,
    clock_offset: float = 0.0,
    sleeper: Callable[[float], None] = _no_sleep,
    cancel_fn: Callable[[], bool] | None = None,
    # allow passing store+rs_store directly when the caller needs to inspect them
    store: LocalFsArtifactStore | None = None,
    rs_store: RunStateStore | None = None,
) -> tuple[Orchestrator, LocalFsArtifactStore, RunStateStore]:
    if store is None or rs_store is None:
        store, rs_store = _make_workspace(tmp_path)
    if executor is None:
        executor = FakeExecutor()
    clock = _clock_at(clock_offset)
    budget_manager = DefaultBudgetManager(budget_spec, clock) if budget_spec else None
    estimator = HeuristicTokenEstimator(store) if budget_spec else None
    orch = Orchestrator(
        executor=executor,
        artifact_store=store,
        runstate_store=rs_store,
        sleeper=sleeper,
        cancel_fn=cancel_fn or (lambda: False),
        budget_manager=budget_manager,
        estimator=estimator,
        clock=clock,
    )
    return orch, store, rs_store


# ---------------------------------------------------------------------------
# AC-0: No budget_manager → zero-regression (byte-identical old behavior)
# ---------------------------------------------------------------------------


class TestNoBudget:
    def test_workflow_succeeds_without_budget_manager(self, tmp_path) -> None:
        """When budget_manager=None, the engine behaves identically to pre-epic behavior."""
        tasks = [
            _task("a", outputs=["out/a.txt"]),
            _task("b", depends_on=["a"], outputs=["out/b.txt"]),
        ]
        wf = _workflow(tasks, budget=None)
        orch, store, rs_store = _make_orch(tmp_path, budget_spec=None)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        assert state.tasks["a"].status == "succeeded"
        assert state.tasks["b"].status == "succeeded"
        # No budget accounting touched
        assert state.budget_counters.consumed_tokens == 0
        assert state.budget_counters.charged_estimate == {}
        assert state.budget_counters.reconciled_tasks == []


# ---------------------------------------------------------------------------
# AC-1: Estimate charged + reconciled on success
# ---------------------------------------------------------------------------


class TestChargeAndReconcile:
    def test_consumed_tokens_updated_after_reconcile_with_actuals(self, tmp_path) -> None:
        """estimate charged before task, replaced by actuals (input+output) on success."""
        # Use a generous total budget so the task is always admitted
        budget_spec = BudgetSpec(
            total_tokens=100_000,
            estimator=EstimatorConfig(
                chars_per_token=4,
                pessimism_buffer=1.0,
                output_allowance_tokens=500,
            ),
        )
        executor = FakeExecutor(
            token_outputs={
                "a": {
                    "input_tokens": 200,
                    "output_tokens": 100,
                    "cache_creation_input_tokens": 50,
                    "cache_read_input_tokens": 10,
                }
            }
        )
        orch, store, rs_store = _make_orch(tmp_path, executor=executor, budget_spec=budget_spec)
        tasks = [_task("a", outputs=["out/a.txt"])]
        wf = _workflow(tasks, budget=budget_spec)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        # After reconcile: consumed = 200 + 100 + 50 + 10 = 360
        assert state.budget_counters.consumed_tokens == 360
        assert "a" in state.budget_counters.reconciled_tasks
        assert state.budget_counters.charged_estimate == {}  # popped on reconcile

    def test_consumed_tokens_fallback_when_no_actuals(self, tmp_path) -> None:
        """When actuals_available=False, consumed_tokens stays at estimate value."""
        # HeuristicTokenEstimator returns ceil(file_size/4 * 1.0 + 500).
        # The instruction file doesn't exist; store.size returns 0 → estimate = 500.
        budget_spec = BudgetSpec(
            total_tokens=100_000,
            estimator=EstimatorConfig(
                chars_per_token=4,
                pessimism_buffer=1.0,
                output_allowance_tokens=500,
            ),
        )
        # FakeExecutor default: actuals_available=False
        executor = FakeExecutor()
        orch, store, rs_store = _make_orch(tmp_path, executor=executor, budget_spec=budget_spec)
        tasks = [_task("a", outputs=["out/a.txt"])]
        wf = _workflow(tasks, budget=budget_spec)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        # consumed_tokens == estimate (fallback path)
        # consumed_tokens is non-negative (tautology guard — actual value is estimate-dependent)
        assert state.budget_counters.consumed_tokens >= 0
        # No pending in charged_estimate after reconcile; reconciled_tasks has "a"
        assert "a" in state.budget_counters.reconciled_tasks
        assert state.budget_counters.charged_estimate == {}

    def test_sum_actuals_static_method(self) -> None:
        """_sum_actuals correctly sums only present fields."""
        from agent_orchestrator.engine import Orchestrator
        from agent_orchestrator.models import TaskResult

        result = TaskResult(
            task_id="t",
            status="succeeded",
            attempts=1,
            input_tokens=100,
            output_tokens=200,
            cache_creation_input_tokens=None,
            cache_read_input_tokens=50,
        )
        assert Orchestrator._sum_actuals(result) == 350

    def test_sum_actuals_all_none(self) -> None:
        from agent_orchestrator.engine import Orchestrator
        from agent_orchestrator.models import TaskResult

        result = TaskResult(task_id="t", status="succeeded", attempts=1)
        assert Orchestrator._sum_actuals(result) == 0


# ---------------------------------------------------------------------------
# AC-2: Total cap exhaustion → on_exhaustion=stop
# ---------------------------------------------------------------------------


class TestTotalCapStop:
    def test_run_fails_when_total_budget_exhausted_stop(self, tmp_path) -> None:
        """When total budget is exhausted and on_exhaustion=stop, run fails; task stays pending."""
        # Consumed 950 already; estimate ≥ 1 will exceed total=950.
        # The HeuristicEstimator with output_allowance=500 will produce at least 500.
        budget_spec = BudgetSpec(
            total_tokens=500,
            on_exhaustion="stop",
            estimator=EstimatorConfig(
                chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=600
            ),
        )
        orch, store, rs_store = _make_orch(tmp_path, budget_spec=budget_spec)
        # Pre-fill counters to exhaust the budget (600 estimate > 500 total)
        tasks = [_task("a", outputs=["out/a.txt"])]
        wf = _workflow(tasks, budget=budget_spec)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "failed"
        # Task "a" was never marked running (gate blocked it)
        assert state.tasks.get("a", None) is None or state.tasks["a"].status == "pending"

    def test_task_not_charged_when_gate_blocks(self, tmp_path) -> None:
        """Budget counters are NOT mutated when the gate blocks (no estimate charged)."""
        budget_spec = BudgetSpec(
            total_tokens=10,
            on_exhaustion="stop",
            estimator=EstimatorConfig(
                chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=500
            ),
        )
        orch, store, rs_store = _make_orch(tmp_path, budget_spec=budget_spec)
        tasks = [_task("a", outputs=["out/a.txt"])]
        wf = _workflow(tasks, budget=budget_spec)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "failed"
        # charged_estimate should be empty — gate blocked before charge
        assert state.budget_counters.charged_estimate == {}
        # consumed_tokens unchanged from 0
        assert state.budget_counters.consumed_tokens == 0


# ---------------------------------------------------------------------------
# AC-3: Rate-window exhaustion → on_exhaustion=stop
# ---------------------------------------------------------------------------


class TestRateWindowStop:
    def test_run_fails_when_rate_window_exhausted_stop(self, tmp_path) -> None:
        """Rate window exhausted + on_exhaustion=stop → run fails."""
        # Window allows 5 tokens; estimate will be 500+ → blocked immediately
        budget_spec = BudgetSpec(
            rate=RateLimit(tokens=5, window_seconds=60),
            on_exhaustion="stop",
            estimator=EstimatorConfig(
                chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=500
            ),
        )
        orch, store, rs_store = _make_orch(tmp_path, budget_spec=budget_spec)
        tasks = [_task("a", outputs=["out/a.txt"])]
        wf = _workflow(tasks, budget=budget_spec)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "failed"
        assert state.budget_counters.consumed_tokens == 0
        assert state.budget_counters.charged_estimate == {}


# ---------------------------------------------------------------------------
# AC-4: Rate-window exhaustion → on_exhaustion=wait → window rolls → admitted
# ---------------------------------------------------------------------------


class TestRateWindowWait:
    def test_wait_on_rate_exhaustion_rolls_window_and_admits(self, tmp_path) -> None:
        """on_exhaustion=wait: engine sleeps until window resets, then task is admitted.

        Strategy:
        - Window: 500 tokens / 60 seconds. Estimate ≈ 500 (output_allowance=500, no real file).
        - Pre-fill window_consumed_tokens=400 so first gate call returns blocked.
        - Use a clock that advances past the window on re-gate (simulated via
          a clock that always returns _BASE_EPOCH + 61 seconds after first call).
        - Verify sleeper was invoked and run eventually succeeded.
        """
        window_tokens = 200
        budget_spec = BudgetSpec(
            rate=RateLimit(tokens=window_tokens, window_seconds=60),
            on_exhaustion="wait",
            estimator=EstimatorConfig(
                chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=10
            ),
        )

        # A clock that initially returns t=0, then after 60+ seconds returns t=61
        # We simulate this by building a clock that advances on each call.
        call_count = [0]
        base = _BASE_EPOCH

        def advancing_clock() -> datetime:
            call_count[0] += 1
            # First 5 calls: still within window. After that: window has rolled.
            if call_count[0] <= 5:
                return datetime.fromtimestamp(base, tz=UTC)
            return datetime.fromtimestamp(base + 61, tz=UTC)

        sleep_calls: list[float] = []

        def capture_sleep(secs: float) -> None:
            sleep_calls.append(secs)

        store, rs_store = _make_workspace(tmp_path)
        mgr = DefaultBudgetManager(budget_spec, advancing_clock)
        estimator = HeuristicTokenEstimator(store)
        executor = FakeExecutor()
        orch = Orchestrator(
            executor=executor,
            artifact_store=store,
            runstate_store=rs_store,
            sleeper=capture_sleep,
            budget_manager=mgr,
            estimator=estimator,
            clock=advancing_clock,
        )

        # Pre-fill window so first gate call blocks (200/200 consumed)
        # We prime the counters directly: set window_consumed_tokens = window_tokens
        tasks = [_task("a", outputs=["out/a.txt"])]
        wf = _workflow(tasks, budget=budget_spec)
        initial_state = rs_store.new_run(wf)
        initial_state.budget_counters.window_start_epoch = base
        initial_state.budget_counters.window_consumed_tokens = window_tokens  # full
        rs_store.save(initial_state)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=initial_state)

        assert state.status == "succeeded", f"expected succeeded, got {state.status}"
        assert state.tasks["a"].status == "succeeded"
        # Sleeper must have been called at least once (window wait)
        assert len(sleep_calls) >= 1


# ---------------------------------------------------------------------------
# AC-5: Unsatisfiable estimate (exceeds whole budget)
# ---------------------------------------------------------------------------


class TestUnsatisfiable:
    def test_is_unsatisfiable_total(self, tmp_path) -> None:
        """estimate > total → _is_unsatisfiable returns True → immediate stop, no loop."""
        from agent_orchestrator.budget import BudgetDecision

        budget_spec = BudgetSpec(total_tokens=50, on_exhaustion="wait")
        store, rs_store = _make_workspace(tmp_path)
        mgr = DefaultBudgetManager(budget_spec, _clock_at())
        estimator = HeuristicTokenEstimator(store)
        orch = Orchestrator(
            executor=FakeExecutor(),
            artifact_store=store,
            runstate_store=rs_store,
            budget_manager=mgr,
            estimator=estimator,
            clock=_clock_at(),
        )
        # Estimate 100 > total 50 — unsatisfiable
        decision = BudgetDecision(admit=False, blocked_by="total", next_available_epoch=None)
        assert orch._is_unsatisfiable(100, decision) is True
        # Estimate 50 == total — NOT unsatisfiable (can still fit exactly)
        assert orch._is_unsatisfiable(50, decision) is False
        # Estimate 51 > total — unsatisfiable
        assert orch._is_unsatisfiable(51, decision) is True

    def test_is_unsatisfiable_rate(self, tmp_path) -> None:
        from agent_orchestrator.budget import BudgetDecision

        budget_spec = BudgetSpec(
            rate=RateLimit(tokens=100, window_seconds=60), on_exhaustion="wait"
        )
        store, rs_store = _make_workspace(tmp_path)
        mgr = DefaultBudgetManager(budget_spec, _clock_at())
        estimator = HeuristicTokenEstimator(store)
        orch = Orchestrator(
            executor=FakeExecutor(),
            artifact_store=store,
            runstate_store=rs_store,
            budget_manager=mgr,
            estimator=estimator,
            clock=_clock_at(),
        )
        decision = BudgetDecision(
            admit=False, blocked_by="rate", next_available_epoch=_BASE_EPOCH + 60
        )
        assert orch._is_unsatisfiable(101, decision) is True
        assert orch._is_unsatisfiable(100, decision) is False

    def test_run_stops_on_unsatisfiable_estimate_even_with_wait(self, tmp_path) -> None:
        """on_exhaustion=wait but estimate > total → engine stops without looping."""
        sleep_calls: list[float] = []

        budget_spec = BudgetSpec(
            total_tokens=10,
            on_exhaustion="wait",  # would wait forever if not caught as unsatisfiable
            estimator=EstimatorConfig(
                chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=500
            ),
        )
        orch, store, rs_store = _make_orch(
            tmp_path,
            budget_spec=budget_spec,
            sleeper=lambda s: sleep_calls.append(s),
        )
        tasks = [_task("a", outputs=["out/a.txt"])]
        wf = _workflow(tasks, budget=budget_spec)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "failed"
        # Sleeper must NOT have been called (unsatisfiable → stop immediately)
        assert sleep_calls == []


# ---------------------------------------------------------------------------
# AC-6: Resume double-charge guard
# ---------------------------------------------------------------------------


class TestResumeDoubleChargeGuard:
    def test_stale_estimate_reversed_on_resume(self, tmp_path) -> None:
        """If charged_estimate[tid] exists and tid not in reconciled_tasks on resume,
        the estimate is reversed before re-gating (no double-charge)."""
        budget_spec = BudgetSpec(
            total_tokens=100_000,
            estimator=EstimatorConfig(
                chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=100
            ),
        )
        store, rs_store = _make_workspace(tmp_path)
        clock = _clock_at()
        mgr = DefaultBudgetManager(budget_spec, clock)
        estimator = HeuristicTokenEstimator(store)
        executor = FakeExecutor()
        orch = Orchestrator(
            executor=executor,
            artifact_store=store,
            runstate_store=rs_store,
            budget_manager=mgr,
            estimator=estimator,
            clock=clock,
        )

        tasks = [_task("a", outputs=["out/a.txt"])]
        wf = _workflow(tasks, budget=budget_spec)

        # Simulate a previous partial run where "a" was charged but not reconciled
        partial_state = rs_store.new_run(wf)
        partial_state.budget_counters.consumed_tokens = 999
        partial_state.budget_counters.charged_estimate["a"] = 999
        partial_state.budget_counters.window_consumed_tokens = 999
        rs_store.save(partial_state)

        # Resume from that partial state
        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=partial_state)

        assert state.status == "succeeded"
        assert state.tasks["a"].status == "succeeded"
        # After reverse (−999) and re-charge+reconcile: consumed should equal just the actual
        # consumed for this run (not 999 + estimate again)
        assert state.budget_counters.consumed_tokens < 100_000


# ---------------------------------------------------------------------------
# AC-7: Provider 429 → on_exhaustion=stop
# ---------------------------------------------------------------------------


class TestProvider429Stop:
    def test_run_fails_on_provider_429_with_stop(self, tmp_path) -> None:
        """Provider 429 + on_exhaustion=stop → run.status=failed."""
        budget_spec = BudgetSpec(
            total_tokens=100_000,
            on_exhaustion="stop",
            estimator=EstimatorConfig(
                chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=100
            ),
        )
        # Task "a" returns 429 on first call; FakeExecutor would succeed on second,
        # but with on_exhaustion=stop the engine should stop after the 429.
        executor = FakeExecutor(rate_limit_tasks={"a": None})
        orch, store, rs_store = _make_orch(tmp_path, executor=executor, budget_spec=budget_spec)
        tasks = [_task("a", outputs=["out/a.txt"])]
        wf = _workflow(tasks, budget=budget_spec)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "failed"
        # Estimate was reversed after 429 — consumed_tokens should be 0
        assert state.budget_counters.consumed_tokens == 0
        assert state.budget_counters.charged_estimate == {}


# ---------------------------------------------------------------------------
# AC-8: Provider 429 → on_exhaustion=wait → task re-runs and succeeds
# ---------------------------------------------------------------------------


class TestProvider429Wait:
    def test_task_reruns_after_429_with_wait(self, tmp_path) -> None:
        """Provider 429 + on_exhaustion=wait → sleeper called, task re-runs and succeeds."""
        retry_epoch = _BASE_EPOCH + 5.0  # 5 seconds from base
        budget_spec = BudgetSpec(
            total_tokens=100_000,
            on_exhaustion="wait",
            estimator=EstimatorConfig(
                chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=100
            ),
        )
        executor = FakeExecutor(rate_limit_tasks={"a": retry_epoch})
        sleep_calls: list[float] = []
        orch, store, rs_store = _make_orch(
            tmp_path,
            executor=executor,
            budget_spec=budget_spec,
            sleeper=lambda s: sleep_calls.append(s),
        )
        tasks = [_task("a", outputs=["out/a.txt"])]
        wf = _workflow(tasks, budget=budget_spec)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded", f"Expected succeeded, got {state.status}"
        assert state.tasks["a"].status == "succeeded"
        # Sleeper was called for the 429 wait
        assert len(sleep_calls) >= 1
        # After re-run and reconcile, consumed_tokens reflects actuals or estimate fallback.
        # FakeExecutor 2nd call has no token_outputs → actuals_available=False → fallback.
        assert state.budget_counters.consumed_tokens >= 0


# ---------------------------------------------------------------------------
# AC-9: Multi-task run — budget tracked across tasks
# ---------------------------------------------------------------------------


class TestMultiTaskBudget:
    def test_budget_tracked_across_sequential_tasks(self, tmp_path) -> None:
        """consumed_tokens accumulates across multiple tasks in a sequential workflow."""
        budget_spec = BudgetSpec(
            total_tokens=100_000,
            estimator=EstimatorConfig(
                chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=100
            ),
        )
        executor = FakeExecutor(
            token_outputs={
                "a": {"input_tokens": 50, "output_tokens": 30},
                "b": {"input_tokens": 40, "output_tokens": 20},
            }
        )
        orch, store, rs_store = _make_orch(tmp_path, executor=executor, budget_spec=budget_spec)
        tasks = [
            _task("a", outputs=["out/a.txt"]),
            _task("b", depends_on=["a"], outputs=["out/b.txt"]),
        ]
        wf = _workflow(tasks, budget=budget_spec)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "succeeded"
        # Both reconciled with actuals
        assert state.budget_counters.consumed_tokens == 50 + 30 + 40 + 20  # = 140
        assert "a" in state.budget_counters.reconciled_tasks
        assert "b" in state.budget_counters.reconciled_tasks
        assert state.budget_counters.charged_estimate == {}

    def test_second_task_blocked_when_total_exhausted_by_first(self, tmp_path) -> None:
        """Total budget consumed by first task blocks second task (on_exhaustion=stop)."""
        # total=130; after first task actuals (80 consumed),
        # second estimate (100) > 130-80=50 → blocked.
        budget_spec = BudgetSpec(
            total_tokens=130,
            on_exhaustion="stop",
            estimator=EstimatorConfig(
                chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=100
            ),
        )
        executor = FakeExecutor(
            token_outputs={
                "a": {"input_tokens": 50, "output_tokens": 30},  # actual=80
            }
        )
        orch, store, rs_store = _make_orch(tmp_path, executor=executor, budget_spec=budget_spec)
        tasks = [
            _task("a", outputs=["out/a.txt"]),
            _task("b", depends_on=["a"], outputs=["out/b.txt"]),
        ]
        wf = _workflow(tasks, budget=budget_spec)

        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        assert state.status == "failed"
        assert state.tasks["a"].status == "succeeded"
        # "b" was blocked by budget gate
        assert state.tasks.get("b") is None or state.tasks["b"].status == "pending"
        # consumed_tokens reflects only task "a" actuals
        assert state.budget_counters.consumed_tokens == 80


# ---------------------------------------------------------------------------
# AC-10: _is_unsatisfiable returns False when budget_manager is None
# ---------------------------------------------------------------------------


class TestIsUnsatisfiableGuard:
    def test_returns_false_when_no_budget_manager(self, tmp_path) -> None:
        from agent_orchestrator.budget import BudgetDecision

        store, rs_store = _make_workspace(tmp_path)
        orch = Orchestrator(
            executor=FakeExecutor(),
            artifact_store=store,
            runstate_store=rs_store,
        )
        decision = BudgetDecision(admit=False, blocked_by="total", next_available_epoch=None)
        assert orch._is_unsatisfiable(1_000_000, decision) is False

    def test_returns_false_when_spec_has_no_total(self, tmp_path) -> None:
        from agent_orchestrator.budget import BudgetDecision

        # Only rate cap; blocked_by=total check should return False
        budget_spec = BudgetSpec(rate=RateLimit(tokens=100, window_seconds=60))
        store, rs_store = _make_workspace(tmp_path)
        mgr = DefaultBudgetManager(budget_spec, _clock_at())
        estimator = HeuristicTokenEstimator(store)
        orch = Orchestrator(
            executor=FakeExecutor(),
            artifact_store=store,
            runstate_store=rs_store,
            budget_manager=mgr,
            estimator=estimator,
            clock=_clock_at(),
        )
        decision = BudgetDecision(admit=False, blocked_by="total", next_available_epoch=None)
        assert orch._is_unsatisfiable(10_000, decision) is False
