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


class ControlFileError(OrchestratorError):
    """Raised by the shared bounded-JSON control-file reader (`artifacts.read_control` and its
    typed helpers) — used by loop gates, routers, and verdict breakers alike (NFR-1: engine reads
    only bounded control/verdict JSON, never payload artifacts). Raised on: missing file, size
    over `MAX_CONTROL_FILE_BYTES`, invalid JSON, non-object root, or a field failing its typed
    check.
    """


class GateError(ControlFileError):
    """Raised when a loop gate file is missing, its field is absent, or the value is non-bool.

    Kept as a distinct subclass of `ControlFileError` for backward compatibility: engine.py's
    loop-gate handling catches this concrete type (see `artifacts.read_gate`).
    """


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


# --- Task isolation: git porcelain (E-Wk9Tz3 T-Gt4Pw8) ----------------------------------
#
# `isolation/git.py` is the single choke point that shells out to git; every failure it
# raises is one of these, never a raw `subprocess.CalledProcessError`/`TimeoutExpired`.


class GitError(OrchestratorError):
    """Base class for every error `isolation/git.py`'s `GitRepo` can raise.

    `stderr_tail` is already truncated (<= 4 KiB) by the raiser — this class never
    re-truncates, so constructing one directly with a longer string is a caller bug.
    """

    def __init__(self, argv: list[str], exit_code: int | None, stderr_tail: str) -> None:
        super().__init__(f"git command failed (exit={exit_code}): {' '.join(argv)}\n{stderr_tail}")
        self.argv = argv
        self.exit_code = exit_code
        self.stderr_tail = stderr_tail


class GitTimeoutError(GitError):
    """Raised when a git invocation exceeds its bounded timeout (`exit_code` is None)."""


class GitUnavailableError(GitError):
    """Raised when the `git` binary itself cannot be found/executed.

    Version-too-old is deliberately NOT raised from here (`GitRepo.version()` is a
    never-raising probe per AC-10) -- a caller that requires a minimum version compares
    the probed tuple itself and decides whether to degrade or raise.
    """


class GitForbiddenCommandError(GitError):
    """Raised when a `FORBIDDEN_SUBCOMMANDS` verb reaches `_run`, before any subprocess is
    spawned (S-1 / no-network-ever guarantee). `exit_code` is always None.
    """
