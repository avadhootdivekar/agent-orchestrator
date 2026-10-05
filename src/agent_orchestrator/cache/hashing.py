"""Bounded, hostile-filesystem-safe content hashing for the result-cache key (HLD 8.2.3).

`HashBudget` caps the bytes and file count one lookup may hash. `hash_regular_file` hashes a
regular file (never follows a symlink, never blocks on a FIFO, detects a file that changes while
it is read). `hash_directory` hashes a canonical, order-independent manifest of a directory tree.
`digest_path` dispatches on the path's type. Every failure is an `UncacheableError` with one of
the `input_*` reasons: an input that cannot be hashed safely makes the task uncacheable
(fail-closed), never a silent partial hash.

Directory walks skip only `.git` (any depth), the caller's `skip_abs` (the `.orchestrator` state
dir) and `exclude_abs` (the task's own declared outputs). Everything else is hashed on purpose,
including editor / OS noise and a crash-leftover restore staging file
(`.ao-result-cache-*.tmp[.bak]`): it causes false misses, never false hits. A name-based skip would
let any writer hide a file from the key (G2-S1); `ao cache prune` sweeps the leftovers.
"""

from __future__ import annotations

import os
import stat
from hashlib import sha256

from agent_orchestrator.cache import safeio
from agent_orchestrator.cache.constants import (
    DIR_DIGEST_SCHEMA,
    DIR_ENTRY_DIR,
    DIR_ENTRY_FILE,
    DIR_WALK_SKIP_NAMES,
    HASH_CHUNK_BYTES,
    KIND_DIR,
    KIND_FILE,
    REASON_INPUT_MISSING,
    REASON_INPUT_NOT_REGULAR,
    REASON_INPUT_TOO_LARGE,
    REASON_INPUT_UNREADABLE,
    REASON_INPUT_UNSTABLE,
)
from agent_orchestrator.cache.types import Digest, UncacheableError, canonical_json


class HashBudget:
    """Per-lookup byte / file allowance, charged BEFORE any bytes are read."""

    def __init__(self, max_bytes: int, max_files: int) -> None:
        self.max_bytes = max_bytes
        self.max_files = max_files
        self.bytes = 0
        self.files = 0

    def charge(self, nbytes: int, nfiles: int) -> None:
        self.bytes += nbytes
        self.files += nfiles
        if self.bytes > self.max_bytes or self.files > self.max_files:
            raise UncacheableError(REASON_INPUT_TOO_LARGE, f"bytes={self.bytes} files={self.files}")


def hash_regular_file(abs_path: str, budget: HashBudget) -> Digest:
    """sha256 of a REGULAR file's bytes; `size` is the byte count."""
    try:
        fd = safeio.open_regular_read(abs_path)
    except FileNotFoundError as e:
        raise UncacheableError(REASON_INPUT_MISSING, abs_path) from e
    except safeio.NotRegularFileError as e:
        raise UncacheableError(REASON_INPUT_NOT_REGULAR, abs_path) from e
    except OSError as e:
        raise UncacheableError(REASON_INPUT_UNREADABLE, abs_path) from e
    try:
        size = os.fstat(fd).st_size
        budget.charge(size, 1)  # bounded BEFORE the bytes are read
        digest = sha256()
        seen = 0
        while chunk := os.read(fd, HASH_CHUNK_BYTES):
            seen += len(chunk)
            if seen > size:  # grew while being read
                raise UncacheableError(REASON_INPUT_UNSTABLE, abs_path)
            digest.update(chunk)
        if seen != size:  # shrank while being read
            raise UncacheableError(REASON_INPUT_UNSTABLE, abs_path)
        return Digest(KIND_FILE, digest.hexdigest(), seen)
    except OSError as e:
        raise UncacheableError(REASON_INPUT_UNREADABLE, abs_path) from e
    finally:
        os.close(fd)


def hash_directory(
    abs_dir: str,
    budget: HashBudget,
    *,
    exclude_abs: frozenset[str] | set[str],
    skip_abs: frozenset[str] | set[str],
) -> Digest:
    """sha256 of the canonical manifest of *abs_dir* (`ao.result-cache.dir/v1`).

    The manifest is `[["D", rel], ["F", rel, size, sha256], ...]` sorted by the filesystem-encoded
    relative path, so creation / scan order never matters and a rename changes the digest. A
    symlink or special file inside raises `input_not_regular` without being opened.
    """
    entries: list[list[object]] = []
    stack = [abs_dir]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for e in it:
                    if e.name in DIR_WALK_SKIP_NAMES or e.path in skip_abs or e.path in exclude_abs:
                        continue
                    rel = safeio.posix_rel(e.path, abs_dir)
                    if e.is_symlink():
                        raise UncacheableError(REASON_INPUT_NOT_REGULAR, e.path)
                    if e.is_dir(follow_symlinks=False):
                        budget.charge(0, 1)
                        entries.append([DIR_ENTRY_DIR, rel])
                        stack.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        dg = hash_regular_file(e.path, budget)
                        entries.append([DIR_ENTRY_FILE, rel, dg.size, dg.sha256])
                    else:  # FIFO / socket / device: never opened
                        raise UncacheableError(REASON_INPUT_NOT_REGULAR, e.path)
        except OSError as e:
            raise UncacheableError(REASON_INPUT_UNREADABLE, current) from e
    entries.sort(key=lambda item: os.fsencode(str(item[1])))
    manifest = canonical_json({"schema": DIR_DIGEST_SCHEMA, "entries": entries})
    return Digest(KIND_DIR, sha256(manifest.encode("ascii")).hexdigest(), len(entries))


def digest_path(
    abs_path: str,
    budget: HashBudget,
    *,
    exclude_abs: frozenset[str] | set[str] = frozenset(),
    skip_abs: frozenset[str] | set[str] = frozenset(),
) -> Digest:
    """Digest of a regular file or a directory; anything else is `input_not_regular`."""
    try:
        mode = os.lstat(abs_path).st_mode
    except FileNotFoundError as e:
        raise UncacheableError(REASON_INPUT_MISSING, abs_path) from e
    except OSError as e:
        raise UncacheableError(REASON_INPUT_UNREADABLE, abs_path) from e
    if stat.S_ISDIR(mode):
        return hash_directory(abs_path, budget, exclude_abs=exclude_abs, skip_abs=skip_abs)
    if stat.S_ISREG(mode):
        return hash_regular_file(abs_path, budget)
    raise UncacheableError(REASON_INPUT_NOT_REGULAR, abs_path)  # symlink, FIFO, device, socket
