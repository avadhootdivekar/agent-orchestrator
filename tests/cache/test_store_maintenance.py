"""T-HjxNQ0 (U-SM1..SM12, class shape): the `CacheAdmin` half of `LocalFsCacheStore` (HLD 8.4.3).

Every expectation is deterministic: `prune` / `stats` / `maybe_enforce_limits` take an explicit
`now` (a fixed clock), entry "last used" is set with `touch_entry`, and blob / temp ages are set
with `os.utime` relative to that clock. Symlink / FIFO tests carry skip markers.
"""

from __future__ import annotations

import inspect
import io
import json
import math
import os
import shutil
import stat
import sys
import threading
import tracemalloc
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from agent_orchestrator.cache import safeio
from agent_orchestrator.cache import store as store_mod
from agent_orchestrator.cache.constants import (
    BLOB_SWEEP_GRACE_SECONDS,
    BLOBS_DIR,
    ENTRIES_DIR,
    ENTRIES_VERSION_DIR,
    EVICT_LOW_WATER_RATIO,
    MAX_TTL_DAYS,
    REASON_CORRUPT_ENTRY,
    REASON_STORE_ERROR,
    TMP_DIR,
    TMP_SWEEP_GRACE_SECONDS,
    TRASH_DIR_PREFIX,
)
from agent_orchestrator.cache.store import LocalFsCacheStore
from agent_orchestrator.cache.types import (
    CacheAdmin,
    CacheEntry,
    CacheError,
    CacheStore,
    CacheUnsafePathError,
    EntryInfo,
    PruneReport,
)
from tests.cache.fakes import key_of, make_entry, sha_of
from tests.cache.store_contract import CacheStoreContract

NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)
OLD_SECONDS = 2 * BLOB_SWEEP_GRACE_SECONDS  # older than every grace period
BLOB_BOUND = 1024 * 1024
MAX_BYTES = 1024**3
TTL_DAYS = 30

needs_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no mkfifo on this platform")
needs_posix = pytest.mark.skipif(sys.platform == "win32", reason="symlinks are POSIX-only")


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


def root_of(ws: Path) -> Path:
    return ws / ".orchestrator" / "cache"


def entry_file(ws: Path, key: str) -> Path:
    return root_of(ws) / ENTRIES_DIR / ENTRIES_VERSION_DIR / key[:2] / f"{key}.json"


def blob_file(ws: Path, sha: str) -> Path:
    return root_of(ws) / BLOBS_DIR / sha[:2] / sha


def set_mtime(path: Path, seconds_before_now: float) -> None:
    stamp = NOW.timestamp() - seconds_before_now
    os.utime(path, (stamp, stamp), follow_symlinks=False)


def add(
    ws: Path,
    store: LocalFsCacheStore,
    name: str,
    *,
    age_days: float = 0,
    files: dict[str, bytes] | None = None,
    last_used_seconds_ago: float = 0,
    blob_age_seconds: float = OLD_SECONDS,
) -> CacheEntry:
    """Store a complete entry: its blobs (aged), the entry (created `age_days` ago, last used
    `last_used_seconds_ago` before NOW)."""
    contents = files if files is not None else {f"out/{name}.txt": name.encode() * 50}
    for data in contents.values():
        ref = store.put_blob(io.BytesIO(data), max_bytes=BLOB_BOUND)
        set_mtime(blob_file(ws, ref.sha256), blob_age_seconds)
    entry = make_entry(
        key_of(name),
        outputs=list(contents.items()),
        created_at=NOW - timedelta(days=age_days),
    )
    store.put_entry(entry)
    store.touch_entry(entry.key, NOW - timedelta(seconds=last_used_seconds_ago))
    return entry


def tree(root: Path) -> dict[str, tuple[Any, ...]]:
    """Everything under `root` without following links: type, size, mtime, link target."""
    out: dict[str, tuple[Any, ...]] = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in [*dirnames, *filenames]:
            path = Path(dirpath) / name
            st = path.lstat()
            link = os.readlink(path) if stat.S_ISLNK(st.st_mode) else None
            out[path.relative_to(root).as_posix()] = (
                stat.S_IFMT(st.st_mode),
                st.st_size,
                st.st_mtime_ns,
                link,
            )
    return out


def names_of(ws: Path, store: LocalFsCacheStore) -> set[str]:
    return {info.key for info in store.iter_entries() if info.entry is not None}


# --------------------------------------------------------------------------- class shape (AC-15)
class TestContractStillPasses(CacheStoreContract):
    @pytest.fixture
    def store(self, tmp_path: Path) -> CacheStore:
        workspace = tmp_path / "contract-ws"
        workspace.mkdir()
        return LocalFsCacheStore.for_workspace(workspace, max_bytes=MAX_BYTES, ttl_days=TTL_DAYS)


def test_store_is_an_instance_of_both_abcs(store: LocalFsCacheStore) -> None:
    assert isinstance(store, CacheStore) and isinstance(store, CacheAdmin)
    assert not LocalFsCacheStore.__abstractmethods__


