"""Tests for concurrency-correct budget/requeue/failure/cancel semantics under
the wave/barrier scheduler (T-VSfAUN, epic E-IasNXu, ADR-0007 D7: "drain, don't
kill").

New file -- no existing test bodies are edited here or anywhere else (the
T-j8YLGd gate is that every test in tests/test_engine*.py, and the rest of the
suite, passes UNEDITED at the default max_parallel=1; this file adds the
concurrency-specific coverage T-VSfAUN's acceptance criteria call for).

Covers:
- AC-1: budget cap holds under concurrency -- max_parallel=4, a budget that
  admits only 3 of 4 ready tasks concurrently; the 4th is gated (BLOCKED)
  until a completion frees the budget; no double-charge; final
  `consumed_tokens` identical across two forced completion orders.
- AC-2: no budget deadlock -- a rolling rate window blocks while a sibling is
  in flight (drains, does not sleep -- R3), then sleeps once nothing is in
  flight, then re-gates successfully. Proven via the exact `run.log`
  budget.* event order (deterministic, no threading needed for this one --
  see the test's own docstring for why).
- AC-3: quota-exhaustion / provider-429 / self-heal requeue a task while a
  sibling drains normally (not cancelled); the requeued task waits, then
  re-dispatches on a later wave and succeeds; its estimate is reversed
  exactly once (quota/429) with nothing left stranded.
- AC-4: a hard circuit breaker trips while a sibling is still in flight ->
  the engine stops admitting, drains the sibling (settled + persisted, not
  killed), final status "failed"; a subsequent resume does not re-run the
  sibling that actually succeeded during the drain.
- AC-5: `cancel_fn` observed at the top of a wave while two siblings are in
  flight -> both drained (not killed) and persisted, status "cancelled";
  resume re-runs only the genuinely unfinished work.
- AC-6: no thread leak -- folded into every scenario above (each asserts
  `threading.active_count()` returns to its own pre-run baseline after
  `thread.join()`), since each exercises a DIFFERENT new drain path
  (BLOCKED-drain, quota/429/self-heal-drain, breaker-halt-drain,
  cancel-drain) -- stronger than one generic dedicated test.

Concurrency proofs reuse `tests.test_wave_scheduler._GatedExecutor` UNCHANGED
(imported, never edited or reimplemented -- see `_ScriptedGatedExecutor`
below) per the ticket's explicit "reuse it... do not build a competing one."
All time-dependent tests use a fixed or explicitly-advancing clock and a
recording/no-op sleeper (no real waits, no wall-clock races).
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.budget import DefaultBudgetManager
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.estimator import HeuristicTokenEstimator
from agent_orchestrator.executors.base import Executor
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    AgentSpec,
    BudgetSpec,
    CircuitBreakerSpec,
    EstimatorConfig,
    RateLimit,
    RepoRef,
    RepoSet,
    RunState,
    TaskContext,
    TaskResult,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore
from tests.test_wave_scheduler import _GatedExecutor

# ---------------------------------------------------------------------------
# Shared helpers (mirrors tests/test_wave_scheduler.py / tests/test_engine_budget.py)
# ---------------------------------------------------------------------------

_FIXED_DT = datetime(2026, 1, 1, tzinfo=UTC)
_BASE_EPOCH: float = _FIXED_DT.timestamp()


def _make_workspace(tmp_path: Path) -> tuple[LocalFsArtifactStore, RunStateStore]:
    store = LocalFsArtifactStore(str(tmp_path))
    rs_store = RunStateStore(str(tmp_path), store, clock=lambda: _FIXED_DT)
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
    tid: str,
    depends_on: list[str] | None = None,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
) -> TaskSpec:
    return TaskSpec(
        id=tid,
        agent="ag",
        instruction="specs/instructions/design.md",
        depends_on=depends_on or [],
        inputs=inputs or [],
        outputs=outputs or [],
    )


def _workflow(
    tasks: list[TaskSpec],
    wf_id: str = "wf",
    budget: BudgetSpec | None = None,
    circuit_breakers: list[CircuitBreakerSpec] | None = None,
) -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0",
        id=wf_id,
        repo_set="rs",
        tasks=tasks,
        budget=budget,
        circuit_breakers=circuit_breakers or [],
    )


def _write_instruction(tmp_path: Path) -> None:
    instr_dir = tmp_path / "specs" / "instructions"
    instr_dir.mkdir(parents=True, exist_ok=True)
    (instr_dir / "design.md").write_text("do the task")


def _fixed_clock() -> datetime:
    return _FIXED_DT


def _compute_estimate(tmp_path: Path, cfg: EstimatorConfig) -> int:
    """Compute the deterministic per-task estimate under *cfg* directly via the
    real estimator, instead of hand-deriving a fragile magic constant -- every
    task in these tests shares the same instruction file and no inputs, so
    every task's estimate is identical."""
    store, _ = _make_workspace(tmp_path)
    estimator = HeuristicTokenEstimator(store)
    ctx = TaskContext(
        run_id="r",
        task_id="_estimate_probe",
        agent=AgentSpec(executor="fake"),  # type: ignore[arg-type]
        instruction_path=store.resolve("specs/instructions/design.md"),
        input_paths=[],
        output_paths=[],
        repo_paths={},
        timeout_seconds=1800,
    )
    return estimator.estimate(ctx, cfg)


