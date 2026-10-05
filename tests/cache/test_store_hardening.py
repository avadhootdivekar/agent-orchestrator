"""G1a remediation (SEC-01, SEC-02, SEC-08, SEC-09, reviewer S-2..S-5): `LocalFsCacheStore`.

Inline maintenance must never read, walk or count an unbounded amount of whatever the cache
directory holds (HLD 8.4.3 / D19); the blob store must not trust whatever sits at a blob path; a
transient I/O error must never be treated as corruption; the destructive sweep fails CLOSED.
Every clock is fixed (`NOW`); permission failures are simulated by patching, never `chmod`.
"""

from __future__ import annotations

import errno
import io
import os
import stat
import sys
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.cache import safeio
from agent_orchestrator.cache import store as store_mod
from agent_orchestrator.cache.constants import (
    BLOBS_DIR,
    ENTRIES_DIR,
    ENTRIES_VERSION_DIR,
    REASON_STORE_ERROR,
    TRASH_DIR_PREFIX,
)
from agent_orchestrator.cache.store import LocalFsCacheStore
from agent_orchestrator.cache.types import (
    CacheError,
    CacheIntegrityError,
    CacheUnsafePathError,
)
from tests.cache.fakes import key_of
from tests.cache.test_store_maintenance import (
    BLOB_BOUND,
    MAX_BYTES,
    NOW,
    OLD_SECONDS,
    TTL_DAYS,
    _Spy,
    add,
    blob_file,
    entry_file,
    root_of,
    set_mtime,
)

needs_posix = pytest.mark.skipif(sys.platform == "win32", reason="symlinks are POSIX-only")
WALL_SECONDS = 1.0  # the inline bound must hold in well under this on any machine
ONE_MIB = 1024 * 1024


@pytest.fixture(autouse=True)
def _cache_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AO_CACHE", raising=False)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


@pytest.fixture
def store(ws: Path) -> LocalFsCacheStore:
    return LocalFsCacheStore.for_workspace(ws, max_bytes=MAX_BYTES, ttl_days=TTL_DAYS)


@pytest.fixture
def tight(ws: Path) -> LocalFsCacheStore:
    """A store that is always over budget, so inline enforcement always wants to prune."""
    return LocalFsCacheStore.for_workspace(ws, max_bytes=1, ttl_days=TTL_DAYS)


def plant_files(directory: Path, count: int, *, size: int = 0) -> None:
    """`count` (sparse) files: they cost inodes, not disk, exactly like a planted tree."""
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        with (directory / f"{i:04d}.json").open("wb") as fh:
            fh.truncate(size)


# ------------------------------------------------------------------------- SEC-01 inline bound
def test_inline_defers_on_a_planted_foreign_version_tree_without_reading_it(
    ws: Path, tight: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_mod, "INLINE_PRUNE_MAX_ENTRIES", 10)
    add(ws, tight, "real")
    plant_files(root_of(ws) / ENTRIES_DIR / "v9" / "aa", 30)
    spy = _Spy(monkeypatch)
    report = tight.maybe_enforce_limits(now=NOW)
    assert report is not None and report.deferred
    assert (spy.parsed, spy.read) == (0, 0)  # the scan is lstat-only
    assert entry_file(ws, key_of("real")).is_file()  # nothing removed


def test_inline_defers_on_files_nested_under_a_v1_shard(
    ws: Path, tight: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_mod, "INLINE_PRUNE_MAX_ENTRIES", 10)
    add(ws, tight, "real")
    plant_files(root_of(ws) / ENTRIES_DIR / ENTRIES_VERSION_DIR / "aa" / "deep" / "er", 30)
    spy = _Spy(monkeypatch)
    report = tight.maybe_enforce_limits(now=NOW)
    assert report is not None and report.deferred
    assert (spy.parsed, spy.read) == (0, 0)