# --------------------------------------------------------------------------- U-SM1 TTL
class StepClock:
    """A fixed clock that only moves when the test advances it."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def advance(self, **kwargs: float) -> datetime:
        self.now += timedelta(**kwargs)
        return self.now


def test_ttl_removes_exactly_the_expired_entries_as_the_clock_steps(
    ws: Path, store: LocalFsCacheStore
) -> None:
    for name, age in (("a", 0), ("b", 10), ("c", 25)):
        add(ws, store, name, age_days=age)
    clock = StepClock(NOW)
    first = store.prune(now=clock.now, max_bytes=None, ttl_days=TTL_DAYS)
    assert first.removed_entries == {} and first.total_removed == 0
    clock.advance(days=10)  # c is now 35 days old, b 20, a 10
    second = store.prune(now=clock.now, max_bytes=None, ttl_days=TTL_DAYS)
    assert second.removed_entries == {"expired": 1}
    assert names_of(ws, store) == {key_of("a"), key_of("b")}
    clock.advance(days=15)  # b is 35 days old, a 25
    third = store.prune(now=clock.now, max_bytes=None, ttl_days=TTL_DAYS)
    assert third.removed_entries == {"expired": 1}
    assert names_of(ws, store) == {key_of("a")}


def test_ttl_none_removes_nothing_however_old(ws: Path, store: LocalFsCacheStore) -> None:
    add(ws, store, "ancient", age_days=10_000)
    report = store.prune(now=NOW, max_bytes=None, ttl_days=None)
    assert report.total_removed == 0
    assert store.get_entry(key_of("ancient")) is not None


def test_expiry_boundary_and_a_future_created_at(ws: Path, store: LocalFsCacheStore) -> None:
    add(ws, store, "exact", age_days=TTL_DAYS)
    add(ws, store, "future", age_days=-365)
    add(ws, store, "over", age_days=TTL_DAYS + 0.001)
    report = store.prune(now=NOW, max_bytes=None, ttl_days=TTL_DAYS)
    assert report.removed_entries == {"expired": 1}
    assert names_of(ws, store) == {key_of("exact"), key_of("future")}


# --------------------------------------------------------------------------- U-SM2 LRU
def _expected_removals(total: int, max_bytes: int, unit: int) -> int:
    target = int(max_bytes * EVICT_LOW_WATER_RATIO)
    return math.ceil((total - target) / unit)


def test_lru_removes_the_least_recently_used_down_to_the_low_water_mark(
    ws: Path, store: LocalFsCacheStore
) -> None:
    count = 10
    names = [f"e{i}" for i in range(count)]
    for i, name in enumerate(names):  # e0 is the oldest, e9 the newest
        add(
            ws,
            store,
            name,
            files={f"out/{name}.txt": bytes([i]) * 1000},
            last_used_seconds_ago=(count - i) * 3600,
        )
    unit = entry_file(ws, key_of("e0")).stat().st_size + 1000
    total = count * unit
    max_bytes = total - 1
    removals = _expected_removals(total, max_bytes, unit)
    assert 1 < removals < count
    report = store.prune(now=NOW, max_bytes=max_bytes, ttl_days=None)
    assert report.removed_entries == {"lru": removals}
    assert names_of(ws, store) == {key_of(n) for n in names[removals:]}  # the newest survive
    after = store.stats(now=NOW)
    assert after.total_bytes <= int(max_bytes * EVICT_LOW_WATER_RATIO)
    assert (report.bytes_before, report.bytes_after) == (total, after.total_bytes)


def test_lru_ties_on_mtime_are_broken_by_key(ws: Path, store: LocalFsCacheStore) -> None:
    names = [f"t{i}" for i in range(10)]
    for name in names:
        add(
            ws,
            store,
            name,
            files={f"out/{name}.txt": name.encode().ljust(1000, b"x")},
            last_used_seconds_ago=60,
        )
    unit = entry_file(ws, key_of("t0")).stat().st_size + 1000
    total = 10 * unit
    removals = _expected_removals(total, total - 1, unit)
    store.prune(now=NOW, max_bytes=total - 1, ttl_days=None)
    survivors = names_of(ws, store)
    by_key = sorted(key_of(n) for n in names)
    assert survivors == set(by_key[removals:])  # the smallest keys went first


def test_max_bytes_zero_is_a_valid_budget_and_removes_everything(
    ws: Path, store: LocalFsCacheStore
) -> None:
    for name in "abc":
        add(ws, store, name)
    report = store.prune(now=NOW, max_bytes=0, ttl_days=None)
    assert report.removed_entries == {"lru": 3}
    assert names_of(ws, store) == set()
    assert not any((root_of(ws) / BLOBS_DIR).rglob("*.*")) and report.bytes_after == 0
    assert store.stats(now=NOW).blobs == 0  # the freed blobs were old: swept in the same pass


def test_within_budget_removes_nothing(ws: Path, store: LocalFsCacheStore) -> None:
    add(ws, store, "a")
    report = store.prune(now=NOW, max_bytes=MAX_BYTES, ttl_days=None)
    assert report.total_removed == 0 and report.removed_blobs == 0


def test_invalid_entries_are_removed_first_and_reported_as_invalid(
    ws: Path, store: LocalFsCacheStore
) -> None:
    good = add(ws, store, "good")
    bad = add(ws, store, "bad")
    entry_file(ws, bad.key).write_bytes(b"{not json")
    report = store.prune(now=NOW, max_bytes=None, ttl_days=None)
    assert report.removed_entries == {"invalid": 1}
    assert store.get_entry(good.key) is not None and not entry_file(ws, bad.key).exists()
    assert not blob_file(ws, bad.outputs[0].sha256).exists()  # its blob was unprotected and old


# --------------------------------------------------------------------------- U-SM3 shared blobs
def test_a_blob_shared_by_two_entries_survives_removing_one(
    ws: Path, store: LocalFsCacheStore
) -> None:
    shared = {"out/shared.txt": b"shared payload" * 20}
    old = add(ws, store, "old", age_days=100, files=shared)
    new = add(ws, store, "new", age_days=0, files=shared)
    sha = old.outputs[0].sha256
    assert sha == new.outputs[0].sha256
    first = store.prune(now=NOW, max_bytes=None, ttl_days=TTL_DAYS)
    assert first.removed_entries == {"expired": 1} and first.removed_blobs == 0
    assert store.has_blob(sha) and store.get_entry(new.key) is not None
    last = store.prune(now=NOW + timedelta(days=60), max_bytes=None, ttl_days=TTL_DAYS)
    assert last.removed_entries == {"expired": 1} and last.removed_blobs == 1
    assert not store.has_blob(sha)


def test_lru_counts_a_shared_blob_once_and_frees_it_with_its_last_reference(
    ws: Path, store: LocalFsCacheStore
) -> None:
    shared = {"out/s.txt": b"s" * 200}
    add(ws, store, "a", files=shared, last_used_seconds_ago=3600)
    add(ws, store, "b", files=shared, last_used_seconds_ago=60)
    entry_size = entry_file(ws, key_of("a")).stat().st_size
    total = 2 * entry_size + 200  # the shared blob is counted ONCE
    assert store.stats(now=NOW).total_bytes == total
    assert store.prune(now=NOW, max_bytes=total, ttl_days=None).total_removed == 0
    report = store.prune(now=NOW, max_bytes=total - 1, ttl_days=None)
    assert report.removed_entries == {"lru": 1}  # the older entry; the blob is still referenced
    assert names_of(ws, store) == {key_of("b")} and store.has_blob(sha_of(b"s" * 200))
    assert store.prune(now=NOW, max_bytes=0, ttl_days=None).removed_blobs == 1  # last reference


# --------------------------------------------------------------------------- U-SM4 grace periods
def test_unreferenced_blobs_and_temp_files_respect_their_grace_period(
    ws: Path, store: LocalFsCacheStore
) -> None:
    store.ensure_layout()
    fresh = store.put_blob(io.BytesIO(b"fresh orphan"), max_bytes=BLOB_BOUND)
    stale = store.put_blob(io.BytesIO(b"stale orphan"), max_bytes=BLOB_BOUND)
    set_mtime(blob_file(ws, fresh.sha256), BLOB_SWEEP_GRACE_SECONDS - 60)
    set_mtime(blob_file(ws, stale.sha256), BLOB_SWEEP_GRACE_SECONDS + 60)
    tmp_dir = root_of(ws) / TMP_DIR
    young, aged = tmp_dir / "1-1-aa.blob.tmp", tmp_dir / "1-1-bb.blob.tmp"
    young.write_bytes(b"x")
    aged.write_bytes(b"x")
    set_mtime(young, TMP_SWEEP_GRACE_SECONDS - 60)
    set_mtime(aged, TMP_SWEEP_GRACE_SECONDS + 60)
    report = store.prune(now=NOW, max_bytes=None, ttl_days=None)
    assert (report.removed_blobs, report.removed_tmp) == (1, 1)
    assert store.has_blob(fresh.sha256) and not store.has_blob(stale.sha256)
    assert young.exists() and not aged.exists()


def test_referenced_blobs_are_never_swept_however_old(ws: Path, store: LocalFsCacheStore) -> None:
    entry = add(ws, store, "kept", blob_age_seconds=10 * OLD_SECONDS)
    report = store.prune(now=NOW + timedelta(days=1), max_bytes=None, ttl_days=None)
    assert report.removed_blobs == 0 and store.has_blob(entry.outputs[0].sha256)


# --------------------------------------------------------------------------- U-SM5 version safety
def _plant_v2(ws: Path, blob_sha: str) -> Path:
    v2 = root_of(ws) / ENTRIES_DIR / "v2" / "ab"
    v2.mkdir(parents=True)
    path = v2 / f"{key_of('v2-entry')}.json"
    path.write_text(json.dumps({"schema": "ao.result-cache.entry/v2", "blobs": [blob_sha]}))
    return path


def test_a_foreign_version_entry_and_its_blob_survive_prune_and_inline_enforcement(
    ws: Path, store: LocalFsCacheStore
) -> None:
    store.ensure_layout()
    ref = store.put_blob(io.BytesIO(b"v2-only blob"), max_bytes=BLOB_BOUND)
    set_mtime(blob_file(ws, ref.sha256), OLD_SECONDS)
    v2_entry = _plant_v2(ws, ref.sha256)
    add(ws, store, "v1")
    for _ in range(2):
        store.prune(now=NOW, max_bytes=0, ttl_days=0)
    tiny = LocalFsCacheStore.for_workspace(ws, max_bytes=1, ttl_days=0)
    tiny.maybe_enforce_limits(now=NOW)
    assert v2_entry.is_file() and store.has_blob(ref.sha256)
    assert names_of(ws, store) == set()
    report = store.verify()
    assert report.ok
    assert [p.kind for p in report.problems if p.kind == "foreign_version"] == ["foreign_version"]
    assert store.stats(now=NOW).foreign_version_dirs == ("v2",)


def test_a_blob_named_only_by_a_kept_junk_v1_file_is_protected_by_the_hex_token_mark(
    ws: Path, store: LocalFsCacheStore
) -> None:
    store.ensure_layout()
    ref = store.put_blob(io.BytesIO(b"referenced by junk"), max_bytes=BLOB_BOUND)
    set_mtime(blob_file(ws, ref.sha256), OLD_SECONDS)
    shard = root_of(ws) / ENTRIES_DIR / ENTRIES_VERSION_DIR / "ab"
    shard.mkdir(parents=True)
    junk = shard / "unparseable.json"  # not an entry name: kept, never parsed, never deleted
    junk.write_text("not json but it names " + ref.sha256)
    wrong_shard = shard / f"{key_of('misplaced')}.json"  # hex name in the wrong shard: same
    wrong_shard.write_text(ref.sha256)
    store.prune(now=NOW, max_bytes=0, ttl_days=0)
    assert junk.is_file() and wrong_shard.is_file() and store.has_blob(ref.sha256)


def test_an_invalid_v1_entry_that_is_removed_does_not_protect_its_blob(
    ws: Path, store: LocalFsCacheStore
) -> None:
    entry = add(ws, store, "doomed")
    entry_file(ws, entry.key).write_text("corrupt " + entry.outputs[0].sha256)
    report = store.prune(now=NOW, max_bytes=None, ttl_days=None)
    assert report.removed_entries == {"invalid": 1} and report.removed_blobs == 1


# --------------------------------------------------------------------------- U-SM6 trash
def test_a_stale_trash_directory_is_removed_by_the_next_prune(
    ws: Path, store: LocalFsCacheStore
) -> None:
    store.ensure_layout()
    trash = root_of(ws) / f"{TRASH_DIR_PREFIX}deadbeef"
    (trash / "entries").mkdir(parents=True)
    (trash / "entries" / "x").write_bytes(b"x")
    dry = store.prune(now=NOW, max_bytes=None, ttl_days=None, dry_run=True)
    assert dry.removed_trash == 1 and trash.exists()
    real = store.prune(now=NOW, max_bytes=None, ttl_days=None)
    assert real.removed_trash == 1 and not trash.exists()


# --------------------------------------------------------------------------- U-SM7 clear
def test_clear_creates_its_trash_first_and_leaves_an_empty_cache(
    ws: Path, store: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("a", "b"):
        add(ws, store, name)
    expected_bytes = store.stats(now=NOW).total_bytes
    seen: list[list[str]] = []
    real_replace = os.replace

    def spy(src: Any, dst: Any) -> None:
        seen.append(sorted(p.name for p in root_of(ws).iterdir() if p.name.startswith("trash-")))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    report = store.clear()
    monkeypatch.undo()
    assert seen and all(len(trashes) == 1 for trashes in seen)  # trash existed before any move
    assert (report.removed_entries, report.removed_blobs) == (2, 2)
    assert report.bytes_freed == expected_bytes
    stats = store.stats(now=NOW)
    assert (stats.entries, stats.blobs, stats.trash_dirs) == (0, 0, 0)
    for name in (ENTRIES_DIR, BLOBS_DIR, TMP_DIR):
        assert not (root_of(ws) / name).exists()
    assert (root_of(ws) / "layout.json").is_file()  # the layout files are not cache content
    store.put_entry(make_entry(key_of("after")))  # the cache is usable again
    assert store.get_entry(key_of("after")) is not None


def test_clear_failing_to_remove_its_trash_is_a_store_error(
    ws: Path, store: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    add(ws, store, "a")

    def failing(path: Any, *args: Any, **kwargs: Any) -> None:
        raise OSError(16, "Device or resource busy (simulated)")

    monkeypatch.setattr(shutil, "rmtree", failing)
    with pytest.raises(CacheError) as err:
        store.clear()
    assert err.value.reason == REASON_STORE_ERROR


def test_clear_detects_a_removal_that_silently_did_nothing(
    ws: Path, store: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    add(ws, store, "a")
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **k: None)  # an `ignore_errors`-style no-op
    with pytest.raises(CacheError) as err:
        store.clear()
    assert err.value.reason == REASON_STORE_ERROR


def test_clear_on_a_missing_cache_is_a_no_op_and_removes_stale_trash(
    ws: Path, store: LocalFsCacheStore
) -> None:
    empty = store.clear()
    assert (empty.removed_entries, empty.removed_blobs, empty.bytes_freed) == (0, 0, 0)
    assert list(ws.iterdir()) == []
    add(ws, store, "a")
    stale = root_of(ws) / f"{TRASH_DIR_PREFIX}old"
    stale.mkdir()
    store.clear()
    assert not stale.exists()


@needs_posix
def test_clear_unlinks_a_symlinked_directory_but_never_touches_its_target(
    ws: Path, store: LocalFsCacheStore, tmp_path: Path
) -> None:
    store.ensure_layout()
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "precious.txt").write_text("keep me")
    shutil.rmtree(root_of(ws) / BLOBS_DIR)
    (root_of(ws) / BLOBS_DIR).symlink_to(victim, target_is_directory=True)
    store.clear()
    assert (victim / "precious.txt").read_text() == "keep me"
    assert not os.path.lexists(root_of(ws) / BLOBS_DIR)


# --------------------------------------------------------------------------- U-SM8 verify
def test_verify_on_a_healthy_cache_and_on_a_missing_one(ws: Path, store: LocalFsCacheStore) -> None:
    assert store.verify().ok  # nothing to check
    add(ws, store, "a")
    report = store.verify()
    assert report.ok and report.problems == ()
    assert (report.entries_checked, report.blobs_checked) == (1, 1)


def _kinds(report: Any) -> list[str]:
    return sorted(p.kind for p in report.problems)


def test_verify_reports_a_corrupt_entry_and_a_key_mismatch(
    ws: Path, store: LocalFsCacheStore
) -> None:
    bad = add(ws, store, "bad")
    other = add(ws, store, "other")
    entry_file(ws, bad.key).write_bytes(b"garbage")
    entry_file(ws, other.key).write_bytes(make_entry(key_of("not-other")).to_canonical_bytes())
    report = store.verify()
    assert not report.ok
    # the blobs of the two broken entries are no longer referenced: informational orphans
    assert _kinds(report) == ["corrupt_entry", "key_mismatch", "orphan_blob", "orphan_blob"]
    by_kind = {p.kind: p.key for p in report.problems if p.kind != "orphan_blob"}
    assert by_kind == {"corrupt_entry": bad.key, "key_mismatch": other.key}


def test_verify_reports_a_missing_and_a_corrupt_blob(ws: Path, store: LocalFsCacheStore) -> None:
    gone = add(ws, store, "gone")
    rotten = add(ws, store, "rotten")
    blob_file(ws, gone.outputs[0].sha256).unlink()
    blob_file(ws, rotten.outputs[0].sha256).write_bytes(b"flipped bits")
    report = store.verify()
    assert not report.ok
    problems = {p.kind: p for p in report.problems}
    assert problems["missing_blob"].key == gone.key
    assert problems["missing_blob"].blob == gone.outputs[0].sha256
    assert problems["corrupt_blob"].blob == rotten.outputs[0].sha256


def test_verify_treats_orphans_foreign_versions_and_junk_as_informational(
    ws: Path, store: LocalFsCacheStore
) -> None:
    add(ws, store, "a")
    orphan = store.put_blob(io.BytesIO(b"nobody references me"), max_bytes=BLOB_BOUND)
    _plant_v2(ws, sha_of(b"something else"))
    (root_of(ws) / ENTRIES_DIR / "stray.txt").write_text("junk in entries/")
    (root_of(ws) / ENTRIES_DIR / ENTRIES_VERSION_DIR / "ab").mkdir()
    (root_of(ws) / ENTRIES_DIR / ENTRIES_VERSION_DIR / "ab" / "readme.md").write_text("junk")
    report = store.verify()
    assert report.ok  # none of these is a failure
    assert _kinds(report) == [
        "foreign_version",
        "orphan_blob",
        "unexpected_file",
        "unexpected_file",
    ]
    assert [p.blob for p in report.problems if p.kind == "orphan_blob"] == [orphan.sha256]


@needs_posix
def test_verify_reports_symlinks_as_failures_and_never_follows_them(
    ws: Path, store: LocalFsCacheStore, tmp_path: Path
) -> None:
    entry = add(ws, store, "a")
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / f"{key_of('zz')}.json").write_text("{}")
    (root_of(ws) / ENTRIES_DIR / ENTRIES_VERSION_DIR / "cd").symlink_to(
        victim, target_is_directory=True
    )
    blob_dir = blob_file(ws, entry.outputs[0].sha256).parent
    (blob_dir / sha_of(b"link")).symlink_to(victim / f"{key_of('zz')}.json")
    report = store.verify()
    assert not report.ok
    assert _kinds(report) == ["symlink", "symlink"]


def test_verify_deletes_and_modifies_nothing(ws: Path, store: LocalFsCacheStore) -> None:
    good = add(ws, store, "good")
    bad = add(ws, store, "bad")
    entry_file(ws, bad.key).write_bytes(b"garbage")
    blob_file(ws, good.outputs[0].sha256).write_bytes(b"flipped")
    store.put_blob(io.BytesIO(b"orphan"), max_bytes=BLOB_BOUND)
    _plant_v2(ws, sha_of(b"x"))
    before = tree(root_of(ws))
    report = store.verify()
    assert not report.ok and report.problems
    assert tree(root_of(ws)) == before


# --------------------------------------------------------------------------- iter_entries
def test_iter_entries_is_a_generator_and_reports_each_entry(
    ws: Path, store: LocalFsCacheStore
) -> None:
    a = add(ws, store, "a")
    iterator = store.iter_entries()
    assert inspect.isgenerator(iterator)
    infos = list(iterator)
    assert len(infos) == 1
    info = infos[0]
    assert isinstance(info, EntryInfo) and info.entry is not None and not info.anomaly
    assert (info.key, info.size) == (a.key, entry_file(ws, a.key).stat().st_size)
    assert info.mtime == NOW.timestamp()
    assert info.error is None


def test_iter_entries_on_a_missing_or_empty_cache_yields_nothing(
    ws: Path, store: LocalFsCacheStore
) -> None:
    assert list(store.iter_entries()) == []
    store.ensure_layout()
    assert list(store.iter_entries()) == []


def test_iter_entries_marks_invalid_files_and_anomalies(ws: Path, store: LocalFsCacheStore) -> None:
    good = add(ws, store, "good")
    bad = add(ws, store, "bad")
    mismatch = add(ws, store, "mismatch")
    entry_file(ws, bad.key).write_bytes(b"{")
    entry_file(ws, mismatch.key).write_bytes(make_entry(key_of("zzz")).to_canonical_bytes())
    shard = entry_file(ws, good.key).parent
    (shard / "junk.txt").write_text("junk")
    (shard / "notakey.json").write_text("{}")
    dir_named_like_an_entry = entry_file(ws, key_of("dir"))
    dir_named_like_an_entry.mkdir(parents=True)
    infos = {info.key: info for info in store.iter_entries()}
    assert infos[good.key].entry is not None
    assert (infos[bad.key].entry, infos[bad.key].error) == (None, REASON_CORRUPT_ENTRY)
    assert infos[mismatch.key].error == "key_mismatch"
    anomalies = {info.key for info in infos.values() if info.anomaly}
    assert anomalies == {
        f"{good.key[:2]}/junk.txt",
        f"{good.key[:2]}/notakey.json",
        f"{key_of('dir')[:2]}/{key_of('dir')}.json",
    }
    assert all(infos[k].entry is None for k in anomalies)


@needs_posix
def test_iter_entries_never_follows_a_symlinked_shard_or_file(
    ws: Path, store: LocalFsCacheStore, tmp_path: Path
) -> None:
    entry = add(ws, store, "a")
    victim = tmp_path / "victim"
    victim.mkdir()
    victim_entry = victim / f"{key_of('v')}.json"
    victim_entry.write_bytes(make_entry(key_of("v")).to_canonical_bytes())
    version_dir = root_of(ws) / ENTRIES_DIR / ENTRIES_VERSION_DIR
    (version_dir / "ef").symlink_to(victim, target_is_directory=True)
    link_name = entry_file(ws, entry.key).parent / f"{key_of('l')}.json"
    link_name.symlink_to(victim_entry)
    before = victim_entry.stat().st_mtime_ns
    infos = list(store.iter_entries())
    assert {i.key for i in infos if i.anomaly} == {"ef", f"{entry.key[:2]}/{link_name.name}"}
    assert all(i.error == "symlink" for i in infos if i.anomaly)
    assert {i.key for i in infos if i.entry is not None} == {entry.key}
    assert victim_entry.stat().st_mtime_ns == before


def test_iter_entries_over_10000_entries_streams_in_bounded_memory(
    ws: Path, store: LocalFsCacheStore
) -> None:
    store.ensure_layout()
    template = make_entry(key_of("template"))
    version_dir = root_of(ws) / ENTRIES_DIR / ENTRIES_VERSION_DIR
    for i in range(10_000):
        key = key_of(f"stream-{i}")
        shard = version_dir / key[:2]
        shard.mkdir(exist_ok=True)
        entry = template.model_copy(update={"key": key})
        (shard / f"{key}.json").write_bytes(entry.to_canonical_bytes())
    tracemalloc.start()
    try:
        seen = 0
        for info in store.iter_entries():
            assert info.entry is not None
            seen += 1  # nothing is retained
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert seen == 10_000
    assert peak < 20 * 1024 * 1024


# --------------------------------------------------------------------------- U-SM10 inline bounds
class _Spy:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.parsed = 0
        self.read = 0
        real_parse, real_read = store_mod.parse_entry_bytes, safeio.read_bounded

        def parse(raw: bytes, key: str) -> CacheEntry:
            self.parsed += 1
            return real_parse(raw, key)

        def read(path: str, max_bytes: int) -> bytes:
            self.read += 1
            return real_read(path, max_bytes)

        monkeypatch.setattr(store_mod, "parse_entry_bytes", parse)
        monkeypatch.setattr(safeio, "read_bounded", read)


def test_inline_enforcement_defers_above_the_entry_count_without_parsing_any_entry(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(store_mod, "INLINE_PRUNE_MAX_ENTRIES", 10)
    small = LocalFsCacheStore.for_workspace(
        ws, max_bytes=1, ttl_days=MAX_TTL_DAYS
    )  # always over budget
    for i in range(10):
        add(ws, small, f"n{i}")
    spy = _Spy(monkeypatch)
    at_limit = small.maybe_enforce_limits(now=NOW)
    assert at_limit is not None and not at_limit.deferred  # exactly 10: not deferred, pruned
    add(ws, small, "extra-a")
    for i in range(10):
        add(ws, small, f"m{i}")
    spy.parsed = spy.read = 0
    small._approx_total = None
    report = small.maybe_enforce_limits(now=NOW)
    assert report is not None and report.deferred
    assert (report.removed_entries, report.removed_blobs) == ({}, 0)
    assert (spy.parsed, spy.read) == (0, 0)  # lstat only: no entry file was opened
    assert len(list(small.iter_entries())) > 10  # nothing was removed


def test_inline_enforcement_defers_above_the_entry_file_bytes_without_reading_any_file(
    ws: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    small = LocalFsCacheStore.for_workspace(ws, max_bytes=1, ttl_days=MAX_TTL_DAYS)
    for i in range(3):
        add(ws, small, f"b{i}")
    total = sum(entry_file(ws, key_of(f"b{i}")).stat().st_size for i in range(3))
    monkeypatch.setattr(store_mod, "INLINE_PRUNE_MAX_ENTRY_FILE_BYTES", 1024)
    assert total > 1024
    spy = _Spy(monkeypatch)
    small._approx_total = None
    report = small.maybe_enforce_limits(now=NOW)
    assert report is not None and report.deferred
    assert (spy.parsed, spy.read) == (0, 0)
    assert len(list(small.iter_entries())) == 3


def test_inline_enforcement_prunes_only_when_over_max_bytes(
    ws: Path, store: LocalFsCacheStore
) -> None:
    for i in range(6):
        add(
            ws,
            store,
            f"p{i}",
            files={f"out/p{i}.txt": bytes([i]) * 1000},
            last_used_seconds_ago=(6 - i) * 60,
        )
    assert store.maybe_enforce_limits(now=NOW) is None  # far under max_bytes: a cheap scan only
    total = store.stats(now=NOW).total_bytes
    tight = LocalFsCacheStore.for_workspace(ws, max_bytes=total - 1, ttl_days=MAX_TTL_DAYS)
    report = tight.maybe_enforce_limits(now=NOW)
    assert isinstance(report, PruneReport) and not report.deferred
    assert report.removed_entries.get("lru", 0) >= 1
    assert tight.stats(now=NOW).total_bytes <= int((total - 1) * EVICT_LOW_WATER_RATIO)
    assert tight._approx_total is None  # prune forces a fresh scan next time


def test_the_inline_total_follows_writes_made_through_this_store(
    ws: Path,
) -> None:
    small = LocalFsCacheStore.for_workspace(ws, max_bytes=4000, ttl_days=MAX_TTL_DAYS)
    assert small.maybe_enforce_limits(now=NOW) is None  # empty: the total is now known (0)
    for i in range(8):
        add(ws, small, f"w{i}", files={f"out/w{i}.txt": bytes([i]) * 1000})
    report = small.maybe_enforce_limits(now=NOW)  # put_* kept the approximation up to date
    assert report is not None and report.removed_entries.get("lru", 0) >= 1


# --------------------------------------------------------------------------- U-SM11 dry run
def test_dry_run_reports_what_a_real_prune_does_and_deletes_nothing(
    ws: Path, store: LocalFsCacheStore
) -> None:
    for i in range(5):
        add(ws, store, f"d{i}", age_days=i * 20, files={f"out/d{i}.txt": bytes([i]) * 500})
    store.put_blob(io.BytesIO(b"stale orphan"), max_bytes=BLOB_BOUND)
    set_mtime(blob_file(ws, sha_of(b"stale orphan")), OLD_SECONDS)
    (root_of(ws) / f"{TRASH_DIR_PREFIX}x").mkdir()
    bad = add(ws, store, "bad")
    entry_file(ws, bad.key).write_bytes(b"{")
    before = tree(root_of(ws))
    dry = store.prune(now=NOW, max_bytes=3000, ttl_days=TTL_DAYS, dry_run=True)
    assert dry.dry_run and tree(root_of(ws)) == before
    real = store.prune(now=NOW, max_bytes=3000, ttl_days=TTL_DAYS)
    assert not real.dry_run and tree(root_of(ws)) != before
    for field in (
        "removed_entries",
        "removed_blobs",
        "removed_tmp",
        "removed_trash",
        "bytes_before",
        "bytes_after",
    ):
        assert getattr(dry, field) == getattr(real, field), field
    assert real.removed_blobs >= 1 and real.removed_trash == 1
    assert {"invalid", "expired"} <= set(real.removed_entries)


# --------------------------------------------------------------------------- U-SM12 shapes
STATS_SCHEMA = {
    "type": "object",
    "required": ["schema", "root", "exists"],
    "properties": {
        "schema": {"const": "ao.result-cache.stats/v1"},
        "root": {"type": "string"},
        "exists": {"type": "boolean"},
        "entries": {"type": "integer"},
        "invalid_entries": {"type": "integer"},
        "expired_entries": {"type": "integer"},
        "foreign_version_dirs": {"type": "array", "items": {"type": "string"}},
        "blobs": {"type": "integer"},
        "orphan_blobs": {"type": "integer"},
        "tmp_files": {"type": "integer"},
        "trash_dirs": {"type": "integer"},
        "anomalies": {"type": "integer"},
        "bytes": {
            "type": "object",
            "properties": {
                k: {"type": "integer"}
                for k in ("entries", "blobs", "referenced_blobs", "orphan_blobs", "total")
            },
        },
        "limits": {
            "type": "object",
            "properties": {
                "max_bytes": {"type": "integer"},
                "max_entry_bytes": {"type": "integer"},
                "ttl_days": {"type": ["integer", "null"]},
            },
        },
        "oldest_created_at": {"type": ["string", "null"]},
        "newest_created_at": {"type": ["string", "null"]},
    },
}
PRUNE_SCHEMA = {
    "type": "object",
    "required": [
        "schema",
        "dry_run",
        "removed_entries",
        "removed_blobs",
        "bytes_before",
        "bytes_after",
    ],
    "properties": {
        "schema": {"const": "ao.result-cache.prune/v1"},
        "dry_run": {"type": "boolean"},
        "removed_entries": {
            "type": "object",
            "properties": {k: {"type": "integer"} for k in ("expired", "lru", "invalid")},
            "additionalProperties": False,
        },
        "removed_blobs": {"type": "integer"},
        "removed_tmp": {"type": "integer"},
        "removed_trash": {"type": "integer"},
        "bytes_before": {"type": "integer"},
        "bytes_after": {"type": "integer"},
    },
}
CLEAR_SCHEMA = {
    "type": "object",
    "required": ["schema", "removed_entries", "removed_blobs", "bytes_freed"],
    "properties": {
        "schema": {"const": "ao.result-cache.clear/v1"},
        "removed_entries": {"type": "integer"},
        "removed_blobs": {"type": "integer"},
        "bytes_freed": {"type": "integer"},
    },
}
VERIFY_KINDS = [
    "corrupt_entry",
    "key_mismatch",
    "missing_blob",
    "corrupt_blob",
    "orphan_blob",
    "foreign_version",
    "symlink",
    "unexpected_file",
]
VERIFY_SCHEMA = {
    "type": "object",
    "required": ["schema", "ok", "problems"],
    "properties": {
        "schema": {"const": "ao.result-cache.verify/v1"},
        "ok": {"type": "boolean"},
        "entries_checked": {"type": "integer"},
        "blobs_checked": {"type": "integer"},
        "problems": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["kind"],
                "properties": {
                    "kind": {"enum": VERIFY_KINDS},
                    "key": {"type": ["string", "null"]},
                    "blob": {"type": ["string", "null"]},
                    "detail": {"type": ["string", "null"]},
                },
            },
        },
    },
}
MAX_ENTRY_BYTES_FOR_SHAPE = 64 * 1024**2


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def test_reports_carry_everything_the_13_4_json_shapes_need(
    ws: Path, store: LocalFsCacheStore
) -> None:
    add(ws, store, "a", age_days=1)
    add(ws, store, "b", age_days=40)
    store.put_blob(io.BytesIO(b"orphan"), max_bytes=BLOB_BOUND)
    stats = store.stats(now=NOW)
    doc = {
        "schema": "ao.result-cache.stats/v1",
        "root": stats.root,
        "exists": stats.exists,
        "entries": stats.entries,
        "invalid_entries": stats.invalid_entries,
        "expired_entries": stats.expired_entries,
        "foreign_version_dirs": list(stats.foreign_version_dirs),
        "blobs": stats.blobs,
        "orphan_blobs": stats.orphan_blobs,
        "tmp_files": stats.tmp_files,
        "trash_dirs": stats.trash_dirs,
        "anomalies": stats.anomalies,
        "bytes": {
            "entries": stats.entries_bytes,
            "blobs": stats.blobs_bytes,
            "referenced_blobs": stats.referenced_blobs_bytes,
            "orphan_blobs": stats.orphan_blobs_bytes,
            "total": stats.total_bytes,
        },
        "limits": {
            "max_bytes": stats.max_bytes,
            "max_entry_bytes": MAX_ENTRY_BYTES_FOR_SHAPE,  # the CLI fills this from settings
            "ttl_days": stats.ttl_days,
        },
        "oldest_created_at": _iso(stats.oldest_created_at),
        "newest_created_at": _iso(stats.newest_created_at),
    }
    jsonschema.validate(doc, STATS_SCHEMA)
    assert (stats.entries, stats.expired_entries, stats.blobs, stats.orphan_blobs) == (2, 1, 3, 1)
    assert stats.oldest_created_at == NOW - timedelta(days=40)
    assert stats.newest_created_at == NOW - timedelta(days=1)
    assert stats.total_bytes == stats.entries_bytes + stats.blobs_bytes
    assert stats.referenced_blobs_bytes + stats.orphan_blobs_bytes == stats.blobs_bytes
    assert (stats.max_bytes, stats.ttl_days) == (MAX_BYTES, TTL_DAYS)

    prune = store.prune(now=NOW, max_bytes=None, ttl_days=TTL_DAYS, dry_run=True)
    jsonschema.validate(
        {
            "schema": "ao.result-cache.prune/v1",
            "dry_run": prune.dry_run,
            "removed_entries": dict(prune.removed_entries),
            "removed_blobs": prune.removed_blobs,
            "removed_tmp": prune.removed_tmp,
            "removed_trash": prune.removed_trash,
            "bytes_before": prune.bytes_before,
            "bytes_after": prune.bytes_after,
        },
        PRUNE_SCHEMA,
    )
    verify = store.verify()
    jsonschema.validate(
        {
            "schema": "ao.result-cache.verify/v1",
            "ok": verify.ok,
            "entries_checked": verify.entries_checked,
            "blobs_checked": verify.blobs_checked,
            "problems": [
                {"kind": p.kind, "key": p.key, "blob": p.blob, "detail": p.detail}
                for p in verify.problems
            ],
        },
        VERIFY_SCHEMA,
    )
    cleared = store.clear()
    jsonschema.validate(
        {
            "schema": "ao.result-cache.clear/v1",
            "removed_entries": cleared.removed_entries,
            "removed_blobs": cleared.removed_blobs,
            "bytes_freed": cleared.bytes_freed,
        },
        CLEAR_SCHEMA,
    )


def test_stats_of_a_missing_cache_and_of_a_populated_one(
    ws: Path, store: LocalFsCacheStore
) -> None:
    missing = store.stats(now=NOW)
    assert (missing.exists, missing.entries, missing.total_bytes) == (False, 0, 0)
    assert missing.root == str(root_of(ws)) and missing.max_bytes == MAX_BYTES
    add(ws, store, "a")
    bad = add(ws, store, "bad")
    entry_file(ws, bad.key).write_bytes(b"{")
    (root_of(ws) / TMP_DIR / "1-1-x.blob.tmp").write_bytes(b"x")
    (root_of(ws) / f"{TRASH_DIR_PREFIX}s").mkdir()
    (entry_file(ws, key_of("a")).parent / "stray.txt").write_text("junk")
    stats = store.stats(now=NOW)
    assert stats.exists
    assert (stats.entries, stats.invalid_entries) == (1, 1)
    assert (stats.tmp_files, stats.trash_dirs, stats.anomalies) == (1, 1, 1)
    assert stats.orphan_blobs == 1  # the corrupt entry's blob is no longer referenced
    assert stats.foreign_version_dirs == ()


# --------------------------------------------------------------------------- unsafe paths (D33)
@needs_posix
def test_prune_over_a_symlinked_entries_directory_raises_and_deletes_nothing(
    ws: Path, store: LocalFsCacheStore, tmp_path: Path
) -> None:
    entry = add(ws, store, "a", age_days=100)
    victim = tmp_path / "victim"
    shutil.copytree(root_of(ws) / ENTRIES_DIR, victim / ENTRIES_DIR)
    shutil.rmtree(root_of(ws) / ENTRIES_DIR)
    (root_of(ws) / ENTRIES_DIR).symlink_to(victim / ENTRIES_DIR, target_is_directory=True)
    before = tree(victim)
    for call in (
        lambda: store.prune(now=NOW, max_bytes=0, ttl_days=0),
        lambda: store.stats(now=NOW),
        store.verify,
        lambda: list(store.iter_entries()),
        lambda: store.maybe_enforce_limits(now=NOW),
    ):
        with pytest.raises(CacheUnsafePathError):
            call()
    assert tree(victim) == before
    assert blob_file(ws, entry.outputs[0].sha256).exists()


@needs_posix
def test_prune_never_removes_through_a_symlinked_blob_shard(
    ws: Path, store: LocalFsCacheStore, tmp_path: Path
) -> None:
    store.ensure_layout()
    ref = store.put_blob(io.BytesIO(b"orphan"), max_bytes=BLOB_BOUND)
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / ref.sha256).write_bytes(b"orphan")
    set_mtime(victim / ref.sha256, OLD_SECONDS)
    shard = blob_file(ws, ref.sha256).parent
    shutil.rmtree(shard)
    shard.symlink_to(victim, target_is_directory=True)
    report = store.prune(now=NOW, max_bytes=None, ttl_days=None)  # the shard is an anomaly
    assert report.removed_blobs == 0
    assert (victim / ref.sha256).read_bytes() == b"orphan"
    assert store.stats(now=NOW).anomalies == 1


@needs_fifo
def test_a_fifo_entry_file_is_invalid_and_is_removed_without_blocking(
    ws: Path, store: LocalFsCacheStore
) -> None:
    entry = add(ws, store, "a")
    path = entry_file(ws, entry.key)
    path.unlink()
    os.mkfifo(path)
    box: dict[str, Any] = {}

    def run() -> None:
        box["report"] = store.prune(now=NOW, max_bytes=None, ttl_days=None)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(5)
    assert not thread.is_alive(), "prune blocked on a FIFO"
    assert box["report"].removed_entries == {"invalid": 1}
    assert not os.path.lexists(path)


def test_prune_on_a_workspace_without_a_cache_is_an_empty_report(
    ws: Path, store: LocalFsCacheStore
) -> None:
    report = store.prune(now=NOW, max_bytes=0, ttl_days=0)
    assert (report.total_removed, report.removed_blobs, report.bytes_before) == (0, 0, 0)
    assert list(ws.iterdir()) == []
