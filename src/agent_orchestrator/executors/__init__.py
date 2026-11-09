"""Executor implementations and dispatch."""

from ..models import TaskContext, TaskResult
from .base import Executor
from .claude_cli import ClaudeCliExecutor
from .fake import FakeExecutor


class DispatchExecutor(Executor):
    """Dispatches execute() calls to the right executor based on ctx.agent.executor."""

    def __init__(self) -> None:
        self._claude = ClaudeCliExecutor()
        self._fake = FakeExecutor()

    def execute(self, ctx: TaskContext) -> TaskResult:
        if ctx.agent.executor == "fake":
            return self._fake.execute(ctx)
        return self._claude.execute(ctx)


# Executor name (AgentSpec.executor) -> provider-neutral settings it translates; no instantiation.
EXECUTOR_SUPPORTED_SETTINGS: dict[str, frozenset[str]] = {
    "claude_cli": ClaudeCliExecutor.supported_settings,
    "fake": FakeExecutor.supported_settings,
}

__all__ = [
    "EXECUTOR_SUPPORTED_SETTINGS",
    "Executor",
    "ClaudeCliExecutor",
    "FakeExecutor",
    "DispatchExecutor",
]
