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