# ---------------------------------------------------------------------------
# Scripted, thread-safe gated executor double (AC-3): WRAPS (composition, not
# reimplementation) the shared `_GatedExecutor` test double imported from
# tests.test_wave_scheduler -- gating/release/entered-event/raise_for/
# concurrency bookkeeping all delegate to the wrapped instance UNCHANGED; the
# only new behavior is scripting per-call TaskResult outcomes (quota-
# exhausted / provider-429 / failed) needed to prove requeue-under-
# concurrency semantics. This is deliberately a wrapper around the existing
# double, not a competing implementation, per the ticket's explicit "reuse
# it... do not build a competing one" -- and tests/test_wave_scheduler.py
# itself is never edited (0 deletions on existing test files).
# ---------------------------------------------------------------------------


class _ScriptedGatedExecutor(Executor):
    def __init__(self) -> None:
        self._inner = _GatedExecutor()
        self._script_lock = threading.Lock()
        self._scripts: dict[str, list[TaskResult]] = {}

    # -- delegated gating/observation API (identical surface to _GatedExecutor) --
    def gate(self, task_id: str) -> threading.Event:
        return self._inner.gate(task_id)

    def entered_event(self, task_id: str) -> threading.Event:
        return self._inner.entered_event(task_id)

    def raise_for(self, task_id: str) -> None:
        self._inner.raise_for(task_id)

    @property
    def entered_snapshot(self) -> dict[str, frozenset[str]]:
        return self._inner.entered_snapshot

    @property
    def entered_order(self) -> list[str]:
        return self._inner.entered_order

    @property
    def max_concurrent(self) -> int:
        return self._inner.max_concurrent

    # -- new scripting API --
    def script(self, task_id: str, results: list[TaskResult]) -> None:
        """Queue TaskResults returned, in order, on successive `execute()`
        calls for *task_id*. Once exhausted, `execute()` falls back to the
        wrapped `_GatedExecutor`'s default succeeded result -- gate/release
        still apply on every call, keyed by task id, exactly as in the base
        class (a redispatch after a requeue reuses the SAME release Event,
        which stays set once released, so it never re-blocks)."""
        with self._script_lock:
            self._scripts[task_id] = list(results)

    def execute(self, ctx: TaskContext) -> TaskResult:
        base_result = self._inner.execute(ctx)  # gating/bookkeeping reused verbatim
        with self._script_lock:
            queue = self._scripts.get(ctx.task_id)
            if queue:
                return queue.pop(0)
        return base_result


# ---------------------------------------------------------------------------
# AC-1: budget cap holds under concurrency
# ---------------------------------------------------------------------------


