"""Local-filesystem result-cache store (HLD 8.4; ADR-0019 D2, D17, D18, D33).

`LocalFsCacheStore` is a content-addressed, crash-safe store of entries and blobs under
`<workspace>/.orchestrator/cache`. Safe across processes (atomic renames, no locks) and against
hostile data: every operation first runs the root and directory-chain checks, keys and shas are
validated before any path is built, and every open goes through `safeio` (O_NOFOLLOW).

The class implements both halves: the `CacheStore` hot path (T-U7ckfd) and the `CacheAdmin`
maintenance surface (T-HjxNQ0: streaming iteration, stats, prune, clear, read-only verify, and the
bounded inline enforcement of D19). Maintenance never follows a symlink and deletes only through
`delete_entry` / `delete_blob`, so the component checks (D33) apply to every removal.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256 as _sha256
from typing import BinaryIO, cast
from uuid import uuid4

from agent_orchestrator.cache import safeio
from agent_orchestrator.cache.constants import (
    BLOB_SWEEP_GRACE_SECONDS,
    BLOBS_DIR,
    CACHE_DIR_MODE,
    CACHE_DIR_PARTS,
    CACHEDIR_TAG_BODY,
    CACHEDIR_TAG_NAME,
    ENTRIES_DIR,
    ENTRIES_VERSION_DIR,
    ENTRY_SUFFIX,
    EVICT_LOW_WATER_RATIO,
    GITIGNORE_BODY,
    GITIGNORE_NAME,
    HASH_CHUNK_BYTES,
    HEX64_TOKEN_RE_BYTES,
    INLINE_PRUNE_MAX_ENTRIES,
    INLINE_PRUNE_MAX_ENTRY_FILE_BYTES,
    LAYOUT_FILE,
    LAYOUT_SCHEMA,
    MAX_ENTRY_FILE_BYTES,
    MAX_LAYOUT_FILE_BYTES,
    REASON_BLOB_CORRUPT,
    REASON_CORRUPT_ENTRY,
    REASON_ENTRY_TOO_LARGE,
    REASON_EVICT_INVALID,
    REASON_EVICT_LRU,
    REASON_EXPIRED,
    REASON_KEY_MISMATCH,
    REASON_STORE_ERROR,
    REASON_STORE_UNAVAILABLE,
    REASON_UNSAFE_PATH,
    SHA256_HEX_RE,
    SHARD_CHARS,
    TMP_DIR,
    TMP_SUFFIX,
    TMP_SWEEP_GRACE_SECONDS,
    TRASH_DIR_PREFIX,
)
from agent_orchestrator.cache.types import (
    BlobRef,
    CacheAdmin,
    CacheBlobMissingError,
    CacheEntry,
    CacheError,
    CacheIntegrityError,
    CacheLayoutError,
    CacheStats,
    CacheStore,
    CacheTooLargeError,
    CacheUnsafePathError,
    ClearReport,
    EntryInfo,
    PruneReport,
    VerifyProblem,
    VerifyReport,
    canonical_json,
    parse_entry_bytes,
)

# `.orchestrator` is shared with run state and other tools: create it the way they do (the umask
# decides the final mode); only the cache root and below are forced to CACHE_DIR_MODE.
_PARENT_DIR_MODE = 0o777
_KIND_BLOB, _KIND_ENTRY = "blob", "entry"
# `verify` / `iter_entries` problem kinds (HLD 13.4 `ao.result-cache.verify/v1`).
_PROBLEM_CORRUPT_ENTRY, _PROBLEM_KEY_MISMATCH = "corrupt_entry", "key_mismatch"
_PROBLEM_MISSING_BLOB, _PROBLEM_CORRUPT_BLOB, _PROBLEM_ORPHAN_BLOB = (
    "missing_blob",
    "corrupt_blob",
    "orphan_blob",
)
_PROBLEM_FOREIGN_VERSION, _PROBLEM_SYMLINK, _PROBLEM_UNEXPECTED = (
    "foreign_version",
    "symlink",
    "unexpected_file",
)
# Problems that make `verify().ok` False; orphan blobs, foreign versions and junk are informational.
_FAILING_PROBLEMS = frozenset(
    {
        _PROBLEM_CORRUPT_ENTRY,
        _PROBLEM_KEY_MISMATCH,
        _PROBLEM_MISSING_BLOB,
        _PROBLEM_CORRUPT_BLOB,
        _PROBLEM_SYMLINK,
    }
)


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


def _iter_dir(path: str) -> Iterator[os.DirEntry[str]]:
    """Stream the children of `path` (nothing if it is missing); never follows a symlink itself."""
    try:
        listing = os.scandir(path)
    except (FileNotFoundError, NotADirectoryError):
        return
    with listing:
        yield from listing


class _Sha256Sink:
    """Write-only sink that only hashes (verify re-hashes a blob through `read_blob`)."""

    def __init__(self) -> None:
        self._digest = _sha256()

    def write(self, data: bytes) -> int:
        self._digest.update(data)
        return len(data)

    def hexdigest(self) -> str:
        return self._digest.hexdigest()


@dataclass(frozen=True)
class _SizeScan:
    total_bytes: int
    over_limit: bool


@dataclass(frozen=True)
class _BlobScan:
    """Blob files by sha -> (size, mtime), plus (relative name, problem kind) for the junk."""

    blobs: dict[str, tuple[int, float]]
    anomalies: list[tuple[str, str]]


class LocalFsCacheStore(CacheStore, CacheAdmin):
    """`CacheStore` + `CacheAdmin` over `<workspace>/.orchestrator/cache` (HLD 8.4.1, 8.4.3)."""

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

    # ================================================================== CacheAdmin (T-HjxNQ0)
    def _version_dir(self) -> str:
        return os.path.join(self.root, ENTRIES_DIR, ENTRIES_VERSION_DIR)

    def _check_all_dirs(self) -> None:
        """Every directory a maintenance pass walks, checked up front: an unsafe component aborts
        the whole operation before anything is read in bulk or removed (D33)."""
        self._checks(self.root)
        for target in (
            self._version_dir(),
            os.path.join(self.root, BLOBS_DIR),
            os.path.join(self.root, TMP_DIR),
        ):
            self._checks(target)

    def iter_entries(self) -> Iterator[EntryInfo]:
        """STREAMING generator over `entries/v1/*/*.json`: never builds a list, never follows a
        symlink. A symlinked shard, a symlinked or directory-like file, a junk name or a file in
        the wrong shard is yielded as an `anomaly` (counted and reported, never parsed or
        deleted); its `key` is its name relative to `entries/v1` and `error` its problem kind."""
        version_dir = self._version_dir()
        self._checks(version_dir)
        for shard in _iter_dir(version_dir):
            if shard.is_symlink():
                yield self._anomaly(shard.name, _PROBLEM_SYMLINK)
                continue
            if not shard.is_dir(follow_symlinks=False):
                yield self._anomaly(shard.name, _PROBLEM_UNEXPECTED)
                continue
            for item in _iter_dir(shard.path):
                info = self._entry_info(shard.name, item)
                if info is not None:
                    yield info

    @staticmethod
    def _anomaly(name: str, kind: str) -> EntryInfo:
        return EntryInfo(key=name, size=0, mtime=0.0, error=kind, anomaly=True)

    def _entry_info(self, shard: str, item: os.DirEntry[str]) -> EntryInfo | None:
        rel = f"{shard}/{item.name}"
        if item.is_symlink():
            return self._anomaly(rel, _PROBLEM_SYMLINK)
        key = item.name[: -len(ENTRY_SUFFIX)]
        if (
            item.is_dir(follow_symlinks=False)
            or not item.name.endswith(ENTRY_SUFFIX)
            or not SHA256_HEX_RE.fullmatch(key)
            or key[:SHARD_CHARS] != shard
        ):
            return self._anomaly(rel, _PROBLEM_UNEXPECTED)
        try:
            st = item.stat(follow_symlinks=False)
        except FileNotFoundError:
            return None  # removed concurrently
        try:
            entry = self.get_entry(key)
        except CacheIntegrityError as exc:
            return EntryInfo(key, size=st.st_size, mtime=st.st_mtime, error=exc.reason)
        if entry is None:
            return None
        return EntryInfo(key, size=st.st_size, mtime=st.st_mtime, entry=entry)

    def _referenced_blobs(self, *, exclude_v1_keys: set[str]) -> set[str]:
        """MARK phase: every 64-hex token in EVERY entry file under `entries/**` (any version,
        parseable or not) protects the blob of that name, except v1 entries being removed by this
        prune (D18). Symlinked directories are skipped, never followed."""
        refs: set[str] = set()
        entries_root = os.path.join(self.root, ENTRIES_DIR)
        self._checks(entries_root)
        pending: list[tuple[str, tuple[str, ...]]] = [(entries_root, ())]
        while pending:
            directory, parts = pending.pop()
            for item in _iter_dir(directory):
                if item.is_symlink():
                    continue
                if item.is_dir(follow_symlinks=False):
                    pending.append((item.path, (*parts, item.name)))
                    continue
                if not item.is_file(follow_symlinks=False):
                    continue
                is_v1_entry = (
                    len(parts) == 2
                    and parts[0] == ENTRIES_VERSION_DIR
                    and item.name.endswith(ENTRY_SUFFIX)
                )
                if is_v1_entry and item.name[: -len(ENTRY_SUFFIX)] in exclude_v1_keys:
                    continue
                try:
                    raw = safeio.read_bounded(item.path, MAX_ENTRY_FILE_BYTES)
                except (safeio.SafeIOError, OSError):
                    continue
                refs.update(m.group(0).decode("ascii") for m in HEX64_TOKEN_RE_BYTES.finditer(raw))
        return refs

    def _scan_blobs(self) -> _BlobScan:
        """`blobs/*/*`: regular files with a 64-hex name in their own shard are blobs; everything
        else (links, junk, misplaced names) is an anomaly. lstat only."""
        blobs: dict[str, tuple[int, float]] = {}
        anomalies: list[tuple[str, str]] = []
        for shard in _iter_dir(os.path.join(self.root, BLOBS_DIR)):
            if shard.is_symlink():
                anomalies.append((shard.name, _PROBLEM_SYMLINK))
                continue
            if not shard.is_dir(follow_symlinks=False):
                anomalies.append((shard.name, _PROBLEM_UNEXPECTED))
                continue
            for item in _iter_dir(shard.path):
                rel = f"{shard.name}/{item.name}"
                if item.is_symlink():
                    anomalies.append((rel, _PROBLEM_SYMLINK))
                elif (
                    item.is_file(follow_symlinks=False)
                    and SHA256_HEX_RE.fullmatch(item.name)
                    and item.name[:SHARD_CHARS] == shard.name
                ):
                    try:
                        st = item.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    blobs[item.name] = (st.st_size, st.st_mtime)
                else:
                    anomalies.append((rel, _PROBLEM_UNEXPECTED))
        return _BlobScan(blobs, anomalies)

    def _tmp_files(self) -> list[tuple[str, float]]:
        """(path, mtime) of every non-directory under `tmp/`; a link is listed, never followed."""
        found = []
        for item in _iter_dir(os.path.join(self.root, TMP_DIR)):
            if item.is_dir(follow_symlinks=False):
                continue
            try:
                found.append((item.path, item.stat(follow_symlinks=False).st_mtime))
            except FileNotFoundError:
                continue
        return found

    def _trash_dirs(self) -> list[str]:
        """Real (non-link) `trash-*` directories directly under the root."""
        return [
            item.path
            for item in _iter_dir(self.root)
            if item.name.startswith(TRASH_DIR_PREFIX)
            and item.is_dir(follow_symlinks=False)
            and not item.is_symlink()
        ]

    def _foreign_entry_children(self) -> tuple[list[str], list[tuple[str, str]]]:
        """Children of `entries/` other than `v1`: (foreign version dirs, (name, kind) junk)."""
        entries_root = os.path.join(self.root, ENTRIES_DIR)
        self._checks(entries_root)
        foreign: list[str] = []
        junk: list[tuple[str, str]] = []
        for item in _iter_dir(entries_root):
            if item.name == ENTRIES_VERSION_DIR:
                continue
            if item.is_symlink():
                junk.append((item.name, _PROBLEM_SYMLINK))
            elif item.is_dir(follow_symlinks=False):
                foreign.append(item.name)
            else:
                junk.append((item.name, _PROBLEM_UNEXPECTED))
        return sorted(foreign), junk

    # ------------------------------------------------------------------ stats
    def stats(self, *, now: datetime) -> CacheStats:
        if not os.path.lexists(self.root):
            return CacheStats(
                root=self.root, exists=False, max_bytes=self.max_bytes, ttl_days=self.ttl_days
            )
        self._check_all_dirs()
        entries = invalid = expired = anomalies = entries_bytes = 0
        oldest: datetime | None = None
        newest: datetime | None = None
        for info in self.iter_entries():
            if info.anomaly:
                anomalies += 1
                continue
            entries_bytes += info.size
            if info.entry is None:
                invalid += 1
                continue
            entries += 1
            created = info.entry.created_at
            oldest = created if oldest is None or created < oldest else oldest
            newest = created if newest is None or created > newest else newest
            if self.ttl_days is not None and is_expired(created, now, self.ttl_days):
                expired += 1
        foreign, junk = self._foreign_entry_children()
        scan = self._scan_blobs()
        protected = self._referenced_blobs(exclude_v1_keys=set())
        orphans = [sha for sha in scan.blobs if sha not in protected]
        orphan_bytes = sum(scan.blobs[sha][0] for sha in orphans)
        blobs_bytes = sum(size for size, _mtime in scan.blobs.values())
        return CacheStats(
            root=self.root,
            exists=True,
            entries=entries,
            invalid_entries=invalid,
            expired_entries=expired,
            foreign_version_dirs=tuple(foreign),
            blobs=len(scan.blobs),
            orphan_blobs=len(orphans),
            tmp_files=len(self._tmp_files()),
            trash_dirs=len(self._trash_dirs()),
            anomalies=anomalies + len(scan.anomalies) + len(junk),
            entries_bytes=entries_bytes,
            blobs_bytes=blobs_bytes,
            referenced_blobs_bytes=blobs_bytes - orphan_bytes,
            orphan_blobs_bytes=orphan_bytes,
            total_bytes=entries_bytes + blobs_bytes,
            max_bytes=self.max_bytes,
            ttl_days=self.ttl_days,
            oldest_created_at=oldest,
            newest_created_at=newest,
        )

    # ------------------------------------------------------------------ prune
    def prune(
        self,
        *,
        now: datetime,
        max_bytes: int | None,
        ttl_days: int | None,
        dry_run: bool = False,
    ) -> PruneReport:
        """Invalid, then expired, then LRU down to the low-water mark; then sweep unreferenced
        blobs (after a grace), stale temp files and stale trash. `dry_run` computes the same
        report and removes nothing. Foreign-version entries are never removed (D18)."""
        self._check_all_dirs()
        remove: dict[str, str] = {}  # v1 key -> reason
        live: list[tuple[float, str, int, list[str]]] = []  # (mtime, key, size, blob shas)
        entry_sizes: dict[str, int] = {}
        for info in self.iter_entries():  # anomalies are counted elsewhere, never deleted
            if info.anomaly:
                continue
            entry_sizes[info.key] = info.size
            if info.entry is None:
                remove[info.key] = REASON_EVICT_INVALID
            elif ttl_days is not None and is_expired(info.entry.created_at, now, ttl_days):
                remove[info.key] = REASON_EXPIRED
            else:
                shas = [o.sha256 for o in info.entry.outputs]
                live.append((info.mtime, info.key, info.size, shas))
        blobs = self._scan_blobs().blobs
        refcount = Counter(sha for _mtime, _key, _size, shas in live for sha in shas)
        total = sum(size for _mtime, _key, size, _shas in live) + sum(
            blobs[sha][0] for sha in refcount if sha in blobs
        )
        if max_bytes is not None and total > max_bytes:  # `is not None`: 0 is a valid budget
            target = int(max_bytes * EVICT_LOW_WATER_RATIO)
            for _mtime, key, size, shas in sorted(live):  # LRU first; the key breaks mtime ties
                if total <= target:
                    break
                remove[key] = REASON_EVICT_LRU
                total -= size
                for sha in shas:
                    refcount[sha] -= 1
                    if refcount[sha] == 0 and sha in blobs:
                        total -= blobs[sha][0]
        protected = self._referenced_blobs(exclude_v1_keys=set(remove))
        blob_cutoff = now.timestamp() - BLOB_SWEEP_GRACE_SECONDS
        orphans = sorted(
            sha
            for sha, (_size, mtime) in blobs.items()
            if sha not in protected and mtime < blob_cutoff
        )
        tmp_cutoff = now.timestamp() - TMP_SWEEP_GRACE_SECONDS
        stale_tmp = [path for path, mtime in self._tmp_files() if mtime < tmp_cutoff]
        stale_trash = self._trash_dirs()
        bytes_before = sum(entry_sizes.values()) + sum(size for size, _mtime in blobs.values())
        bytes_after = (
            bytes_before
            - sum(entry_sizes[key] for key in remove)
            - sum(blobs[sha][0] for sha in orphans)
        )
        if not dry_run:
            for key in sorted(remove):
                self.delete_entry(key)  # the checks run first; FileNotFoundError -> False
            for sha in orphans:
                self.delete_blob(sha)
            for path in stale_tmp:
                self._unlink_quietly(path)
            for path in stale_trash:
                self._rmtree_checked(path)
        self._approx_total = None
        return PruneReport(
            removed_entries=dict(Counter(remove.values())),
            removed_blobs=len(orphans),
            removed_tmp=len(stale_tmp),
            removed_trash=len(stale_trash),
            bytes_before=bytes_before,
            bytes_after=bytes_after,
            dry_run=dry_run,
        )

    @staticmethod
    def _rmtree_checked(path: str) -> None:
        """Remove a trash directory completely or raise `CacheError(store_error)`: no
        `ignore_errors`, a silently kept tree would hide an incomplete clear."""
        name = os.path.basename(path)
        try:
            shutil.rmtree(path)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise CacheError(REASON_STORE_ERROR, f"cannot remove {name}") from exc
        if os.path.lexists(path):
            raise CacheError(REASON_STORE_ERROR, f"incomplete removal: {name}")

    def _scan_sizes(self, *, entry_limit: int, entry_bytes_limit: int) -> _SizeScan:
        """lstat-only walk of entry files and blobs. Stops as soon as the entry COUNT or the total
        entry-file BYTES exceeds its limit (D19): nothing is opened, read or parsed."""
        count = entry_bytes = blob_bytes = 0
        for shard in _iter_dir(self._version_dir()):
            if shard.is_symlink() or not shard.is_dir(follow_symlinks=False):
                continue
            for item in _iter_dir(shard.path):
                if item.is_symlink() or not item.is_file(follow_symlinks=False):
                    continue
                count += 1
                entry_bytes += item.stat(follow_symlinks=False).st_size
                if count > entry_limit or entry_bytes > entry_bytes_limit:
                    return _SizeScan(entry_bytes, over_limit=True)
        for shard in _iter_dir(os.path.join(self.root, BLOBS_DIR)):
            if shard.is_symlink() or not shard.is_dir(follow_symlinks=False):
                continue
            for item in _iter_dir(shard.path):
                if item.is_file(follow_symlinks=False) and not item.is_symlink():
                    blob_bytes += item.stat(follow_symlinks=False).st_size
        return _SizeScan(entry_bytes + blob_bytes, over_limit=False)

    def maybe_enforce_limits(self, *, now: datetime) -> PruneReport | None:
        """INLINE enforcement, bounded twice (D19): defers (`PruneReport(deferred=True)`, the
        coordinator warns) when the store holds more than `INLINE_PRUNE_MAX_ENTRIES` entries or
        more than `INLINE_PRUNE_MAX_ENTRY_FILE_BYTES` of entry files; otherwise prunes only when
        the approximate total is over `max_bytes`."""
        if self._approx_total is None:
            self._check_all_dirs()
            scan = self._scan_sizes(
                entry_limit=INLINE_PRUNE_MAX_ENTRIES,
                entry_bytes_limit=INLINE_PRUNE_MAX_ENTRY_FILE_BYTES,
            )
            if scan.over_limit:
                return PruneReport(deferred=True)
            self._approx_total = scan.total_bytes
        if self._approx_total <= self.max_bytes:
            return None
        return self.prune(now=now, max_bytes=self.max_bytes, ttl_days=self.ttl_days)

    # ------------------------------------------------------------------ clear
    def clear(self) -> ClearReport:
        """Move `entries/`, `blobs/` and `tmp/` into a fresh `trash-<uuid>` directory, delete it,
        and verify nothing is left (a failed removal is `CacheError(store_error)`, never
        swallowed). Stale `trash-*` directories from an earlier crash go too."""
        self._checks(self.root)
        if not os.path.lexists(self.root):
            return ClearReport()
        trash = os.path.join(self.root, TRASH_DIR_PREFIX + uuid4().hex)
        os.mkdir(trash, CACHE_DIR_MODE)
        for name in (ENTRIES_DIR, BLOBS_DIR, TMP_DIR):
            try:
                os.replace(os.path.join(self.root, name), os.path.join(trash, name))
            except FileNotFoundError:
                pass
        entries_removed, blobs_removed, freed = self._count_tree(trash)
        self._rmtree_checked(trash)
        for name in (ENTRIES_DIR, BLOBS_DIR, TMP_DIR):
            if os.path.lexists(os.path.join(self.root, name)):
                raise CacheError(REASON_STORE_ERROR, f"clear incomplete: {name}")
        for stale in self._trash_dirs():
            self._rmtree_checked(stale)
        self._approx_total = None
        return ClearReport(
            removed_entries=entries_removed, removed_blobs=blobs_removed, bytes_freed=freed
        )

    @staticmethod
    def _count_tree(trash: str) -> tuple[int, int, int]:
        """(entry files, blob files, bytes) under a trash directory; links are never followed."""
        counts = {ENTRIES_DIR: 0, BLOBS_DIR: 0}
        freed = 0
        for top in counts:
            pending = [os.path.join(trash, top)]
            while pending:
                for item in _iter_dir(pending.pop()):
                    if item.is_dir(follow_symlinks=False):
                        pending.append(item.path)
                    elif item.is_file(follow_symlinks=False):
                        counts[top] += 1
                        freed += item.stat(follow_symlinks=False).st_size
        return counts[ENTRIES_DIR], counts[BLOBS_DIR], freed

    # ------------------------------------------------------------------ verify
    def verify(self) -> VerifyReport:
        """READ-ONLY integrity check (`--repair` is deferred): nothing is deleted or modified."""
        if not os.path.lexists(self.root):
            return VerifyReport(ok=True)
        self._check_all_dirs()
        problems: list[VerifyProblem] = []
        entries_checked = 0
        for info in self.iter_entries():
            if info.anomaly:
                problems.append(VerifyProblem(info.error or _PROBLEM_UNEXPECTED, detail=info.key))
                continue
            entries_checked += 1
            if info.entry is None:
                kind = (
                    _PROBLEM_KEY_MISMATCH
                    if info.error == REASON_KEY_MISMATCH
                    else _PROBLEM_CORRUPT_ENTRY
                )
                problems.append(VerifyProblem(kind, key=info.key, detail=info.error))
                continue
            for output in info.entry.outputs:
                if not self.has_blob(output.sha256):
                    problems.append(
                        VerifyProblem(_PROBLEM_MISSING_BLOB, key=info.key, blob=output.sha256)
                    )
        foreign, junk = self._foreign_entry_children()
        problems += [VerifyProblem(_PROBLEM_FOREIGN_VERSION, detail=name) for name in foreign]
        problems += [VerifyProblem(kind, detail=f"entries/{name}") for name, kind in junk]
        scan = self._scan_blobs()
        problems += [VerifyProblem(kind, detail=f"blobs/{name}") for name, kind in scan.anomalies]
        for sha, (size, _mtime) in sorted(scan.blobs.items()):
            if not self._blob_matches(sha, size):
                problems.append(VerifyProblem(_PROBLEM_CORRUPT_BLOB, blob=sha))
        protected = self._referenced_blobs(exclude_v1_keys=set())
        problems += [
            VerifyProblem(_PROBLEM_ORPHAN_BLOB, blob=sha)
            for sha in sorted(scan.blobs)
            if sha not in protected
        ]
        return VerifyReport(
            ok=not any(p.kind in _FAILING_PROBLEMS for p in problems),
            problems=tuple(problems),
            entries_checked=entries_checked,
            blobs_checked=len(scan.blobs),
        )

    def _blob_matches(self, sha: str, size: int) -> bool:
        """Re-hash a blob through `read_blob`: name == sha256(content) and size == st_size."""
        sink = _Sha256Sink()
        try:
            copied = self.read_blob(sha, cast(BinaryIO, sink), max_bytes=size)
        except CacheBlobMissingError:
            return True  # removed since the scan: not corruption
        except CacheIntegrityError:
            return False
        return copied == size and sink.hexdigest() == sha
