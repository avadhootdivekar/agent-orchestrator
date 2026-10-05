"""T-u3jG8F (U-R1..U-R12, edge cases): capture and restore (HLD 8.5).

Runs against `InMemoryCacheStore` (plus one round-trip smoke test against `LocalFsCacheStore`).
FIFO tests run in a thread joined with a timeout; permission failures are simulated by patching,
never produced with `chmod` (CI may run as root).
"""

from __future__ import annotations

import hashlib
import io
import os
import stat
import sys
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.cache import restore as restore_mod
from agent_orchestrator.cache import safeio
from agent_orchestrator.cache.constants import (
    ENTRY_SCHEMA,
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
)
from agent_orchestrator.cache.restore import (
    HashingWriter,
    RestoreResult,
    capture_outputs,
    restore_outputs,
)
from agent_orchestrator.cache.store import LocalFsCacheStore
from agent_orchestrator.cache.types import (
    CacheEntry,
    CacheError,
    CacheIntegrityError,
    CacheStore,
    CacheUnsafePathError,
    OutputRecord,
    RestoreMiss,
    StoreSkip,
)
from tests.cache.fakes import InMemoryCacheStore, key_of, make_entry, sha_of

FIFO_TIMEOUT_SECONDS = 5
CAP = 1024 * 1024

needs_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no mkfifo on this platform")
needs_posix = pytest.mark.skipif(sys.platform == "win32", reason="symlinks/modes are POSIX-only")


@pytest.fixture(autouse=True)
def _cache_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AO_CACHE", raising=False)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


@pytest.fixture
def store() -> InMemoryCacheStore:
    return InMemoryCacheStore()


def run_with_timeout(fn: Callable[[], Any], timeout: float = FIFO_TIMEOUT_SECONDS) -> Any:
    box: dict[str, Any] = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - surfaced to the caller below
            box["error"] = exc

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), "call did not return in time (blocked on a FIFO?)"
    if "error" in box:
        raise box["error"]
    return box.get("value")


def seed(
    store: CacheStore,
    files: Mapping[str, bytes],
    *,
    mode: int = 0o644,
    manifest_paths: Mapping[str, str] | None = None,
) -> CacheEntry:
    """An entry whose blobs are in `store`; `manifest_paths` renames a rel path in the manifest."""
    records = []
    for rel, data in files.items():
        ref = store.put_blob(io.BytesIO(data), max_bytes=CAP)
        path = (manifest_paths or {}).get(rel, rel)
        records.append(OutputRecord(path=path, sha256=ref.sha256, size=ref.size, mode=mode))
    base = make_entry(key_of("restore"))
    return CacheEntry(
        schema=ENTRY_SCHEMA,
        key=base.key,
        key_schema=base.key_schema,
        created_at=base.created_at,
        source=base.source,
        usage=base.usage,
        outputs=records,
        key_summary=base.key_summary,
    )


def expected_for(ws: Path, *rels: str) -> dict[str, str]:
    return {rel: str(ws / rel) for rel in rels}


def restore(
    entry: CacheEntry, ws: Path, store: CacheStore, expected: Mapping[str, str] | None = None
) -> RestoreResult:
    exp = expected if expected is not None else expected_for(ws, *(o.path for o in entry.outputs))
    return restore_outputs(entry, exp, store, workspace_root=str(ws), max_entry_bytes=CAP)


def leftovers(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.name.startswith(RESTORE_TMP_PREFIX))


