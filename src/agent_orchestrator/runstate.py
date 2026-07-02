"""Run-state persistence: atomic save/load, resume, and skip logic."""

from __future__ import annotations

import json
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

    def _status_path(self, run_id: str) -> Path:
        return self._root / run_id / "status.json"

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

    def write_status(self, state: RunState) -> None:
        """Derive status.json from *state* and atomic-write it next to state.json.

        Called inside save() so the two files can never diverge (ADR-002).
        The ``origin`` field uses a getattr fallback so Area-2 can add the real
        field to TaskRunState without breaking this code.
        """
        # Count tasks by status
        counts: dict[str, int] = {
            "succeeded": 0,
            "failed": 0,
            "running": 0,
            "pending": 0,
            "skipped": 0,
            "cancelled": 0,
            "timed_out": 0,
        }
        for ts in state.tasks.values():
            counts[ts.status] = counts.get(ts.status, 0) + 1

        # First task that is running or pending (left-to-right dict insertion order)
        current_task: str | None = None
        for tid, ts in state.tasks.items():
            if ts.status in ("running", "pending"):
                current_task = tid
                break

        snapshot = {
            "run_id": state.run_id,
            "workflow_id": state.workflow_id,
            "status": state.status,
            "updated_at": state.updated_at,
            "current_task": current_task,
            "counts": counts,
            "tasks": [
                {
                    "id": tid,
                    "status": ts.status,
                    "attempts": ts.attempts,
                    "output_artifact_path": getattr(ts, "output_artifact_path", None),
                    "origin": getattr(ts, "origin", "static"),
                }
                for tid, ts in state.tasks.items()
            ],
        }

        sp = self._status_path(state.run_id)
        sp.parent.mkdir(parents=True, exist_ok=True)
        tmp = sp.with_suffix(".tmp")
        tmp.write_text(json.dumps(snapshot, indent=2))
        os.replace(tmp, sp)

    def save(self, state: RunState) -> None:
        """Atomically persist *state* to disk (write-then-rename)."""
        state.updated_at = self._clock().isoformat()
        p = self._path(state.run_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(state.model_dump_json(indent=2))
        os.replace(tmp, p)
        # Derive and write the snapshot immediately after state.json (ADR-002 / R4).
        self.write_status(state)

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

        1. Merges ``state.injected_tasks`` back into ``workflow.tasks`` so the
           engine sees the full expanded graph before rebuilding the DAG (FR-8).
        2. Tasks that have succeeded AND whose outputs are all present are kept as-is.
        3. All other non-pending tasks are reset to "pending" so the engine will
           re-run them.

        The caller passes in the live ``workflow`` object; mutating ``workflow.tasks``
        here is intentional — the workflow object is only used within one run session.
        """
        # Re-attach injected tasks (emit + loop clones) to the workflow so build_dag
        # produces the same expanded graph as the original run (FR-8, NFR-3).
        existing_ids = {t.id for t in workflow.tasks}
        for injected in state.injected_tasks:
            if injected.id not in existing_ids:
                workflow.tasks.append(injected)
                existing_ids.add(injected.id)

        # Now evaluate skip/reset for the full (possibly expanded) task list
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