class TestBudgetCapUnderConcurrency:
    """max_parallel=4, a total-token budget that admits exactly 3 of 4 ready
    tasks concurrently; the 4th is gated (BLOCKED) until a completion
    reconciles and frees the budget. Proves: exactly 3 concurrent, no
    double-charge, no stranded estimate, and an order-independent final
    `consumed_tokens` across two forced completion orders."""

    _ACTUAL_ABC = 10  # a/b/c: identical small actual -- releasing ANY ONE of
    # them frees exactly enough room for d (symmetric, so order-independent).
    _ACTUAL_D = 20

    def _run(self, tmp_path: Path, first_release: str) -> tuple[RunState, int]:
        _write_instruction(tmp_path)
        cfg = EstimatorConfig(chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=1000)
        estimate = _compute_estimate(tmp_path, cfg)
        # Exactly 3 estimates fit; releasing one of {a,b,c} (actual=10) frees
        # exactly enough room for d: 2*estimate + 10 + estimate == total.
        total = 3 * estimate + self._ACTUAL_ABC
        budget = BudgetSpec(total_tokens=total, on_exhaustion="wait", estimator=cfg)
        tasks = [_task(t, outputs=[f"out/{t}.txt"]) for t in ("a", "b", "c", "d")]
        wf = _workflow(tasks, budget=budget)

        gated = _ScriptedGatedExecutor()
        releases: dict[str, threading.Event] = {}
        entered: dict[str, threading.Event] = {}
        for tid, actual in (("a", 10), ("b", 10), ("c", 10), ("d", self._ACTUAL_D)):
            releases[tid] = gated.gate(tid)
            entered[tid] = gated.entered_event(tid)
            gated.script(
                tid,
                [
                    TaskResult(
                        task_id=tid,
                        status="succeeded",
                        attempts=1,
                        exit_code=0,
                        actuals_available=True,
                        input_tokens=actual,
                        output_tokens=0,
                    )
                ],
            )

        store, rs_store = _make_workspace(tmp_path)
        budget_manager = DefaultBudgetManager(budget, _fixed_clock)
        estimator = HeuristicTokenEstimator(store)
        orch = Orchestrator(
            gated,
            store,
            rs_store,
            budget_manager=budget_manager,
            estimator=estimator,
            clock=_fixed_clock,
            max_parallel=4,
        )

        baseline = threading.active_count()
        result: dict[str, RunState] = {}

        def _run_orch() -> None:
            result["state"] = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        thread = threading.Thread(target=_run_orch)
        thread.start()
        try:
            for t in ("a", "b", "c"):
                assert entered[t].wait(timeout=5), f"{t} never entered execute()"
            # d's estimate cannot fit alongside a+b+c -- it must still be
            # blocked (never even reached pool.submit -- BLOCKED is decided
            # entirely on the main thread before dispatch).
            assert "d" not in gated.entered_order
            assert gated.max_concurrent >= 3

            releases[first_release].set()
            assert entered["d"].wait(timeout=5), (
                "d was never admitted after a completion freed the budget window"
            )
            for t in ("a", "b", "c"):
                releases[t].set()
            releases["d"].set()
        finally:
            thread.join(timeout=5)

        assert not thread.is_alive()
        assert threading.active_count() == baseline  # AC-6: no thread leak
        return result["state"], estimate

    def test_exactly_three_concurrent_fourth_blocked_then_admitted(self, tmp_path: Path) -> None:
        state, _estimate = self._run(tmp_path, first_release="a")
        assert state.status == "succeeded"
        for t in ("a", "b", "c", "d"):
            assert state.tasks[t].status == "succeeded"
        # No double-charge, nothing stranded: every task charged exactly once,
        # reconciled exactly once.
        assert state.budget_counters.charged_estimate == {}
        assert sorted(state.budget_counters.reconciled_tasks) == ["a", "b", "c", "d"]
        assert state.budget_counters.consumed_tokens == 10 + 10 + 10 + self._ACTUAL_D

    def test_final_consumed_tokens_identical_across_two_completion_orders(
        self, tmp_path: Path, tmp_path_factory
    ) -> None:
        tmp_a = tmp_path_factory.mktemp("order-a-first")
        tmp_c = tmp_path_factory.mktemp("order-c-first")
        state_a, _ = self._run(tmp_a, first_release="a")
        state_c, _ = self._run(tmp_c, first_release="c")

        assert state_a.status == "succeeded"
        assert state_c.status == "succeeded"
        expected_total = 10 + 10 + 10 + self._ACTUAL_D
        assert state_a.budget_counters.consumed_tokens == expected_total
        assert state_c.budget_counters.consumed_tokens == expected_total
        assert state_a.budget_counters.consumed_tokens == state_c.budget_counters.consumed_tokens


