"""Orchestration engine — drives a workflow DAG to completion."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime

from .artifacts import ArtifactStore, read_manifest
from .dag import build_dag
from .executors.base import Executor
from .models import RunState, TaskContext, TaskResult, TaskRunState, WorkflowSpec
from .runstate import RunStateStore

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
    """

    def __init__(
        self,
        executor: Executor,
        artifact_store: ArtifactStore,
        runstate_store: RunStateStore,
        sleeper: Callable[[float], None] = time.sleep,
        cancel_fn: Callable[[], bool] = _NO_CANCEL,
    ) -> None:
        self._executor = executor
        self._store = artifact_store
        self._runstate = runstate_store
        self._sleeper = sleeper
        self._cancel_fn = cancel_fn

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

        repo_set = reposets[workflow.repo_set]
        repo_paths = {r.id: self._store.resolve(r.path) for r in repo_set.repos}

        for tid in order:
            if self._cancel_fn():
                logger.info("Cancellation requested; halting after task %s", tid)
                state.status = "cancelled"
                break

            task = workflow.task(tid)

            # Resume / idempotency: skip completed tasks
            if self._runstate.should_skip(task, state):
                logger.info("Skipping task %s (already succeeded with outputs present)", tid)
                # Mutate in-place to preserve persisted fields (e.g. dynamic_outputs)
                ts = state.tasks.setdefault(tid, TaskRunState())
                ts.status = "skipped"
                self._runstate.save(state)
                continue

            # Check required inputs exist
            missing = [inp for inp in task.inputs if not self._store.exists(inp)]
            if missing:
                logger.error("Task %s: missing required inputs: %s", tid, missing)
                state.tasks[tid] = TaskRunState(status="failed")
                self._runstate.save(state)
                state.status = "failed"
                break

            # Collect dynamic outputs from all upstream tasks declared in depends_on
            dynamic_input_paths: list[str] = []
            for dep_id in task.depends_on:
                dep_ts = state.tasks.get(dep_id)
                if dep_ts and dep_ts.dynamic_outputs:
                    dynamic_input_paths.extend(dep_ts.dynamic_outputs)

            # Mark as running
            ts = state.tasks.setdefault(tid, TaskRunState())
            ts.status = "running"
            ts.started_at = datetime.now(UTC).isoformat()
            self._runstate.save(state)

            # Execute with retries
            result = self._run_with_retries(
                task, workflow, agents, repo_paths, state, dynamic_input_paths
            )

            ts.attempts = result.attempts
            ts.ended_at = datetime.now(UTC).isoformat()

            if result.status == "succeeded":
                # Verify declared outputs were actually produced
                missing_outputs = [o for o in task.outputs if not self._store.exists(o)]
                if missing_outputs:
                    logger.error(
                        "Task %s succeeded but declared outputs missing: %s",
                        tid,
                        missing_outputs,
                    )
                    ts.status = "failed"
                    ts.outputs_present = False
                else:
                    ts.status = "succeeded"
                    ts.outputs_present = True
                    # Read output manifest if declared; failure fails the task
                    if task.output_manifest:
                        try:
                            ts.dynamic_outputs = read_manifest(self._store, task.output_manifest)
                            logger.info(
                                "Task %s: loaded %d dynamic outputs from manifest",
                                tid,
                                len(ts.dynamic_outputs),
                            )
                        except ValueError as exc:
                            logger.error("Task %s: output_manifest read failed: %s", tid, exc)
                            ts.status = "failed"
                            ts.outputs_present = False
            else:
                ts.status = result.status

            self._runstate.save(state)

            if ts.status not in ("succeeded", "skipped"):
                state.status = "failed"
                break
        else:
            # Loop completed without break — all tasks processed
            if state.status == "running":
                state.status = "succeeded"

        self._runstate.save(state)
        return state

    def _run_with_retries(
        self,
        task,
        workflow: WorkflowSpec,
        agents: dict,
        repo_paths: dict,
        state: RunState,
        dynamic_input_paths: list[str] | None = None,
    ) -> TaskResult:
        """Execute *task* with the configured retry policy.

        Builds a TaskContext containing paths only (NFR-1 invariant).
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
