"""Tests for hook suppression in T2 conflict-resolver dispatch (E-AMSSHX, T-jI3P4p, AC-12).

AC-12 (BLOCKING early-gate finding): T2 conflict-resolver dispatch must NOT fire task hooks.
A companion test confirms T3 rerun dispatch DOES fire them normally.
"""

from __future__ import annotations

from agent_orchestrator.isolation.escalation import build_rerun_task, build_resolver_dispatch
from agent_orchestrator.models import (
    AgentSpec,
    HookRef,
    IntegrationSpec,
    TaskSpec,
)


class TestResolverDispatchHookSuppression:
    """AC-12: T2 conflict-resolver dispatch suppresses hooks."""

    def test_resolver_dispatch_clears_prehook_and_posthook(self) -> None:
        """AC-12: build_resolver_dispatch explicitly sets pre_hook=None, post_hook=None."""
        # Original task with hooks declared
        original_task = TaskSpec(
            id="conflicted_task",
            agent="agent1",
            instruction="specs/task.md",
            pre_hook=HookRef(use="check_disk"),
            post_hook=HookRef(use="grade"),
        )

        # Set up for resolver dispatch
        agents = {
            "agent1": AgentSpec(executor="fake"),
            "resolver": AgentSpec(executor="fake"),
        }

        spec = IntegrationSpec(
            resolver_agent="resolver",
            resolver_instruction="specs/resolve_conflict.md",
        )

        resolver_dispatch = build_resolver_dispatch(
            original_task,
            agents,
            env_overlay={},
            spec=spec,
            manifest_relpath="conflict.json",
            instruction_relpath="resolve.md",
        )

        resolver_task = resolver_dispatch.task

        # Verify hooks are cleared
        assert resolver_task.pre_hook is None
        assert resolver_task.post_hook is None
        # Verify other fields are substituted (agent, instruction)
        assert resolver_task.agent == "resolver"
        assert resolver_task.instruction == "resolve.md"
        # Original task id stays the same
        assert resolver_task.id == "conflicted_task"

    def test_resolver_dispatch_from_task_without_hooks(self) -> None:
        """AC-12: Task without hooks still works (None -> None, no change)."""
        original_task = TaskSpec(
            id="task",
            agent="agent1",
            instruction="specs/task.md",
            # No pre_hook or post_hook
        )

        agents = {
            "agent1": AgentSpec(executor="fake"),
            "resolver": AgentSpec(executor="fake"),
        }

        spec = IntegrationSpec(
            resolver_agent="resolver",
            resolver_instruction="specs/resolve.md",
        )

        resolver_dispatch = build_resolver_dispatch(
            original_task,
            agents,
            env_overlay={},
            spec=spec,
            manifest_relpath="conflict.json",
            instruction_relpath="resolve.md",
        )

        resolver_task = resolver_dispatch.task

        # Still None after the copy
        assert resolver_task.pre_hook is None
        assert resolver_task.post_hook is None


class TestRerunDispatchHooksUntouched:
    """AC-12 companion: T3 rerun dispatch DOES fire hooks (no suppression)."""

    def test_rerun_task_preserves_hooks(self) -> None:
        """T3 rerun dispatch: hooks fire normally (no suppression)."""
        original_task = TaskSpec(
            id="task",
            agent="agent1",
            instruction="specs/task.md",
            pre_hook=HookRef(use="check_disk"),
            post_hook=HookRef(use="grade"),
        )

        rerun_task = build_rerun_task(original_task, patch_relpath="patch.diff")

        # Hooks should be UNCHANGED for T3 rerun
        assert rerun_task.pre_hook == original_task.pre_hook
        assert rerun_task.post_hook == original_task.post_hook
        assert rerun_task.pre_hook.use == "check_disk"
        assert rerun_task.post_hook.use == "grade"
        # But input is modified to include the patch
        assert "patch.diff" in rerun_task.inputs


class TestHookSuppression:
    """Additional test to verify hook suppression in both T2 and T3 paths."""

    def test_resolver_vs_rerun_hook_behavior(self) -> None:
        """Comparison: resolver (T2) suppresses, rerun (T3) does not."""
        base_task = TaskSpec(
            id="task",
            agent="agent1",
            instruction="specs/task.md",
            pre_hook=HookRef(use="check"),
            post_hook=HookRef(use="grade"),
        )

        agents = {
            "agent1": AgentSpec(executor="fake"),
            "resolver": AgentSpec(executor="fake"),
        }
        spec = IntegrationSpec(resolver_agent="resolver")

        # T2: Resolver dispatch clears hooks
        resolver_dispatch = build_resolver_dispatch(
            base_task,
            agents,
            env_overlay={},
            spec=spec,
            manifest_relpath="conflict.json",
            instruction_relpath="resolve.md",
        )
        assert resolver_dispatch.task.pre_hook is None
        assert resolver_dispatch.task.post_hook is None

        # T3: Rerun dispatch preserves hooks
        rerun_task = build_rerun_task(base_task, patch_relpath="patch.diff")
        assert rerun_task.pre_hook == base_task.pre_hook
        assert rerun_task.post_hook == base_task.post_hook