# ---------------------------------------------------------------------------
# AC-2: no budget deadlock on a rolling rate window
# ---------------------------------------------------------------------------


class TestNoBudgetDeadlockOnRollingWindow:
    """Proves the R3 fix precisely via `run.log` event ORDER rather than
    threading: task `x` is submitted first (and so is genuinely a member of
    `in_flight` -- the engine's own definition of "in flight" throughout this
    design, see `_ready_ids`'s `in_flight_ids`) when `y`'s FIRST gate check
    happens in the SAME fill pass -- no thread-timing race is needed to prove
    this, since dispatch within one wave is strictly sequential on the main
    thread. `y`'s SECOND gate check (after `x` has drained) genuinely has
    nothing in flight, and only THAT check may sleep. The exact budget.*
    event order is the discriminating assertion: a version without the R3
    fix would sleep on `y`'s FIRST (in-flight) block instead of draining
    first -- this test fails loudly if that regresses.
    """

    def test_wait_drains_in_flight_sibling_before_sleeping_and_admits_after_roll(
        self, tmp_path: Path, read_jsonl
    ) -> None:
        _write_instruction(tmp_path)
        cfg = EstimatorConfig(chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=1000)
        estimate = _compute_estimate(tmp_path, cfg)
        budget = BudgetSpec(
            rate=RateLimit(tokens=estimate, window_seconds=60),
            on_exhaustion="wait",
            estimator=cfg,
        )
        tasks = [_task("x", outputs=["out/x.txt"]), _task("y", outputs=["out/y.txt"])]
        wf = _workflow(tasks, budget=budget)

        # Mutable clock cell: reads `now`; the sleeper advances it by the
        # requested duration -- the standard fixed-clock-plus-fake-sleeper
        # pattern (no real waits, no wall-clock races).
        now = [_BASE_EPOCH]

        def clock() -> datetime:
            return datetime.fromtimestamp(now[0], tz=UTC)

        sleep_calls: list[float] = []

        def sleeper(secs: float) -> None:
            sleep_calls.append(secs)
            now[0] += secs

        store, rs_store = _make_workspace(tmp_path)
        budget_manager = DefaultBudgetManager(budget, clock)
        estimator = HeuristicTokenEstimator(store)
        orch = Orchestrator(
            FakeExecutor(),
            store,
            rs_store,
            sleeper=sleeper,
            budget_manager=budget_manager,
            estimator=estimator,
            clock=clock,
            max_parallel=2,
        )

        baseline = threading.active_count()
        state = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())
        assert threading.active_count() == baseline  # AC-6: no thread leak

        assert state.status == "succeeded"
        assert state.tasks["x"].status == "succeeded"
        assert state.tasks["y"].status == "succeeded"
        # Slept exactly once -- the "nothing in flight" branch -- for the
        # full window duration (60s); never while x held the window's capacity.
        assert sleep_calls == [60.0]

        run_dir = Path(rs_store._path(state.run_id).parent)
        records = read_jsonl(run_dir / "run.log")
        _tracked_events = (
            "budget.charge",
            "budget.gate_block",
            "budget.wait",
            "budget.resume",
            "budget.reconcile",
        )
        budget_events = [
            (r.get("event"), r.get("task_id")) for r in records if r.get("event") in _tracked_events
        ]
        assert budget_events == [
            ("budget.charge", "x"),
            ("budget.gate_block", "y"),  # blocked while x in flight -- NO wait here
            ("budget.reconcile", "x"),  # x drains first, frees nothing needed for the fix itself
            ("budget.gate_block", "y"),  # blocked again -- now genuinely nothing in flight
            ("budget.wait", "y"),  # ONLY now does it sleep
            ("budget.resume", "y"),
            ("budget.charge", "y"),  # window rolled -- admitted
            ("budget.reconcile", "y"),
        ]