def snapshot(root: Path) -> dict[str, tuple[bytes, float]]:
    return {
        p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


# ----------------------------------------------------------------------------- HashingWriter
def test_hashing_writer_forwards_and_hashes_exactly_what_was_written() -> None:
    sink = io.BytesIO()
    writer = HashingWriter(sink)
    assert writer.write(b"abc") == 3
    assert writer.write(b"") == 0
    assert writer.write(b"def") == 3
    assert sink.getvalue() == b"abcdef"
    assert writer.size == 6
    assert writer.hexdigest() == hashlib.sha256(b"abcdef").hexdigest()


# ----------------------------------------------------------------------------- U-R1 capture
def test_capture_records_sha_size_and_mode_and_stores_the_blob(
    ws: Path, store: InMemoryCacheStore
) -> None:
    (ws / "out").mkdir()
    (ws / "out" / "b.md").write_bytes(b"second")
    (ws / "a.txt").write_bytes(b"first file")
    (ws / "a.txt").chmod(0o640)
    abs_map = {"out/b.md": str(ws / "out" / "b.md"), "a.txt": str(ws / "a.txt")}
    records = capture_outputs(abs_map, store, max_entry_bytes=CAP)
    assert [r.path for r in records] == ["a.txt", "out/b.md"]  # sorted by path
    first = records[0]
    assert (first.sha256, first.size, first.kind) == (sha_of(b"first file"), 10, "file")
    assert first.mode == 0o640
    assert store.has_blob(sha_of(b"second"))
    assert store.blobs[sha_of(b"first file")] == b"first file"


@needs_posix
def test_capture_masks_the_stored_mode_to_the_permission_bits(
    ws: Path, store: InMemoryCacheStore
) -> None:
    target = ws / "setuid.sh"
    target.write_bytes(b"#!/bin/sh\n")
    target.chmod(0o4755)
    (record,) = capture_outputs({"setuid.sh": str(target)}, store, max_entry_bytes=CAP)
    assert record.mode == 0o755  # the setuid bit never reaches the entry


# ----------------------------------------------------------------------------- U-R2
def test_capture_of_a_directory_is_not_regular(ws: Path, store: InMemoryCacheStore) -> None:
    (ws / "d").mkdir()
    with pytest.raises(StoreSkip) as err:
        capture_outputs({"d": str(ws / "d")}, store, max_entry_bytes=CAP)
    assert err.value.reason == REASON_OUTPUT_NOT_REGULAR


@needs_posix
def test_capture_of_a_symlink_is_not_regular(
    ws: Path, store: InMemoryCacheStore, tmp_path: Path
) -> None:
    (tmp_path / "real").write_bytes(b"real")
    (ws / "link").symlink_to(tmp_path / "real")
    with pytest.raises(StoreSkip) as err:
        capture_outputs({"link": str(ws / "link")}, store, max_entry_bytes=CAP)
    assert err.value.reason == REASON_OUTPUT_NOT_REGULAR
    assert store.blobs == {}


@needs_posix
def test_capture_through_a_swapped_parent_directory_is_refused(
    ws: Path, store: InMemoryCacheStore, tmp_path: Path
) -> None:
    """SEC-05: `out` was swapped for a link to an outside directory after the key was built; the
    outside file must never be read into a blob."""
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "id_rsa").write_bytes(b"PRIVATE-KEY-MATERIAL")
    (ws / "out").symlink_to(outside, target_is_directory=True)
    with pytest.raises(StoreSkip) as err:
        capture_outputs({"out/id_rsa": str(ws / "out" / "id_rsa")}, store, max_entry_bytes=CAP)
    assert err.value.reason == REASON_OUTPUT_NOT_REGULAR
    assert store.blobs == {}


@needs_fifo
def test_capture_of_a_fifo_is_not_regular_and_does_not_block(
    ws: Path, store: InMemoryCacheStore
) -> None:
    os.mkfifo(ws / "pipe")
    with pytest.raises(StoreSkip) as err:
        run_with_timeout(
            lambda: capture_outputs({"pipe": str(ws / "pipe")}, store, max_entry_bytes=CAP)
        )
    assert err.value.reason == REASON_OUTPUT_NOT_REGULAR


def test_capture_of_a_missing_output_is_output_missing(ws: Path, store: InMemoryCacheStore) -> None:
    with pytest.raises(StoreSkip) as err:
        capture_outputs({"nope.txt": str(ws / "nope.txt")}, store, max_entry_bytes=CAP)
    assert (err.value.reason, err.value.detail) == (REASON_OUTPUT_MISSING, "nope.txt")


