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
    CacheIntegrityError,
    CacheStore,
    CacheTooLargeError,
    OutputRecord,
    RestoreMiss,
    StoreSkip,
)

# Parent directories of restored outputs are created the way the agent would have created them:
# the umask decides the final mode (unlike the cache's own 0o700 directories).
_RESTORE_DIR_MODE = 0o777


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
    staged: list[tuple[str, str, int]] = []  # (tmp, dest, stored mode)
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
            staged.append((tmp, dest, rec.mode))
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
            if n != rec.size or writer.hexdigest() != rec.sha256:
                raise RestoreMiss(REASON_BLOB_CORRUPT, evict=True, blob=rec.sha256)
        for tmp, dest, mode in staged:  # phase 2: commit (each rename is atomic)
            os.chmod(tmp, mode & RESTORED_MODE_MASK)  # M-8: no setuid / group / other write
            os.replace(tmp, dest)
        committed = len(staged)
        staged = []
        return RestoreResult(files=committed, bytes=sum(o.size for o in entry.outputs))
    except (OSError, safeio.SafeIOError) as exc:
        raise RestoreMiss(REASON_RESTORE_FAILED, evict=False, detail=type(exc).__name__) from exc
    finally:
        for tmp, _dest, _mode in staged:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