# ---------------------------------------------------------------------------
# AC-3: quota / provider-429 / self-heal requeue with a sibling in flight
# ---------------------------------------------------------------------------


class TestRequeueWithSiblingInFlight:
    """A requeued task's estimate is reversed exactly once and its sibling
    drains (settles normally), never gets cancelled, while the requeued task
    waits and re-dispatches on a later wave.

    Synchronization pattern (race-free, no sleeps): gate the requeued task's
    FIRST call too (not just the sibling's) so the test can rendezvous on
    "both genuinely dispatched" before re-arming a FRESH `entered_event` for
    the requeued task's SECOND call and only THEN releasing it -- this avoids
    a race where the redispatch could complete before the test re-arms the
    event (both calls share the SAME release Event once set; only
    `entered_event` needs re-arming per call).
    """

    def test_quota_exhaustion_requeue_drains_sibling_and_succeeds(self, tmp_path: Path) -> None:
        _write_instruction(tmp_path)
        cfg = EstimatorConfig(chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=1000)
        budget = BudgetSpec(total_tokens=100_000, on_exhaustion="wait", estimator=cfg)
        # q declares no outputs: _GatedExecutor.execute() writes them
        # unconditionally regardless of the scripted override, and a stale
        # output file from the first (quota-exhausted) attempt would make
        # should_skip() short-circuit the redispatch this test needs to see.
        tasks = [_task("q"), _task("s", outputs=["out/s.txt"])]
        wf = _workflow(tasks, budget=budget)

        executor = _ScriptedGatedExecutor()
        executor.script(
            "q",
            [
                TaskResult(
                    task_id="q",
                    status="failed",
                    attempts=1,
                    claude_quota_exhausted=True,
                    error="fake quota exhaustion",
                )
            ],
        )
        q_release = executor.gate("q")
        q_entered_1 = executor.entered_event("q")
        s_release = executor.gate("s")
        s_entered = executor.entered_event("s")

        sleep_calls: list[float] = []
        store, rs_store = _make_workspace(tmp_path)
        budget_manager = DefaultBudgetManager(budget, _fixed_clock)
        estimator = HeuristicTokenEstimator(store)
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            sleeper=lambda s: sleep_calls.append(s),
            budget_manager=budget_manager,
            estimator=estimator,
            clock=_fixed_clock,
            max_parallel=2,
        )

        baseline = threading.active_count()
        result: dict[str, RunState] = {}

        def _run() -> None:
            result["state"] = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        thread = threading.Thread(target=_run)
        thread.start()
        try:
            assert q_entered_1.wait(timeout=5), "q never entered execute()"
            assert s_entered.wait(timeout=5), "s never entered execute()"
            # Both genuinely dispatched and still gated -- safe to re-arm now.
            q_entered_2 = executor.entered_event("q")
            q_release.set()
            assert q_entered_2.wait(timeout=5), (
                "q was never re-dispatched after quota-exhaustion requeue"
            )
            # s must still be gated/in-flight here -- proven structurally: the
            # run loop can only have redispatched q (a NEW wave, requiring a
            # completed drain+settle of q's first attempt) while s -- gated
            # this whole time -- was never touched.
        finally:
            s_release.set()
            thread.join(timeout=5)

        assert not thread.is_alive()
        assert threading.active_count() == baseline  # AC-6: no thread leak

        state = result["state"]
        assert state.status == "succeeded"
        assert state.tasks["q"].status == "succeeded"
        assert state.tasks["s"].status == "succeeded"
        assert len(sleep_calls) >= 1  # quota wait genuinely happened
        # Reversed exactly once (first attempt), recharged + reconciled exactly
        # once (successful redispatch) -- nothing stranded, no double-count.
        assert state.budget_counters.charged_estimate == {}
        assert state.budget_counters.reconciled_tasks.count("q") == 1
        assert state.budget_counters.reconciled_tasks.count("s") == 1

    def test_provider_429_requeue_drains_sibling_and_succeeds(self, tmp_path: Path) -> None:
        _write_instruction(tmp_path)
        cfg = EstimatorConfig(chars_per_token=4, pessimism_buffer=1.0, output_allowance_tokens=1000)
        budget = BudgetSpec(total_tokens=100_000, on_exhaustion="wait", estimator=cfg)
        # p declares no outputs -- see the quota-exhaustion test's comment above.
        tasks = [_task("p"), _task("s", outputs=["out/s.txt"])]
        wf = _workflow(tasks, budget=budget)

        executor = _ScriptedGatedExecutor()
        executor.script(
            "p",
            [
                TaskResult(
                    task_id="p",
                    status="failed",
                    attempts=1,
                    provider_rate_limited=True,
                    provider_retry_after_epoch=None,
                    error="fake 429",
                )
            ],
        )
        p_release = executor.gate("p")
        p_entered_1 = executor.entered_event("p")
        s_release = executor.gate("s")
        s_entered = executor.entered_event("s")

        sleep_calls: list[float] = []
        store, rs_store = _make_workspace(tmp_path)
        budget_manager = DefaultBudgetManager(budget, _fixed_clock)
        estimator = HeuristicTokenEstimator(store)
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            sleeper=lambda s: sleep_calls.append(s),
            budget_manager=budget_manager,
            estimator=estimator,
            clock=_fixed_clock,
            max_parallel=2,
        )

        baseline = threading.active_count()
        result: dict[str, RunState] = {}

        def _run() -> None:
            result["state"] = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        thread = threading.Thread(target=_run)
        thread.start()
        try:
            assert p_entered_1.wait(timeout=5)
            assert s_entered.wait(timeout=5)
            p_entered_2 = executor.entered_event("p")
            p_release.set()
            assert p_entered_2.wait(timeout=5), "p was never re-dispatched after 429 requeue"
        finally:
            s_release.set()
            thread.join(timeout=5)

        assert not thread.is_alive()
        assert threading.active_count() == baseline  # AC-6: no thread leak

        state = result["state"]
        assert state.status == "succeeded"
        assert state.tasks["p"].status == "succeeded"
        assert state.tasks["s"].status == "succeeded"
        assert len(sleep_calls) >= 1
        assert state.budget_counters.charged_estimate == {}
        assert state.budget_counters.reconciled_tasks.count("p") == 1

    def test_self_heal_transient_failure_requeue_drains_sibling_and_succeeds(
        self, tmp_path: Path
    ) -> None:
        _write_instruction(tmp_path)
        # h declares no outputs -- see the quota-exhaustion test's comment above.
        tasks = [_task("h"), _task("s", outputs=["out/s.txt"])]
        wf = _workflow(tasks)  # no budget: self-heal is orthogonal (no reversal semantics)

        executor = _ScriptedGatedExecutor()
        executor.script(
            "h",
            [
                TaskResult(
                    task_id="h",
                    status="failed",
                    attempts=1,
                    exit_code=1,
                    # Matches RuleBasedMonitor's DEFAULT_TRANSIENT_PATTERNS
                    # ("connection\\s+reset") -> decide_task_failure == "retry".
                    error="connection reset by peer",
                )
            ],
        )
        h_release = executor.gate("h")
        h_entered_1 = executor.entered_event("h")
        s_release = executor.gate("s")
        s_entered = executor.entered_event("s")

        sleep_calls: list[float] = []
        store, rs_store = _make_workspace(tmp_path)
        orch = Orchestrator(
            executor,
            store,
            rs_store,
            sleeper=lambda s: sleep_calls.append(s),
            self_heal_enabled=True,
            max_parallel=2,
        )

        baseline = threading.active_count()
        result: dict[str, RunState] = {}

        def _run() -> None:
            result["state"] = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        thread = threading.Thread(target=_run)
        thread.start()
        try:
            assert h_entered_1.wait(timeout=5)
            assert s_entered.wait(timeout=5)
            h_entered_2 = executor.entered_event("h")
            h_release.set()
            assert h_entered_2.wait(timeout=5), "h was never re-dispatched after self-heal retry"
        finally:
            s_release.set()
            thread.join(timeout=5)

        assert not thread.is_alive()
        assert threading.active_count() == baseline  # AC-6: no thread leak

        state = result["state"]
        assert state.status == "succeeded"
        assert state.tasks["h"].status == "succeeded"
        assert state.tasks["s"].status == "succeeded"
        assert len(sleep_calls) >= 1  # self-heal wait_seconds happened
        assert len(state.monitor_decisions) == 1
        assert state.monitor_decisions[0].decision == "retry"