def test_inline_defers_on_the_bytes_of_a_planted_sparse_tree(
    ws: Path, tight: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_mod, "INLINE_PRUNE_MAX_ENTRY_FILE_BYTES", 2 * ONE_MIB)
    plant_files(root_of(ws) / ENTRIES_DIR / "v9" / "aa", 4, size=ONE_MIB)
    spy = _Spy(monkeypatch)
    report = tight.maybe_enforce_limits(now=NOW)
    assert report is not None and report.deferred and spy.read == 0


def test_inline_defers_on_the_number_of_directory_entries_visited(
    ws: Path, tight: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_mod, "INLINE_PRUNE_MAX_WALK_ITEMS", 20)
    for i in range(50):  # empty planted directories: nothing to read, plenty to walk
        (root_of(ws) / ENTRIES_DIR / "v9" / f"d{i}").mkdir(parents=True)
    report = tight.maybe_enforce_limits(now=NOW)
    assert report is not None and report.deferred


def test_the_planted_sparse_tree_defers_fast_with_the_real_limits(
    ws: Path, tight: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEC-01 reproduction: 1000 sparse 1 MiB files under `entries/v9` took ~10 s per 1000 files
    (0.3 s per 30) before the fix. The default 64 MiB bound now stops it after 64 files."""
    plant_files(root_of(ws) / ENTRIES_DIR / "v9" / "aa", 1000, size=ONE_MIB)
    spy = _Spy(monkeypatch)
    started = time.perf_counter()
    report = tight.maybe_enforce_limits(now=NOW)
    elapsed = time.perf_counter() - started
    assert report is not None and report.deferred and spy.read == 0
    assert elapsed < WALL_SECONDS


def test_a_tree_planted_after_the_size_scan_is_bounded_by_the_prune_itself(
    ws: Path, tight: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The size scan runs once per store instance; the prune it triggers later must bound its
    own reads, or a tree planted in between is read in full inline (TOCTOU)."""
    monkeypatch.setattr(store_mod, "INLINE_PRUNE_MAX_ENTRIES", 10)
    add(ws, tight, "real")
    assert tight.maybe_enforce_limits(now=NOW) is not None  # scans (within bounds) and prunes
    plant_files(root_of(ws) / ENTRIES_DIR / "v9" / "aa", 100)
    add(ws, tight, "again")
    tight._approx_total = 10**9  # the scan result is cached: the prune runs without re-scanning
    spy = _Spy(monkeypatch)
    report = tight.maybe_enforce_limits(now=NOW)
    assert report is not None and report.deferred
    # each read phase (entry parse, mark) has its own budget of 10: never the 100 planted files
    assert spy.read <= 2 * 10
    assert entry_file(ws, key_of("again")).is_file()  # a deferral never leaves a partial prune
    assert tight._approx_total is None  # the next store re-checks cheaply


def test_the_explicit_prune_stays_unbounded(
    ws: Path, store: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ao cache prune` is the escape hatch for a deferred store: it must still do the work."""
    monkeypatch.setattr(store_mod, "INLINE_PRUNE_MAX_ENTRIES", 5)
    monkeypatch.setattr(store_mod, "INLINE_PRUNE_MAX_WALK_ITEMS", 5)
    for i in range(12):
        add(ws, store, f"e{i}", age_days=TTL_DAYS + 1)
    plant_files(root_of(ws) / ENTRIES_DIR / "v9" / "aa", 30)
    report = store.prune(now=NOW, max_bytes=None, ttl_days=TTL_DAYS)
    assert not report.deferred and report.removed_entries == {"expired": 12}


# ------------------------------------------------------------------------- SEC-02 / S-1 / S-2
def test_inline_defers_above_the_blob_count(
    ws: Path, tight: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_mod, "INLINE_PRUNE_MAX_BLOBS", 5)
    tight.ensure_layout()
    for i in range(6):
        tight.put_blob(io.BytesIO(bytes([i]) * 10), max_bytes=BLOB_BOUND)
    tight._approx_total = None
    report = tight.maybe_enforce_limits(now=NOW)
    assert report is not None and report.deferred
    assert sum(1 for _ in (root_of(ws) / BLOBS_DIR).glob("*/*")) == 6  # nothing removed


def test_inline_at_the_blob_limit_is_not_deferred(
    ws: Path, tight: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_mod, "INLINE_PRUNE_MAX_BLOBS", 6)
    tight.ensure_layout()
    for i in range(6):
        tight.put_blob(io.BytesIO(bytes([i]) * 10), max_bytes=BLOB_BOUND)
    tight._approx_total = None
    report = tight.maybe_enforce_limits(now=NOW)
    assert report is not None and not report.deferred


def test_a_blob_vanishing_during_the_inline_scan_is_tolerated(
    ws: Path, tight: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S-2: a concurrent `ao cache prune` removes a blob between the listing and its lstat."""
    for i in range(3):
        add(ws, tight, f"v{i}")

    class Vanishing:
        def __init__(self, item: os.DirEntry[str]) -> None:
            self._item = item

        def __getattr__(self, name: str) -> Any:
            return getattr(self._item, name)

        def stat(self, *, follow_symlinks: bool = True) -> os.stat_result:
            raise FileNotFoundError(self._item.path)

    real_iter = store_mod._iter_dir

    def vanishing_iter(path: str, budget: Any = None) -> Any:
        for item in real_iter(path, budget):
            yield Vanishing(item) if f"/{BLOBS_DIR}/" in path and item.is_file() else item

    monkeypatch.setattr(store_mod, "_iter_dir", vanishing_iter)
    tight._approx_total = None
    assert tight.maybe_enforce_limits(now=NOW) is not None  # no FileNotFoundError escaped


# ------------------------------------------------------------------------- SEC-08 dedupe
def planted_blob_slot(ws: Path, store: LocalFsCacheStore, data: bytes) -> Path:
    store.ensure_layout()
    sha = store_mod._sha256(data).hexdigest()
    slot = blob_file(ws, sha)
    slot.parent.mkdir(parents=True, exist_ok=True)
    return slot


@needs_posix
def test_put_blob_replaces_a_planted_symlink_and_never_touches_its_target(
    ws: Path, store: LocalFsCacheStore, tmp_path: Path
) -> None:
    data = b"content to cache"
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"precious")
    slot = planted_blob_slot(ws, store, data)
    slot.symlink_to(outside)
    ref = store.put_blob(io.BytesIO(data), max_bytes=BLOB_BOUND)
    assert ref.new  # not a dedupe onto the poison
    assert store.has_blob(ref.sha256) and not slot.is_symlink()
    assert slot.read_bytes() == data and outside.read_bytes() == b"precious"


def test_put_blob_replaces_a_wrong_size_regular_file(ws: Path, store: LocalFsCacheStore) -> None:
    data = b"the real content"
    slot = planted_blob_slot(ws, store, data)
    slot.write_bytes(b"half-writ")  # a torn file of the wrong size
    ref = store.put_blob(io.BytesIO(data), max_bytes=BLOB_BOUND)
    assert ref.new and slot.read_bytes() == data


def test_put_blob_dedupes_onto_a_regular_file_of_the_right_size(
    ws: Path, store: LocalFsCacheStore
) -> None:
    data = b"already stored"
    first = store.put_blob(io.BytesIO(data), max_bytes=BLOB_BOUND)
    os.utime(blob_file(ws, first.sha256), (1.0, 1.0))  # long ago
    second = store.put_blob(io.BytesIO(data), max_bytes=BLOB_BOUND)
    assert first.new and not second.new
    assert blob_file(ws, first.sha256).stat().st_mtime > 1.0  # refreshed (the prune grace period)


def test_put_blob_moves_a_planted_directory_aside_and_installs_the_blob(
    ws: Path, store: LocalFsCacheStore
) -> None:
    data = b"blob behind a planted directory"
    slot = planted_blob_slot(ws, store, data)
    (slot / "nested").mkdir(parents=True)
    (slot / "nested" / "junk").write_bytes(b"x")
    ref = store.put_blob(io.BytesIO(data), max_bytes=BLOB_BOUND)
    assert ref.new and store.has_blob(ref.sha256) and slot.read_bytes() == data
    set_mtime(slot, 0)  # fresh on the fixed clock: inside the sweep grace period
    trash = [p for p in root_of(ws).iterdir() if p.name.startswith(TRASH_DIR_PREFIX)]
    assert len(trash) == 1  # the poison waits there for the next prune
    report = store.prune(now=NOW, max_bytes=None, ttl_days=None)
    assert report.removed_trash == 1 and store.has_blob(ref.sha256)


def test_deleting_a_directory_at_a_blob_or_entry_slot_is_unsafe_path_not_a_raw_oserror(
    ws: Path, store: LocalFsCacheStore
) -> None:
    data = b"x"
    slot = planted_blob_slot(ws, store, data)
    slot.mkdir()
    with pytest.raises(CacheUnsafePathError):
        store.delete_blob(slot.name)
    entry_slot = entry_file(ws, key_of("dir-entry"))
    entry_slot.parent.mkdir(parents=True, exist_ok=True)
    entry_slot.mkdir()
    with pytest.raises(CacheUnsafePathError):
        store.delete_entry(key_of("dir-entry"))
    assert slot.is_dir() and entry_slot.is_dir()  # never removed through this path


# ------------------------------------------------------------------------- SEC-09 read_blob
def test_a_transient_open_error_is_a_store_error_not_corruption(
    ws: Path, store: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    ref = store.put_blob(io.BytesIO(b"good data"), max_bytes=BLOB_BOUND)

    def emfile(path: str) -> int:
        raise OSError(errno.EMFILE, "Too many open files")

    monkeypatch.setattr(safeio, "open_regular_read", emfile)
    with pytest.raises(CacheError) as err:
        store.read_blob(ref.sha256, io.BytesIO(), max_bytes=BLOB_BOUND)
    assert not isinstance(err.value, CacheIntegrityError)  # so no caller evicts on it
    assert err.value.reason == REASON_STORE_ERROR
    assert blob_file(ws, ref.sha256).is_file()


@needs_posix
def test_an_irregular_blob_is_still_corruption(ws: Path, store: LocalFsCacheStore) -> None:
    ref = store.put_blob(io.BytesIO(b"good data"), max_bytes=BLOB_BOUND)
    slot = blob_file(ws, ref.sha256)
    slot.unlink()
    slot.symlink_to(ws)
    with pytest.raises(CacheIntegrityError):
        store.read_blob(ref.sha256, io.BytesIO(), max_bytes=BLOB_BOUND)


def test_verify_reports_an_unreadable_blob_instead_of_calling_it_corrupt(
    ws: Path, store: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    add(ws, store, "one")

    def boom(*_a: Any, **_k: Any) -> int:
        raise CacheError(REASON_STORE_ERROR, "OSError")

    monkeypatch.setattr(store, "read_blob", boom)
    report = store.verify()
    assert [p.kind for p in report.problems] == ["unreadable"] and not report.ok


# ------------------------------------------------------------------------- S-3 / S-4 unreadable
class UnreadableEntryFile:
    """Make `safeio.read_bounded` fail with EACCES for one path (never `chmod`: CI may be root)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
        self.path = str(path)
        real = safeio.read_bounded

        def read(p: str, max_bytes: int) -> bytes:
            if p == self.path:
                raise PermissionError(errno.EACCES, "Permission denied", p)
            return real(p, max_bytes)

        monkeypatch.setattr(safeio, "read_bounded", read)


def test_an_unreadable_entry_does_not_abort_verify_stats_or_prune(
    ws: Path, store: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    add(ws, store, "fine")
    bad = add(ws, store, "bad")
    UnreadableEntryFile(monkeypatch, entry_file(ws, bad.key))
    report = store.verify()
    assert not report.ok
    assert [(p.kind, p.detail) for p in report.problems if p.kind == "unreadable"] == [
        ("unreadable", f"{bad.key[:2]}/{bad.key}.json")
    ]
    stats = store.stats(now=NOW)
    assert (stats.entries, stats.anomalies) == (1, 1)
    pruned = store.prune(now=NOW, max_bytes=None, ttl_days=None, dry_run=True)
    assert pruned.removed_entries == {}
    store.prune(now=NOW, max_bytes=None, ttl_days=None)
    assert entry_file(ws, bad.key).is_file()  # unreadable is not provably invalid: never deleted


def test_the_sweep_fails_closed_when_an_entry_file_cannot_be_read(
    ws: Path, store: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S-4: an unreadable entry file may reference any blob; sweeping would destroy it."""
    store.ensure_layout()
    ref = store.put_blob(io.BytesIO(b"maybe referenced"), max_bytes=BLOB_BOUND)
    set_mtime(blob_file(ws, ref.sha256), OLD_SECONDS)
    foreign = root_of(ws) / ENTRIES_DIR / "v9" / "aa" / "x.json"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("names " + ref.sha256)
    with monkeypatch.context() as patched:
        UnreadableEntryFile(patched, foreign)
        assert store.prune(now=NOW, max_bytes=None, ttl_days=None).removed_blobs == 0
        assert store.stats(now=NOW).orphan_blobs == 0  # cannot be claimed an orphan
        assert [p for p in store.verify().problems if p.kind == "orphan_blob"] == []
    assert store.has_blob(ref.sha256)
    foreign.unlink()  # control: once readable (and gone) the same blob IS an orphan and swept
    assert store.prune(now=NOW, max_bytes=None, ttl_days=None).removed_blobs == 1


def test_a_too_large_foreign_file_also_fails_the_sweep_closed(
    ws: Path, store: LocalFsCacheStore
) -> None:
    store.ensure_layout()
    ref = store.put_blob(io.BytesIO(b"maybe referenced too"), max_bytes=BLOB_BOUND)
    set_mtime(blob_file(ws, ref.sha256), OLD_SECONDS)
    big = root_of(ws) / ENTRIES_DIR / "v9" / "aa" / "big.json"
    big.parent.mkdir(parents=True)
    big.write_bytes(b" " * (2 * ONE_MIB))  # larger than any entry file can be: cannot be scanned
    assert store.prune(now=NOW, max_bytes=None, ttl_days=None).removed_blobs == 0
    assert store.has_blob(ref.sha256)


# ------------------------------------------------------------------------- S-5 sweep race
def test_a_blob_refreshed_between_the_scan_and_the_sweep_is_not_deleted(
    ws: Path, store: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A concurrent `put_blob` dedupes onto an old unreferenced blob (refreshing its mtime) and
    has not written its entry yet: the sweep must re-check the age right before deleting."""
    store.ensure_layout()
    ref = store.put_blob(io.BytesIO(b"about to be reused"), max_bytes=BLOB_BOUND)
    set_mtime(blob_file(ws, ref.sha256), OLD_SECONDS)
    real_mark = store._referenced_blobs

    def mark_then_get_reused(**kwargs: Any) -> Any:
        marks = real_mark(**kwargs)
        os.utime(blob_file(ws, ref.sha256), (NOW.timestamp(), NOW.timestamp()))
        return marks

    monkeypatch.setattr(store, "_referenced_blobs", mark_then_get_reused)
    report = store.prune(now=NOW + timedelta(seconds=1), max_bytes=None, ttl_days=None)
    assert store.has_blob(ref.sha256) and stat.S_ISREG(blob_file(ws, ref.sha256).lstat().st_mode)
    assert report.removed_blobs == 0  # the guard skipped the unlink and the report says so
