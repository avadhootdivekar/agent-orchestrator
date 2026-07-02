"""Orchestration engine — drives a workflow DAG to completion."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal

from .artifacts import ArtifactStore, read_gate, read_manifest, read_task_manifest
from .budget import BudgetDecision, BudgetManager
from .dag import build_dag
from .errors import GateError, InjectionError
from .estimator import TokenEstimator
from .executors.base import Executor
from .logging_setup import attach_run_handler, detach_run_handler, get_run_logger
from .models import (
    EstimatorConfig,
    LoopSpec,
    RunState,
    TaskContext,
    TaskResult,
    TaskRunState,
    WorkflowSpec,
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
    ) -> None:
        self._executor = executor
        self._store = artifact_store
        self._runstate = runstate_store
        self._sleeper = sleeper
        self._cancel_fn = cancel_fn
        self._budget_manager = budget_manager
        self._estimator = estimator
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))

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

            # Terminal-success set: skip re-running tasks that already succeeded
            done: set[str] = {
                tid for tid, ts in state.tasks.items() if ts.status in ("succeeded", "skipped")
            }

            # Re-entrant cursor (ADR-005): drives execution over a recomputable order.
            # After injection, order is rebuilt and cursor is reset to the first undone task.
            cursor = 0
            failed = False

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

                # Check required inputs exist
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

                ts.attempts = result.attempts
                ts.ended_at = datetime.now(UTC).isoformat()
                # Record captured output path (FR-5); engine never reads the files.
                if result.output_artifact_path:
                    ts.output_artifact_path = result.output_artifact_path

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
                        self._inject(new_specs, workflow, state, origin="injected")
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
                                self._inject(clones, workflow, state, origin="loop")
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

        for attempt in range(1, retry.max_attempts + 1):
            if self._cancel_fn():
                return TaskResult(
                    task_id=task.id,
                    status="cancelled",
                    attempts=attempt,
                )

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
                output_dir=output_dir,
                task_manifest_path=resolved_task_manifest_path,
                gate_output_path=gate_output_path,
            )

            result = self._executor.execute(ctx)
            result.attempts = attempt
            last_result = result

            if result.status == "succeeded":
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
    ) -> None:
        """Merge *new* TaskSpec objects into the live workflow and RunState.

        Raises InjectionError if any id in *new* already exists in the workflow
        (NFR-6 — duplicate-id rejection).
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
            state.tasks[spec.id] = TaskRunState(origin=origin)

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
