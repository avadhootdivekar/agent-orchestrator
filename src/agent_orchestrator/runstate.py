"""Run-state persistence: atomic save/load, resume, and skip logic."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .artifacts import ArtifactStore
from .models import RunState, TaskRunState, TaskSpec, WorkflowSpec

_FMT = "%Y%m%dT%H%M%SZ"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class RunStateStore:
    """Persists RunState to ``<workspace_root>/.orchestrator/runs/<run_id>/state.json``.

    Parameters
    ----------
    workspace_root:
        Root directory of the workspace.
    artifact_store:
        Used for output-existence checks during skip/resume decisions.
    clock:
        Injectable callable returning the current UTC datetime (for testing).
    """

    def __init__(
        self,
        workspace_root: str,
        artifact_store: ArtifactStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._root = Path(workspace_root) / ".orchestrator" / "runs"
        self._store = artifact_store
        self._clock: Callable[[], datetime] = clock or _utc_now

    def _path(self, run_id: str) -> Path:
        return self._root / run_id / "state.json"

    def new_run(self, workflow: WorkflowSpec) -> RunState:
        """Create a fresh RunState for a workflow."""
        now = self._clock()
        run_id = f"{workflow.id}-{now.strftime(_FMT)}"
        return RunState(
            run_id=run_id,
            workflow_id=workflow.id,
            repo_set=workflow.repo_set,
            started_at=now.isoformat(),
            updated_at=now.isoformat(),
            tasks={t.id: TaskRunState() for t in workflow.tasks},
        )

    def save(self, state: RunState) -> None:
        """Atomically persist *state* to disk (write-then-rename)."""
        state.updated_at = self._clock().isoformat()
        p = self._path(state.run_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(state.model_dump_json(indent=2))
        os.replace(tmp, p)

    def load(self, run_id: str) -> RunState:
        """Load a previously saved RunState from disk."""
        p = self._path(run_id)
        if not p.exists():
            raise FileNotFoundError(f"Run state not found: {run_id}")
        return RunState.model_validate_json(p.read_text())

    def should_skip(self, task: TaskSpec, state: RunState) -> bool:
        """Return True if this task can be skipped.

        A task is skipped if:
        - Its state is "succeeded" and all declared outputs exist, OR
        - skip_if_outputs_exist=True and all declared outputs already exist.
        """
        ts = state.tasks.get(task.id)
        if ts and ts.status == "succeeded":
            if not task.outputs or all(self._store.exists(o) for o in task.outputs):
                return True

        if task.skip_if_outputs_exist and task.outputs:
            if all(self._store.exists(o) for o in task.outputs):
                return True

        return False

    def prepare_resume(self, state: RunState, workflow: WorkflowSpec) -> RunState:
        """Prepare an existing RunState for resume.

        Tasks that have succeeded AND whose outputs are all present are kept as-is.
        All other non-pending tasks are reset to "pending" so the engine will re-run them.
        """
        for task in workflow.tasks:
            ts = state.tasks.get(task.id, TaskRunState())
            if ts.status == "succeeded" and (
                not task.outputs or all(self._store.exists(o) for o in task.outputs)
            ):
                # Keep as succeeded — idempotent skip
                pass
            elif ts.status != "pending":
                state.tasks[task.id] = TaskRunState(status="pending")

        state.status = "running"
        return state