# ---------------------------------------------------------------------------
# AC-4: circuit breaker halt mid-wave drains the in-flight sibling
# ---------------------------------------------------------------------------


class TestBreakerHaltDrainsInFlight:
    def test_breaker_trip_mid_wave_drains_sibling_and_stays_resumable(self, tmp_path: Path) -> None:
        _write_instruction(tmp_path)
        # s declares no outputs: _GatedExecutor.execute() writes them
        # unconditionally regardless of the scripted "failed" override, and a
        # stale output file would make should_skip() wrongly skip s's genuine
        # retry on resume (t keeps its outputs -- it never gets redispatched
        # in this scenario, so this gotcha doesn't apply to it).
        tasks = [_task("s"), _task("t", outputs=["out/t.txt"])]
        wf = _workflow(
            tasks,
            circuit_breakers=[
                CircuitBreakerSpec(id="ff", condition="task_failures", threshold=1, action="fail"),
            ],
        )

        executor = _ScriptedGatedExecutor()
        executor.script(
            "s",
            [TaskResult(task_id="s", status="failed", attempts=1, exit_code=1, error="boom")],
        )
        s_release = executor.gate("s")
        s_entered = executor.entered_event("s")
        t_release = executor.gate("t")
        t_entered = executor.entered_event("t")

        store, rs_store = _make_workspace(tmp_path)
        orch = Orchestrator(executor, store, rs_store, max_parallel=2)

        baseline = threading.active_count()
        result: dict[str, RunState] = {}

        def _run() -> None:
            result["state"] = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        thread = threading.Thread(target=_run)
        thread.start()
        try:
            assert s_entered.wait(timeout=5)
            assert t_entered.wait(timeout=5)
            s_release.set()  # s fails -> task_failures breaker trips -> HALT
        finally:
            # t must be DRAINED (settled on the main thread), never killed --
            # release it regardless of exactly when the HALT is observed.
            t_release.set()
            thread.join(timeout=5)

        assert not thread.is_alive()
        assert threading.active_count() == baseline  # AC-6: no thread leak

        state = result["state"]
        assert state.status == "failed"
        assert state.tasks["s"].status == "failed"
        assert state.tasks["t"].status == "succeeded"  # drained + persisted, not left running
        assert len(state.tripped_breakers) == 1
        assert state.tripped_breakers[0].id == "ff"
        run_id = state.run_id

        # Resume must not re-run t (it actually succeeded during the drain);
        # only s (which genuinely failed) is retried. Trap: if resume
        # incorrectly re-dispatched t, this executor would fail it.
        resume_executor = FakeExecutor(behaviors={"t": "fail"})
        orch2 = Orchestrator(resume_executor, store, rs_store, max_parallel=2)
        existing = rs_store.load(run_id)
        existing = rs_store.prepare_resume(existing, wf)
        assert existing.tasks["t"].status == "succeeded"  # prepare_resume kept it as-is
        assert existing.tasks["s"].status == "pending"  # genuinely failed -> reset for retry

        state2 = orch2.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=existing)
        assert state2.status == "succeeded"
        assert state2.tasks["s"].status == "succeeded"
        assert state2.tasks["t"].status in ("succeeded", "skipped")


