"""`WorkspaceRunLock` — the per-workspace, cross-process run lock that makes D5's checkout-
sync guarantee true under two concurrent `ao` runs in one workspace (E-Wk9Tz3 T-Wl2Bq7, HLD
§12.3, ADR-0013 D8).

**What this protects, and what it does not.** The per-repo `IntegrationLock` (`locks.py`)
plus the `update-ref` CAS already serialize two runs' `integrate()` calls correctly, and each
run lands onto its own integration ref — landing is safe under concurrency BY CONSTRUCTION,
with no help from this module. The unsafe part is the checkout sync
(`Orchestrator._sync_checkout`): a fast-forward of the ONE shared physical checkout, which has
no lock, no CAS, and no awareness that a second run has a different integration branch. This
lock exists to serialize exactly that: only one run may hold it (and therefore be allowed to
fast-forward the shared checkout) at a time, for the run's entire lifetime — not just around
the fast-forward call itself (see the HLD's own "why not lock only the fast-forward" note:
locking only the FF would make the sync atomic but not the window between it and a
non-isolated task reading the tree right after).

Backing file: ``$AO_STATE_DIR/runlocks/<workspace_key>.lock`` — `flock`-held (exclusive,
non-blocking) for the life of the run, payload ``{run_id, pid, boot_id, at}`` as deterministic
JSON. A single, bounded, non-blocking `acquire()` attempt (never a poll/wait loop, unlike
`IntegrationLock`) -- a workspace lock denial is a policy decision (degrade / proceed
unprotected), never something worth blocking a run's startup on.

**Stale-lock reclamation.** `service/supervisor.py`'s own singleton lock (`acquire_singleton_
lock`) is the only prior art for a `flock`-based process-liveness lock in this codebase — read
first, per this ticket's own instruction. It turns out NOT to be reusable as-is: it relies
solely on `flock`'s OS-guaranteed auto-release on process death (verified in its own tests) and
never inspects the lock file's content to decide anything; its pid is written only so a
contending process's error message can name the holder. That is sufficient for a single-
machine daemon lock, but this ticket's AC explicitly requires DEAD-PID and DIFFERENT-BOOT-ID
detection as distinctly testable, loggable outcomes (`integration.runlock_reclaimed`) — a
purely `flock`-only design cannot distinguish "fresh, uncontended lock" from "reclaimed from a
crashed holder" for that log line. This module therefore layers content inspection on top of
`flock` (still `flock`-authoritative for the grant/deny decision itself — content is read only
to CLASSIFY an already-uncontended acquire as fresh vs. reclaimed, never to override a
genuinely contended `flock`): `release()` unlinks the lock file on a clean exit, so any
leftover content found alongside a successful, uncontended `flock` unambiguously means the
previous holder did not release cleanly (crashed) — pid-liveness and boot-id comparison
(mirroring `service/supervisor.py::_pid_alive`/`_read_os_boot_id`, duplicated here rather than
imported: both are private-by-convention module-local helpers there, and
`_pid_alive`'s OWN docstring already establishes local-duplication, not cross-module
import, as this codebase's precedent for this exact helper) then classify WHY it was stale for
the log message.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Literal

from ..errors import WorkspaceLockHeldError
from . import paths as isolation_paths

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------------------
# Constants (no magic literals at call sites)
# ---------------------------------------------------------------------------------------

_RUNLOCKS_SUBDIR = "runlocks"
_LOCK_SUFFIX = ".lock"
_LOCK_FILE_MODE = 0o600

# Payload keys (deterministic JSON: `json.dumps(..., sort_keys=True)`).
_PAYLOAD_RUN_ID = "run_id"
_PAYLOAD_PID = "pid"
_PAYLOAD_BOOT_ID = "boot_id"
_PAYLOAD_AT = "at"

StaleReason = Literal["dead_pid", "boot_id_mismatch"]


# ---------------------------------------------------------------------------------------
# pid-liveness / boot-id helpers -- mirrors `service/supervisor.py::_pid_alive`/
# `_read_os_boot_id` (see module docstring: duplicated, not imported, matching that
# module's own established precedent for this exact pair of helpers).
# ---------------------------------------------------------------------------------------


def _pid_alive(pid: int) -> bool:
    """Existence probe for a PID this process did not itself spawn. `True` on
    `PermissionError` (the process exists, just not signalable by us) -- only a genuine
    `ProcessLookupError` means "gone"."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_os_boot_id() -> str | None:
    """Best-effort OS boot id: Linux only, `None` everywhere else or on any read error --
    advisory only, never raises."""
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return None


