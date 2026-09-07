"""Run-state persistence: atomic save/load, resume, and skip logic."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .artifacts import ArtifactStore
from .dag import build_dag, compute_cones
from .models import RunState, TaskRunState, TaskSpec, WorkflowSpec, compute_run_usage_totals

_FMT = "%Y%m%dT%H%M%SZ"

# TaskIntegrationState.status values grouped under status.json's top-level "conflict"
# count (AC-18) -- named so the grouping is visible in one place, not re-derived at
# every call site.
_CONFLICT_INTEGRATION_STATUSES = ("conflict_resolver", "conflict_rerun")


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

        # Run-wide integration counts, derived from task_integration (E-Wk9Tz3 AC-18).
        # Zero for every pre-epic run: task_integration defaults to {} (NFR-5).
        integration_counts = {"integrated": 0, "conflict": 0, "failed": 0}
        for tis in state.task_integration.values():
            if tis.status == "integrated":
                integration_counts["integrated"] += 1
            elif tis.status in _CONFLICT_INTEGRATION_STATUSES:
                integration_counts["conflict"] += 1
            elif tis.status == "failed":
                integration_counts["failed"] += 1

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
                    # Cumulative ACTUAL usage across every retry attempt (E-9h3m7k FR-2).
                    "input_tokens": ts.cumulative_input_tokens,
                    "output_tokens": ts.cumulative_output_tokens,
                    "cost_usd": ts.cumulative_cost_usd,
                    # Per-task integration observability (E-Wk9Tz3 AC-18). Defaults match
                    # a never-isolated task: "none" status, no tier reached, zero conflicts.
                    "integration_status": (
                        state.task_integration[tid].status
                        if tid in state.task_integration
                        else "none"
                    ),
                    "tier_reached": (
                        state.task_integration[tid].tier_reached
                        if tid in state.task_integration
                        else None
                    ),
                    "conflicted_count": (
                        len(state.task_integration[tid].conflicted_paths)
                        if tid in state.task_integration
                        else 0
                    ),
                    "dispatch_cycle": ts.dispatch_cycle,
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
            # Run-wide actual usage totals (E-9h3m7k FR-3), derived — see
            # models.compute_run_usage_totals.
            "usage_totals": compute_run_usage_totals(state).model_dump(),
            # Run-wide integration observability (E-Wk9Tz3 FR-15, AC-18).
            "integration": {
                "active": state.integration.active,
                "branch": state.integration.branch,
                "heads": state.integration.heads,
                "tier_counts": state.integration.tier_counts,
                "integrated": integration_counts["integrated"],
                "conflict": integration_counts["conflict"],
                "failed": integration_counts["failed"],
            },
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
                # R-21/AC-17: preserve dispatch_cycle explicitly. This branch replaces the
                # TaskRunState wholesale with a FRESH object -- exactly the "resume wipes
                # non-terminal TaskRunState" behavior that is why integration bookkeeping
                # lives on RunState.task_integration instead of here (see below). But
                # dispatch_cycle DOES live on TaskRunState (it must, to key the per-attempt
                # transcript capture directory), so it must be carried forward by hand or a
                # resumed requeue would silently restart its cycle counter at 0 and clobber
                # an already-captured transcript directory.
                #
                # E-Wk9Tz3 T-Ac6Vd9 (review Major-1): the cumulative ACTUAL usage counters
                # (token/cache/cost fields, E-9h3m7k FR-2) are carried forward the same way,
                # for the same reason -- a crash between a settled cycle's reconcile and the
                # NEXT cycle's own settle must not silently wipe the already-earned actuals
                # from this per-task view. `ts` here is whatever was persisted before the
                # crash (defaults to 0/0.0 for a task that never ran, via the `TaskRunState()`
                # fallback above -- old, pre-this-fix state.json files with a `cumulative_*`
                # value already default the same way, so this is a pure superset: nothing
                # regresses for a task with zero cumulative usage, and a task with real
                # accumulated usage no longer loses it on resume). The run-level ledger
                # (`RunState.budget_counters.consumed_tokens`, used by breakers/rate-window
                # gating) was never affected by this gap -- it is never rebuilt here -- so this
                # fix is purely restoring the per-task DISPLAY/observability view to match it.
                state.tasks[task.id] = TaskRunState(
                    status="pending",
                    dispatch_cycle=ts.dispatch_cycle,
                    cumulative_input_tokens=ts.cumulative_input_tokens,
                    cumulative_output_tokens=ts.cumulative_output_tokens,
                    cumulative_cache_creation_input_tokens=ts.cumulative_cache_creation_input_tokens,
                    cumulative_cache_read_input_tokens=ts.cumulative_cache_read_input_tokens,
                    cumulative_cost_usd=ts.cumulative_cost_usd,
                )

        # E-Wk9Tz3 AC-17: state.integration and state.task_integration are otherwise
        # preserved VERBATIM (this is precisely why integration bookkeeping lives on
        # RunState rather than TaskRunState -- the wholesale-replace-on-non-pending
        # behavior just above would otherwise silently drop it). The one explicit
        # normalization needed: a task whose integration was mid-flight when the run
        # crashed ("integrating") goes back to "pending" so a resumed run retries
        # integration instead of leaving it stuck forever. `mode` (what the NEXT dispatch
        # should do -- normal/resolve/rerun) is preserved so a resumed T2/T3 retry still
        # lands in the right dispatch mode.
        for task_integration_state in state.task_integration.values():
            if task_integration_state.status == "integrating":
                task_integration_state.status = "pending"

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
