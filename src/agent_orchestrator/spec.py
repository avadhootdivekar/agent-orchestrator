"""Load and validate workflow spec files."""

from __future__ import annotations

from pathlib import Path

from .config import _load_file, _validate_against_schema
from .errors import SpecValidationError
from .models import BudgetSpec, WorkflowSpec

# Suffix used for loop iteration cloning — authors must not use this in task ids.
_ITER_SUFFIX_MARKER = "__iter"


def load_workflow(path: str | Path) -> WorkflowSpec:
    """Load a workflow JSON/YAML file, validate against JSON Schema, parse into WorkflowSpec."""
    data = _load_file(path)
    _validate_against_schema(data, "workflow.schema.json")
    try:
        return WorkflowSpec(**data)
    except Exception as exc:
        raise SpecValidationError(f"Workflow model error: {exc}", path=str(path)) from exc


def cross_validate(
    workflow: WorkflowSpec,
    reposets: dict,
    agents: dict,
) -> None:
    """Cross-validate workflow references against loaded reposets and agents.

    Raises SpecValidationError for:
    - Unknown repo_set
    - Task referencing an unknown agent
    - Task depends_on referencing an unknown task id or an unknown loop id not resolvable
    - emit_tasks / task_manifest_path pairing violations (Area 2)
    - LoopSpec body/gate/max_iterations constraints (Area 2)
    - Authored task ids or loop body ids containing '__iter' (reserved suffix, ADR-006)
    """
    if workflow.repo_set not in reposets:
        raise SpecValidationError(
            f"Unknown repo_set: {workflow.repo_set!r}",
            path="repo_set",
        )

    task_ids = {t.id for t in workflow.tasks}
    loop_ids = {lp.id for lp in workflow.loops}

    # Build set of valid dep targets: task ids + loop ids (loop id resolves to last iter last task)
    valid_dep_targets = task_ids | loop_ids

    # Check for reserved suffix in authored task ids
    for task in workflow.tasks:
        if _ITER_SUFFIX_MARKER in task.id:
            raise SpecValidationError(
                f"Task id {task.id!r} contains reserved suffix {_ITER_SUFFIX_MARKER!r}; "
                "authored ids must not use __iter",
                path=f"tasks.{task.id}.id",
            )

    for task in workflow.tasks:
        if task.agent not in agents:
            raise SpecValidationError(
                f"Task {task.id!r}: unknown agent {task.agent!r}",
                path=f"tasks.{task.id}.agent",
            )
        for dep in task.depends_on:
            if dep not in valid_dep_targets:
                raise SpecValidationError(
                    f"Task {task.id!r}: unknown depends_on {dep!r}",
                    path=f"tasks.{task.id}.depends_on",
                )

        # emit_tasks ⇔ task_manifest_path both set or both unset (§3.1 cross-validation)
        if task.emit_tasks and not task.task_manifest_path:
            raise SpecValidationError(
                f"Task {task.id!r}: emit_tasks=True requires task_manifest_path to be set",
                path=f"tasks.{task.id}.emit_tasks",
            )
        if task.task_manifest_path and not task.emit_tasks:
            raise SpecValidationError(
                f"Task {task.id!r}: task_manifest_path is set but emit_tasks=False",
                path=f"tasks.{task.id}.task_manifest_path",
            )

    # LoopSpec cross-validation (§3.2)
    body_task_to_loop: dict[str, str] = {}  # task_id -> loop_id
    for loop in workflow.loops:
        if not loop.body:
            raise SpecValidationError(
                f"Loop {loop.id!r}: body must not be empty",
                path=f"loops.{loop.id}.body",
            )

        if loop.max_iterations < 1:
            raise SpecValidationError(
                f"Loop {loop.id!r}: max_iterations must be >= 1, got {loop.max_iterations}",
                path=f"loops.{loop.id}.max_iterations",
            )

        for bid in loop.body:
            if bid not in task_ids:
                raise SpecValidationError(
                    f"Loop {loop.id!r}: body task {bid!r} not found in workflow tasks",
                    path=f"loops.{loop.id}.body",
                )
            if _ITER_SUFFIX_MARKER in bid:
                raise SpecValidationError(
                    f"Loop {loop.id!r}: body task id {bid!r} contains reserved suffix "
                    f"{_ITER_SUFFIX_MARKER!r}",
                    path=f"loops.{loop.id}.body",
                )
            if bid in body_task_to_loop:
                raise SpecValidationError(
                    f"Loop {loop.id!r}: task {bid!r} already belongs to loop "
                    f"{body_task_to_loop[bid]!r}; a task may be in at most one loop body",
                    path=f"loops.{loop.id}.body",
                )
            body_task_to_loop[bid] = loop.id

            # No nested loops: body task id must not equal any loop id
            if bid in loop_ids:
                raise SpecValidationError(
                    f"Loop {loop.id!r}: body task id {bid!r} matches a loop id (nested loops "
                    "are not supported in this release)",
                    path=f"loops.{loop.id}.body",
                )

        if loop.gate_task_id not in loop.body:
            raise SpecValidationError(
                f"Loop {loop.id!r}: gate_task_id {loop.gate_task_id!r} not in body",
                path=f"loops.{loop.id}.gate_task_id",
            )

        # Gate task must not itself be an emitter (a task cannot be both emitter and gate)
        gate_task = next(t for t in workflow.tasks if t.id == loop.gate_task_id)
        if gate_task.emit_tasks:
            raise SpecValidationError(
                f"Loop {loop.id!r}: gate_task_id {loop.gate_task_id!r} has emit_tasks=True; "
                "a task cannot be both an emitter and a gate",
                path=f"loops.{loop.id}.gate_task_id",
            )

    # Budget cross-validation (T-oh5gl5)
    if workflow.budget is not None:
        budget_cross_validate(workflow.budget)


def budget_cross_validate(budget: BudgetSpec) -> None:
    """Standalone budget cross-validation (called from CLI and cross_validate)."""
    b = budget
    if b.total_tokens is not None and b.total_tokens <= 0:
        raise SpecValidationError(
            "budget.total_tokens must be > 0",
            path="budget.total_tokens",
        )
    if b.rate is not None:
        has_window = b.rate.window is not None
        has_secs = b.rate.window_seconds is not None
        if has_window == has_secs:  # both set or neither set
            raise SpecValidationError(
                "budget.rate: set exactly one of window or window_seconds",
                path="budget.rate",
            )
        if b.rate.tokens <= 0:
            raise SpecValidationError(
                "budget.rate.tokens must be > 0",
                path="budget.rate.tokens",
            )
    if b.estimator.pessimism_buffer < 1.0:
        raise SpecValidationError(
            "budget.estimator.pessimism_buffer must be >= 1.0",
            path="budget.estimator.pessimism_buffer",
        )