def test_capture_oserror_on_open_is_store_error(
    ws: Path, store: InMemoryCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    (ws / "f").write_bytes(b"x")

    def denied(path: str) -> int:
        raise PermissionError(13, "simulated EACCES")

    monkeypatch.setattr(safeio, "open_regular_read", denied)
    with pytest.raises(StoreSkip) as err:
        capture_outputs({"f": str(ws / "f")}, store, max_entry_bytes=CAP)
    assert err.value.reason == REASON_STORE_ERROR


# ----------------------------------------------------------------------------- U-R3 capture bounds
def test_capture_over_the_total_budget_is_entry_too_large(
    ws: Path, store: InMemoryCacheStore
) -> None:
    (ws / "a").write_bytes(b"a" * 60)
    (ws / "b").write_bytes(b"b" * 60)
    abs_map = {"a": str(ws / "a"), "b": str(ws / "b")}
    with pytest.raises(StoreSkip) as err:
        capture_outputs(abs_map, store, max_entry_bytes=100)
    assert (err.value.reason, err.value.detail) == (REASON_ENTRY_TOO_LARGE, "b")
    assert len(capture_outputs(abs_map, store, max_entry_bytes=120)) == 2  # exactly at the budget


def test_capture_of_a_single_file_over_the_budget_is_entry_too_large(
    ws: Path, store: InMemoryCacheStore
) -> None:
    (ws / "big").write_bytes(b"x" * 101)
    with pytest.raises(StoreSkip) as err:
        capture_outputs({"big": str(ws / "big")}, store, max_entry_bytes=100)
    assert err.value.reason == REASON_ENTRY_TOO_LARGE
    assert store.blobs == {}


def test_capture_of_a_file_that_grows_during_capture_is_entry_too_large(ws: Path) -> None:
    target = ws / "growing"
    target.write_bytes(b"x" * 50)

    class GrowingStore(InMemoryCacheStore):
        def put_blob(self, src: Any, *, max_bytes: int) -> Any:
            with open(target, "ab") as grow:  # the producer is still writing
                grow.write(b"y" * 1000)
            return super().put_blob(src, max_bytes=max_bytes)

    with pytest.raises(StoreSkip) as err:
        capture_outputs({"growing": str(target)}, GrowingStore(), max_entry_bytes=100)
    assert err.value.reason == REASON_ENTRY_TOO_LARGE


def test_capture_empty_output_records_the_empty_blob(ws: Path, store: InMemoryCacheStore) -> None:
    (ws / "empty").write_bytes(b"")
    (record,) = capture_outputs({"empty": str(ws / "empty")}, store, max_entry_bytes=0)
    assert (record.sha256, record.size) == (sha_of(b""), 0)


# ----------------------------------------------------------------------------- U-R4 restore
def test_restore_writes_identical_bytes_and_creates_parents(
    ws: Path, store: InMemoryCacheStore
) -> None:
    entry = seed(store, {"out/deep/a.md": b"alpha\n", "b.txt": b"beta"}, mode=0o664)
    result = restore(entry, ws, store)
    assert result == RestoreResult(files=2, bytes=len(b"alpha\n") + len(b"beta"))
    assert (ws / "out" / "deep" / "a.md").read_bytes() == b"alpha\n"
    assert (ws / "b.txt").read_bytes() == b"beta"
    assert stat.S_IMODE((ws / "b.txt").stat().st_mode) == 0o664 & 0o755
    assert leftovers(ws) == []


def test_restore_replaces_an_existing_destination(ws: Path, store: InMemoryCacheStore) -> None:
    (ws / "a.txt").write_bytes(b"stale content that is longer")
    entry = seed(store, {"a.txt": b"fresh"})
    restore(entry, ws, store)
    assert (ws / "a.txt").read_bytes() == b"fresh"


@needs_posix
def test_restore_replaces_a_read_only_existing_destination(
    ws: Path, store: InMemoryCacheStore
) -> None:
    (ws / "a.txt").write_bytes(b"old")
    (ws / "a.txt").chmod(0o444)
    entry = seed(store, {"a.txt": b"new"})
    restore(entry, ws, store)
    assert (ws / "a.txt").read_bytes() == b"new"


# ----------------------------------------------------------------------------- U-R5 (ADV-1)
MANIFEST_CASES: dict[str, tuple[dict[str, bytes], dict[str, str], tuple[str, ...]]] = {
    "dotdot": ({"out.txt": b"x"}, {"out.txt": "../escape"}, ("out.txt",)),
    "nested-dotdot": ({"out.txt": b"x"}, {"out.txt": "a/../../escape"}, ("out.txt",)),
    "absolute": ({"out.txt": b"x"}, {"out.txt": "/tmp/escape"}, ("out.txt",)),
    "missing": ({"a": b"1"}, {}, ("a", "b")),
    "extra": ({"a": b"1", "b": b"2"}, {}, ("a",)),
    "renamed": ({"a": b"1"}, {"a": "A"}, ("a",)),
}


@pytest.mark.parametrize("case", sorted(MANIFEST_CASES))
def test_manifest_that_differs_from_the_spec_is_manifest_mismatch_and_writes_nothing(
    ws: Path, tmp_path: Path, store: InMemoryCacheStore, case: str
) -> None:
    files, renames, spec_rels = MANIFEST_CASES[case]
    entry = seed(store, files, manifest_paths=renames)
    before = sorted(p.name for p in tmp_path.iterdir())
    with pytest.raises(RestoreMiss) as err:
        restore(entry, ws, store, expected_for(ws, *spec_rels))
    assert (err.value.reason, err.value.evict) == (REASON_MANIFEST_MISMATCH, True)
    assert sorted(p.name for p in tmp_path.iterdir()) == before  # nothing outside the workspace
    assert list(ws.iterdir()) == []
    assert store.called("read_blob") == 0


def test_duplicate_manifest_path_is_manifest_mismatch(ws: Path, store: InMemoryCacheStore) -> None:
    entry = seed(store, {"a": b"1"})
    dup = entry.model_copy(update={"outputs": [entry.outputs[0], entry.outputs[0]]})
    with pytest.raises(RestoreMiss) as err:
        restore(dup, ws, store, expected_for(ws, "a"))
    assert (err.value.reason, err.value.evict) == (REASON_MANIFEST_MISMATCH, True)


def test_size_sum_above_the_cap_is_corrupt_entry(ws: Path, store: InMemoryCacheStore) -> None:
    entry = seed(store, {"a": b"x" * 60, "b": b"y" * 60})
    with pytest.raises(RestoreMiss) as err:
        restore_outputs(
            entry, expected_for(ws, "a", "b"), store, workspace_root=str(ws), max_entry_bytes=100
        )
    assert (err.value.reason, err.value.evict) == (REASON_CORRUPT_ENTRY, True)
    assert list(ws.iterdir()) == []


# ----------------------------------------------------------------------------- U-R6 (ADV-3)
@pytest.mark.parametrize(
    "tampered",
    [b"tampered!!", b"short", b"much longer than the original bytes were"],
    ids=["same-size-wrong-hash", "shorter", "longer"],
)
def test_corrupt_blob_is_blob_corrupt_and_no_destination_is_touched(
    ws: Path, store: InMemoryCacheStore, tampered: bytes
) -> None:
    (ws / "good.txt").write_bytes(b"pre-existing good")
    (ws / "bad.txt").write_bytes(b"pre-existing bad")
    os.utime(ws / "good.txt", (1000, 1000))
    os.utime(ws / "bad.txt", (1000, 1000))
    entry = seed(store, {"good.txt": b"new good", "bad.txt": b"0123456789"})
    bad_sha = sha_of(b"0123456789")
    store.blobs[bad_sha] = tampered
    before = snapshot(ws)
    with pytest.raises(RestoreMiss) as err:
        restore(entry, ws, store)
    assert (err.value.reason, err.value.evict, err.value.blob) == (
        REASON_BLOB_CORRUPT,
        True,
        bad_sha,
    )
    assert snapshot(ws) == before  # contents and mtimes untouched
    assert leftovers(ws) == []


def test_store_integrity_error_on_read_is_blob_corrupt(ws: Path, store: InMemoryCacheStore) -> None:
    entry = seed(store, {"a": b"data"})
    store.fail_with("read_blob", CacheIntegrityError(REASON_BLOB_CORRUPT, "unreadable/irregular"))
    with pytest.raises(RestoreMiss) as err:
        restore(entry, ws, store)
    assert (err.value.reason, err.value.evict, err.value.blob) == (
        REASON_BLOB_CORRUPT,
        True,
        entry.outputs[0].sha256,
    )
    assert leftovers(ws) == []
    assert not (ws / "a").exists()


def test_lying_size_in_the_manifest_is_blob_corrupt(ws: Path, store: InMemoryCacheStore) -> None:
    entry = seed(store, {"a": b"0123456789"})
    lying = entry.outputs[0].model_copy(update={"size": 3})
    with pytest.raises(RestoreMiss) as err:
        restore(entry.model_copy(update={"outputs": [lying]}), ws, store)
    assert (err.value.reason, err.value.evict) == (REASON_BLOB_CORRUPT, True)
    assert not (ws / "a").exists() and leftovers(ws) == []


# ----------------------------------------------------------------------------- U-R7 missing blob
def test_missing_blob_is_blob_missing(ws: Path, store: InMemoryCacheStore) -> None:
    entry = seed(store, {"a": b"data", "b": b"other"})
    del store.blobs[sha_of(b"other")]
    with pytest.raises(RestoreMiss) as err:
        restore(entry, ws, store)
    assert (err.value.reason, err.value.evict, err.value.blob) == (REASON_BLOB_MISSING, True, None)
    assert list(ws.iterdir()) == []


# ----------------------------------------------------------------------------- U-R8 directory
def test_destination_that_is_a_directory_is_restore_failed_and_other_files_are_untouched(
    ws: Path, store: InMemoryCacheStore
) -> None:
    (ws / "a").write_bytes(b"old a")
    (ws / "z").mkdir()  # sorts after "a": the failure must still precede every commit
    entry = seed(store, {"a": b"new a", "z": b"new z"})
    with pytest.raises(RestoreMiss) as err:
        restore(entry, ws, store)
    assert (err.value.reason, err.value.evict) == (REASON_RESTORE_FAILED, False)
    assert (ws / "a").read_bytes() == b"old a"
    assert (ws / "z").is_dir() and leftovers(ws) == []


# ----------------------------------------------------------------------------- U-R9 (ADV-10)
@pytest.mark.parametrize(
    "rel",
    [".git/hooks/pre-commit", "CLAUDE.md", ".github/workflows/x.yml", ".claude/settings.json"],
)
def test_sensitive_destination_is_refused_and_nothing_is_created(
    ws: Path, store: InMemoryCacheStore, rel: str
) -> None:
    entry = seed(store, {rel: b"#!/bin/sh\nevil\n"})
    with pytest.raises(RestoreMiss) as err:
        restore(entry, ws, store)
    assert (err.value.reason, err.value.evict) == (REASON_SENSITIVE_OUTPUT, False)
    assert list(ws.iterdir()) == []
    assert store.called("read_blob") == 0


@pytest.mark.parametrize("rel", [".GIT/hooks/pre-commit", "Claude.md", "docs/claude.md"])
def test_case_variants_of_sensitive_paths_are_refused(
    ws: Path, store: InMemoryCacheStore, rel: str
) -> None:
    """SEC-06: a case-insensitive filesystem makes these the protected targets."""
    with pytest.raises(RestoreMiss) as err:
        restore(seed(store, {rel: b"x"}), ws, store)
    assert (err.value.reason, err.value.evict) == (REASON_SENSITIVE_OUTPUT, False)
    assert list(ws.iterdir()) == []


# ----------------------------------------------------------------------------- U-R10 links (M-5)
@needs_posix
def test_parent_that_is_a_symlink_is_restore_failed_and_nothing_is_written_through_it(
    ws: Path, tmp_path: Path, store: InMemoryCacheStore
) -> None:
    victim = tmp_path / "victim"
    victim.mkdir()
    (ws / "out").symlink_to(victim, target_is_directory=True)
    entry = seed(store, {"out/a.txt": b"payload"})
    with pytest.raises(RestoreMiss) as err:
        restore(entry, ws, store)
    assert (err.value.reason, err.value.evict) == (REASON_RESTORE_FAILED, False)
    assert list(victim.iterdir()) == []


@needs_posix
def test_parent_swapped_for_a_link_after_the_chain_check_is_restore_failed(
    ws: Path, tmp_path: Path, store: InMemoryCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    victim = tmp_path / "victim"
    victim.mkdir()
    real_ensure = safeio.ensure_dir_chain

    def ensure_then_swap(root: str, path: str, mode: int) -> None:
        real_ensure(root, path, mode)
        os.rmdir(path)
        os.symlink(victim, path)  # the window between the chain check and staging

    monkeypatch.setattr(safeio, "ensure_dir_chain", ensure_then_swap)
    entry = seed(store, {"out/a.txt": b"payload"})
    with pytest.raises(RestoreMiss) as err:
        restore(entry, ws, store)
    assert (err.value.reason, err.value.evict) == (REASON_RESTORE_FAILED, False)
    assert list(victim.iterdir()) == []


@needs_posix
def test_destination_that_is_a_symlink_is_restore_failed_and_its_target_untouched(
    ws: Path, tmp_path: Path, store: InMemoryCacheStore
) -> None:
    target = tmp_path / "target.txt"
    target.write_bytes(b"precious")
    (ws / "a.txt").symlink_to(target)
    with pytest.raises(RestoreMiss) as err:
        restore(seed(store, {"a.txt": b"new"}), ws, store)
    assert (err.value.reason, err.value.evict) == (REASON_RESTORE_FAILED, False)
    assert target.read_bytes() == b"precious"
    assert leftovers(ws) == []


def test_destination_parent_outside_the_workspace_is_restore_failed(
    ws: Path, tmp_path: Path, store: InMemoryCacheStore
) -> None:
    entry = seed(store, {"a.txt": b"x"})
    outside = {"a.txt": str(tmp_path / "outside" / "a.txt")}
    with pytest.raises(RestoreMiss) as err:
        restore(entry, ws, store, outside)
    assert err.value.reason == REASON_RESTORE_FAILED
    assert not (tmp_path / "outside").exists()


# ----------------------------------------------------------------------------- U-R11 commit failure
def test_replace_failure_part_way_is_restore_failed_and_removes_every_temp_file(
    ws: Path, store: InMemoryCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = seed(store, {"a": b"1", "b": b"2", "c": b"3"})
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src: Any, dst: Any) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError(28, "No space left on device (simulated)")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(RestoreMiss) as err:
        restore(entry, ws, store)
    assert (err.value.reason, err.value.evict, err.value.detail) == (
        REASON_RESTORE_FAILED,
        False,
        "OSError",
    )
    assert leftovers(ws) == []  # a partial commit is never accepted, but never leaves litter


def test_simulated_eacces_on_staging_is_restore_failed(
    ws: Path, store: InMemoryCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def denied(path: str, mode: int = 0) -> int:
        raise PermissionError(13, "simulated EACCES")

    monkeypatch.setattr(safeio, "create_exclusive", denied)
    with pytest.raises(RestoreMiss) as err:
        restore(seed(store, {"a": b"1"}), ws, store)
    assert (err.value.reason, err.value.evict, err.value.detail) == (
        REASON_RESTORE_FAILED,
        False,
        "PermissionError",
    )
    assert leftovers(ws) == []


def test_oserror_from_the_store_read_is_restore_failed(ws: Path, store: InMemoryCacheStore) -> None:
    entry = seed(store, {"a": b"1"})
    store.fail_with("read_blob", OSError(5, "simulated EIO"))
    with pytest.raises(RestoreMiss) as err:
        restore(entry, ws, store)
    assert (err.value.reason, err.value.evict) == (REASON_RESTORE_FAILED, False)
    assert leftovers(ws) == [] and not (ws / "a").exists()


def test_unsafe_path_from_the_store_propagates_unchanged_and_cleans_up(
    ws: Path, store: InMemoryCacheStore
) -> None:
    entry = seed(store, {"a": b"1"})
    boom = CacheUnsafePathError("unsafe_path", "symlinked shard")
    store.fail_with("read_blob", boom)
    with pytest.raises(CacheUnsafePathError) as err:
        restore(entry, ws, store)
    assert err.value is boom
    assert leftovers(ws) == [] and not (ws / "a").exists()


# ----------------------------------------------------------------------------- U-R12 modes (ADV-8)
@needs_posix
@pytest.mark.parametrize(
    ("stored", "restored"),
    [(0o777, 0o755), (0o666, 0o644), (0o700, 0o700), (0o444, 0o444), (0o000, 0o000)],
)
def test_restored_mode_is_the_stored_mode_masked_to_0o755(
    ws: Path, store: InMemoryCacheStore, stored: int, restored: int
) -> None:
    restore(seed(store, {"f": b"data"}, mode=stored), ws, store)
    assert stat.S_IMODE((ws / "f").stat().st_mode) == restored


@needs_posix
def test_the_staging_file_is_opened_exclusive_nofollow_and_cloexec(
    ws: Path, store: InMemoryCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, int]] = []
    real_open = os.open

    def spy(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        if RESTORE_TMP_PREFIX in os.fspath(path):
            seen.append((os.fspath(path), flags))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", spy)
    restore(seed(store, {"a": b"1", "b": b"2"}), ws, store)
    assert len(seen) == 2
    for _path, flags in seen:
        assert flags & os.O_CLOEXEC
        assert flags & os.O_EXCL and flags & os.O_NOFOLLOW and flags & os.O_CREAT


@needs_posix
def test_the_staging_file_is_private_until_its_content_is_verified(
    ws: Path, store: InMemoryCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    modes: list[int] = []
    real_fchmod = os.fchmod

    def spy(fd: int, mode: int) -> None:
        modes.append(stat.S_IMODE(os.fstat(fd).st_mode))  # mode BEFORE the final fchmod
        real_fchmod(fd, mode)

    monkeypatch.setattr(os, "fchmod", spy)
    restore(seed(store, {"a": b"1"}, mode=0o777), ws, store)
    assert modes == [0o600]


@needs_posix
def test_the_mode_is_applied_to_the_descriptor_never_to_a_path(
    ws: Path, store: InMemoryCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEC-07: `os.chmod(path)` follows a link swapped in after staging; `fchmod(fd)` cannot."""

    def forbidden(path: Any, mode: int, **kwargs: Any) -> None:
        raise AssertionError(f"restore chmod'ed by path: {path}")

    monkeypatch.setattr(os, "chmod", forbidden)
    restore(seed(store, {"a": b"1", "b": b"2"}, mode=0o777), ws, store)
    assert stat.S_IMODE((ws / "a").stat().st_mode) == 0o755  # M-8 mask still applied


@needs_posix
def test_a_failing_commit_rolls_back_every_rename_already_done(
    ws: Path, store: InMemoryCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SEC-07: a failure on the Nth rename leaves NO partially restored workspace."""
    (ws / "a").write_bytes(b"old a")
    (ws / "b").write_bytes(b"old b")  # exists: restored from its hard-link backup
    entry = seed(store, {"a": b"new a", "b": b"new b", "c": b"new c"})
    real_replace = os.replace
    commits = {"n": 0}

    def flaky(src: Any, dst: Any) -> None:
        if RESTORE_TMP_PREFIX in os.fspath(src) and not os.fspath(src).endswith(".bak"):
            commits["n"] += 1
            if commits["n"] == 3:  # a and b are already renamed into place
                raise OSError(28, "No space left on device (simulated)")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(RestoreMiss) as err:
        restore(entry, ws, store)
    assert (err.value.reason, err.value.evict) == (REASON_RESTORE_FAILED, False)
    assert (ws / "a").read_bytes() == b"old a" and (ws / "b").read_bytes() == b"old b"
    assert not (ws / "c").exists()
    assert leftovers(ws) == []


@needs_posix
def test_a_rollback_removes_a_destination_that_did_not_exist_before(
    ws: Path, store: InMemoryCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = seed(store, {"a": b"new a", "b": b"new b"})
    real_replace = os.replace
    commits = {"n": 0}

    def flaky(src: Any, dst: Any) -> None:
        if RESTORE_TMP_PREFIX in os.fspath(src) and not os.fspath(src).endswith(".bak"):
            commits["n"] += 1
            if commits["n"] == 2:
                raise OSError(5, "Input/output error (simulated)")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(RestoreMiss):
        restore(entry, ws, store)
    assert not (ws / "a").exists() and not (ws / "b").exists() and leftovers(ws) == []


def test_a_successful_restore_leaves_no_backup_litter(ws: Path, store: InMemoryCacheStore) -> None:
    (ws / "a").write_bytes(b"old")
    restore(seed(store, {"a": b"new"}), ws, store)
    assert (ws / "a").read_bytes() == b"new" and leftovers(ws) == []


@needs_posix
def test_a_transient_blob_read_failure_is_a_non_evicting_miss(
    ws: Path, store: InMemoryCacheStore
) -> None:
    """SEC-09: EMFILE / EIO says nothing about the blob; never destroy cached data on it."""
    entry = seed(store, {"a": b"data"})
    store.fail_with("read_blob", CacheError(REASON_STORE_ERROR, "OSError"))
    with pytest.raises(RestoreMiss) as err:
        restore(entry, ws, store)
    assert (err.value.reason, err.value.evict) == (REASON_STORE_ERROR, False)
    assert list(ws.iterdir()) == []


# ----------------------------------------------------------------------------- edge cases
def test_zero_byte_output_round_trips(ws: Path, store: InMemoryCacheStore) -> None:
    (ws / "empty").write_bytes(b"")
    (record,) = capture_outputs({"empty": str(ws / "empty")}, store, max_entry_bytes=CAP)
    (ws / "empty").unlink()
    entry = seed(store, {"empty": b""})
    assert entry.outputs[0].size == 0 == record.size
    assert restore(entry, ws, store) == RestoreResult(files=1, bytes=0)
    assert (ws / "empty").read_bytes() == b""


def test_two_outputs_sharing_one_blob_both_restore(ws: Path, store: InMemoryCacheStore) -> None:
    entry = seed(store, {"one.txt": b"same", "sub/two.txt": b"same"})
    assert len(store.blobs) == 1
    restore(entry, ws, store)
    assert store.called("read_blob") == 2  # read twice
    assert (ws / "one.txt").read_bytes() == (ws / "sub" / "two.txt").read_bytes() == b"same"


def test_restore_of_nothing_new_is_idempotent(ws: Path, store: InMemoryCacheStore) -> None:
    entry = seed(store, {"a": b"1"})
    restore(entry, ws, store)
    restore(entry, ws, store)
    assert (ws / "a").read_bytes() == b"1" and leftovers(ws) == []


# ----------------------------------------------------------------------------- real store smoke
def test_round_trip_through_the_local_store_into_another_workspace(
    tmp_path: Path, ws: Path
) -> None:
    (ws / "out").mkdir()
    (ws / "out" / "report.md").write_bytes(b"# report\n" * 100)
    (ws / "out" / "report.md").chmod(0o640)
    (ws / "data.bin").write_bytes(bytes(range(256)))
    local = LocalFsCacheStore.for_workspace(ws, max_bytes=CAP * 64, ttl_days=30)
    records = capture_outputs(
        {"out/report.md": str(ws / "out" / "report.md"), "data.bin": str(ws / "data.bin")},
        local,
        max_entry_bytes=CAP,
    )
    other = tmp_path / "other-ws"
    other.mkdir()
    entry = make_entry(key_of("smoke")).model_copy(update={"outputs": records})
    result = restore_outputs(
        entry,
        expected_for(other, "out/report.md", "data.bin"),
        local,
        workspace_root=str(other),
        max_entry_bytes=CAP,
    )
    assert result.files == 2
    assert (other / "out" / "report.md").read_bytes() == (ws / "out" / "report.md").read_bytes()
    assert (other / "data.bin").read_bytes() == bytes(range(256))
    by_path = {r.path: r for r in records}
    for rel in ("out/report.md", "data.bin"):
        assert stat.S_IMODE((other / rel).stat().st_mode) == by_path[rel].mode & 0o755
    assert by_path["out/report.md"].mode == 0o640
    assert leftovers(other) == []


def test_module_exports_are_stable() -> None:
    for name in ("HashingWriter", "RestoreResult", "capture_outputs", "restore_outputs"):
        assert hasattr(restore_mod, name)
