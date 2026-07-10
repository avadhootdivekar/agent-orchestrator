"""Run-state persistence: atomic save/load, resume, and skip logic."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .artifacts import ArtifactStore
from .dag import build_dag, compute_cones
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
            "not_taken": 0,
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
                    "route": getattr(ts, "route", None),
                    "not_taken_reason": getattr(ts, "not_taken_reason", None),
                }
                for tid, ts in state.tasks.items()
            ],
            # Routing + circuit-breaker observability (FR-CB4, LLD §10.2).
            "route_decisions": state.route_decisions,
            "tripped_breakers": [
                {
                    "id": tb.id,
                    "condition": tb.condition,
                    "action": tb.action,
                    "at": tb.at,
                    "detail": tb.detail,
                }
                for tb in state.tripped_breakers
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
        3. Tasks already ``not_taken`` (a persisted routing verdict) are kept
           as-is — never reset to pending (LLD §9, FR-CB5).
        4. All other non-pending tasks are reset to "pending" so the engine will
           re-run them.
        5. Deterministically re-derives ``not_taken`` for any task belonging to
           an unselected route's cone from persisted ``state.route_decisions``
           (NFR-2) — the router itself stays ``succeeded`` and is never re-run,
           and verdict files are never re-read.

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
            elif ts.status == "not_taken":
                # Keep — never reset a routing verdict (LLD §9, FR-CB5).
                pass
            elif ts.status != "pending":
                state.tasks[task.id] = TaskRunState(status="pending")

        # Deterministically re-derive not_taken for any task that belongs to an
        # unselected route's cone, from the persisted state.route_decisions
        # (source of truth) — never a fresh verdict read (NFR-2). The router
        # task itself is untouched by this block: it stays "succeeded" in
        # state.tasks (handled by the loop above) and is never re-run. This
        # mirrors engine.py's `_on_router_success` guard/field conventions
        # exactly so re-derived and originally-derived not_taken tasks are
        # indistinguishable in state.
        graph = build_dag(workflow)
        cones, _membership = compute_cones(workflow, graph)
        for router_id, selected in state.route_decisions.items():
            router = next((r for r in workflow.branches if r.id == router_id), None)
            if router is None:
                continue
            router_cones = cones.get(router_id, {})
            for route_id, route_cone in router_cones.items():
                if route_id in selected:
                    continue
                for t in route_cone:
                    settled = state.tasks.get(t)
                    if settled is not None and settled.status in ("succeeded", "skipped", "failed"):
                        continue  # never override an already-settled task (mirrors
                        # engine.py's _on_router_success guard)
                    cone_ts = state.tasks.setdefault(t, TaskRunState())
                    cone_ts.status = "not_taken"
                    cone_ts.route = f"{router_id}:{route_id}"
                    cone_ts.not_taken_reason = (
                        f"router={router.router_task_id} route={route_id} not selected (resumed)"
                    )

        state.status = "running"
        return state
