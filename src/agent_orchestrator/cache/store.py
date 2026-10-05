"""Local-filesystem result-cache store (HLD 8.4; ADR-0019 D2, D17, D18, D33).

`LocalFsCacheStore` is a content-addressed, crash-safe store of entries and blobs under
`<workspace>/.orchestrator/cache`. Safe across processes (atomic renames, no locks) and against
hostile data: every operation first runs the root and directory-chain checks, keys and shas are
validated before any path is built, and every open goes through `safeio` (O_NOFOLLOW).

This module currently holds the `CacheStore` (hot path) half. T-HjxNQ0 adds the `CacheAdmin`
base, the maintenance methods and the real `maybe_enforce_limits`.
"""

from __future__ import annotations

import json
import os
import stat
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from hashlib import sha256 as _sha256
from typing import BinaryIO
from uuid import uuid4

from agent_orchestrator.cache import safeio
from agent_orchestrator.cache.constants import (
    BLOBS_DIR,
    CACHE_DIR_MODE,
    CACHE_DIR_PARTS,
    CACHEDIR_TAG_BODY,
    CACHEDIR_TAG_NAME,
    ENTRIES_DIR,
    ENTRIES_VERSION_DIR,
    ENTRY_SUFFIX,
    GITIGNORE_BODY,
    GITIGNORE_NAME,
    HASH_CHUNK_BYTES,
    LAYOUT_FILE,
    LAYOUT_SCHEMA,
    MAX_ENTRY_FILE_BYTES,
    MAX_LAYOUT_FILE_BYTES,
    REASON_BLOB_CORRUPT,
    REASON_CORRUPT_ENTRY,
    REASON_ENTRY_TOO_LARGE,
    REASON_STORE_UNAVAILABLE,
    REASON_UNSAFE_PATH,
    SHA256_HEX_RE,
    SHARD_CHARS,
    TMP_DIR,
    TMP_SUFFIX,
)
from agent_orchestrator.cache.types import (
    BlobRef,
    CacheBlobMissingError,
    CacheEntry,
    CacheIntegrityError,
    CacheLayoutError,
    CacheStore,
    CacheTooLargeError,
    CacheUnsafePathError,
    PruneReport,
    canonical_json,
    parse_entry_bytes,
)

# `.orchestrator` is shared with run state and other tools: create it the way they do (the umask
# decides the final mode); only the cache root and below are forced to CACHE_DIR_MODE.
_PARENT_DIR_MODE = 0o777
_KIND_BLOB, _KIND_ENTRY = "blob", "entry"


def is_expired(created_at: datetime, now: datetime, ttl_days: int) -> bool:
    """THE TTL helper. Both datetimes are timezone-aware; a future `created_at` (clock skew) is
    a negative age, hence never expired."""
    return (now - created_at) > timedelta(days=ttl_days)


@contextmanager
def _unsafe_as_cache_error() -> Iterator[None]:
    """`safeio.UnsafePathError` (a symlinked or foreign component) -> `CacheUnsafePathError`."""
    try:
        yield
    except safeio.UnsafePathError as exc:
        raise CacheUnsafePathError(REASON_UNSAFE_PATH, str(exc)) from exc


@contextmanager
def _unsafe_root_as_layout_error() -> Iterator[None]:
    """`safeio.UnsafePathError` from a ROOT check -> `CacheLayoutError` (store_unavailable)."""
    try:
        yield
    except safeio.UnsafePathError as exc:
        raise CacheLayoutError(REASON_STORE_UNAVAILABLE, str(exc)) from exc


def _write_all(fd: int, data: bytes) -> None:
    """os.write until every byte is written (a short write is legal)."""
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


