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


class WorktreeCollisionError(OrchestratorError):
    """Raised by `WorktreeManager.ensure()` (E-Wk9Tz3 T-Wk3Nv6, HLD §11 M3 "D-ENS") when the
    `ao/<run>/<task>` branch a task's worktree needs is already in use by a worktree this
    call did not just create -- either a different worktree already occupies the expected
    path (on a different branch, or with no readable HEAD), or the branch itself is checked
    out at an entirely different worktree path.

    `ensure()` never deletes a ref to resolve this: the `ao/` namespace is reserved (S-8),
    so a pre-existing branch here is almost always this run's OWN leftover from a crash, and
    deleting it would destroy work §12.1's crash-recovery story promises to preserve.
    Deletion belongs only to `release()`/`reconcile()`/`gc_run()`, where ownership has
    already been established -- this error exists for the one state those three can't safely
    resolve on their own: an operator (or a second live process) is the only thing that can
    know whether reusing, removing or renaming the conflicting worktree is correct.
    """

    def __init__(
        self,
        expected_branch: str,
        worktree_path: str,
        remedy: str,
        *,
        found_branch: str | None = None,
    ) -> None:
        detail = f"found {found_branch!r} checked out there" if found_branch else "already in use"
        super().__init__(
            f"cannot use worktree path {worktree_path!r} for branch {expected_branch!r}: "
            f"{detail}. {remedy}"
        )
        self.expected_branch = expected_branch
        self.worktree_path = worktree_path
        self.found_branch = found_branch
        self.remedy = remedy


# --- Task isolation: integration (E-Wk9Tz3 T-Ib5Qy9) -------------------------------------


class IntegrationLockTimeoutError(OrchestratorError):
    """Raised by `IntegrationLock.__enter__` (the context-manager convenience path only)
    when `.acquire()` cannot obtain the lock within its configured timeout.

    `Integrator` itself never raises this: it calls `.acquire(timeout)` directly and turns
    a `False` return into `IntegrationResult(status="failed", reason="lock_timeout")`
    (AC-13) — a lock timeout is an expected, recoverable outcome on the hot path, not an
    exceptional one.
    """

    def __init__(self, common_dir: str, timeout: float) -> None:
        super().__init__(f"integration lock on {common_dir!r} not acquired within {timeout}s")
        self.common_dir = common_dir
        self.timeout = timeout


# --- Task isolation: workspace run lock (E-Wk9Tz3 T-Wl2Bq7) ------------------------------


class WorkspaceLockHeldError(OrchestratorError):
    """Raised by `WorkspaceRunLock.__enter__` (the context-manager convenience path only)
    when `.acquire()` is denied by a live holder.

    `Orchestrator._activate_integration` never raises this itself: it calls `.acquire()`
    directly (never-raising, like `IntegrationLock.acquire()`) and turns a denied
    `WorkspaceLockClaim` into a graceful per-policy outcome (degrade to `isolation: none`
    for `"require"`; proceed without checkout-sync protection for `"skip_sync"`) — a
    denied claim is an expected, recoverable outcome on the hot path, not an exceptional
    one, mirroring `IntegrationLockTimeoutError`'s own precedent above.
    """

    def __init__(self, lock_path: str, holder_run_id: str | None, holder_pid: int | None) -> None:
        super().__init__(
            f"workspace run lock {lock_path!r} is held by run {holder_run_id!r} (pid {holder_pid})"
        )
        self.lock_path = lock_path
        self.holder_run_id = holder_run_id
        self.holder_pid = holder_pid
