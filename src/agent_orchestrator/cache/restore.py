"""Workspace-side byte I/O of the result cache: capture and restore (HLD 8.5; ADR-0019 D20, D29).

`capture_outputs` reads declared output files into store blobs; `restore_outputs` writes verified
blobs to spec-derived, re-validated, non-sensitive destinations. Every restore is TWO-PHASE: all
outputs are validated, staged next to their destinations and hash-verified before the first
destination is touched; only then are the staged files renamed into place.

Every `RestoreMiss` raised here is NOT storable for this dispatch (the coordinator sets no
pending token, HLD 8.5): a partially committed restore contradicts the "absent" prior preseed.
A store-side `CacheUnsafePathError` is deliberately NOT caught: the coordinator handles it as
`unsafe_path` (never evicted, D33).

Imports only the stdlib, `safeio`, `types` and `constants` (HLD 8.0).
"""

from __future__ import annotations

import os
import stat
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256 as _sha256
from typing import BinaryIO, cast
from uuid import uuid4

from agent_orchestrator.cache import safeio
from agent_orchestrator.cache.constants import (
    REASON_BLOB_CORRUPT,
    REASON_BLOB_MISSING,
    REASON_CORRUPT_ENTRY,
    REASON_ENTRY_TOO_LARGE,
    REASON_MANIFEST_MISMATCH,
    REASON_OUTPUT_MISSING,
    REASON_OUTPUT_NOT_REGULAR,
    REASON_RESTORE_FAILED,
    REASON_SENSITIVE_OUTPUT,
    REASON_STORE_ERROR,
    RESTORE_TMP_PREFIX,
    RESTORED_MODE_MASK,
    STORED_MODE_MASK,
    TMP_FILE_MODE,
    TMP_SUFFIX,
)
from agent_orchestrator.cache.types import (
    CacheBlobMissingError,
    CacheEntry,
    CacheError,
    CacheIntegrityError,
    CacheStore,
    CacheTooLargeError,
    CacheUnsafePathError,
    OutputRecord,
    RestoreMiss,
    StoreSkip,
)

# Parent directories of restored outputs are created the way the agent would have created them:
# the umask decides the final mode (unlike the cache's own 0o700 directories).
_RESTORE_DIR_MODE = 0o777
_BACKUP_SUFFIX = ".bak"  # appended to the staging name, so every restore sweep also finds it


class HashingWriter:
    """A write-only file-like that forwards to `sink` while hashing exactly what was written."""

    def __init__(self, sink: BinaryIO) -> None:
        self._sink = sink
        self._digest = _sha256()
        self.size = 0

    def write(self, data: bytes) -> int:
        written = self._sink.write(data)
        self._digest.update(data)
        self.size += len(data)
        return len(data) if written is None else written

    def hexdigest(self) -> str:
        return self._digest.hexdigest()


@dataclass(frozen=True)
class RestoreResult:
    files: int
    bytes: int


@dataclass
class _Staged:
    """One verified staged file. `backup` is a hard link to the destination it will replace;
    `existed` is whether the destination was there at all (rollback: restore or remove it)."""

    tmp: str
    dest: str
    backup: str | None = None
    existed: bool = False


def _unlink_quietly(path: str) -> None:
    """Best-effort removal of a temp name (cleanup must never mask the real outcome)."""
    try:
        os.unlink(path)
    except OSError:
        pass


def _set_mode(fd: int, mode: int) -> None:
    if hasattr(os, "fchmod"):
        os.fchmod(fd, mode)
    else:  # pragma: no cover - non-POSIX: no descriptor chmod, no permission bits to protect
        pass


def _backup_existing(item: _Staged) -> None:
    """Hard-link the destination about to be replaced (still atomic: `os.replace` of the staged
    file stays the only change). Where hard links are unavailable the file is simply not
    backed up and a mid-commit failure leaves that one destination replaced (documented residual,
    HLD 8.5)."""
    item.existed = os.path.lexists(item.dest)
    if not item.existed:
        return
    backup = item.tmp + _BACKUP_SUFFIX
    try:
        os.link(item.dest, backup, follow_symlinks=False)
    except (OSError, NotImplementedError):
        return
    item.backup = backup


def _rollback(done: list[_Staged]) -> None:
    """Undo the renames already done, newest first: put each backup back, or remove a destination
    that did not exist before. Best effort: the restore is already a (non-storable) miss."""
    for item in reversed(done):
        try:
            if item.backup is not None:
                os.replace(item.backup, item.dest)
            elif not item.existed:
                os.unlink(item.dest)
        except OSError:
            pass


def capture_outputs(
    output_abs: Mapping[str, str], store: CacheStore, *, max_entry_bytes: int
) -> list[OutputRecord]:
    """Read every declared output into a blob; return the manifest records (sorted by path).

    `output_abs` maps the normalized relative path to its absolute path. Raises `StoreSkip` when
    the outcome cannot be stored (missing, irregular, unreadable or too large output).
    """
    records: list[OutputRecord] = []
    remaining = max_entry_bytes
    for rel in sorted(output_abs):
        # `output_abs` is the KEY-TIME path: re-validate the whole chain now (SEC-05), exactly as
        # restore does. `O_NOFOLLOW` below covers only the final component, so a parent directory
        # swapped for a link to the outside would otherwise be read through and its bytes stored.
        if os.path.realpath(output_abs[rel]) != output_abs[rel]:
            raise StoreSkip(REASON_OUTPUT_NOT_REGULAR, rel)
        try:
            fd = safeio.open_regular_read(output_abs[rel])
        except FileNotFoundError:
            raise StoreSkip(REASON_OUTPUT_MISSING, rel) from None
        except safeio.NotRegularFileError:
            raise StoreSkip(REASON_OUTPUT_NOT_REGULAR, rel) from None
        except OSError:
            raise StoreSkip(REASON_STORE_ERROR, rel) from None
        with os.fdopen(fd, "rb") as fh:
            st = os.fstat(fh.fileno())
            if st.st_size > remaining:
                raise StoreSkip(REASON_ENTRY_TOO_LARGE, rel)
            try:
                ref = store.put_blob(fh, max_bytes=remaining)  # also bounds a file that grew
            except CacheTooLargeError:
                raise StoreSkip(REASON_ENTRY_TOO_LARGE, rel) from None
            remaining -= ref.size
            records.append(
                OutputRecord(
                    path=rel,
                    sha256=ref.sha256,
                    size=ref.size,
                    mode=stat.S_IMODE(st.st_mode) & STORED_MODE_MASK,
                )
            )
    return records