# ---------------------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkspaceLockClaim:
    """The outcome of one `WorkspaceRunLock.acquire()` call."""

    granted: bool
    # Populated on both a denied claim (the live holder) and a granted-via-reclaim claim
    # (the stale holder being taken over from) -- `None` on a genuinely fresh, uncontended
    # acquire with no prior content at all.
    holder_run_id: str | None = None
    holder_pid: int | None = None
    holder_boot_id: str | None = None
    # True iff this acquire succeeded over a lock file some prior holder left behind
    # without a clean `release()` (AC-2). Always False when `granted` is False.
    reclaimed: bool = False
    # Best-effort classification of WHY the reclaimed holder was stale -- diagnostic only,
    # never load-bearing for the grant/deny decision itself (see module docstring). Review
    # W-5: can be `None` even when `reclaimed` is True -- a stale lock's pid can (rarely)
    # coincide with a DIFFERENT, genuinely live process on the SAME boot (pid reuse), in
    # which case neither classification applies even though the acquire is still provably
    # a reclaim (an uncontended `flock` alone already proves the prior holder is gone).
    # `stale_reason: None` + `reclaimed: True` is an expected combination, not a
    # contradiction to debug.
    stale_reason: StaleReason | None = None


def _lock_path_for(workspace_root: str) -> Path:
    key = isolation_paths.workspace_key(workspace_root)
    return isolation_paths.state_dir() / _RUNLOCKS_SUBDIR / f"{key}{_LOCK_SUFFIX}"


# ---------------------------------------------------------------------------------------
# WorkspaceRunLock
# ---------------------------------------------------------------------------------------


