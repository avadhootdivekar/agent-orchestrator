"""Hostile-filesystem-safe I/O primitives shared by hashing, fingerprint, store and restore.

HLD 8.2.2 / ADR-0019 D18, D29, D31, D33. Every cache-side file open goes through this module (the
AST guard in tests/cache/test_ast_guard.py forbids bare `open(` / `os.open(` elsewhere in the
package). Imports only the stdlib and `constants` (HLD 8.0).
"""

from __future__ import annotations

import errno
import os
import re
import stat

from agent_orchestrator.cache.constants import (
    CACHE_DIR_MODE,
    GROUP_OTHER_WRITE_BITS,
    HASH_CHUNK_BYTES,
    RESTORE_BACKUP_SUFFIX,
    RESTORE_TMP_PREFIX,
    SENSITIVE_BASENAMES,
    SENSITIVE_PATH_COMPONENTS,
    TMP_FILE_MODE,
    TMP_SUFFIX,
)

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
O_SAFE_READ = os.O_RDONLY | _NOFOLLOW | _NONBLOCK | _CLOEXEC
O_SAFE_CREATE = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC
_O_SAFE_DIR = os.O_RDONLY | _NOFOLLOW | _DIRECTORY | _CLOEXEC

# C0 controls (incl. \n, \t, \r, ESC), DEL and C1 controls (CWE-150 terminal/log injection).
_CONTROL_CHARS_RE = re.compile("[\x00-\x1f\x7f-\x9f]")


class SafeIOError(Exception):
    """Base of every safeio failure (never raised bare)."""


class NotRegularFileError(SafeIOError):
    """symlink (ELOOP), directory, FIFO, device, socket."""


class TooLargeError(SafeIOError):
    """A file larger than the caller's bound."""


class UnsafePathError(SafeIOError):
    """A symlinked or foreign component, or a path outside its root."""


def open_regular_read(path: str) -> int:
    """fd for a REGULAR file; never follows a final symlink, never blocks on a FIFO."""
    try:
        fd = os.open(path, O_SAFE_READ)
    except OSError as e:  # FileNotFoundError passes through unchanged
        if e.errno == errno.ELOOP:
            raise NotRegularFileError(path) from e
        raise
    try:
        is_regular = stat.S_ISREG(os.fstat(fd).st_mode)
    except BaseException:
        os.close(fd)
        raise
    if not is_regular:
        os.close(fd)
        raise NotRegularFileError(path)
    return fd


