"""Orchestration engine — drives a workflow DAG to completion."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from .artifacts import ArtifactStore, read_gate, read_manifest, read_routes, read_task_manifest
from .breakers import apply_breaker_extension, evaluate_breakers, record_trip
from .budget import BudgetDecision, BudgetManager
from .dag import build_dag, compute_cones
from .errors import ControlFileError, GateError, InjectionError
from .estimator import TokenEstimator
from .executors.base import Executor
from .logging_setup import attach_run_handler, detach_run_handler, get_run_logger
from .models import (
    BUILTIN_BUDGET_EXHAUSTED,
    BUILTIN_BUDGET_UNSATISFIABLE,
    BUILTIN_QUOTA_MAX_WAIT,
    DEFAULT_MAX_EXTENSIONS_PER_BREAKER,
    DEFAULT_MAX_HEAL_RETRIES_PER_TASK,
    DEFAULT_MAX_MONITOR_CALLS_PER_RUN,
    DEFAULT_QUOTA_MAX_WAIT_SECONDS,
    DEFAULT_QUOTA_POLL_SECONDS,
    CircuitBreakerSpec,
    EstimatorConfig,
    LoopSpec,
    MonitorDecisionRecord,
    RouterSpec,
    RunState,
    TaskContext,
    TaskResult,
    TaskRunState,
    TaskSpec,
    WorkflowSpec,
    count_monitor_breaker_extensions,
    count_monitor_calls_made,
    count_monitor_heal_retries,
)
from .monitoring import (
    SAFE_DEFAULT_BREAKER_VERDICT,
    SAFE_DEFAULT_HEAL_VERDICT,
    BreakerTripSummary,
    BreakerVerdict,
    HealVerdict,
    Monitor,
    RuleBasedMonitor,
    build_task_failure_summary,
)
from .runstate import RunStateStore

# Valid origin values for injected/loop tasks
_TaskOrigin = Literal["static", "injected", "loop"]

logger = logging.getLogger(__name__)


def _no_cancel() -> bool:
    return False


_NO_CANCEL: Callable[[], bool] = _no_cancel


class Orchestrator:
    """Drives a WorkflowSpec DAG to completion.

    Design invariants:
    - Engine never reads artifact or instruction file contents (NFR-1).
    - All artifact access goes through ArtifactStore.resolve()/exists() only.
    - Injectable sleeper and cancel_fn for deterministic testing.

    Parameters
    ----------
    executor:
        Executor implementation (ClaudeCliExecutor in production, FakeExecutor in tests).
    artifact_store:
        Path resolution + existence checks.
    runstate_store:
        Persist and load RunState.
    sleeper:
        Injectable sleep function (default: time.sleep).
    cancel_fn:
        Returns True when the run should be cancelled (default: never).
    budget_manager:
        Optional token budget manager; when None, budget enforcement is skipped.
    estimator:
        Optional token estimator paired with budget_manager; when None, budget gate is skipped.
    clock:
        Injectable clock returning the current UTC datetime (default: datetime.now(UTC)).
    quota_max_wait_seconds:
        Maximum total seconds ao will wait across consecutive quota-exhaustion events before
        giving up (default: DEFAULT_QUOTA_MAX_WAIT_SECONDS).  The timer resets after each
        successful task — it tracks only the current exhaustion episode.
    quota_poll_seconds:
        How long to sleep between quota-exhaustion re-run attempts
        (default: DEFAULT_QUOTA_POLL_SECONDS).
    monitor:
        Agent-based monitoring/self-healing implementation (E-XyfjuZ). Defaults to a fresh
        `RuleBasedMonitor()` (deterministic, zero-cost) when None — recommend-mode breakers
        and self-heal both need SOME monitor available so a workflow that declares
        `mode: "recommend"` gets consistent behavior regardless of whether the caller wired
        one up explicitly. Only ever consulted for `mode: "recommend"` breaker trips
        (Consult Point A) and, when `self_heal_enabled`, task failures (Consult Point B) --
        hard-mode breakers and disabled self-heal are completely unaffected (byte-identical).
    max_extensions_per_breaker:
        Cap on monitor-driven extensions per breaker id this run (default
        DEFAULT_MAX_EXTENSIONS_PER_BREAKER). Enforced centrally here, never trusted to the
        Monitor implementation — bound exhausted halts regardless of the monitor's answer.
        Tracked via `count_monitor_breaker_extensions(state, breaker_id)` (derived from
        `RunState.monitor_decisions`, D9) — independent of the unbounded, operator-driven
        `ao resume --extend-breaker` mechanism (E-3JTmVu), which this never touches.
    max_monitor_calls_per_run:
        Cap on total ACTUAL monitor consults this run, shared across both consult points
        (default DEFAULT_MAX_MONITOR_CALLS_PER_RUN). Exhausting it falls back to the safe
        default (halt / accept_failure) without calling the monitor again.
    self_heal_enabled:
        Opt-in switch for Consult Point B (task-failure self-healing, default False —
        byte-identical to today when unset). When True, a task that settles `"failed"`
        after exhausting its `RetryPolicy` is offered ONE bounded extra retry via
        `self._monitor.decide_task_failure(...)` before the run gives up on it. Scoped
        strictly to `result.status == "failed"` — `timed_out`/`cancelled` are never healed
        (deliberate MVP boundary).
    max_heal_retries_per_task:
        Cap on heal retries per task id this run (default
        DEFAULT_MAX_HEAL_RETRIES_PER_TASK). Enforced centrally here via
        `count_monitor_heal_retries(state, task_id)` (derived from
        `RunState.monitor_decisions`, D9) — heal retries never consume
        `RetryPolicy.max_attempts` accounting (a completely separate counter).
    """

    def __init__(
        self,
        executor: Executor,
        artifact_store: ArtifactStore,
        runstate_store: RunStateStore,
        sleeper: Callable[[float], None] = time.sleep,
        cancel_fn: Callable[[], bool] = _NO_CANCEL,
        budget_manager: BudgetManager | None = None,
        estimator: TokenEstimator | None = None,
        clock: Callable[[], datetime] | None = None,
        quota_max_wait_seconds: float = DEFAULT_QUOTA_MAX_WAIT_SECONDS,
        quota_poll_seconds: float = DEFAULT_QUOTA_POLL_SECONDS,
        monitor: Monitor | None = None,
        max_extensions_per_breaker: int = DEFAULT_MAX_EXTENSIONS_PER_BREAKER,
        max_monitor_calls_per_run: int = DEFAULT_MAX_MONITOR_CALLS_PER_RUN,
        self_heal_enabled: bool = False,
        max_heal_retries_per_task: int = DEFAULT_MAX_HEAL_RETRIES_PER_TASK,
    ) -> None:
        self._executor = executor
        self._store = artifact_store
        self._runstate = runstate_store
        self._sleeper = sleeper
        self._cancel_fn = cancel_fn
        self._budget_manager = budget_manager
        self._estimator = estimator
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self._quota_max_wait_seconds = quota_max_wait_seconds
        self._quota_poll_seconds = quota_poll_seconds
        # Agent-based monitoring & self-healing (E-XyfjuZ). Defaults to a fresh
        # RuleBasedMonitor() (never a shared module-level instance) so each Orchestrator
        # owns an independent, stateless-but-distinct monitor.
        self._monitor: Monitor = monitor if monitor is not None else RuleBasedMonitor()
        self._max_extensions_per_breaker = max_extensions_per_breaker
        self._max_monitor_calls_per_run = max_monitor_calls_per_run
        self._self_heal_enabled = self_heal_enabled
        self._max_heal_retries_per_task = max_heal_retries_per_task

    def run(
        self,
        workflow: WorkflowSpec,
        reposets: dict,
        agents: dict,
        run_state: RunState | None = None,
    ) -> RunState:
        """Execute *workflow* to completion and return the final RunState.

        Parameters
        ----------
        workflow:
            Parsed and cross-validated WorkflowSpec.
        reposets:
            Dict of repo_set_id -> RepoSet loaded from config.
        agents:
            Dict of agent_id -> AgentSpec loaded from config.
        run_state:
            Existing RunState to resume from; if None, a new run is created.
        """
        # Validate DAG structure (raises CycleError on cycle)
        graph = build_dag(workflow)
        order = graph.topological_order()

        state = run_state or self._runstate.new_run(workflow)
        self._runstate.save(state)

        # Derive run directory: <workspace>/.orchestrator/runs/<run_id>
        # The runstate store root is <workspace>/.orchestrator/runs — go up two
        # levels from the state.json path to get the workspace.
        run_dir = str(self._runstate._path(state.run_id).parent)
        log_path = os.path.join(run_dir, "run.log")

        # Attach per-run structured log handler (FR-2, FR-3).
        # Detached in finally so it is removed even if run() raises.
        attach_run_handler(state.run_id, log_path)
        run_log = get_run_logger(state.run_id)
        run_log.info("run.start", extra={"event": "run.start", "workflow_id": workflow.id})

        try:
            repo_set = reposets[workflow.repo_set]
            repo_paths = {r.id: self._store.resolve(r.path) for r in repo_set.repos}

            # Merge any previously injected tasks back into the workflow before building
            # the DAG (handles the case where run_state comes from prepare_resume and
            # injected tasks were already merged there, but also guards a direct re-use
            # of a RunState that skipped prepare_resume).
            # Note: prepare_resume handles the canonical resume path; this guard ensures
            # the engine is always safe even if called with a raw loaded state.
            existing_ids = {t.id for t in workflow.tasks}
            if run_state and run_state.injected_tasks:
                for inj in run_state.injected_tasks:
                    if inj.id not in existing_ids:
                        workflow.tasks.append(inj)
                        existing_ids.add(inj.id)

            graph = build_dag(workflow)
            order = graph.topological_order()

            # Route cones/membership (LLD §4.3, T-m2h5t7): computed ONCE here and
            # cached for the whole run() call — never recomputed on injection.
            # Injected task ids are new and never appear in a pre-computed cone;
            # they inherit their emitter's route instead (§5.5, R4). Empty when
            # workflow.branches is empty (the common/pre-routing case).
            cones, membership = compute_cones(workflow, graph)

            # Terminal-success set: skip re-running tasks that already succeeded
            done: set[str] = {
                tid for tid, ts in state.tasks.items() if ts.status in ("succeeded", "skipped")
            }

            # Re-entrant cursor (ADR-005): drives execution over a recomputable order.
            # After injection, order is rebuilt and cursor is reset to the first undone task.
            cursor = 0
            failed = False
            # Tracks the epoch when the current quota-exhaustion episode began.
            # Resets to None after any task succeeds (new exhaustion → fresh max_wait window).
            _quota_exhausted_since: float | None = None

            while cursor < len(order):
                if self._cancel_fn():
                    run_log.info(
                        "Cancellation requested; halting",
                        extra={"event": "run.cancelled"},
                    )
                    state.status = "cancelled"
                    failed = True
                    break

                tid = order[cursor]
                cursor += 1

                # Not-taken skip (LLD §5.3): a task on an unselected route never
                # dispatches, never bills. Checked ahead of `done` (which stays
                # succeeded/skipped-only, §5.6) so the two sets never conflate.
                ts0 = state.tasks.get(tid)
                if ts0 is not None and ts0.status == "not_taken":
                    continue

                if tid in done:
                    continue

                task = workflow.task(tid)
                task_log = get_run_logger(state.run_id, tid)

                # Resume / idempotency: skip completed tasks
                if self._runstate.should_skip(task, state):
                    task_log.info(
                        "Skipping task (already succeeded with outputs present)",
                        extra={"event": "task.skip"},
                    )
                    # Mutate in-place to preserve persisted fields (e.g. dynamic_outputs)
                    ts = state.tasks.setdefault(tid, TaskRunState())
                    ts.status = "skipped"
                    done.add(tid)
                    self._runstate.save(state)
                    continue

                # Join handling (LLD §5.4): a convergence task with a not_taken
                # dependency propagates not_taken (join="all") or is skipped only
                # when EVERY effective dependency is not_taken (join="any").
                # Evaluated before dispatch so a not_taken task never reaches the
                # budget gate or the executor.
                if self._apply_join(task, state, membership, graph.producer_of) == "not_taken":
                    self._runstate.save(state)
                    continue

                # Check required inputs exist. join="any" tasks relax this check
                # (§5.4a): an input whose SOLE producer is not_taken never gets
                # written and must not fail the task — apply_join already proved
                # at least one other effective dependency is live.
                if task.join == "any":
                    optional_inputs: set[str] = set()
                    for inp in task.inputs:
                        producer = graph.producer_of(inp)
                        if (
                            producer is not None
                            and state.tasks.get(producer) is not None
                            and state.tasks[producer].status == "not_taken"
                        ):
                            optional_inputs.add(inp)
                    missing = [
                        inp
                        for inp in task.inputs
                        if inp not in optional_inputs and not self._store.exists(inp)
                    ]
                else:
                    missing = [inp for inp in task.inputs if not self._store.exists(inp)]
                if missing:
                    task_log.error(
                        "Missing required inputs: %s",
                        missing,
                        extra={"event": "task.fail", "reason": "missing_inputs"},
                    )
                    state.tasks[tid] = TaskRunState(status="failed")
                    self._runstate.save(state)
                    state.status = "failed"
                    failed = True
                    break

                # Collect dynamic outputs from all upstream tasks declared in depends_on
                dynamic_input_paths: list[str] = []
                for dep_id in task.depends_on:
                    dep_ts = state.tasks.get(dep_id)
                    if dep_ts and dep_ts.dynamic_outputs:
                        dynamic_input_paths.extend(dep_ts.dynamic_outputs)

                # _estimate is declared at task-block scope so the reconcile block can
                # reference it regardless of whether the budget gate ran (T-algywf, NFR-3).
                _estimate = 0

                # ---- Budget gate (T-algywf, FR-1..FR-3, FR-4) ----
                if self._budget_manager is not None and self._estimator is not None:
                    _est_agent = agents[task.agent]
                    _est_instr = self._store.resolve(task.instruction)
                    _est_inputs = [self._store.resolve(p) for p in task.inputs]
                    _est_cfg = (
                        workflow.budget.estimator if workflow.budget else None
                    ) or EstimatorConfig()
                    _est_ctx = TaskContext(
                        run_id=state.run_id,
                        task_id=tid,
                        agent=_est_agent,
                        instruction_path=_est_instr,
                        input_paths=_est_inputs,
                        output_paths=[self._store.resolve(p) for p in task.outputs],
                        dynamic_input_paths=dynamic_input_paths,
                        repo_paths=repo_paths,
                        timeout_seconds=task.timeout_seconds or workflow.defaults.timeout_seconds,
                    )
                    _estimate = self._estimator.estimate(_est_ctx, _est_cfg)

                    # Resume double-charge guard (R2, NFR-3): if this task was charged but
                    # not yet reconciled in a previous run, reverse the stale estimate before
                    # re-gating.
                    if (
                        tid in state.budget_counters.charged_estimate
                        and tid not in state.budget_counters.reconciled_tasks
                    ):
                        self._budget_manager.reverse_estimate(tid, state.budget_counters)
                        run_log.info(
                            "Reversed stale estimate for task %s on resume",
                            tid,
                            extra={"event": "budget.resume_reverse", "task_id": tid},
                        )

                    # Gate loop: admit or block; handles wait-retry on window roll
                    _gate_admitted = False
                    while not _gate_admitted:
                        _decision = self._budget_manager.gate(tid, _estimate, state.budget_counters)
                        if _decision.admit:
                            _gate_admitted = True
                            break

                        task_log.warning(
                            "Budget gate blocked task %s: blocked_by=%s next_available=%s",
                            tid,
                            _decision.blocked_by,
                            _decision.next_available_epoch,
                            extra={
                                "event": "budget.gate_block",
                                "task_id": tid,
                                "blocked_by": _decision.blocked_by,
                                "next_available_epoch": _decision.next_available_epoch,
                                "estimate": _estimate,
                            },
                        )

                        # Unsatisfiable: estimate exceeds the entire budget/window — would wait
                        # forever; stop immediately.
                        if self._is_unsatisfiable(_estimate, _decision):
                            run_log.error(
                                "Task %s estimate %d exceeds %s limit — unsatisfiable; stopping",
                                tid,
                                _estimate,
                                _decision.blocked_by,
                                extra={
                                    "event": "budget.exhausted",
                                    "task_id": tid,
                                    "blocked_by": _decision.blocked_by,
                                },
                            )
                            # Re-frame onto trip->record->act (T-r3j9b6, LLD §8.2): additive
                            # only -- the event/status/break above are unchanged (ADR-RC-003).
                            record_trip(
                                state=state,
                                breaker_id=BUILTIN_BUDGET_UNSATISFIABLE,
                                condition="projected_cost_exceeds",
                                action="fail",
                                detail={
                                    "task_id": tid,
                                    "estimate": _estimate,
                                    "blocked_by": _decision.blocked_by,
                                },
                                clock=self._clock,
                                run_log=run_log,
                            )
                            state.status = "failed"
                            failed = True
                            break

                        _on_exhaustion = (
                            workflow.budget.on_exhaustion if workflow.budget else "stop"
                        )

                        if _on_exhaustion == "stop":
                            run_log.warning(
                                "Budget exhausted; stopping run",
                                extra={
                                    "event": "budget.exhausted",
                                    "task_id": tid,
                                    "blocked_by": _decision.blocked_by,
                                    "next_available_epoch": _decision.next_available_epoch,
                                },
                            )
                            # Re-frame onto trip->record->act (T-r3j9b6, LLD §8.2): additive
                            # only -- the event/status/break above are unchanged (ADR-RC-003).
                            # Condition name reflects which limit blocked admission
                            # (_decision.blocked_by), not a single hardcoded string.
                            _budget_condition = (
                                "total_tokens" if _decision.blocked_by == "total" else "rate_window"
                            )
                            record_trip(
                                state=state,
                                breaker_id=BUILTIN_BUDGET_EXHAUSTED,
                                condition=_budget_condition,
                                action="fail",
                                detail={
                                    "task_id": tid,
                                    "blocked_by": _decision.blocked_by,
                                    "next_available_epoch": _decision.next_available_epoch,
                                },
                                clock=self._clock,
                                run_log=run_log,
                            )
                            state.status = "failed"
                            failed = True
                            break
                        else:  # wait
                            _next = _decision.next_available_epoch or (
                                self._clock().timestamp() + 1
                            )
                            _sleep_secs = max(0.0, _next - self._clock().timestamp())
                            run_log.info(
                                "Budget wait: sleeping %.1f seconds until %.3f",
                                _sleep_secs,
                                _next,
                                extra={
                                    "event": "budget.wait",
                                    "task_id": tid,
                                    "sleep_seconds": _sleep_secs,
                                    "next_available_epoch": _next,
                                },
                            )
                            self._sleeper(_sleep_secs)
                            if self._cancel_fn():
                                run_log.info(
                                    "Cancelled during budget wait",
                                    extra={"event": "run.cancelled"},
                                )
                                state.status = "cancelled"
                                failed = True
                                break
                            run_log.info(
                                "Budget wait ended; re-gating task %s",
                                tid,
                                extra={"event": "budget.resume", "task_id": tid},
                            )
                            # Loop back to re-gate (window should have rolled)

                    if failed:
                        self._runstate.save(state)
                        break

                    # Charge the estimate (admitted)
                    self._budget_manager.charge_estimate(tid, _estimate, state.budget_counters)
                    self._runstate.save(state)
                    task_log.info(
                        "Budget charged estimate %d for task %s",
                        _estimate,
                        tid,
                        extra={
                            "event": "budget.charge",
                            "task_id": tid,
                            "estimate": _estimate,
                            "consumed_tokens": state.budget_counters.consumed_tokens,
                        },
                    )

                # Mark as running
                ts = state.tasks.setdefault(tid, TaskRunState())
                ts.status = "running"
                # Only set started_at on the task's FIRST dispatch (E-3JTmVu FR-1 fix): the
                # quota-exhaustion/429/budget-wait paths below reset ts.status to "pending" and
                # loop back to this same line (`cursor -= 1; continue`) to redispatch the SAME
                # task after a real sleep -- without this guard, that redispatch used to
                # overwrite started_at, silently excluding the wait from
                # `run_active_seconds`'s (ended_at - started_at) sum even though those waits are
                # genuine engine-busy/blocked time on this task, not an operator-initiated stop.
                # Safe: started_at has exactly one writer (here) and prepare_resume already
                # hands a fresh TaskRunState() (started_at=None) to any task reset for `ao
                # resume`, so a resumed dispatch still gets its own fresh started_at.
                if ts.started_at is None:
                    ts.started_at = datetime.now(UTC).isoformat()
                self._runstate.save(state)
                task_log.info("Task started", extra={"event": "task.start"})

                # Resolve emit_tasks task manifest path (Area 2)
                resolved_task_manifest_path: str | None = None
                if task.emit_tasks and task.task_manifest_path:
                    resolved_task_manifest_path = self._store.resolve(task.task_manifest_path)

                # Resolve gate output path for loop gate tasks (Area 2)
                gate_loop = self._loop_for_gate(workflow, tid)
                resolved_gate_output_path: str | None = None
                if gate_loop is not None:
                    cur_iter = state.loop_iterations.get(gate_loop.id, 1)
                    raw_gate_path = self._gate_path_for_iter(gate_loop, cur_iter)
                    resolved_gate_output_path = self._store.resolve(raw_gate_path)

                # Execute with retries
                result = self._run_with_retries(
                    task,
                    workflow,
                    agents,
                    repo_paths,
                    state,
                    dynamic_input_paths,
                    task_manifest_path=resolved_task_manifest_path,
                    gate_output_path=resolved_gate_output_path,
                )

                # ---- Claude quota exhaustion route (distinct from provider 429) ----
                if result.claude_quota_exhausted:
                    # Reverse any estimate charged for this task before re-queuing.
                    if self._budget_manager is not None:
                        self._budget_manager.reverse_estimate(tid, state.budget_counters)

                    now = self._clock().timestamp()
                    if _quota_exhausted_since is None:
                        _quota_exhausted_since = now

                    elapsed = now - _quota_exhausted_since
                    remaining = self._quota_max_wait_seconds - elapsed

                    if remaining <= 0:
                        run_log.error(
                            "Claude quota exhaustion max_wait exceeded "
                            "(%.0f s elapsed, limit=%.0f s); stopping run",
                            elapsed,
                            self._quota_max_wait_seconds,
                            extra={
                                "event": "quota.max_wait_exceeded",
                                "task_id": tid,
                                "elapsed_seconds": elapsed,
                                "max_wait_seconds": self._quota_max_wait_seconds,
                            },
                        )
                        # Re-frame onto trip->record->act (T-r3j9b6, LLD §8.2): additive
                        # only -- the event/status/break above are unchanged (ADR-RC-003).
                        record_trip(
                            state=state,
                            breaker_id=BUILTIN_QUOTA_MAX_WAIT,
                            condition="quota_exhaustion_wait_exceeded",
                            action="fail",
                            detail={
                                "task_id": tid,
                                "elapsed_seconds": elapsed,
                                "max_wait_seconds": self._quota_max_wait_seconds,
                            },
                            clock=self._clock,
                            run_log=run_log,
                        )
                        state.status = "failed"
                        failed = True
                        self._runstate.save(state)
                        break

                    sleep_secs = min(self._quota_poll_seconds, remaining)
                    run_log.info(
                        "Claude quota exhausted on task %s; waiting %.0f s (elapsed=%.0f/max=%.0f)",
                        tid,
                        sleep_secs,
                        elapsed,
                        self._quota_max_wait_seconds,
                        extra={
                            "event": "quota.wait",
                            "task_id": tid,
                            "sleep_seconds": sleep_secs,
                            "elapsed_seconds": elapsed,
                            "max_wait_seconds": self._quota_max_wait_seconds,
                        },
                    )
                    self._sleeper(sleep_secs)

                    if self._cancel_fn():
                        run_log.info(
                            "Cancelled during quota wait",
                            extra={"event": "run.cancelled"},
                        )
                        state.status = "cancelled"
                        failed = True
                        self._runstate.save(state)
                        break

                    run_log.info(
                        "Quota wait ended; re-running task %s",
                        tid,
                        extra={"event": "quota.resume", "task_id": tid},
                    )
                    # Reset task to pending so it re-executes cleanly.
                    ts.status = "pending"
                    cursor -= 1
                    self._runstate.save(state)
                    continue  # skip budget reconcile + normal outcome handling

                # ---- Budget reconcile / 429 route (T-algywf, FR-4, FR-5, FR-8) ----
                if self._budget_manager is not None and self._estimator is not None:
                    if result.provider_rate_limited:
                        # Provider 429: reverse the estimate (task will re-run) then stop or wait
                        self._budget_manager.reverse_estimate(tid, state.budget_counters)
                        _429_decision = self._budget_manager.on_provider_429(
                            result.provider_retry_after_epoch, state.budget_counters
                        )
                        run_log.warning(
                            "Provider 429 on task %s; retry_after=%.3f",
                            tid,
                            _429_decision.next_available_epoch or 0,
                            extra={
                                "event": "budget.provider_429",
                                "task_id": tid,
                                "next_available_epoch": _429_decision.next_available_epoch,
                            },
                        )
                        _on_exhaustion_429 = (
                            workflow.budget.on_exhaustion if workflow.budget else "stop"
                        )
                        if (
                            _on_exhaustion_429 == "wait"
                            and _429_decision.next_available_epoch is not None
                        ):
                            _sleep_secs_429 = max(
                                0.0,
                                _429_decision.next_available_epoch - self._clock().timestamp(),
                            )
                            run_log.info(
                                "Waiting %.1f seconds for provider rate limit to reset",
                                _sleep_secs_429,
                                extra={
                                    "event": "budget.wait",
                                    "task_id": tid,
                                    "sleep_seconds": _sleep_secs_429,
                                },
                            )
                            self._sleeper(_sleep_secs_429)
                            if self._cancel_fn():
                                state.status = "cancelled"
                                failed = True
                                self._runstate.save(state)
                                break
                            run_log.info(
                                "Provider rate limit wait ended; re-running task %s",
                                tid,
                                extra={"event": "budget.resume", "task_id": tid},
                            )
                            # Re-run the task: step cursor back and continue the while loop
                            cursor -= 1
                            self._runstate.save(state)
                            continue
                        else:
                            # stop (default) or no next_available — end the run
                            run_log.warning(
                                "Stopping run due to provider rate limit",
                                extra={"event": "budget.exhausted", "task_id": tid},
                            )
                            # Re-frame onto trip->record->act (T-r3j9b6, LLD §8.2): additive
                            # only -- the event/status/break above are unchanged (ADR-RC-003).
                            # Shares BUILTIN_BUDGET_EXHAUSTED with the gate-path stop above
                            # (LLD §8.1 groups both under one builtin id); the condition name
                            # keeps the two sites distinguishable in tripped_breakers.
                            record_trip(
                                state=state,
                                breaker_id=BUILTIN_BUDGET_EXHAUSTED,
                                condition="provider_429",
                                action="fail",
                                detail={
                                    "task_id": tid,
                                    "next_available_epoch": _429_decision.next_available_epoch,
                                },
                                clock=self._clock,
                                run_log=run_log,
                            )
                            state.status = "failed"
                            failed = True
                            self._runstate.save(state)
                            break
                    else:
                        # Normal reconcile: replace estimate with actuals or keep estimate
                        if result.actuals_available:
                            _actual = self._sum_actuals(result)
                        else:
                            # Fallback (FR-5): keep the estimate already stored in
                            # charged_estimate (charge_estimate stored it; reconcile will pop it)
                            _actual = state.budget_counters.charged_estimate.get(tid, 0)
                        self._budget_manager.reconcile(tid, _actual, state.budget_counters)
                        task_log.info(
                            "Budget reconciled task %s: actual=%d estimate_delta=%d",
                            tid,
                            _actual,
                            _actual - _estimate,
                            extra={
                                "event": "budget.reconcile",
                                "task_id": tid,
                                "actual": _actual,
                                "consumed_tokens": state.budget_counters.consumed_tokens,
                            },
                        )
                        self._runstate.save(state)

                # ---- Consult Point B: task-failure self-healing (E-XyfjuZ, opt-in) ----
                # Design Decision D4: placed BEFORE ts.attempts/ts.ended_at are ever
                # touched for this attempt -- mirrors exactly where the quota-exhaustion
                # and provider-429 routes above already short-circuit on result.* fields,
                # before any TaskRunState settle-time mutation. A successfully healed
                # failure therefore never sets ts.status="failed", never reaches
                # evaluate_breakers, and never touches ts.started_at (the E-3JTmVu
                # single-writer guard is untouched -- this block only ever resets
                # ts.status, exactly like the quota-exhaustion requeue above). Scoped
                # strictly to result.status == "failed": timed_out/cancelled are a
                # deliberate MVP boundary, never healed.
                if self._self_heal_enabled and result.status == "failed":
                    _heal_verdict = self._consult_task_failure_heal(tid, result, state, run_log)
                    if _heal_verdict is not None and _heal_verdict.decision == "retry":
                        # Reviewer-flagged Critical fix: this failed cycle's REAL actuals
                        # (a failed attempt can still report actuals_available -- tokens/
                        # cost were genuinely spent before the error) would otherwise be
                        # silently discarded by the `continue` below, since this cycle
                        # never reaches the settle-time cumulative block further down.
                        # Accumulate now (mirrors _run_with_retries' own cum_* pattern one
                        # level up, across heal cycles instead of within one call) --
                        # `+=` because a later heal cycle (if max_heal_retries_per_task > 1)
                        # must not clobber an earlier one's already-accumulated actuals.
                        # This is the same bug class E-9h3m7k fixed for retries WITHIN one
                        # _run_with_retries call; self-heal's cross-call redispatch needed
                        # the identical treatment, caught by a late-gate reviewer pass.
                        if result.actuals_available:
                            ts.cumulative_input_tokens += result.input_tokens or 0
                            ts.cumulative_output_tokens += result.output_tokens or 0
                            ts.cumulative_cache_creation_input_tokens += (
                                result.cache_creation_input_tokens or 0
                            )
                            ts.cumulative_cache_read_input_tokens += (
                                result.cache_read_input_tokens or 0
                            )
                            ts.cumulative_cost_usd += result.cost_usd or 0.0
                        self._sleeper(_heal_verdict.wait_seconds)
                        if self._cancel_fn():
                            run_log.info(
                                "Cancelled during self-heal wait",
                                extra={"event": "run.cancelled"},
                            )
                            state.status = "cancelled"
                            failed = True
                            self._runstate.save(state)
                            break
                        ts.status = "pending"
                        run_log.info(
                            "Self-heal retry: re-running task %s after %.1f s",
                            tid,
                            _heal_verdict.wait_seconds,
                            extra={
                                "event": "monitor.heal_retry",
                                "task_id": tid,
                                "wait_seconds": _heal_verdict.wait_seconds,
                            },
                        )
                        cursor -= 1
                        self._runstate.save(state)
                        continue
                    # else: bound/cap exhausted (_heal_verdict is None) or an explicit
                    # accept_failure verdict -- fall through to the existing, unmodified
                    # settle/outcome handling below exactly as if self-heal were disabled.

                ts.attempts = result.attempts
                ts.ended_at = datetime.now(UTC).isoformat()
                # Record captured output path (FR-5); engine never reads the files.
                if result.output_artifact_path:
                    ts.output_artifact_path = result.output_artifact_path
                # Cumulative actual usage across every attempt (E-9h3m7k FR-2) — result's
                # token/cost fields already sum all attempts (_run_with_retries). `+=` (not
                # `=`) so a prior, healed-and-discarded cycle's already-accumulated actuals
                # (added above, in the Consult Point B retry branch) are preserved rather
                # than clobbered -- safe for every other caller too: this line runs at most
                # once per dispatch outside of self-heal, and `prepare_resume` hands any
                # re-dispatched task a fresh `TaskRunState()` (cumulative_* defaulted to 0),
                # so `+=` is byte-identical to `=` whenever nothing was accumulated first.
                if result.actuals_available:
                    ts.cumulative_input_tokens += result.input_tokens or 0
                    ts.cumulative_output_tokens += result.output_tokens or 0
                    ts.cumulative_cache_creation_input_tokens += (
                        result.cache_creation_input_tokens or 0
                    )
                    ts.cumulative_cache_read_input_tokens += result.cache_read_input_tokens or 0
                    ts.cumulative_cost_usd += result.cost_usd or 0.0

                if result.status == "succeeded":
                    # Verify declared outputs were actually produced
                    missing_outputs = [o for o in task.outputs if not self._store.exists(o)]
                    if missing_outputs:
                        task_log.error(
                            "Task succeeded but declared outputs missing: %s",
                            missing_outputs,
                            extra={"event": "task.fail", "reason": "missing_outputs"},
                        )
                        ts.status = "failed"
                        ts.outputs_present = False
                    else:
                        ts.status = "succeeded"
                        ts.outputs_present = True
                        # Read output manifest if declared; failure fails the task
                        if task.output_manifest:
                            try:
                                ts.dynamic_outputs = read_manifest(
                                    self._store, task.output_manifest
                                )
                                task_log.info(
                                    "Loaded %d dynamic outputs from manifest",
                                    len(ts.dynamic_outputs),
                                    extra={"event": "task.manifest_loaded"},
                                )
                            except ValueError as exc:
                                task_log.error(
                                    "output_manifest read failed: %s",
                                    exc,
                                    extra={"event": "task.fail", "reason": "manifest_error"},
                                )
                                ts.status = "failed"
                                ts.outputs_present = False
                else:
                    ts.status = result.status

                if ts.status == "succeeded":
                    task_log.info(
                        "Task succeeded",
                        extra={
                            "event": "task.end",
                            "status": "succeeded",
                            "exit_code": result.exit_code,
                        },
                    )
                    done.add(tid)
                    _quota_exhausted_since = None  # successful task resets the quota-wait timer

                    # ---- Router-success hook (T-m2h5t7, LLD §5.2) ----
                    # Runs BEFORE the circuit-breaker evaluation below so
                    # route_decisions/not_taken are settled before any breaker
                    # inspects RunState.
                    router = self._router_for_task(workflow, tid)
                    if router is not None:
                        if self._on_router_success(router, state, cones, run_log) == "failed":
                            failed = True
                            break
                else:
                    task_log.warning(
                        "Task ended with status %s",
                        ts.status,
                        extra={
                            "event": "task.end",
                            "status": ts.status,
                            "exit_code": result.exit_code,
                        },
                    )

                self._runstate.save(state)

                # ---- Circuit-breaker evaluation (T-x8v4d3, LLD §6.1) ----
                # Task boundary: after outcome handling + save, before the next dispatch.
                # Runs regardless of ts.status (a failure is itself a boundary a breaker may
                # react to, e.g. the future task_failures/consecutive_failures conditions).
                # No-op today: workflow.circuit_breakers defaults to [] and no built-ins are
                # wired yet (T-r3j9b6), so the loop body never executes for existing workflows.
                #
                # Consult Point A (E-XyfjuZ, epic doc Design Decisions D1-D3):
                # evaluate_breakers() itself is UNCHANGED here (D2) -- its byte-identical no-op
                # guarantee (no clock() call when there are no specs) and every existing test
                # that calls it directly are preserved. "Which specs newly tripped this
                # boundary" is instead derived AT THE CALL SITE by diffing state.tripped_breakers
                # ids before/after this one call. Built-in re-framed stops (budget/quota/429)
                # can never appear in that diff: each already breaks the run loop via its own
                # record_trip() call earlier in this same iteration, well before
                # evaluate_breakers is ever reached (D1) -- so they are unconditionally hard by
                # construction, never consultable, regardless of any workflow.circuit_breakers
                # declaration.
                _before_tripped_ids = {tb.id for tb in state.tripped_breakers}
                breaker_action = evaluate_breakers(
                    workflow, state, self._clock, self._store, run_log
                )
                if breaker_action is not None:
                    _newly_tripped_ids = {
                        tb.id for tb in state.tripped_breakers if tb.id not in _before_tripped_ids
                    }
                    # Filtering workflow.circuit_breakers (rather than iterating the id set
                    # directly) preserves DECLARED order (D3's "consult in declared order").
                    _newly_tripped_specs = [
                        b for b in workflow.circuit_breakers if b.id in _newly_tripped_ids
                    ]
                    _consultable = (
                        len(_newly_tripped_specs) == len(_newly_tripped_ids)
                        and bool(_newly_tripped_specs)
                        and all(b.mode == "recommend" for b in _newly_tripped_specs)
                    )
                    # No self._runstate.save() happens between evaluate_breakers() recording
                    # the trip(s) above and the consult resolving below (early-gate architect
                    # finding) -- a half-consulted state must never hit disk.
                    _consult_outcome = (
                        self._consult_breaker_trips(_newly_tripped_specs, state, run_log)
                        if _consultable
                        else "halt"
                    )
                    if _consult_outcome == "halt":
                        # fail/stop/pause all land on resumable status="failed" for MVP
                        # (ADR-RC-004); the distinguishing action is preserved in
                        # tripped_breakers[].action.
                        state.status = "failed"
                        failed = True
                        self._runstate.save(state)
                        break
                    # _consult_outcome == "extend": _consult_breaker_trips already applied
                    # apply_breaker_extension (bumping breaker_overrides + un-latching the
                    # tripped record(s)) for every newly-tripped breaker -- fall through to the
                    # rest of the loop body exactly as if breaker_action had been None (today's
                    # no-trip path).
                    self._runstate.save(state)

                if ts.status not in ("succeeded", "skipped"):
                    state.status = "failed"
                    failed = True
                    break

                # ---- Dynamic expansion hooks (Area 2) ----

                # 2a: emit_tasks — read manifest, inject new tasks, rebuild DAG + order
                if task.emit_tasks and ts.status == "succeeded":
                    try:
                        new_specs = read_task_manifest(self._store, task.task_manifest_path)  # type: ignore[arg-type]
                    except ValueError as exc:
                        task_log.error(
                            "task_manifest_path read failed: %s",
                            exc,
                            extra={"event": "task.fail", "reason": "manifest_error"},
                        )
                        ts.status = "failed"
                        ts.outputs_present = False
                        state.status = "failed"
                        self._runstate.save(state)
                        failed = True
                        break
                    try:
                        self._inject(new_specs, workflow, state, origin="injected", route=ts.route)
                    except InjectionError as exc:
                        task_log.error(
                            "Task injection failed: %s",
                            exc,
                            extra={"event": "task.fail", "reason": "injection_error"},
                        )
                        ts.status = "failed"
                        state.status = "failed"
                        self._runstate.save(state)
                        failed = True
                        break
                    graph = build_dag(workflow)
                    order, cursor = self._recompute_order(graph, done)
                    task_log.info(
                        "Injected %d tasks; order recomputed (%d remaining)",
                        len(new_specs),
                        len(order) - cursor,
                        extra={"event": "task.injected"},
                    )
                    self._runstate.save(state)

                # 2b/2c: loop gate — check if a completed task is a gate task
                loop = self._loop_for_gate(workflow, tid)
                if loop is not None and ts.status == "succeeded":
                    cur_iter = state.loop_iterations.get(loop.id, 1)
                    if cur_iter < loop.max_iterations:
                        # Determine gate path for the current iteration
                        gate_path = self._gate_path_for_iter(loop, cur_iter)
                        try:
                            should_cont = read_gate(self._store, gate_path, loop.gate_field)
                        except GateError as exc:
                            task_log.error(
                                "Gate read failed: %s",
                                exc,
                                extra={"event": "task.fail", "reason": "gate_error"},
                            )
                            ts.status = "failed"
                            state.status = "failed"
                            self._runstate.save(state)
                            failed = True
                            break
                        if should_cont:
                            next_iter = cur_iter + 1
                            clones = self._clone_body(loop, next_iter, workflow)
                            try:
                                self._inject(clones, workflow, state, origin="loop", route=ts.route)
                            except InjectionError as exc:
                                task_log.error(
                                    "Loop clone injection failed: %s",
                                    exc,
                                    extra={"event": "task.fail", "reason": "injection_error"},
                                )
                                ts.status = "failed"
                                state.status = "failed"
                                self._runstate.save(state)
                                failed = True
                                break
                            state.loop_iterations[loop.id] = next_iter
                            graph = build_dag(workflow)
                            order, cursor = self._recompute_order(graph, done)
                            run_log.info(
                                "Loop %s starting iteration %d",
                                loop.id,
                                next_iter,
                                extra={
                                    "event": "loop.iterate",
                                    "loop_id": loop.id,
                                    "iteration": next_iter,
                                },
                            )
                            self._runstate.save(state)
                    # else: max_iterations reached or gate says stop — loop ends, proceed

            if not failed and state.status == "running":
                state.status = "succeeded"

            run_log.info(
                "run.end",
                extra={"event": "run.end", "status": state.status},
            )
            self._runstate.save(state)
            return state

        finally:
            detach_run_handler(state.run_id)

    # -------------------------------------------------------------------------
    # Budget helpers (T-algywf)
    # -------------------------------------------------------------------------

    @staticmethod
    def _sum_actuals(result: TaskResult) -> int:
        """Sum all token fields from a TaskResult (input + output + cache fields)."""
        total = 0
        for field in (
            result.input_tokens,
            result.output_tokens,
            result.cache_creation_input_tokens,
            result.cache_read_input_tokens,
        ):
            if field is not None:
                total += field
        return total

    def _is_unsatisfiable(self, estimate: int, decision: BudgetDecision) -> bool:
        """Return True if this estimate can NEVER be admitted (estimate alone exceeds limit).

        This prevents an infinite wait loop when a single task's estimate exceeds the
        entire window budget or total budget.
        """
        if self._budget_manager is None:
            return False
        spec = getattr(self._budget_manager, "_spec", None)
        if spec is None:
            return False
        if decision.blocked_by == "total" and spec.total_tokens is not None:
            return estimate > spec.total_tokens
        if decision.blocked_by == "rate" and spec.rate is not None:
            return estimate > spec.rate.tokens
        return False

    # -------------------------------------------------------------------------
    # Routing helpers (T-m2h5t7, LLD §5)
    # -------------------------------------------------------------------------

    def _router_for_task(self, workflow: WorkflowSpec, task_id: str) -> RouterSpec | None:
        """Return the RouterSpec whose ``router_task_id`` matches *task_id*, if any."""
        for router in workflow.branches:
            if router.router_task_id == task_id:
                return router
        return None

    def _apply_join(
        self,
        task: TaskSpec,
        state: RunState,
        membership: dict[str, set[tuple[str, str]]],
        producer_of: Callable[[str], str | None],
    ) -> str | None:
        """Evaluate join policy pre-dispatch (LLD §5.4).

        Returns ``"not_taken"`` when the task must be skipped without dispatching
        (its join policy says so — see below), else ``None`` (dispatch normally).

        ``membership`` is accepted (unused in the resolution below) to mirror the
        LLD §5.4 signature and leave room for future join diagnostics; resolving
        the join only needs each *effective dependency*'s settled status.

        - ``join == "all"`` (default): any not_taken effective dependency
          propagates not_taken to *task* (a full convergence needs every branch).
        - ``join == "any"``: *task* is not_taken only when EVERY effective
          dependency is not_taken; it dispatches once at least one is live (its
          missing-input check is separately relaxed for not_taken producers,
          §5.4a, at the call site).

        "Effective dependencies" = declared ``depends_on`` plus every inferred
        producer of a declared input not already in ``depends_on`` (mirrors the
        DAG's own inferred-edge rule, ``dag.build_dag``).
        """
        effective_deps: list[str] = list(task.depends_on)
        for inp in task.inputs:
            producer = producer_of(inp)
            if producer is not None and producer not in effective_deps:
                effective_deps.append(producer)

        not_taken_deps = [
            d
            for d in effective_deps
            if state.tasks.get(d) is not None and state.tasks[d].status == "not_taken"
        ]

        if task.join == "all":
            if not_taken_deps:
                ts = state.tasks.setdefault(task.id, TaskRunState())
                ts.status = "not_taken"
                ts.not_taken_reason = "join=all; dep(s) not_taken: " + ",".join(not_taken_deps)
                return "not_taken"
            return None  # topo order already guarantees deps are settled

        # join == "any": dispatch once at least one effective dependency is live.
        live_deps = [d for d in effective_deps if d not in not_taken_deps]
        if not live_deps:
            ts = state.tasks.setdefault(task.id, TaskRunState())
            ts.status = "not_taken"
            ts.not_taken_reason = "join=any; all dep(s) not_taken: " + ",".join(not_taken_deps)
            return "not_taken"
        return None

    def _on_router_success(
        self,
        router: RouterSpec,
        state: RunState,
        cones: dict[str, dict[str, set[str]]],
        run_log: logging.LoggerAdapter,
    ) -> Literal["ok", "failed"]:
        """Router-success hook (LLD §5.2), called once ``router.router_task_id``
        settles ``succeeded``: read the verdict, persist ``route_decisions`` as
        the source of truth, mark every unselected route's exclusive cone
        ``not_taken``, and tag activated tasks' ``route``.

        Returns ``"failed"`` when the run must fail (verdict unreadable, or an
        empty/unknown verdict with no ``default_route``) — ``_route_fail`` has
        already set ``state.status="failed"`` and persisted; the caller sets the
        run loop's ``failed=True`` and breaks (mirrors the loop's existing
        break-on-terminal-failure pattern). Returns ``"ok"`` otherwise.
        """
        try:
            selected_raw = read_routes(self._store, router.verdict_path, router.verdict_field)
        except ControlFileError as exc:
            self._route_fail(state, router, f"verdict unreadable: {exc}", run_log)
            return "failed"

        known = set(router.routes.keys())
        selected = [r for r in selected_raw if r in known]  # drop unknown route ids

        if not selected:
            if router.default_route is not None:
                selected = [router.default_route]
            else:
                self._route_fail(state, router, "empty/unknown verdict, no default_route", run_log)
                return "failed"

        state.route_decisions[router.id] = selected  # source of truth, persisted below

        router_cones = cones.get(router.id, {})
        not_taken_ids: list[str] = []
        for route_id, route_cone in router_cones.items():
            if route_id in selected:
                continue
            for t in route_cone:
                ts = state.tasks.setdefault(t, TaskRunState())
                if ts.status in ("succeeded", "skipped", "failed"):
                    continue  # never override an already-settled task
                ts.status = "not_taken"
                ts.route = f"{router.id}:{route_id}"
                ts.not_taken_reason = (
                    f"router={router.router_task_id} route={route_id} not selected"
                )
                not_taken_ids.append(t)

        for route_id in selected:
            for t in router_cones.get(route_id, set()):
                state.tasks.setdefault(t, TaskRunState()).route = f"{router.id}:{route_id}"

        run_log.info(
            "branch.route",
            extra={
                "event": "branch.route",
                "router_id": router.id,
                "router_task_id": router.router_task_id,
                "selected": selected,
                "not_taken_count": len(not_taken_ids),
            },
        )
        self._runstate.save(state)
        return "ok"

    def _route_fail(
        self,
        state: RunState,
        router: RouterSpec,
        reason: str,
        run_log: logging.LoggerAdapter,
    ) -> None:
        """Validation-style routing failure (LLD §5.2/§5.6) — distinct from a
        circuit breaker. Sets ``state.status="failed"``, emits ``branch.route``
        with an ``error`` field, and persists. The caller is responsible for
        setting the run loop's ``failed=True`` and breaking (mirrors the engine's
        existing break-on-terminal-failure pattern)."""
        state.status = "failed"
        run_log.error(
            "branch.route",
            extra={
                "event": "branch.route",
                "router_id": router.id,
                "router_task_id": router.router_task_id,
                "error": reason,
            },
        )
        self._runstate.save(state)

    # -------------------------------------------------------------------------
    # Monitoring / self-healing helpers (E-XyfjuZ, Consult Point A)
    # -------------------------------------------------------------------------

    def _consult_breaker_trips(
        self,
        newly_tripped_specs: list[CircuitBreakerSpec],
        state: RunState,
        run_log: logging.LoggerAdapter,
    ) -> Literal["extend", "halt"]:
        """Consult Point A (epic doc Design Decisions D1-D3): per newly-tripped
        recommend-mode breaker, in DECLARED order, ask ``self._monitor`` whether to
        extend or halt.

        All-or-nothing (D3): if EVERY consulted breaker resolves to ``"extend"``, every
        extension is applied (via the existing ``apply_breaker_extension``, un-latching
        each breaker) and ``"extend"`` is returned so the caller lets the run continue. If
        ANY breaker resolves to ``"halt"`` — an explicit monitor answer, or a
        bound/cap already exhausted — NO extension is applied at all and ``"halt"`` is
        returned (today's halt path). The caller is responsible for persisting ``state``
        — this method mutates it in-memory only, mirroring
        ``evaluate_breakers``/``apply_breaker_extension``'s own persistence convention.

        Defense-in-depth: every *newly_tripped_specs* entry must be ``mode="recommend"``
        (the caller already filters for this before calling). Raised (not an ``assert``,
        which ``python -O`` strips) because built-in hard stops must NEVER reach this
        method by construction (D1) — this is enforcement in code, not convention.

        Reviewer-flagged edge case: an EMPTY *newly_tripped_specs* would make the later
        ``all(...)`` vacuously ``True`` (Python's `all([])` is `True`), which would
        wrongly return ``"extend"``. The current call site never invokes this with an
        empty list (it only calls in when `_newly_tripped_specs` is non-empty), but this
        guard makes that precondition explicit and safe for any future caller.
        """
        if not newly_tripped_specs:
            return "halt"
        if any(b.mode != "recommend" for b in newly_tripped_specs):
            raise AssertionError(
                "_consult_breaker_trips must only ever be called with recommend-mode breakers"
            )

        decisions: list[tuple[CircuitBreakerSpec, BreakerVerdict | None]] = []
        for spec in newly_tripped_specs:
            prior = count_monitor_breaker_extensions(state, spec.id)
            if prior >= self._max_extensions_per_breaker:
                # Bound exhausted -> forced halt contribution WITHOUT consulting at all
                # (never even calls the monitor, per the epic brief's "bound exhausted ->
                # halt regardless of monitor answer").
                decisions.append((spec, None))
                continue
            if count_monitor_calls_made(state) >= self._max_monitor_calls_per_run:
                run_log.warning(
                    "monitor call cap reached; falling back to the safe default (halt)",
                    extra={
                        "event": "monitor.cap_exceeded",
                        "consult_point": "breaker_trip",
                        "subject_id": spec.id,
                        "max_monitor_calls_per_run": self._max_monitor_calls_per_run,
                    },
                )
                decisions.append((spec, None))
                continue

            trip_record = next((tb for tb in state.tripped_breakers if tb.id == spec.id), None)
            trip = BreakerTripSummary(
                breaker_id=spec.id,
                condition=spec.condition,
                action=spec.action,
                detail=trip_record.detail if trip_record is not None else {},
                prior_extensions=prior,
            )
            run_log.info(
                "monitor.consult",
                extra={
                    "event": "monitor.consult",
                    "consult_point": "breaker_trip",
                    "subject_id": spec.id,
                    "monitor": self._monitor.name,
                },
            )
            try:
                verdict = self._monitor.decide_breaker_trip(trip, run_id=state.run_id)
            except Exception as exc:
                # NFR-2: a monitor bug must never make the run less safe than today --
                # fall back to the safe default rather than propagating.
                run_log.error(
                    "Monitor.decide_breaker_trip raised; falling back to the safe default: %s",
                    exc,
                    extra={
                        "event": "monitor.decision",
                        "consult_point": "breaker_trip",
                        "subject_id": spec.id,
                        "monitor": self._monitor.name,
                        "decision": SAFE_DEFAULT_BREAKER_VERDICT.decision,
                    },
                )
                state.monitor_decisions.append(
                    MonitorDecisionRecord(
                        at=self._clock().isoformat(),
                        consult_point="breaker_trip",
                        subject_id=spec.id,
                        decision=SAFE_DEFAULT_BREAKER_VERDICT.decision,
                        monitor=self._monitor.name,
                        detail={"reason": f"monitor raised: {exc}"},
                    )
                )
                decisions.append((spec, SAFE_DEFAULT_BREAKER_VERDICT))
                continue

            state.monitor_decisions.append(
                MonitorDecisionRecord(
                    at=self._clock().isoformat(),
                    consult_point="breaker_trip",
                    subject_id=spec.id,
                    decision=verdict.decision,
                    monitor=self._monitor.name,
                    detail={
                        "extend_by_seconds": verdict.extend_by_seconds,
                        "reason": verdict.reason,
                    },
                )
            )
            run_log.info(
                "monitor.decision",
                extra={
                    "event": "monitor.decision",
                    "consult_point": "breaker_trip",
                    "subject_id": spec.id,
                    "monitor": self._monitor.name,
                    "decision": verdict.decision,
                },
            )
            decisions.append((spec, verdict))

        if all(v is not None and v.decision == "extend" for _, v in decisions):
            for spec, resolved_verdict in decisions:
                assert resolved_verdict is not None  # narrowed by the all(...) check above
                apply_breaker_extension(
                    state,
                    spec,
                    extend_by_seconds=resolved_verdict.extend_by_seconds,
                    extend_by_same=resolved_verdict.extend_by_seconds is None,
                    clock=self._clock,
                    run_log=run_log,
                )
            return "extend"
        return "halt"

    def _consult_task_failure_heal(
        self,
        tid: str,
        result: TaskResult,
        state: RunState,
        run_log: logging.LoggerAdapter,
    ) -> HealVerdict | None:
        """Consult Point B (epic doc Design Decision D4): ask ``self._monitor`` whether a
        task that just settled ``"failed"`` (after exhausting its ``RetryPolicy``) should
        get one bounded extra retry.

        Returns ``None`` when the bound (``max_heal_retries_per_task``) or the shared cap
        (``max_monitor_calls_per_run``) is already exhausted — the caller treats ``None``
        identically to an explicit ``"accept_failure"`` verdict: fall through to the
        existing, unmodified failure-handling code without even asking the monitor.
        Returns the Monitor's actual verdict otherwise (which may itself be
        ``"accept_failure"``).
        """
        prior = count_monitor_heal_retries(state, tid)
        if prior >= self._max_heal_retries_per_task:
            return None  # bound exhausted -> accept the failure, never even consult
        if count_monitor_calls_made(state) >= self._max_monitor_calls_per_run:
            run_log.warning(
                "monitor call cap reached; falling back to the safe default (accept_failure)",
                extra={
                    "event": "monitor.cap_exceeded",
                    "consult_point": "task_failure",
                    "subject_id": tid,
                    "max_monitor_calls_per_run": self._max_monitor_calls_per_run,
                },
            )
            return None

        summary = build_task_failure_summary(result, prior_heal_retries=prior)
        run_log.info(
            "monitor.consult",
            extra={
                "event": "monitor.consult",
                "consult_point": "task_failure",
                "subject_id": tid,
                "monitor": self._monitor.name,
            },
        )
        try:
            verdict = self._monitor.decide_task_failure(summary, run_id=state.run_id)
        except Exception as exc:
            # NFR-2: a monitor bug must never make the run less safe than today -- fall
            # back to the safe default rather than propagating.
            run_log.error(
                "Monitor.decide_task_failure raised; falling back to the safe default: %s",
                exc,
                extra={
                    "event": "monitor.decision",
                    "consult_point": "task_failure",
                    "subject_id": tid,
                    "monitor": self._monitor.name,
                    "decision": SAFE_DEFAULT_HEAL_VERDICT.decision,
                },
            )
            state.monitor_decisions.append(
                MonitorDecisionRecord(
                    at=self._clock().isoformat(),
                    consult_point="task_failure",
                    subject_id=tid,
                    decision=SAFE_DEFAULT_HEAL_VERDICT.decision,
                    monitor=self._monitor.name,
                    detail={"reason": f"monitor raised: {exc}"},
                )
            )
            return SAFE_DEFAULT_HEAL_VERDICT

        state.monitor_decisions.append(
            MonitorDecisionRecord(
                at=self._clock().isoformat(),
                consult_point="task_failure",
                subject_id=tid,
                decision=verdict.decision,
                monitor=self._monitor.name,
                detail={"wait_seconds": verdict.wait_seconds, "reason": verdict.reason},
            )
        )
        run_log.info(
            "monitor.decision",
            extra={
                "event": "monitor.decision",
                "consult_point": "task_failure",
                "subject_id": tid,
                "monitor": self._monitor.name,
                "decision": verdict.decision,
            },
        )
        return verdict

    def _run_with_retries(
        self,
        task,
        workflow: WorkflowSpec,
        agents: dict,
        repo_paths: dict,
        state: RunState,
        dynamic_input_paths: list[str] | None = None,
        task_manifest_path: str | None = None,
        gate_output_path: str | None = None,
    ) -> TaskResult:
        """Execute *task* with the configured retry policy.

        Builds a TaskContext containing paths only (NFR-1 invariant).

        Parameters
        ----------
        task_manifest_path:
            Resolved path for an emit_tasks task to write its task manifest.
        gate_output_path:
            Resolved path for a loop gate task to write its verdict (iteration-suffixed).
        """
        retry = task.retries or workflow.defaults.retries
        timeout = task.timeout_seconds or workflow.defaults.timeout_seconds
        agent_spec = agents[task.agent]

        # Resolve all paths through the artifact store (no content reads)
        instruction_path = self._store.resolve(task.instruction)
        input_paths = [self._store.resolve(p) for p in task.inputs]
        output_paths = [self._store.resolve(p) for p in task.outputs]
        output_manifest_path = (
            self._store.resolve(task.output_manifest) if task.output_manifest else None
        )

        # Resolve task_manifest_path for emit_tasks (already resolved at call site, passed in)
        resolved_task_manifest_path: str | None = task_manifest_path

        # Working directory the agent runs in: AgentSpec.working_dir override (resolved
        # against, and path-guarded to, the workspace root) or the workspace root itself.
        # Ensures agents' relative output paths land inside the workspace deterministically.
        agent_cwd = self._store.resolve(agent_spec.working_dir or ".")

        # Capture directory: .orchestrator/runs/<run_id>/<task_id>/  (FR-4)
        output_dir = self._store.resolve(
            os.path.join(".orchestrator", "runs", state.run_id, task.id)
        )

        last_result: TaskResult | None = None
        # Running sums across every attempt of THIS call (E-9h3m7k FR-2): a task that
        # fails on attempt 1 and succeeds on attempt 2 must report the actual cost of
        # BOTH attempts, not just the winning one — money was spent on attempt 1 too.
        cum_input_tokens = 0
        cum_output_tokens = 0
        cum_cache_creation_input_tokens = 0
        cum_cache_read_input_tokens = 0
        cum_cost_usd = 0.0
        any_actuals = False

        for attempt in range(1, retry.max_attempts + 1):
            if self._cancel_fn():
                return TaskResult(
                    task_id=task.id,
                    status="cancelled",
                    attempts=attempt,
                )

            # Attempt-suffixed capture dir (E-9h3m7k): each retry gets its own
            # transcript.jsonl/result.json instead of the next attempt overwriting the
            # previous one's — a failed attempt's output is real observability data, not
            # noise to discard.
            attempt_output_dir = os.path.join(output_dir, f"attempt-{attempt}")

            ctx = TaskContext(
                run_id=state.run_id,
                task_id=task.id,
                agent=agent_spec,
                instruction_path=instruction_path,
                input_paths=input_paths,
                output_paths=output_paths,
                output_manifest_path=output_manifest_path,
                dynamic_input_paths=dynamic_input_paths or [],
                repo_paths=repo_paths,
                timeout_seconds=timeout,
                cwd=agent_cwd,
                output_dir=attempt_output_dir,
                task_manifest_path=resolved_task_manifest_path,
                gate_output_path=gate_output_path,
            )

            result = self._executor.execute(ctx)
            result.attempts = attempt

            if result.actuals_available:
                any_actuals = True
                cum_input_tokens += result.input_tokens or 0
                cum_output_tokens += result.output_tokens or 0
                cum_cache_creation_input_tokens += result.cache_creation_input_tokens or 0
                cum_cache_read_input_tokens += result.cache_read_input_tokens or 0
                cum_cost_usd += result.cost_usd or 0.0

            # Redefine the result's token/cost fields to mean "cumulative across every
            # attempt of this task so far" — the correct semantic for a task-level result.
            # Degenerates to today's single-attempt values when there's no retry.
            result.actuals_available = any_actuals
            result.input_tokens = cum_input_tokens if any_actuals else None
            result.output_tokens = cum_output_tokens if any_actuals else None
            result.cache_creation_input_tokens = (
                cum_cache_creation_input_tokens if any_actuals else None
            )
            result.cache_read_input_tokens = cum_cache_read_input_tokens if any_actuals else None
            result.cost_usd = cum_cost_usd if any_actuals else None

            last_result = result

            if result.status == "succeeded":
                return result

            # Quota exhaustion is handled by the engine's outer loop (wait + re-run);
            # don't burn retry attempts on it.
            if result.claude_quota_exhausted:
                return result

            logger.warning(
                "Task %s attempt %d/%d failed: %s",
                task.id,
                attempt,
                retry.max_attempts,
                result.error,
            )

            if attempt < retry.max_attempts and retry.backoff_seconds > 0:
                self._sleeper(retry.backoff_seconds)

        # All attempts exhausted
        assert last_result is not None
        return last_result

    # -------------------------------------------------------------------------
    # Dynamic-expansion helpers (Area 2)
    # -------------------------------------------------------------------------

    def _inject(
        self,
        new: list,  # list[TaskSpec]
        workflow: WorkflowSpec,
        state: RunState,
        origin: _TaskOrigin,
        route: str | None = None,
    ) -> None:
        """Merge *new* TaskSpec objects into the live workflow and RunState.

        Raises InjectionError if any id in *new* already exists in the workflow
        (NFR-6 — duplicate-id rejection).

        Parameters
        ----------
        route:
            The emitting task's ``TaskRunState.route`` (LLD §5.5, R4) — inherited
            by every injected task so branch bookkeeping (not_taken tagging,
            breaker counts) stays consistent across dynamic expansion. None when
            the emitter is not on an activated branch (no ``branches`` declared,
            or a pre-routing workflow).
        """
        existing = {t.id for t in workflow.tasks}
        for spec in new:
            if spec.id in existing:
                raise InjectionError(
                    f"Cannot inject task {spec.id!r}: id already exists in the workflow"
                )
            workflow.tasks.append(spec)
            existing.add(spec.id)
            state.injected_tasks.append(spec)
            state.tasks[spec.id] = TaskRunState(origin=origin, route=route)

    def _recompute_order(self, graph, done: set[str]) -> tuple[list[str], int]:
        """Recompute full topological order and return (order, cursor).

        The cursor is set to the index of the first task not yet in *done*
        so the while loop resumes from the right point after injection.
        """
        order = graph.topological_order()
        cursor = 0
        for i, tid in enumerate(order):
            if tid not in done:
                cursor = i
                break
        else:
            # All tasks are done
            cursor = len(order)
        return order, cursor

    def _loop_for_gate(self, workflow: WorkflowSpec, task_id: str) -> LoopSpec | None:
        """Return the LoopSpec whose gate_task_id matches *task_id* (or its iter variant).

        Handles iteration-suffixed gate task ids by stripping the ``__iter<N>`` suffix
        before comparing against the loop's authored gate_task_id.
        """
        for loop in workflow.loops:
            # Direct match (iteration 1, un-suffixed)
            if task_id == loop.gate_task_id:
                return loop
            # Iteration N match: task_id ends with __iter<N>
            if "__iter" in task_id:
                base_id = task_id.split("__iter")[0]
                if base_id == loop.gate_task_id:
                    return loop
        return None

    def _gate_path_for_iter(self, loop: LoopSpec, iteration: int) -> str:
        """Return the gate_output_path for the given iteration number.

        Iteration 1 uses the un-suffixed authored path.
        Iteration N (N >= 2) uses a path suffixed with ``__iter{N}`` inserted
        before the file extension (or appended if no extension).
        """
        if iteration == 1:
            return loop.gate_output_path
        # Insert suffix before extension, e.g. gate.json -> gate__iter2.json
        path = loop.gate_output_path
        dot = path.rfind(".")
        slash = path.rfind("/")
        if dot > slash:  # has an extension
            return path[:dot] + f"__iter{iteration}" + path[dot:]
        return path + f"__iter{iteration}"

    def _clone_body(
        self, loop: LoopSpec, iter_n: int, workflow: WorkflowSpec
    ) -> list:  # list[TaskSpec]
        """Clone the loop body tasks for iteration *iter_n* (>= 2).

        Rules (HLD §4.5, AC from T-sfdybw):
        - Task ids are suffixed ``__iter{N}``.
        - Intra-body ``depends_on`` are rewritten to suffixed ids.
        - The first task in the clone depends on the last task of the previous iteration.
        - The gate_output_path for the gate task is suffixed to isolate each verdict.
        """
        suffix = f"__iter{iter_n}"
        prev_suffix = f"__iter{iter_n - 1}" if iter_n > 2 else ""
        # Last task id of the previous iteration
        prev_last = loop.body[-1] + prev_suffix

        body_set = set(loop.body)
        clones = []

        for i, tid in enumerate(loop.body):
            base = workflow.task(tid)
            # Rewrite depends_on: intra-body deps get the new suffix; others unchanged
            new_depends_on = [(d + suffix if d in body_set else d) for d in base.depends_on]
            # Chain: first task of this iteration depends on last task of previous iteration
            if i == 0:
                new_depends_on.append(prev_last)

            # Copy all fields; override id and depends_on.
            # Clear inputs and outputs on clones to avoid spurious DAG inferred edges:
            # cloned tasks use the same artifact paths as the original body (they overwrite),
            # but if we keep inputs/outputs the DAG inferred-edge logic would create cycles
            # (e.g. develop__iter2 outputs impl.md -> inferred edge to review which already ran).
            # Execution ordering is fully handled by the rewritten depends_on chain.
            # gate_output_path lives on LoopSpec, not TaskSpec — suffixing is handled
            # via _gate_path_for_iter when the gate verdict is read.
            clone = base.model_copy(
                deep=True,
                update={
                    "id": tid + suffix,
                    "depends_on": new_depends_on,
                    "inputs": [],
                    "outputs": [],
                    "output_manifest": None,
                    # Clones never emit tasks (they are already clones of static body tasks)
                    "emit_tasks": False,
                    "task_manifest_path": None,
                },
            )

            clones.append(clone)

        return clones