class LocalFsCacheStore(CacheStore):
    """`CacheStore` over `<workspace>/.orchestrator/cache` (HLD 8.4.1, 8.4.3)."""

    def __init__(self, workspace_root: str | os.PathLike[str], *, max_bytes: int, ttl_days: int):
        # abspath, not realpath: constructing the store must perform no I/O (U-ST1).
        self._ws = os.path.abspath(os.fspath(workspace_root))
        self._orchestrator_dir = os.path.join(self._ws, CACHE_DIR_PARTS[0])
        self.root = os.path.join(self._ws, *CACHE_DIR_PARTS)
        self.max_bytes = max_bytes
        self.ttl_days = ttl_days
        # Approximate bytes in the store; None = unknown (maintenance computes it, T-HjxNQ0).
        self._approx_total: int | None = None

    @classmethod
    def for_workspace(
        cls, workspace_root: str | os.PathLike[str], *, max_bytes: int, ttl_days: int
    ) -> LocalFsCacheStore:
        """Factory; creates nothing (the layout is created lazily by the first write)."""
        return cls(workspace_root, max_bytes=max_bytes, ttl_days=ttl_days)

    # ------------------------------------------------------------------ paths (M-2)
    def _entry_path(self, key: str) -> str:
        if not SHA256_HEX_RE.fullmatch(key):  # BEFORE any path is built
            raise ValueError("invalid key")
        return os.path.join(
            self.root, ENTRIES_DIR, ENTRIES_VERSION_DIR, key[:SHARD_CHARS], key + ENTRY_SUFFIX
        )

    def _blob_path(self, sha: str) -> str:
        if not SHA256_HEX_RE.fullmatch(sha):
            raise ValueError("invalid sha")
        return os.path.join(self.root, BLOBS_DIR, sha[:SHARD_CHARS], sha)

    # ------------------------------------------------------------------ checks (D33)
    def _check_root(self) -> None:
        """Root checks (symlink, type, owner, mode, containment); creates nothing."""
        with _unsafe_root_as_layout_error():
            safeio.check_root_dir(self.root, workspace_root=self._ws)

    def _checks(self, target_dir: str) -> None:
        """Runs before EVERY operation: root check (CacheLayoutError), then every existing
        component down to `target_dir` must be a real directory (CacheUnsafePathError)."""
        self._check_root()
        with _unsafe_as_cache_error():
            safeio.check_dir_chain(self.root, target_dir)  # missing directories are fine

    def _read_layout_if_present(self) -> None:
        path = os.path.join(self.root, LAYOUT_FILE)
        try:
            raw = safeio.read_bounded(path, MAX_LAYOUT_FILE_BYTES)
        except FileNotFoundError:
            return
        except (safeio.NotRegularFileError, safeio.TooLargeError) as exc:
            raise CacheLayoutError(REASON_STORE_UNAVAILABLE, type(exc).__name__) from exc
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError) as exc:  # UnicodeDecodeError is a ValueError
            raise CacheLayoutError(REASON_STORE_UNAVAILABLE, "layout is not JSON") from exc
        schema = data.get("schema") if isinstance(data, dict) else None
        if schema != LAYOUT_SCHEMA:
            raise CacheLayoutError(REASON_STORE_UNAVAILABLE, "unknown layout schema")

    def check(self) -> None:
        """Coordinator pre-flight (every lookup); creates nothing."""
        if not os.path.lexists(self.root):
            return  # created lazily by a write
        self._check_root()  # also covers a symlinked `.orchestrator`
        self._read_layout_if_present()  # unknown schema -> CacheLayoutError

    # ------------------------------------------------------------------ layout
    def ensure_layout(self) -> None:
        """Lazy, component-wise creation of the HLD 8.4.1 layout (first write)."""
        self._check_root()
        with _unsafe_as_cache_error():
            safeio.ensure_dir_chain(
                self._orchestrator_dir, self._orchestrator_dir, _PARENT_DIR_MODE
            )
            safeio.ensure_dir_chain(self.root, self.root, CACHE_DIR_MODE)
            self._read_layout_if_present()  # never write into a layout we do not understand
            for parts in (
                (ENTRIES_DIR, ENTRIES_VERSION_DIR),
                (BLOBS_DIR,),
                (TMP_DIR,),
            ):
                safeio.ensure_dir_chain(self.root, os.path.join(self.root, *parts), CACHE_DIR_MODE)
            for name, body in (
                (GITIGNORE_NAME, GITIGNORE_BODY),
                (CACHEDIR_TAG_NAME, CACHEDIR_TAG_BODY),
                (LAYOUT_FILE, canonical_json({"schema": LAYOUT_SCHEMA})),
            ):
                path = os.path.join(self.root, name)
                if not os.path.lexists(path):  # written only if absent; os.replace never follows
                    self._atomic_write_bytes(path, body.encode("ascii"), kind=_KIND_ENTRY)

    # ------------------------------------------------------------------ write helpers
    def _new_tmp(self, kind: str) -> tuple[str, int]:
        """A fresh exclusive 0o600 temp file under tmp/ (swept by prune after the grace)."""
        name = f"{os.getpid()}-{threading.get_ident()}-{uuid4().hex}.{kind}{TMP_SUFFIX}"
        path = os.path.join(self.root, TMP_DIR, name)
        return path, safeio.create_exclusive(path)

    def _atomic_write_bytes(self, dst: str, body: bytes, *, kind: str) -> None:
        """temp file + os.replace: a reader sees the old file or the whole new one. No fsync (it
        matches RunStateStore.save); a torn blob fails verification and becomes a miss."""
        with _unsafe_as_cache_error():
            safeio.ensure_dir_chain(self.root, os.path.dirname(dst), CACHE_DIR_MODE)
        tmp, fd = self._new_tmp(kind)
        fd_open = True
        try:
            _write_all(fd, body)
            os.close(fd)
            fd_open = False
            os.replace(tmp, dst)
        finally:
            if fd_open:
                os.close(fd)
            self._unlink_quietly(tmp)

    @staticmethod
    def _unlink_quietly(path: str) -> None:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass

    def _add_approx(self, nbytes: int) -> None:
        if self._approx_total is not None:
            self._approx_total += nbytes

    # ------------------------------------------------------------------ entries
    def get_entry(self, key: str) -> CacheEntry | None:
        path = self._entry_path(key)
        self._checks(os.path.dirname(path))
        try:
            raw = safeio.read_bounded(path, MAX_ENTRY_FILE_BYTES)
        except FileNotFoundError:
            return None
        except (safeio.NotRegularFileError, safeio.TooLargeError) as exc:  # final component only
            raise CacheIntegrityError(REASON_CORRUPT_ENTRY, type(exc).__name__) from exc
        return parse_entry_bytes(raw, key)  # a total function

    def put_entry(self, entry: CacheEntry) -> int:
        body = entry.to_canonical_bytes()
        if len(body) > MAX_ENTRY_FILE_BYTES:  # refused before anything is created
            raise CacheTooLargeError(REASON_ENTRY_TOO_LARGE)
        self.ensure_layout()
        path = self._entry_path(entry.key)
        self._checks(os.path.dirname(path))
        self._atomic_write_bytes(path, body, kind=_KIND_ENTRY)
        self._add_approx(len(body))
        return len(body)

    def touch_entry(self, key: str, at: datetime) -> None:
        path = self._entry_path(key)
        self._checks(os.path.dirname(path))  # never utime through a link
        stamp = at.timestamp()
        try:
            os.utime(path, (stamp, stamp), follow_symlinks=False)
        except FileNotFoundError:
            pass  # a missing entry is a no-op; any other OSError goes to the boundary

    def delete_entry(self, key: str) -> bool:
        path = self._entry_path(key)
        self._checks(os.path.dirname(path))  # never unlink through a link
        try:
            os.unlink(path)
        except FileNotFoundError:
            return False
        return True

    # ------------------------------------------------------------------ blobs
    def has_blob(self, sha256: str) -> bool:
        path = self._blob_path(sha256)
        self._checks(os.path.dirname(path))
        try:
            return stat.S_ISREG(os.lstat(path).st_mode)  # a directory or a link is not a blob
        except FileNotFoundError:
            return False

    def put_blob(self, src: BinaryIO, *, max_bytes: int) -> BlobRef:
        self.ensure_layout()
        tmp, fd = self._new_tmp(_KIND_BLOB)
        fd_open = True
        try:
            digest = _sha256()
            size = 0
            while chunk := src.read(min(HASH_CHUNK_BYTES, max_bytes + 1 - size)):
                size += len(chunk)
                if size > max_bytes:
                    raise CacheTooLargeError(REASON_ENTRY_TOO_LARGE)
                digest.update(chunk)
                _write_all(fd, chunk)
            os.close(fd)
            fd_open = False
            sha = digest.hexdigest()
            dst = self._blob_path(sha)
            self._checks(os.path.dirname(dst))
            with _unsafe_as_cache_error():
                safeio.ensure_dir_chain(self.root, os.path.dirname(dst), CACHE_DIR_MODE)
            if os.path.lexists(dst):  # dedupe: refresh the mtime (the prune grace period)
                os.utime(dst, None, follow_symlinks=False)
                return BlobRef(sha, size, new=False)
            os.replace(tmp, dst)
            self._add_approx(size)
            return BlobRef(sha, size, new=True)
        finally:
            if fd_open:
                os.close(fd)
            self._unlink_quietly(tmp)

    def read_blob(self, sha256: str, dest: BinaryIO, *, max_bytes: int) -> int:
        path = self._blob_path(sha256)
        self._checks(os.path.dirname(path))
        try:
            fd = safeio.open_regular_read(path)
        except FileNotFoundError:
            raise CacheBlobMissingError(sha256) from None
        except (safeio.NotRegularFileError, OSError) as exc:
            raise CacheIntegrityError(REASON_BLOB_CORRUPT, "unreadable/irregular") from exc
        try:
            copied = 0
            while chunk := os.read(fd, min(HASH_CHUNK_BYTES, max_bytes + 1 - copied)):
                copied += len(chunk)
                dest.write(chunk)
                if copied > max_bytes:
                    break  # the caller sees copied > expected and treats the blob as corrupt
            return copied
        finally:
            os.close(fd)

    def delete_blob(self, sha256: str) -> bool:
        path = self._blob_path(sha256)
        self._checks(os.path.dirname(path))
        try:
            os.unlink(path)
        except FileNotFoundError:
            return False
        return True

    # ------------------------------------------------------------------ limits
    def maybe_enforce_limits(self, *, now: datetime) -> PruneReport | None:
        return None  # TODO(T-HjxNQ0): bounded inline enforcement (HLD 8.4.3, D19)