def read_bounded(path: str, max_bytes: int) -> bytes:
    """open_regular_read + an fstat size check; never reads more than max_bytes + 1."""
    fd = open_regular_read(path)
    try:
        if os.fstat(fd).st_size > max_bytes:
            raise TooLargeError(path)
        chunks: list[bytes] = []
        remaining = max_bytes + 1  # one byte past the bound proves a file that grew
        while remaining > 0:
            chunk = os.read(fd, min(HASH_CHUNK_BYTES, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if len(data) > max_bytes:
        raise TooLargeError(path)
    return data


def create_exclusive(path: str, mode: int = TMP_FILE_MODE) -> int:
    """os.open(path, O_SAFE_CREATE, mode): fails (FileExistsError) if the path exists."""
    return os.open(path, O_SAFE_CREATE, mode)


def _components_below(root: str, path: str) -> list[str]:
    """Lexical components of `path` under `root` (root itself excluded)."""
    norm_root, norm_path = os.path.normpath(root), os.path.normpath(path)
    rel = os.path.relpath(norm_path, norm_root)
    if rel == os.curdir:
        return []
    parts = rel.split(os.sep)
    if parts[0] == os.pardir:
        raise UnsafePathError(f"path is outside the root: {path}")
    return parts


def _check_component(path: str) -> bool:
    """lstat one component: True if it exists as a real directory, False if missing."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    if stat.S_ISLNK(st.st_mode):
        raise UnsafePathError(f"symlinked component: {path}")
    if not stat.S_ISDIR(st.st_mode):
        raise UnsafePathError(f"component is not a directory: {path}")
    return True


def check_dir_chain(root: str, path: str) -> None:
    """Every EXISTING component from root (inclusive) down to path: lstat, S_ISDIR, not a symlink.

    Creates nothing; the walk stops at the first missing component (nothing can exist below it).
    """
    parts = _components_below(root, path)
    current = os.path.normpath(root)
    if not _check_component(current):
        return
    for part in parts:
        current = os.path.join(current, part)
        if not _check_component(current):
            return


def _mkdir_checked(path: str, mode: int) -> None:
    try:
        os.mkdir(path, mode)
    except FileExistsError:
        _check_component(path)  # lost a race or pre-existing: it must still be a real directory
    else:
        _check_component(path)  # also catches a swap between mkdir and use


def ensure_dir_chain(root: str, path: str, mode: int) -> None:
    """os.mkdir one component at a time (root included if missing; its parent must exist);
    on EEXIST, lstat-check that component. Never follows or creates through a symlink."""
    parts = _components_below(root, path)
    current = os.path.normpath(root)
    _mkdir_checked(current, mode)
    for part in parts:
        current = os.path.join(current, part)
        _mkdir_checked(current, mode)


def _owned_by_us(st: os.stat_result) -> bool:
    geteuid = getattr(os, "geteuid", None)
    return geteuid is None or st.st_uid == geteuid()  # no uid concept on Windows


def _tighten_owned_root(root: str) -> None:
    """chmod an owned, group/other-writable root to 0o700 without following a swapped link."""
    fd = os.open(root, _O_SAFE_DIR)
    try:
        st = os.fstat(fd)
        if not stat.S_ISDIR(st.st_mode) or not _owned_by_us(st):
            raise UnsafePathError(f"root changed while checking: {root}")
        os.fchmod(fd, CACHE_DIR_MODE)
    finally:
        os.close(fd)


def check_root_dir(root: str, *, workspace_root: str) -> None:
    """Root: not a symlink, S_ISDIR, st_uid == geteuid(), no group/other write (chmod 0o700
    instead when we own it), realpath inside the workspace; `.orchestrator` not a symlink.

    A missing root is fine (it is created lazily by a write); a missing component is not created.
    """
    parts = _components_below(workspace_root, root)
    current = os.path.normpath(workspace_root)
    for part in parts[:-1]:  # the intermediate components, e.g. `.orchestrator`
        current = os.path.join(current, part)
        if not _check_component(current):
            return
    try:
        st = os.lstat(root)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(st.st_mode):
        raise UnsafePathError(f"cache root is a symlink: {root}")
    if not stat.S_ISDIR(st.st_mode):
        raise UnsafePathError(f"cache root is not a directory: {root}")
    if not _owned_by_us(st):
        raise UnsafePathError(f"cache root is owned by another user: {root}")
    if st.st_mode & GROUP_OTHER_WRITE_BITS:
        if hasattr(os, "fchmod"):
            _tighten_owned_root(root)  # we own it (checked above): fix instead of refusing
        else:  # pragma: no cover - non-POSIX
            raise UnsafePathError(f"cache root is group/other-writable: {root}")
    real_root, real_ws = os.path.realpath(root), os.path.realpath(workspace_root)
    if os.path.commonpath([real_root, real_ws]) != real_ws:
        raise UnsafePathError(f"cache root resolves outside the workspace: {root}")


# Case-folded once: on a case-insensitive filesystem (macOS, Windows) `.GIT/hooks` and
# `Claude.md` name the protected targets (SEC-06). A false refusal only makes a task uncacheable.
_SENSITIVE_COMPONENTS_FOLDED = frozenset(c.casefold() for c in SENSITIVE_PATH_COMPONENTS)
_SENSITIVE_BASENAMES_FOLDED = frozenset(b.casefold() for b in SENSITIVE_BASENAMES)


def is_sensitive_rel_path(rel: str) -> bool:
    """Any component in SENSITIVE_PATH_COMPONENTS, or basename in SENSITIVE_BASENAMES.

    Case-INSENSITIVE (D29 addendum, SEC-06): `CLAUDE.md`, `Claude.md` and `docs/claude.md` are
    all sensitive; the filesystem decides what a name refers to, so assume the worst.
    """
    parts = [p.casefold() for p in rel.replace(os.sep, "/").split("/") if p]
    if any(p in _SENSITIVE_COMPONENTS_FOLDED for p in parts):
        return True
    return bool(parts) and parts[-1] in _SENSITIVE_BASENAMES_FOLDED


def is_restore_tmp_name(name: str) -> bool:
    """True for a restore staging name (`.ao-result-cache-*.tmp`) or its `.bak` backup link.

    A crash (SIGKILL, power loss) between staging and commit leaves such a file next to the
    output. Directory-input hashing ignores these names so a stale leftover can never change a
    key (SEC G1b S-1); the restore itself never deletes anything it did not create.
    """
    stem = name.removesuffix(RESTORE_BACKUP_SUFFIX)
    return name.startswith(RESTORE_TMP_PREFIX) and stem.endswith(TMP_SUFFIX)


def posix_rel(path: str, base: str) -> str:
    """normpath(relpath(path, base)) with "/" separators."""
    return os.path.normpath(os.path.relpath(path, base)).replace(os.sep, "/")


def strip_control_chars(text: str) -> str:
    """Remove C0/C1 control characters and DEL from text printed by the CLI (CWE-150)."""
    return _CONTROL_CHARS_RE.sub("", text)