class WorkspaceRunLock:
    """Per-workspace, cross-process run lock (see module docstring). One instance is
    acquired at most once (not reentrant -- mirrors `IntegrationLock`'s own contract) and
    held for the life of one `ao run`/`ao resume` process.

    Parameters
    ----------
    workspace_root:
        The workspace whose checkout this run would fast-forward -- the sole input to the
        lock's on-disk path (`isolation.paths.workspace_key`, shared with every other
        workspace-scoped path in this epic; no second path-resolution scheme).
    run_id:
        This run's id -- written into the lock payload and echoed on every acquired
        `WorkspaceLockClaim`.
    clock/pid_alive/boot_id_reader:
        Injectable for deterministic tests (fixed timestamps; a fake liveness/boot-id
        oracle that never touches a real `/proc` or a real signal). Defaults to
        `datetime.now(UTC)`, this module's own `_pid_alive`, and `_read_os_boot_id`.
    """

    def __init__(
        self,
        workspace_root: str,
        run_id: str,
        *,
        clock: Callable[[], datetime] | None = None,
        pid_alive: Callable[[int], bool] | None = None,
        boot_id_reader: Callable[[], str | None] | None = None,
    ) -> None:
        self._run_id = run_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._pid_alive = pid_alive or _pid_alive
        self._boot_id = (boot_id_reader or _read_os_boot_id)()
        self._lock_path = _lock_path_for(workspace_root)
        self._fd: int | None = None

    @property
    def lock_path(self) -> Path:
        return self._lock_path

    # --- payload (de)serialization ---------------------------------------------------

    def _payload(self) -> dict[str, object]:
        return {
            _PAYLOAD_RUN_ID: self._run_id,
            _PAYLOAD_PID: os.getpid(),
            _PAYLOAD_BOOT_ID: self._boot_id,
            _PAYLOAD_AT: self._clock().isoformat(),
        }

    def _write_payload(self, fd: int) -> None:
        raw = json.dumps(self._payload(), sort_keys=True).encode("utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        os.ftruncate(fd, 0)
        os.write(fd, raw)
        os.fsync(fd)

    @staticmethod
    def _parse_payload(raw: bytes) -> dict[str, object] | None:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _read_payload_from_fd(self, fd: int) -> dict[str, object] | None:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, 65536)
        if not raw:
            return None
        return self._parse_payload(raw)

    def _read_payload_from_path(self) -> dict[str, object] | None:
        try:
            raw = self._lock_path.read_bytes()
        except OSError:
            return None
        if not raw:
            return None
        return self._parse_payload(raw)

    def _stale_reason(self, prior: dict[str, object]) -> StaleReason | None:
        pid = prior.get(_PAYLOAD_PID)
        if isinstance(pid, int) and not self._pid_alive(pid):
            return "dead_pid"
        if prior.get(_PAYLOAD_BOOT_ID) != self._boot_id:
            return "boot_id_mismatch"
        return None

    @staticmethod
    def _claim_from_payload(
        payload: dict[str, object] | None, *, granted: bool
    ) -> WorkspaceLockClaim:
        if payload is None:
            return WorkspaceLockClaim(granted=granted)
        run_id = payload.get(_PAYLOAD_RUN_ID)
        pid = payload.get(_PAYLOAD_PID)
        boot_id = payload.get(_PAYLOAD_BOOT_ID)
        return WorkspaceLockClaim(
            granted=granted,
            holder_run_id=run_id if isinstance(run_id, str) else None,
            holder_pid=pid if isinstance(pid, int) else None,
            holder_boot_id=boot_id if isinstance(boot_id, str) else None,
        )

    # --- primary API ---------------------------------------------------------------

    def acquire(self) -> WorkspaceLockClaim:
        """One bounded, non-blocking acquire attempt. Never raises for the ordinary
        "already held by a live run" case -- returns `granted=False` (mirrors
        `IntegrationLock.acquire()`'s own "expected, recoverable, not exceptional"
        contract). Not reentrant: raises `RuntimeError` if this instance already holds
        the lock.
        """
        if self._fd is not None:
            raise RuntimeError(
                f"WorkspaceRunLock({self._lock_path!s}) already held by this instance -- "
                "not reentrant; release() before acquiring again"
            )
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, _LOCK_FILE_MODE)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Genuinely contended: `flock` is the ONLY authority for this branch -- never
            # overridden by pid/boot-id content inspection (see module docstring). The
            # held-payload read below is best-effort, for the denial message only.
            holder_payload = self._read_payload_from_path()
            os.close(fd)
            return self._claim_from_payload(holder_payload, granted=False)

        # Uncontended. Any content already on disk here can ONLY be a lock some prior
        # holder did not release cleanly (release() unlinks on a clean exit) -- flock
        # cannot have succeeded while a live holder's fd was still open on this same
        # inode. Classified (not gated) by pid/boot-id purely for the log line.
        prior = self._read_payload_from_fd(fd)
        self._write_payload(fd)
        self._fd = fd
        if prior is None:
            return WorkspaceLockClaim(granted=True)
        claim = self._claim_from_payload(prior, granted=True)
        stale_reason = self._stale_reason(prior)
        return WorkspaceLockClaim(
            granted=True,
            holder_run_id=claim.holder_run_id,
            holder_pid=claim.holder_pid,
            holder_boot_id=claim.holder_boot_id,
            reclaimed=True,
            stale_reason=stale_reason,
        )

    def release(self) -> None:
        """Release the flock, close the fd, and unlink the lock file. NEVER raises --
        every step is independently best-effort. Safe to call on an instance that never
        successfully acquired (no-op)."""
        fd = self._fd
        self._fd = None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            logger.debug("workspace_run_lock.unlock_error", exc_info=True)
        try:
            os.close(fd)
        except OSError:
            logger.debug("workspace_run_lock.close_error", exc_info=True)
        try:
            self._lock_path.unlink(missing_ok=True)
        except OSError:
            logger.debug("workspace_run_lock.unlink_error", exc_info=True)

    def is_held_by_other(self) -> bool:
        """Point-in-time probe, NOT the authoritative gate (`acquire()` is) -- a TOCTOU
        race is possible between this call and a subsequent `acquire()`. `False`
        immediately when THIS instance already holds the lock (never "held by other" in
        that case, without probing anything)."""
        if self._fd is not None:
            return False
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        probe_fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, _LOCK_FILE_MODE)
        try:
            fcntl.flock(probe_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        else:
            fcntl.flock(probe_fd, fcntl.LOCK_UN)
            return False
        finally:
            os.close(probe_fd)

    def __enter__(self) -> WorkspaceRunLock:
        claim = self.acquire()
        if not claim.granted:
            raise WorkspaceLockHeldError(
                str(self._lock_path), claim.holder_run_id, claim.holder_pid
            )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
