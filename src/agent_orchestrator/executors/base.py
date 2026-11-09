"""Abstract base class for task executors."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import TaskContext, TaskResult


class Executor(ABC):
    """Protocol boundary for task execution.

    Implementations must accept a TaskContext (paths/ids only — NFR-1) and
    return a TaskResult. They must not read artifact or instruction file contents.
    """

    # Provider-neutral per-agent settings this executor translates (e.g. "thinking_effort",
    # "autocompact"). A setting an agent sets that its executor lacks is warned about, not an error.
    supported_settings: frozenset[str] = frozenset()

    @abstractmethod
    def execute(self, ctx: TaskContext) -> TaskResult: ...