# ---------------------------------------------------------------------------
# AC-5: cancel mid-wave drains both in-flight siblings and stays resumable
# ---------------------------------------------------------------------------


class TestCancelDrainsInFlight:
    def test_cancel_observed_with_two_in_flight_drains_both_and_resumable(
        self, tmp_path: Path
    ) -> None:
        """3 independent tasks (`trigger`, `t1`, `t2`) all gated so all 3
        dispatch concurrently at max_parallel=3 (proving genuine overlap);
        releasing ONLY `trigger` lets the run loop drain ONE completion and
        return to the top of the wave -- the only place `cancel_fn` is
        polled -- with EXACTLY `t1`/`t2` still in flight. A 4th task
        (`later`), depending on `t1`, is never even reached (cancel stops
        admission before the next fill) -- proving resume re-runs only the
        genuinely unfinished work."""
        _write_instruction(tmp_path)
        tasks = [
            _task("trigger", outputs=["out/trigger.txt"]),
            _task("t1", outputs=["out/t1.txt"]),
            _task("t2", outputs=["out/t2.txt"]),
            _task("later", depends_on=["t1"], inputs=["out/t1.txt"], outputs=["out/later.txt"]),
        ]
        wf = _workflow(tasks)

        executor = _ScriptedGatedExecutor()
        trig_release = executor.gate("trigger")
        trig_entered = executor.entered_event("trigger")
        t1_release = executor.gate("t1")
        t1_entered = executor.entered_event("t1")
        t2_release = executor.gate("t2")
        t2_entered = executor.entered_event("t2")

        cancel_flag = threading.Event()
        store, rs_store = _make_workspace(tmp_path)
        orch = Orchestrator(executor, store, rs_store, cancel_fn=cancel_flag.is_set, max_parallel=3)

        baseline = threading.active_count()
        result: dict[str, RunState] = {}

        def _run() -> None:
            result["state"] = orch.run(wf, _fake_reposets(str(tmp_path)), _fake_agents())

        thread = threading.Thread(target=_run)
        thread.start()
        try:
            assert trig_entered.wait(timeout=5)
            assert t1_entered.wait(timeout=5)
            assert t2_entered.wait(timeout=5)
            assert executor.max_concurrent >= 3  # genuine 3-way overlap proven
            cancel_flag.set()
            trig_release.set()  # only trigger drains normally; t1/t2 stay gated
        finally:
            t1_release.set()
            t2_release.set()
            thread.join(timeout=5)

        assert not thread.is_alive()
        assert threading.active_count() == baseline  # AC-6: no thread leak

        state = result["state"]
        assert state.status == "cancelled"
        assert state.tasks["trigger"].status == "succeeded"
        assert state.tasks["t1"].status == "succeeded"  # drained + persisted, not killed
        assert state.tasks["t2"].status == "succeeded"
        assert state.tasks.get("later") is None or state.tasks["later"].status == "pending"
        run_id = state.run_id

        # Resume must not re-run trigger/t1/t2 (all actually succeeded during
        # the drain); only `later` (never admitted) runs. Trap: if resume
        # incorrectly re-dispatched any of the three, this executor fails them.
        resume_executor = FakeExecutor(behaviors={"trigger": "fail", "t1": "fail", "t2": "fail"})
        orch2 = Orchestrator(resume_executor, store, rs_store, max_parallel=3)
        existing = rs_store.load(run_id)
        existing = rs_store.prepare_resume(existing, wf)
        for tid in ("trigger", "t1", "t2"):
            assert existing.tasks[tid].status == "succeeded"

        state2 = orch2.run(wf, _fake_reposets(str(tmp_path)), _fake_agents(), run_state=existing)
        assert state2.status == "succeeded"
        assert state2.tasks["later"].status == "succeeded"
