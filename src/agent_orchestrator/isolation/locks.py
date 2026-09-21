"""Per-repository integration lock: in-process `threading.Lock` + cross-process `flock`
(E-Wk9Tz3 T-Ib5Qy9, HLD §11 M4, `TASK.md` AC-1).

Two integrations of the SAME git repository must never run concurrently -- whether they
come from two worker threads in one `ao` process (parallel task dispatch, ADR-0007) or two
independent `ao` processes on the same repo (a crash-recovery `ao resume` racing a still-
live original, or two operators). A `threading.Lock` alone only covers the first case; an
`flock` alone is reacquirable by a second thread in the SAME process because POSIX file
locks are per-*process*, not per-thread. Combining both, in that order (thread lock first,
then flock), covers both.

The lock file (`<common_dir>/ao-integration.lock`) is a **companion** file the engine
creates and only ever `flock`s -- never a file git itself reads, writes, or replaces via
`os.replace` (e.g. not `.git/HEAD`, not an index lock) -- so holding it can never race a
git-internal operation or be silently dropped by git rewriting the file out from under an
open fd.

Reentrancy: `IntegrationLock` is **NOT** reentrant. A second `.acquire()` on an instance
that already holds the lock raises `RuntimeError` immediately (same-instance reuse bug); a
second `.acquire()` from the SAME thread via a *different* `IntegrationLock` instance for
the same `common_dir` shares the same underlying `threading.Lock` (keyed by `common_dir`,
process-wide) and simply times out like any other contended acquire -- it never deadlocks,
because `threading.Lock.acquire(timeout=...)` does not special-case the owning thread.
`Integrator` never triggers either case: it acquires each distinct repo's lock at most once
per `integrate()`/`resume_integration()` call, in sorted repo-key order (deadlock avoidance
across multi-repo tasks -- proven by a two-thread, opposite-order test in
`tests/isolation/test_locks.py`).
"""

from __future__ import annotations

import fcntl
import os
import threading
import time
from pathlib import Path
from types import TracebackType

from ..errors import IntegrationLockTimeoutError
from ..models import DEFAULT_INTEGRATION_LOCK_TIMEOUT_SECONDS

# Companion lock file name, relative to a repo's git common dir (never a git-owned path).
LOCK_FILENAME = "ao-integration.lock"

# Poll granularity for the flock-with-timeout loop (AC-1: `fcntl.flock` has no native
# timeout parameter, unlike `threading.Lock.acquire`). Small enough that a real acquire
# (the common case: no contention) still returns effectively immediately on its first
# non-blocking attempt; coarse enough not to busy-loop.
_POLL_INTERVAL_SECONDS = 0.05

_LOCK_FILE_MODE = 0o600

# Process-wide registry of one `threading.Lock` per (normalized) common_dir, so every
# `IntegrationLock(common_dir)` constructed anywhere in this process -- regardless of which
# `Integrator`/thread created it -- contends on the identical Lock object. Guarded by its
# own lock (never the per-repo locks themselves) to keep registry mutation itself
# thread-safe without adding contention to the per-repo locks it hands out.
_registry_guard = threading.Lock()
_thread_locks: dict[str, threading.Lock] = {}


def _thread_lock_for(common_dir: str) -> threading.Lock:
    with _registry_guard:
        lock = _thread_locks.get(common_dir)
        if lock is None:
            lock = threading.Lock()
            _thread_locks[common_dir] = lock
        return lock


def _try_flock_exclusive(fd: int) -> bool:
    """One non-blocking attempt at an exclusive flock. `True` on success; `False` if
    another process (or, defensively, another fd) already holds it -- never raises for the
    ordinary contention case."""
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


class IntegrationLock:
    """Per-repository integration lock, keyed by the repo's git **common dir** (stable
    across worktrees of the same repo -- HLD §7.1's `IsolatedRepo.common_dir`, not any one
    worktree's own path).

    `acquire(timeout)` is the primary API (`Integrator` calls it directly): bounded,
    non-raising, returns `False` on timeout rather than blocking past it or raising. The
    `with` form (`__enter__`/`__exit__`) is a convenience for callers (tests, and any
    non-Integrator caller) that want fail-fast semantics instead of checking a bool; it
    uses the *timeout* passed to `__init__` (default
    `DEFAULT_INTEGRATION_LOCK_TIMEOUT_SECONDS`) and raises `IntegrationLockTimeoutError` on
    timeout.
    """

    def __init__(
        self, common_dir: str, *, timeout: float = DEFAULT_INTEGRATION_LOCK_TIMEOUT_SECONDS
    ) -> None:
        self.common_dir = os.path.normpath(common_dir)
        self._context_timeout = timeout
        self._thread_lock = _thread_lock_for(self.common_dir)
        self._lock_path = Path(self.common_dir) / LOCK_FILENAME
        self._fd: int | None = None
        self._thread_held = False

    def acquire(self, timeout: float) -> bool:
        """Acquire the thread lock, then the flock, within *timeout* seconds total.
        Returns `False` (never raises, never blocks past *timeout*) if either stage cannot
        be acquired in time. Not reentrant on this instance: raises `RuntimeError` if
        already held by it.
        """
        if self._fd is not None:
            raise RuntimeError(
                f"IntegrationLock({self.common_dir!r}) already held by this instance -- "
                "not reentrant; release() before acquiring again"
            )
        deadline = time.monotonic() + max(0.0, timeout)

        if not self._thread_lock.acquire(timeout=timeout):
            return False
        self._thread_held = True

        try:
            fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, _LOCK_FILE_MODE)
        except OSError:
            self._thread_lock.release()
            self._thread_held = False
            raise

        while True:
            if _try_flock_exclusive(fd):
                self._fd = fd
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                os.close(fd)
                self._thread_lock.release()
                self._thread_held = False
                return False
            time.sleep(min(_POLL_INTERVAL_SECONDS, remaining))

    def release(self) -> None:
        """Release the flock (if held) then the thread lock (if held). Safe to call on an
        instance that never successfully acquired (no-op)."""
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None
        if self._thread_held:
            self._thread_lock.release()
            self._thread_held = False

    def __enter__(self) -> IntegrationLock:
        if not self.acquire(self._context_timeout):
            raise IntegrationLockTimeoutError(self.common_dir, self._context_timeout)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