def restore_outputs(
    entry: CacheEntry,
    expected: Mapping[str, str],
    store: CacheStore,
    *,
    workspace_root: str,
    max_entry_bytes: int,
) -> RestoreResult:
    """Write the entry's blobs to `expected` ({normalized rel path: absolute destination}).

    `expected` is derived ONLY from the current spec; the entry's paths are compared against it and
    never used for I/O. Raises `RestoreMiss` (never storable) on any mismatch or failure.
    """
    manifest: dict[str, OutputRecord] = {}
    for record in entry.outputs:
        if record.path in manifest:
            raise RestoreMiss(REASON_MANIFEST_MISMATCH, evict=True)
        manifest[record.path] = record
    if set(manifest) != set(expected):  # '../escape' lands here (M-1)
        raise RestoreMiss(REASON_MANIFEST_MISMATCH, evict=True)
    if sum(o.size for o in entry.outputs) > max_entry_bytes:
        raise RestoreMiss(REASON_CORRUPT_ENTRY, evict=True)
    staged: list[_Staged] = []
    try:
        for rel in sorted(expected):  # phase 1: validate + stage + verify ALL
            rec, dest = manifest[rel], expected[rel]
            if safeio.is_sensitive_rel_path(rel):  # re-assert at restore time (M-14)
                raise RestoreMiss(REASON_SENSITIVE_OUTPUT, evict=False)
            parent = os.path.dirname(dest)
            safeio.ensure_dir_chain(workspace_root, parent, mode=_RESTORE_DIR_MODE)
            if os.path.realpath(dest) != dest:  # a link appeared (M-5)
                raise RestoreMiss(REASON_RESTORE_FAILED, evict=False, detail="link at destination")
            if os.path.isdir(dest):  # fail in phase 1, before any destination is touched
                raise RestoreMiss(REASON_RESTORE_FAILED, evict=False, detail="IsADirectoryError")
            tmp = os.path.join(
                parent,
                f"{RESTORE_TMP_PREFIX}{os.getpid()}-{threading.get_ident()}-{uuid4().hex}"
                f"{TMP_SUFFIX}",
            )
            fd = safeio.create_exclusive(tmp, TMP_FILE_MODE)
            staged.append(_Staged(tmp, dest))
            with os.fdopen(fd, "wb") as fh:
                writer = HashingWriter(fh)
                try:
                    # HashingWriter is the minimal `.write()` sink read_blob needs.
                    sink = cast(BinaryIO, writer)
                    n = store.read_blob(rec.sha256, sink, max_bytes=rec.size)
                except CacheBlobMissingError:
                    raise RestoreMiss(REASON_BLOB_MISSING, evict=True) from None
                except CacheIntegrityError:
                    raise RestoreMiss(REASON_BLOB_CORRUPT, evict=True, blob=rec.sha256) from None
                except CacheError as exc:
                    if isinstance(exc, CacheUnsafePathError):
                        raise  # never evicted, never swallowed (D33): the coordinator maps it
                    # A transient read failure (EMFILE, EIO) is no evidence of corruption: a
                    # non-evicting miss (SEC-09).
                    raise RestoreMiss(REASON_STORE_ERROR, evict=False, blob=rec.sha256) from exc
                if n != rec.size or writer.hexdigest() != rec.sha256:
                    raise RestoreMiss(REASON_BLOB_CORRUPT, evict=True, blob=rec.sha256)
                # M-8: no setuid / group / other write. Applied to the OPEN descriptor once the
                # content is verified, never by path (SEC-07: a path chmod can follow a link that
                # replaced the staging file); phase 2 is then renames only.
                _set_mode(fh.fileno(), rec.mode & RESTORED_MODE_MASK)
        for item in staged:  # phase 1b: hard-link backups, still before any destination changes
            _backup_existing(item)
        done: list[_Staged] = []
        try:
            for item in staged:  # phase 2: commit (each rename is atomic)
                os.replace(item.tmp, item.dest)
                done.append(item)
        except OSError:
            _rollback(done)  # SEC-07: no partial restore
            raise
        for item in staged:  # committed: the backups are no longer needed
            if item.backup is not None:
                _unlink_quietly(item.backup)
        committed = len(staged)
        staged = []
        return RestoreResult(files=committed, bytes=sum(o.size for o in entry.outputs))
    except (OSError, safeio.SafeIOError) as exc:
        raise RestoreMiss(REASON_RESTORE_FAILED, evict=False, detail=type(exc).__name__) from exc
    finally:
        for item in staged:
            _unlink_quietly(item.tmp)
            if item.backup is not None:
                _unlink_quietly(item.backup)
