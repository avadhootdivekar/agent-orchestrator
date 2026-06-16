"""Load and validate workflow spec files."""

from __future__ import annotations

from pathlib import Path

from .config import _load_file, _validate_against_schema
from .errors import SpecValidationError
from .models import WorkflowSpec


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
    - Task depends_on referencing an unknown task id
    """
    if workflow.repo_set not in reposets:
        raise SpecValidationError(
            f"Unknown repo_set: {workflow.repo_set!r}",
            path="repo_set",
        )

    task_ids = {t.id for t in workflow.tasks}

    for task in workflow.tasks:
        if task.agent not in agents:
            raise SpecValidationError(
                f"Task {task.id!r}: unknown agent {task.agent!r}",
                path=f"tasks.{task.id}.agent",
            )
        for dep in task.depends_on:
            if dep not in task_ids:
                raise SpecValidationError(
                    f"Task {task.id!r}: unknown depends_on {dep!r}",
                    path=f"tasks.{task.id}.depends_on",
                )
