"""Schema and cross-validation tests for task lifecycle hooks (E-AMSSHX, T-jI3P4p, AC-10).

AC-10: Workflow with hooks registry + task references passes schema + cross-validation.
Invalid cases: empty command, bad on_failure value, undeclared hook name.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.errors import SpecValidationError
from agent_orchestrator.models import (
    AgentSpec,
    HookRef,
    HookSpec,
    RepoRef,
    RepoSet,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.spec import cross_validate


def _agents() -> dict:
    return {"ag": AgentSpec(executor="fake")}


def _reposets(workspace: str) -> dict:
    return {
        "rs": RepoSet(
            workspace_root=workspace,
            repos=[RepoRef(id="core", path=".", role="primary")],
        )
    }


def _task(tid: str, pre_hook: str | None = None, post_hook: str | None = None) -> TaskSpec:
    return TaskSpec(
        id=tid,
        agent="ag",
        instruction="specs/task.md",
        pre_hook=HookRef(use=pre_hook) if pre_hook else None,
        post_hook=HookRef(use=post_hook) if post_hook else None,
    )


def _wf(tasks: list[TaskSpec], hooks: dict[str, HookSpec] | None = None) -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0",
        id="wf",
        repo_set="rs",
        tasks=tasks,
        hooks=hooks or {},
    )


# ---------------------------------------------------------------------------
# AC-10: Valid workflows
# ---------------------------------------------------------------------------


class TestHooksSchemaValid:
    """AC-10: Valid workflows with hooks pass schema and cross-validation."""

    def test_workflow_with_hooks_registry(self, tmp_path: Path) -> None:
        """AC-10: Workflow with hooks registry + task references."""
        hooks = {
            "check_disk": HookSpec(command=["python3", "check.py"]),
            "grade": HookSpec(command=["python3", "grade.py"], timeout_seconds=60),
        }
        task = _task("task1", pre_hook="check_disk", post_hook="grade")
        wf = _wf([task], hooks=hooks)

        # Should not raise
        cross_validate(wf, _reposets(str(tmp_path)), _agents())

    def test_workflow_without_hooks(self, tmp_path: Path) -> None:
        """AC-10: Workflow with no hooks is still valid."""
        task = _task("task1")
        wf = _wf([task], hooks={})

        # Should not raise
        cross_validate(wf, _reposets(str(tmp_path)), _agents())

    def test_task_without_hooks(self, tmp_path: Path) -> None:
        """AC-10: Task with no pre_hook/post_hook is valid."""
        hooks = {
            "check": HookSpec(command=["echo", "check"]),
        }
        task = _task("task1")  # No hooks
        wf = _wf([task], hooks=hooks)

        # Should not raise
        cross_validate(wf, _reposets(str(tmp_path)), _agents())

    def test_multiple_tasks_same_hook(self, tmp_path: Path) -> None:
        """AC-10: Multiple tasks can reference the same named hook."""
        hooks = {
            "grade": HookSpec(command=["python3", "grade.py"], on_failure="ignore"),
        }
        task1 = _task("task1", post_hook="grade")
        task2 = _task("task2", post_hook="grade")
        wf = _wf([task1, task2], hooks=hooks)

        # Should not raise
        cross_validate(wf, _reposets(str(tmp_path)), _agents())

    def test_hook_with_on_failure_set(self, tmp_path: Path) -> None:
        """AC-10: HookSpec with on_failure set is valid."""
        hooks = {
            "check": HookSpec(
                command=["python3", "check.py"],
                on_failure="fail_task",
            ),
        }
        task = _task("task1", pre_hook="check")
        wf = _wf([task], hooks=hooks)

        # Should not raise
        cross_validate(wf, _reposets(str(tmp_path)), _agents())

    def test_task_hook_ref_with_on_failure_override(self, tmp_path: Path) -> None:
        """AC-10: HookRef with on_failure override is valid."""
        hooks = {
            "grade": HookSpec(command=["python3", "grade.py"]),
        }
        task = TaskSpec(
            id="task1",
            agent="ag",
            instruction="specs/task.md",
            post_hook=HookRef(use="grade", on_failure="fail_task"),
        )
        wf = _wf([task], hooks=hooks)

        # Should not raise
        cross_validate(wf, _reposets(str(tmp_path)), _agents())


# ---------------------------------------------------------------------------
# AC-10: Invalid workflows (schema errors)
# ---------------------------------------------------------------------------


class TestHooksSchemaInvalid:
    """AC-10: Invalid hook specs fail schema validation."""

    def test_empty_command_rejected(self, tmp_path: Path) -> None:
        """AC-10: HookSpec with empty command array is rejected."""
        with pytest.raises(ValueError, match="at least 1"):
            HookSpec(command=[])

    def test_bad_on_failure_value_rejected(self) -> None:
        """AC-10: HookSpec with invalid on_failure value is rejected."""
        with pytest.raises(ValueError):
            HookSpec(command=["echo"], on_failure="invalid_value")  # type: ignore[arg-type]

    def test_timeout_zero_rejected(self) -> None:
        """AC-10: timeout_seconds < 1 is rejected."""
        with pytest.raises(ValueError, match="greater than or equal to 1"):
            HookSpec(command=["echo"], timeout_seconds=0)


# ---------------------------------------------------------------------------
# AC-10: Cross-validation errors (undeclared hook references)
# ---------------------------------------------------------------------------


class TestHooksCrossValidationUndeclared:
    """AC-10: Cross-validation: pre_hook/post_hook must reference declared hooks."""

    def test_undeclared_pre_hook_rejected(self, tmp_path: Path) -> None:
        """AC-10: Task referencing undeclared pre_hook name fails cross-validation."""
        hooks = {
            "check": HookSpec(command=["python3", "check.py"]),
        }
        task = _task("task1", pre_hook="undefined_hook")
        wf = _wf([task], hooks=hooks)

        with pytest.raises(SpecValidationError, match="undefined_hook"):
            cross_validate(wf, _reposets(str(tmp_path)), _agents())

    def test_undeclared_post_hook_rejected(self, tmp_path: Path) -> None:
        """AC-10: Task referencing undeclared post_hook name fails cross-validation."""
        hooks = {
            "grade": HookSpec(command=["python3", "grade.py"]),
        }
        task = _task("task1", post_hook="undefined_hook")
        wf = _wf([task], hooks=hooks)

        with pytest.raises(SpecValidationError, match="undefined_hook"):
            cross_validate(wf, _reposets(str(tmp_path)), _agents())

    def test_multiple_undeclared_hooks_one_error_reported(self, tmp_path: Path) -> None:
        """AC-10: At least the first undeclared hook is reported."""
        task = _task("task1", pre_hook="check1", post_hook="check2")
        wf = _wf([task], hooks={})

        with pytest.raises(SpecValidationError):
            cross_validate(wf, _reposets(str(tmp_path)), _agents())

    def test_one_task_with_undeclared_hook_among_others(self, tmp_path: Path) -> None:
        """AC-10: One task references undeclared hook; others are OK."""
        hooks = {
            "check": HookSpec(command=["python3", "check.py"]),
        }
        task1 = _task("task1", pre_hook="check")  # OK
        task2 = _task("task2", post_hook="undefined")  # ERROR
        wf = _wf([task1, task2], hooks=hooks)

        with pytest.raises(SpecValidationError, match="undefined"):
            cross_validate(wf, _reposets(str(tmp_path)), _agents())

    def test_hook_name_with_leading_space(self, tmp_path: Path) -> None:
        """AC-10: Hook name with space references undeclared hook (cross-validation catches)."""
        task = _task("task1", pre_hook=" check")  # Space prefix
        wf = _wf([task], hooks={"check": HookSpec(command=["echo"])})

        with pytest.raises(SpecValidationError):
            cross_validate(wf, _reposets(str(tmp_path)), _agents())
