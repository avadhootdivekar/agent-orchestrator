"""Neutral, shared file primitives: sidecar flock, atomic write, private dir/file checks.

Introduced by E-Da5Tn9 (dashboard auth, HLD section 11.5) so the credential store adds no
further copies of flock / atomic-write logic. Deliberately **neutral**: it imports nothing from
``agent_orchestrator.auth`` (permission problems raise :class:`UnsafePathError`, which the auth
layer maps to its own ``UnsafePermissionsError``), and the existing call sites
(``isolation/locks.py``, ``service/registry.py``, ``feedback.py``) are not migrated.

Security posture (HLD security M6 / L6):

* Every directory/file verification and permission *fix* goes through an ``O_NOFOLLOW`` file
  descriptor (``os.fstat`` / ``os.fchmod``). There is **no path-based ``chmod`` anywhere**, so a
  path swapped for a symlink between check and fix cannot redirect the change.
* Missing path components are created one at a time with ``os.mkdir(p, mode=0o700)``, never
  ``Path.mkdir(parents=True)`` (whose parents would follow the umask).
* The parent directory is judged with ``os.stat`` of the **resolved** parent.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import os
import pwd
import secrets
import shlex
import stat
import time
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

# Neutral copies of the auth constants (STORE_DIR_MODE / STORE_FILE_MODE / LOCK_POLL_SECONDS);
# this module must not import `agent_orchestrator.auth`. tests/test_fsutil.py pins equality.
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
DEFAULT_LOCK_POLL_SECONDS = 0.05

_GROUP_OTHER_BITS = 0o077
_TEMP_SUFFIX = ".tmp"
_TEMP_TOKEN_BYTES = 8


class LockTimeoutError(OSError):
    """The advisory lock was not acquired within its timeout (auth maps it to 503 / CLI exit 1)."""


class UnsafePathError(OSError):
    """Ownership, mode, symlink or parent-directory problem. The message names the exact fix."""


def _owner_name(uid: int) -> str:
    try:
        return f"{pwd.getpwuid(uid).pw_name} (uid {uid})"
    except KeyError:
        return f"uid {uid}"


class FileLock:
    """Exclusive advisory lock on a SIDECAR file.

    The data files are replaced by rename (:func:`atomic_write_bytes`), so they cannot carry the
    lock themselves; a stable sidecar does. ``flock`` locks belong to the open file description,
    so two ``FileLock`` instances (threads or processes) contend correctly. ``monotonic`` and
    ``sleep`` are injectable so tests need no real waiting.
    """

    def __init__(
        self,
        lock_path: Path,
        *,
        timeout: float,
        poll: float = DEFAULT_LOCK_POLL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._path = lock_path
        self._timeout = timeout
        self._poll = poll
        self._monotonic = monotonic
        self._sleep = sleep
        self._fd: int | None = None

    def __enter__(self) -> FileLock:
        if self._fd is not None:
            raise RuntimeError(f"FileLock {self._path} is not reentrant")
        # O_NOFOLLOW: a symlinked lock path fails (OSError) instead of locking its target.
        fd = os.open(
            self._path,
            os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
            PRIVATE_FILE_MODE,
        )
        deadline = self._monotonic() + self._timeout
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if self._monotonic() >= deadline:
                        raise LockTimeoutError(
                            f"timed out after {self._timeout:g}s waiting for lock {self._path}"
                        ) from None
                    self._sleep(self._poll)
        except BaseException:
            os.close(fd)
            raise
        self._fd = fd
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def atomic_write_bytes(path: Path, data: bytes, *, mode: int = PRIVATE_FILE_MODE) -> None:
    """Replace ``path`` with ``data`` atomically and durably.

    ``O_EXCL|O_NOFOLLOW`` temp file in the same directory (so ``os.replace`` is atomic and a
    pre-planted symlink can never redirect the write) -> ``fsync`` -> ``os.replace`` -> directory
    ``fsync``. The file is ``mode`` regardless of the umask. On any failure after the temp file
    was created it is removed (best effort) and the original ``path`` is left untouched.
    """
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(_TEMP_TOKEN_BYTES)}{_TEMP_SUFFIX}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    try:
        try:
            os.fchmod(fd, mode)  # umask-proof (fd-based, no symlink race)
            view = memoryview(data)
            while view:
                view = view[os.write(fd, view) :]
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def remove_stale_temp_files(path: Path) -> int:
    """Unlink orphaned ``.<name>.*.tmp`` siblings of ``path``; return how many were removed.

    A crash between temp-file creation and ``os.replace`` orphans a file that may hold old
    hashes or seeds. CALL ONLY WHILE HOLDING THE WRITER LOCK, otherwise a live writer's temp
    file could be removed.
    """
    prefix = f".{path.name}."
    removed = 0
    try:
        entries = list(os.scandir(path.parent))
    except FileNotFoundError:
        return 0
    for entry in entries:
        name = entry.name
        if len(name) <= len(prefix) + len(_TEMP_SUFFIX):
            continue
        if not (name.startswith(prefix) and name.endswith(_TEMP_SUFFIX)):
            continue
        try:
            os.unlink(entry.path)  # a symlink is removed itself, never its target
        except OSError:
            continue
        removed += 1
    return removed


def _mkdir_missing(path: Path) -> bool:
    """Create every missing component of ``path`` with ``os.mkdir(p, mode=0o700)``.

    Returns True when this call created the last component. ``FileExistsError`` (a race with
    another creator) is tolerated; the caller verifies the directory afterwards either way.
    """
    missing: list[Path] = []
    cur = path
    while not os.path.lexists(cur):
        missing.append(cur)
        if cur.parent == cur:
            break
        cur = cur.parent
    created_last = False
    for component in reversed(missing):
        try:
            os.mkdir(component, mode=PRIVATE_DIR_MODE)
        except FileExistsError:
            continue
        created_last = component == path
    return created_last


def _open_nofollow(path: Path, flags: int, what: str) -> int:
    """``os.open`` with ``O_NOFOLLOW``; a symlink (or non-directory) becomes UnsafePathError."""
    try:
        return os.open(path, flags | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise UnsafePathError(f"{path} must be a real {what}, not a symlink: {exc}") from exc
        raise


def _check_parent(path: Path) -> list[str]:
    """Judge the RESOLVED parent of ``path`` (HLD section 11.5, security L6)."""
    parent = path.parent.resolve()
    st = os.stat(parent)
    mode = stat.S_IMODE(st.st_mode)
    quoted = shlex.quote(str(parent))
    if st.st_uid == 0 and mode & stat.S_ISVTX:
        return []  # root-owned sticky (a /tmp-style directory): only the owner can rename entries
    if mode & stat.S_IWOTH:
        raise UnsafePathError(
            f"parent directory {parent} is writable by other users; run chmod o-w {quoted}"
        )
    if mode & stat.S_IWGRP:
        if st.st_uid != os.geteuid():
            raise UnsafePathError(
                f"parent directory {parent} is group-writable and owned by "
                f"{_owner_name(st.st_uid)}; run chmod g-w {quoted}"
            )
        # umask-002 hosts (Debian/Ubuntu user-private groups): not an error, a warning.
        return [f"{parent} is group-writable; run chmod g-w {quoted}"]
    return []


def ensure_private_dir(path: Path, *, create: bool, fix: bool) -> list[str]:
    """Ensure ``path`` is a real directory, owned by us, mode 0700; judge its parent.

    ``create=True`` makes missing components (``os.mkdir(mode=0o700)`` each) and 0700s the
    created directory once through an fd. Verification and ``fix`` use an
    ``O_NOFOLLOW|O_DIRECTORY`` fd (``fstat`` / ``fchmod``): no path-based chmod, no symlink race.
    Returns non-fatal notices (tightened mode, group-writable parent). Raises
    :class:`UnsafePathError` for a symlink, a foreign owner, loose permissions without ``fix``,
    or an unsafe parent; ``FileNotFoundError`` propagates when ``create=False`` and it is missing.
    """
    notices: list[str] = []
    created = _mkdir_missing(path) if create else False
    fd = _open_nofollow(path, os.O_RDONLY | os.O_DIRECTORY, "directory")
    try:
        st = os.fstat(fd)
        if st.st_uid != os.geteuid():
            raise UnsafePathError(
                f"{path} is owned by {_owner_name(st.st_uid)}, not by you "
                f"({_owner_name(os.geteuid())}); use a directory you own"
            )
        notices.extend(_check_parent(path))
        if created:
            os.fchmod(fd, PRIVATE_DIR_MODE)  # umask-proof, once, on the fd
        elif stat.S_IMODE(st.st_mode) & _GROUP_OTHER_BITS:
            fix_cmd = f"chmod 700 {shlex.quote(str(path))}"
            if not fix:
                raise UnsafePathError(
                    f"{path} has mode {stat.S_IMODE(st.st_mode):04o}; run {fix_cmd}"
                )
            os.fchmod(fd, PRIVATE_DIR_MODE)
            notices.append(f"tightened {path} to mode 0700 (was {stat.S_IMODE(st.st_mode):04o})")
    finally:
        os.close(fd)
    return notices


def check_private_file(path: Path, *, fix: bool) -> list[str]:
    """Ensure ``path`` is a regular, non-symlink file owned by us with mode 0600 (no group/other).

    Everything runs on one ``O_NOFOLLOW`` fd (no check/use race); ``fix`` is ``os.fchmod``.
    Returns notices; raises :class:`UnsafePathError`; ``FileNotFoundError`` propagates.
    """
    fd = _open_nofollow(path, os.O_RDONLY | os.O_NONBLOCK, "file")
    notices: list[str] = []
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise UnsafePathError(f"{path} must be a regular file")
        if st.st_uid != os.geteuid():
            raise UnsafePathError(
                f"{path} is owned by {_owner_name(st.st_uid)}, not by you "
                f"({_owner_name(os.geteuid())})"
            )
        mode = stat.S_IMODE(st.st_mode)
        if mode & _GROUP_OTHER_BITS:
            fix_cmd = f"chmod 600 {shlex.quote(str(path))}"
            if not fix:
                raise UnsafePathError(f"{path} has mode {mode:04o}; run {fix_cmd}")
            os.fchmod(fd, PRIVATE_FILE_MODE)
            notices.append(f"tightened {path} to mode 0600 (was {mode:04o})")
    finally:
        os.close(fd)
    return notices
