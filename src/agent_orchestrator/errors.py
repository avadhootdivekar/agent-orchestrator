"""Orchestrator error hierarchy."""

from __future__ import annotations


class OrchestratorError(Exception):
    """Base class for all orchestrator errors."""


class SpecValidationError(OrchestratorError):
    def __init__(self, msg: str, path: str = "") -> None:
        super().__init__(msg)
        self.path = path


class CycleError(OrchestratorError):
    def __init__(self, nodes: list[str]) -> None:
        super().__init__(f"Cycle detected: {nodes}")
        self.nodes = nodes


class MissingInputError(OrchestratorError):
    def __init__(self, task_id: str, path: str) -> None:
        super().__init__(f"Task {task_id}: missing input {path}")
        self.task_id = task_id
        self.path = path


class ArtifactPathError(OrchestratorError):
    def __init__(self, path: str) -> None:
        super().__init__(f"Path escapes workspace root: {path}")
        self.path = path


class ExecutorError(OrchestratorError):
    """Raised when an executor encounters an unexpected runtime failure."""


class ConfigError(OrchestratorError):
    """Raised when a config or spec file cannot be loaded."""


class InjectionError(OrchestratorError):
    """Raised when dynamic task injection fails (duplicate id, bad merge)."""


class LoopError(OrchestratorError):
    """Raised for loop configuration or runaway loop failures."""


class GateError(OrchestratorError):
    """Raised when a loop gate file is missing, its field is absent, or the value is non-bool."""


class BudgetExhausted(OrchestratorError):
    """Raised / recorded when the total token budget is exhausted (FR-6, FR-7, ADR-BUD-004).

    The blocking task is left pending and un-charged; RunState is resumable.
    """

    def __init__(self, blocked_by: str, next_available_epoch: float | None = None) -> None:
        super().__init__(
            f"Token budget exhausted (blocked_by={blocked_by!r},"
            f" next_available_epoch={next_available_epoch})"
        )
        self.blocked_by = blocked_by
        self.next_available_epoch = next_available_epoch


class RateLimited(OrchestratorError):
    """Raised / recorded when the rate window or provider 429 blocks the run.

    References: FR-6, FR-8, ADR-BUD-004.
    """

    def __init__(self, next_available_epoch: float | None = None) -> None:
        super().__init__(f"Rate limited (next_available_epoch={next_available_epoch})")
        self.next_available_epoch = next_available_epoch
