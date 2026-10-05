"""T-U7ckfd (U-ST1..ST15 + class shape): `LocalFsCacheStore` hot path (HLD 8.4).

The shared `CacheStoreContract` runs against a real tmp_path workspace; the rest covers the
filesystem-specific guarantees (layout, hostile files, ownership, atomicity, dedupe, bounds, the
D33 unsafe-path rule). Symlink / FIFO / ownership tests carry skip markers; FIFO reads run in a
thread joined with a timeout so a regression cannot hang the suite.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.cache.constants import (
    BLOBS_DIR,
    CACHEDIR_TAG_BODY,
    ENTRIES_DIR,
    ENTRIES_VERSION_DIR,
    GITIGNORE_BODY,
    LAYOUT_FILE,
    LAYOUT_SCHEMA,
    MAX_ENTRY_FILE_BYTES,
    REASON_BLOB_CORRUPT,
    REASON_CORRUPT_ENTRY,
    REASON_KEY_MISMATCH,
    REASON_STORE_UNAVAILABLE,
    REASON_UNSAFE_PATH,
    TMP_DIR,
)
from agent_orchestrator.cache.store import LocalFsCacheStore, is_expired
from agent_orchestrator.cache.types import (
    CacheBlobMissingError,
    CacheIntegrityError,
    CacheLayoutError,
    CacheStore,
    CacheTooLargeError,
    CacheUnsafePathError,
)
from tests.cache.fakes import FIXED_CREATED_AT, key_of, make_entry, sha_of
from tests.cache.store_contract import CacheStoreContract

FIFO_TIMEOUT_SECONDS = 5
MAX_BYTES = 1024**3
TTL_DAYS = 30
BLOB_BOUND = 1024 * 1024

needs_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no mkfifo on this platform")
needs_posix = pytest.mark.skipif(sys.platform == "win32", reason="symlinks/uid are POSIX-only")
needs_euid = pytest.mark.skipif(not hasattr(os, "geteuid"), reason="no uid concept")
needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


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


def tmp_files(ws: Path) -> list[str]:
    tmp = root_of(ws) / TMP_DIR
    return sorted(p.name for p in tmp.iterdir()) if tmp.is_dir() else []


def run_with_timeout(fn: Any, timeout: float = FIFO_TIMEOUT_SECONDS) -> Any:
    """Run fn in a daemon thread; fail (not hang) if it does not return within *timeout*."""
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


# ---------------------------------------------------------------------------- U-ST5: contract
class TestLocalFsStoreContract(CacheStoreContract):
    @pytest.fixture
    def store(self, tmp_path: Path) -> CacheStore:
        workspace = tmp_path / "contract-ws"
        workspace.mkdir()
        return LocalFsCacheStore.for_workspace(workspace, max_bytes=MAX_BYTES, ttl_days=TTL_DAYS)


# ---------------------------------------------------------------------------- class shape (AC-16)
def test_class_derives_from_the_store_abc_and_is_complete() -> None:
    # T-U7ckfd shipped CacheStore only; T-HjxNQ0 added the CacheAdmin base (test_store_maintenance).
    assert CacheStore in LocalFsCacheStore.__mro__
    assert not LocalFsCacheStore.__abstractmethods__


def test_class_instantiates_and_exposes_the_root(ws: Path, store: LocalFsCacheStore) -> None:
    assert isinstance(store, CacheStore)
    assert store.root == str(root_of(ws))
    assert (store.max_bytes, store.ttl_days) == (MAX_BYTES, TTL_DAYS)


def test_maybe_enforce_limits_on_an_empty_store_is_a_no_op(store: LocalFsCacheStore) -> None:
    assert store.maybe_enforce_limits(now=FIXED_CREATED_AT) is None


# ---------------------------------------------------------------------------- U-ST1
class _OsSpy:
    """Records calls to the os functions a filesystem touch would use; delegates to the real one."""

    NAMES = ("mkdir", "open", "stat", "lstat", "unlink", "utime", "replace", "scandir", "rename")

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[str] = []
        self.recording = False
        for name in self.NAMES:
            monkeypatch.setattr(os, name, self._wrap(name, getattr(os, name)))

    def _wrap(self, name: str, real: Any) -> Any:
        def spy(*args: Any, **kwargs: Any) -> Any:
            if self.recording:
                self.calls.append(name)
            return real(*args, **kwargs)

        return spy


def test_for_workspace_does_no_io(ws: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spy = _OsSpy(monkeypatch)
    spy.recording = True
    LocalFsCacheStore.for_workspace(ws, max_bytes=MAX_BYTES, ttl_days=TTL_DAYS)
    spy.recording = False
    assert spy.calls == []
    assert list(ws.iterdir()) == []


def test_check_creates_nothing_without_a_cache_directory(
    ws: Path, store: LocalFsCacheStore
) -> None:
    store.check()
    assert list(ws.iterdir()) == []
    (ws / ".orchestrator").mkdir()
    store.check()  # `.orchestrator` exists, the cache root does not
    assert list((ws / ".orchestrator").iterdir()) == []


def test_reads_on_an_empty_workspace_create_nothing(ws: Path, store: LocalFsCacheStore) -> None:
    assert store.get_entry(key_of("x")) is None
    assert store.has_blob(sha_of(b"x")) is False
    assert store.delete_entry(key_of("x")) is False
    assert store.delete_blob(sha_of(b"x")) is False
    store.touch_entry(key_of("x"), FIXED_CREATED_AT)
    with pytest.raises(CacheBlobMissingError):
        store.read_blob(sha_of(b"x"), io.BytesIO(), max_bytes=10)
    assert list(ws.iterdir()) == []


# ---------------------------------------------------------------------------- U-ST2
def test_first_put_entry_creates_the_layout(ws: Path, store: LocalFsCacheStore) -> None:
    store.put_entry(make_entry(key_of("a")))
    root = root_of(ws)
    for rel in (".", ENTRIES_DIR, f"{ENTRIES_DIR}/{ENTRIES_VERSION_DIR}", BLOBS_DIR, TMP_DIR):
        assert stat.S_IMODE((root / rel).stat().st_mode) == 0o700, rel
    assert (root / ".gitignore").read_text() == GITIGNORE_BODY
    assert (root / ".gitignore").read_text().splitlines()[-1] == "*"
    assert (root / "CACHEDIR.TAG").read_text() == CACHEDIR_TAG_BODY
    assert (
        (root / "CACHEDIR.TAG")
        .read_text()
        .startswith("Signature: 8a477f597d28d172789f06886806bc55")
    )
    assert json.loads((root / LAYOUT_FILE).read_text()) == {"schema": LAYOUT_SCHEMA}
    assert tmp_files(ws) == []


def test_first_put_blob_creates_the_layout_too(ws: Path, store: LocalFsCacheStore) -> None:
    store.put_blob(io.BytesIO(b"x"), max_bytes=BLOB_BOUND)
    assert (root_of(ws) / LAYOUT_FILE).is_file()
    assert (root_of(ws) / TMP_DIR).is_dir()


def test_layout_is_idempotent_and_never_overwrites_existing_files(
    ws: Path, store: LocalFsCacheStore
) -> None:
    store.put_entry(make_entry(key_of("a")))
    gitignore = root_of(ws) / ".gitignore"
    gitignore.write_text("custom\n")
    store.put_entry(make_entry(key_of("b")))
    assert gitignore.read_text() == "custom\n"


@needs_git
def test_git_check_ignore_reports_cache_files_as_ignored(
    ws: Path, store: LocalFsCacheStore
) -> None:
    git_env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    subprocess.run(["git", "init", "-q", str(ws)], check=True, env=git_env)
    entry = make_entry(key_of("ignored"))
    store.put_entry(entry)
    ref = store.put_blob(io.BytesIO(b"blob"), max_bytes=BLOB_BOUND)
    for path in (entry_file(ws, entry.key), blob_file(ws, ref.sha256), root_of(ws) / LAYOUT_FILE):
        rel = path.relative_to(ws).as_posix()
        done = subprocess.run(
            ["git", "-C", str(ws), "check-ignore", "-q", rel], env=git_env, check=False
        )
        assert done.returncode == 0, rel
    status = subprocess.run(
        ["git", "-C", str(ws), "status", "--porcelain", "--untracked-files=all"],
        env=git_env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout.strip() == ""  # the cache never shows up as untracked


# ---------------------------------------------------------------------------- U-ST3 (M-2)
BAD_HEX = ["../x", "A" * 64, "a" * 63]


@pytest.mark.parametrize("bad", BAD_HEX)
def test_invalid_key_or_sha_raises_value_error_before_any_filesystem_call(
    ws: Path, store: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    spy = _OsSpy(monkeypatch)
    calls = [
        lambda: store.get_entry(bad),
        lambda: store.touch_entry(bad, FIXED_CREATED_AT),
        lambda: store.delete_entry(bad),
        lambda: store.has_blob(bad),
        lambda: store.read_blob(bad, io.BytesIO(), max_bytes=10),
        lambda: store.delete_blob(bad),
    ]
    for call in calls:
        spy.recording = True
        try:
            with pytest.raises(ValueError):
                call()
        finally:
            spy.recording = False
    assert spy.calls == []
    assert list(ws.iterdir()) == []


# ---------------------------------------------------------------------------- U-ST4
def test_entry_is_written_as_exactly_its_canonical_bytes(
    ws: Path, store: LocalFsCacheStore
) -> None:
    entry = make_entry(key_of("canon"))
    written = store.put_entry(entry)
    path = entry_file(ws, entry.key)
    assert path.parent.name == entry.key[:2]
    assert path.read_bytes() == entry.to_canonical_bytes()
    assert written == path.stat().st_size


def test_atomic_writes_leave_no_temp_files_and_use_private_modes(
    ws: Path, store: LocalFsCacheStore
) -> None:
    entry = make_entry(key_of("modes"))
    store.put_entry(entry)
    ref = store.put_blob(io.BytesIO(b"blob"), max_bytes=BLOB_BOUND)
    assert tmp_files(ws) == []
    assert stat.S_IMODE(entry_file(ws, entry.key).stat().st_mode) == 0o600
    assert stat.S_IMODE(blob_file(ws, ref.sha256).stat().st_mode) == 0o600
    assert stat.S_IMODE(entry_file(ws, entry.key).parent.stat().st_mode) == 0o700


def test_touch_entry_sets_the_mtime_used_as_the_lru_clock(
    ws: Path, store: LocalFsCacheStore
) -> None:
    entry = make_entry(key_of("lru"))
    store.put_entry(entry)
    at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    store.touch_entry(entry.key, at)
    assert entry_file(ws, entry.key).stat().st_mtime == at.timestamp()


def test_put_entry_over_the_file_bound_creates_nothing(ws: Path, store: LocalFsCacheStore) -> None:
    big = make_entry(key_of("big"), outputs=[(f"{i:04d}" + "p" * 4000, b"x") for i in range(300)])
    assert len(big.to_canonical_bytes()) > MAX_ENTRY_FILE_BYTES
    with pytest.raises(CacheTooLargeError):
        store.put_entry(big)
    assert list(ws.iterdir()) == []


def test_put_creates_a_missing_dot_orchestrator(ws: Path, store: LocalFsCacheStore) -> None:
    assert not (ws / ".orchestrator").exists()
    store.put_entry(make_entry(key_of("a")))
    assert (ws / ".orchestrator").is_dir()


# ---------------------------------------------------------------------------- U-ST6
def _seed(ws: Path, store: LocalFsCacheStore, name: str) -> tuple[str, Path]:
    """Store a valid entry (creating the layout) and return its key and file path."""
    entry = make_entry(key_of(name))
    store.put_entry(entry)
    return entry.key, entry_file(ws, entry.key)


@needs_fifo
def test_fifo_entry_file_is_corrupt_and_does_not_block(ws: Path, store: LocalFsCacheStore) -> None:
    key, path = _seed(ws, store, "fifo")
    path.unlink()
    os.mkfifo(path)
    with pytest.raises(CacheIntegrityError) as err:
        run_with_timeout(lambda: store.get_entry(key))
    assert err.value.reason == REASON_CORRUPT_ENTRY


@needs_posix
def test_final_component_symlink_entry_is_corrupt_and_eviction_unlinks_only_the_link(
    ws: Path, store: LocalFsCacheStore, tmp_path: Path
) -> None:
    key, path = _seed(ws, store, "link")
    target = tmp_path / "target.json"
    target.write_bytes(path.read_bytes())
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(CacheIntegrityError) as err:
        store.get_entry(key)
    assert err.value.reason == REASON_CORRUPT_ENTRY
    assert store.delete_entry(key) is True  # the eviction path: unlinks the link itself
    assert not os.path.lexists(path)
    assert target.is_file()


def test_oversize_entry_file_is_corrupt(ws: Path, store: LocalFsCacheStore) -> None:
    key, path = _seed(ws, store, "huge")
    path.write_bytes(b" " * (2 * 1024 * 1024))
    with pytest.raises(CacheIntegrityError) as err:
        store.get_entry(key)
    assert err.value.reason == REASON_CORRUPT_ENTRY


@pytest.mark.parametrize("junk", [b"", b"{not json", b"[1, 2]", b"\xff\xfe\x00", b"[" * 100_000])
def test_invalid_json_entry_file_is_corrupt(
    ws: Path, store: LocalFsCacheStore, junk: bytes
) -> None:
    key, path = _seed(ws, store, "junk")
    path.write_bytes(junk)
    with pytest.raises(CacheIntegrityError) as err:
        store.get_entry(key)
    assert err.value.reason == REASON_CORRUPT_ENTRY


def test_entry_with_the_wrong_key_is_a_key_mismatch(ws: Path, store: LocalFsCacheStore) -> None:
    key, path = _seed(ws, store, "mismatch")
    other = make_entry(key_of("some-other-key"))
    path.write_bytes(other.to_canonical_bytes())
    with pytest.raises(CacheIntegrityError) as err:
        store.get_entry(key)
    assert err.value.reason == REASON_KEY_MISMATCH


# ---------------------------------------------------------------------------- U-ST7
@needs_posix
def test_symlinked_root_is_a_layout_error(
    ws: Path, store: LocalFsCacheStore, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (ws / ".orchestrator").mkdir()
    root_of(ws).symlink_to(elsewhere)
    for call in (
        store.check,
        lambda: store.get_entry(key_of("a")),
        lambda: store.put_entry(make_entry(key_of("a"))),
        lambda: store.put_blob(io.BytesIO(b"x"), max_bytes=10),
        lambda: store.has_blob(sha_of(b"x")),
    ):
        with pytest.raises(CacheLayoutError) as err:
            call()
        assert err.value.reason == REASON_STORE_UNAVAILABLE
    assert list(elsewhere.iterdir()) == []


@needs_posix
def test_symlinked_dot_orchestrator_is_a_layout_error(
    ws: Path, store: LocalFsCacheStore, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "cache").mkdir(parents=True)
    (ws / ".orchestrator").symlink_to(elsewhere)
    for call in (
        store.check,
        lambda: store.get_entry(key_of("a")),
        lambda: store.put_entry(make_entry(key_of("a"))),
    ):
        with pytest.raises(CacheLayoutError):
            call()
    assert list((elsewhere / "cache").iterdir()) == []


@needs_posix
def test_dangling_dot_orchestrator_link_refuses_a_write(
    ws: Path, store: LocalFsCacheStore, tmp_path: Path
) -> None:
    (ws / ".orchestrator").symlink_to(tmp_path / "does-not-exist")
    with pytest.raises(CacheLayoutError):
        store.put_entry(make_entry(key_of("a")))
    assert not (tmp_path / "does-not-exist").exists()


def test_root_that_is_a_file_is_a_layout_error(ws: Path, store: LocalFsCacheStore) -> None:
    (ws / ".orchestrator").mkdir()
    root_of(ws).write_text("not a directory")
    with pytest.raises(CacheLayoutError):
        store.check()


# ---------------------------------------------------------------------------- U-ST8
@needs_posix
@needs_euid
def test_root_owned_by_another_user_is_a_layout_error(
    ws: Path, store: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.put_entry(make_entry(key_of("a")))
    real_euid = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: real_euid + 1)  # the root now looks foreign-owned
    for call in (store.check, lambda: store.get_entry(key_of("a"))):
        with pytest.raises(CacheLayoutError):
            call()
    with pytest.raises(CacheLayoutError):
        store.put_entry(make_entry(key_of("b")))
    assert not entry_file(ws, key_of("b")).exists()


@needs_posix
def test_group_writable_root_we_own_is_tightened_to_0700(
    ws: Path, store: LocalFsCacheStore
) -> None:
    store.put_entry(make_entry(key_of("a")))
    root_of(ws).chmod(0o770)
    store.check()
    assert stat.S_IMODE(root_of(ws).stat().st_mode) == 0o700
    root_of(ws).chmod(0o707)
    assert store.get_entry(key_of("a")) is not None
    assert stat.S_IMODE(root_of(ws).stat().st_mode) == 0o700


# ---------------------------------------------------------------------------- U-ST9
def _boom(*_args: Any, **_kwargs: Any) -> None:
    raise OSError(28, "No space left on device (simulated)")


def test_failed_rename_in_put_entry_leaves_no_destination_and_no_temp(
    ws: Path, store: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.ensure_layout()
    monkeypatch.setattr(os, "replace", _boom)
    entry = make_entry(key_of("a"))
    with pytest.raises(OSError, match="simulated"):
        store.put_entry(entry)
    assert not os.path.lexists(entry_file(ws, entry.key))
    assert tmp_files(ws) == []


def test_failed_rename_in_put_blob_leaves_no_destination_and_no_temp(
    ws: Path, store: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.ensure_layout()
    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError, match="simulated"):
        store.put_blob(io.BytesIO(b"payload"), max_bytes=BLOB_BOUND)
    assert not os.path.lexists(blob_file(ws, sha_of(b"payload")))
    assert tmp_files(ws) == []


def test_failed_write_in_put_blob_leaves_no_temp(
    ws: Path, store: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.ensure_layout()
    monkeypatch.setattr(os, "write", _boom)
    with pytest.raises(OSError, match="simulated"):
        store.put_blob(io.BytesIO(b"payload"), max_bytes=BLOB_BOUND)
    assert tmp_files(ws) == []


def test_a_short_write_is_continued_until_every_byte_is_written(
    ws: Path, store: LocalFsCacheStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_write = os.write

    def two_bytes_at_a_time(fd: int, data: Any) -> int:
        return real_write(fd, bytes(data)[:2])

    store.ensure_layout()
    monkeypatch.setattr(os, "write", two_bytes_at_a_time)
    data = b"abcdefghij" * 5
    ref = store.put_blob(io.BytesIO(data), max_bytes=BLOB_BOUND)
    monkeypatch.undo()
    assert blob_file(ws, ref.sha256).read_bytes() == data


# ---------------------------------------------------------------------------- U-ST10
def test_identical_bytes_give_one_blob_file_and_refresh_the_mtime(
    ws: Path, store: LocalFsCacheStore
) -> None:
    data = b"shared content"
    first = store.put_blob(io.BytesIO(data), max_bytes=BLOB_BOUND)
    path = blob_file(ws, first.sha256)
    os.utime(path, (1000, 1000))
    second = store.put_blob(io.BytesIO(data), max_bytes=BLOB_BOUND)
    assert (first.new, second.new) == (True, False)
    assert first.sha256 == second.sha256 == sha_of(data)
    assert [p for p in path.parent.iterdir()] == [path]
    assert path.stat().st_mtime > 1000
    assert tmp_files(ws) == []


# ---------------------------------------------------------------------------- U-ST11
def test_put_blob_beyond_max_bytes_is_refused_and_leaves_no_temp(
    ws: Path, store: LocalFsCacheStore
) -> None:
    with pytest.raises(CacheTooLargeError):
        store.put_blob(io.BytesIO(b"x" * 101), max_bytes=100)
    assert tmp_files(ws) == []
    assert not any(
        (root_of(ws) / BLOBS_DIR).rglob("*") if (root_of(ws) / BLOBS_DIR).exists() else []
    )


def test_put_blob_never_reads_the_source_past_the_bound_plus_one(
    store: LocalFsCacheStore,
) -> None:
    requested: list[int] = []

    class Source(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            requested.append(size if size is not None else -1)
            return super().read(size)

    with pytest.raises(CacheTooLargeError):
        store.put_blob(Source(b"x" * 5000), max_bytes=100)
    assert requested and all(0 < r <= 101 for r in requested)


class _CountingSink(io.RawIOBase):
    def __init__(self) -> None:
        self.written = 0

    def writable(self) -> bool:
        return True

    def write(self, data: Any) -> int:
        self.written += len(data)
        return len(data)


def test_read_blob_never_writes_more_than_max_bytes_plus_one(store: LocalFsCacheStore) -> None:
    ref = store.put_blob(io.BytesIO(b"y" * 5000), max_bytes=BLOB_BOUND)
    sink = _CountingSink()
    copied = store.read_blob(ref.sha256, sink, max_bytes=100)  # type: ignore[arg-type]
    assert copied == sink.written == 101


# ---------------------------------------------------------------------------- U-ST12
def test_has_blob_is_false_for_missing_and_for_a_directory(
    ws: Path, store: LocalFsCacheStore
) -> None:
    sha = sha_of(b"never stored")
    assert store.has_blob(sha) is False
    store.ensure_layout()
    path = blob_file(ws, sha)
    path.parent.mkdir()
    path.mkdir()
    assert store.has_blob(sha) is False
    with pytest.raises(CacheIntegrityError) as err:
        store.read_blob(sha, io.BytesIO(), max_bytes=10)
    assert err.value.reason == REASON_BLOB_CORRUPT


@needs_posix
def test_has_blob_is_false_for_a_symlinked_blob_file(
    ws: Path, store: LocalFsCacheStore, tmp_path: Path
) -> None:
    ref = store.put_blob(io.BytesIO(b"real"), max_bytes=BLOB_BOUND)
    path = blob_file(ws, ref.sha256)
    target = tmp_path / "t"
    target.write_bytes(b"real")
    path.unlink()
    path.symlink_to(target)
    assert store.has_blob(ref.sha256) is False
    with pytest.raises(CacheIntegrityError):
        store.read_blob(ref.sha256, io.BytesIO(), max_bytes=10)


@needs_fifo
def test_read_blob_of_a_fifo_is_corrupt_and_does_not_block(
    ws: Path, store: LocalFsCacheStore
) -> None:
    store.ensure_layout()
    sha = sha_of(b"fifo blob")
    path = blob_file(ws, sha)
    path.parent.mkdir()
    os.mkfifo(path)
    with pytest.raises(CacheIntegrityError):
        run_with_timeout(lambda: store.read_blob(sha, io.BytesIO(), max_bytes=10))
    assert store.has_blob(sha) is False


# ---------------------------------------------------------------------------- U-ST13
@pytest.mark.parametrize(
    "layout",
    [
        b'{"schema": "ao.result-cache.layout/v99"}',
        b"{not json",
        b"[]",
        b'{"no_schema": 1}',
        b"\xff\xfe",
        b" " * 5000,
    ],
)
def test_unknown_or_malformed_layout_makes_check_and_writes_fail(
    ws: Path, store: LocalFsCacheStore, layout: bytes
) -> None:
    store.put_entry(make_entry(key_of("a")))
    (root_of(ws) / LAYOUT_FILE).write_bytes(layout)
    with pytest.raises(CacheLayoutError) as err:
        store.check()
    assert err.value.reason == REASON_STORE_UNAVAILABLE
    with pytest.raises(CacheLayoutError):
        store.put_entry(make_entry(key_of("b")))
    assert not entry_file(ws, key_of("b")).exists()


@needs_posix
def test_symlinked_layout_file_is_a_layout_error(
    ws: Path, store: LocalFsCacheStore, tmp_path: Path
) -> None:
    store.put_entry(make_entry(key_of("a")))
    victim = tmp_path / "layout-target.json"
    victim.write_text(json.dumps({"schema": LAYOUT_SCHEMA}))
    (root_of(ws) / LAYOUT_FILE).unlink()
    (root_of(ws) / LAYOUT_FILE).symlink_to(victim)
    with pytest.raises(CacheLayoutError):
        store.check()


def test_missing_layout_file_in_an_existing_root_is_tolerated(
    ws: Path, store: LocalFsCacheStore
) -> None:
    store.put_entry(make_entry(key_of("a")))
    (root_of(ws) / LAYOUT_FILE).unlink()
    store.check()
    store.put_entry(make_entry(key_of("b")))  # rewrites it
    assert (root_of(ws) / LAYOUT_FILE).is_file()


# ---------------------------------------------------------------------------- U-ST14
def test_is_expired_boundaries() -> None:
    now = FIXED_CREATED_AT
    ttl = 30
    assert is_expired(now - timedelta(days=ttl, seconds=1), now, ttl) is True
    assert is_expired(now - timedelta(days=ttl), now, ttl) is False  # exactly the TTL
    assert is_expired(now - timedelta(days=1), now, ttl) is False
    assert is_expired(now + timedelta(days=365), now, ttl) is False  # clock skew: never expired


# ---------------------------------------------------------------------------- U-ST15 (D33)
VICTIM_PAYLOAD_MTIME = 1_500_000_000


def _victim(tmp_path: Path) -> Path:
    victim = tmp_path / "victim"
    victim.mkdir()
    return victim


def _replace_with_symlink(path: Path, target: Path) -> None:
    shutil.rmtree(path)
    path.symlink_to(target, target_is_directory=True)


def _plant_entry_victim(
    ws: Path, store: LocalFsCacheStore, tmp_path: Path, *, whole_version_dir: bool
) -> tuple[str, Path]:
    """Link `entries/v1` (or its shard) to a victim dir that holds the real entry file."""
    entry = make_entry(key_of("victim"))
    store.put_entry(entry)
    victim = _victim(tmp_path)
    if whole_version_dir:
        (victim / entry.key[:2]).mkdir()
        victim_file = victim / entry.key[:2] / f"{entry.key}.json"
        victim_file.write_bytes(entry.to_canonical_bytes())
        _replace_with_symlink(root_of(ws) / ENTRIES_DIR / ENTRIES_VERSION_DIR, victim)
    else:
        victim_file = victim / f"{entry.key}.json"
        victim_file.write_bytes(entry.to_canonical_bytes())
        _replace_with_symlink(entry_file(ws, entry.key).parent, victim)
    os.utime(victim_file, (VICTIM_PAYLOAD_MTIME, VICTIM_PAYLOAD_MTIME))
    return entry.key, victim_file


@needs_posix
@pytest.mark.parametrize("whole_version_dir", [False, True], ids=["shard", "entries-v1"])
def test_symlinked_entry_directory_is_unsafe_and_nothing_is_touched(
    ws: Path, store: LocalFsCacheStore, tmp_path: Path, whole_version_dir: bool
) -> None:
    key, victim_file = _plant_entry_victim(ws, store, tmp_path, whole_version_dir=whole_version_dir)
    before = victim_file.stat().st_mtime
    calls = {
        "get_entry": lambda: store.get_entry(key),
        "touch_entry": lambda: store.touch_entry(key, FIXED_CREATED_AT),
        "delete_entry": lambda: store.delete_entry(key),
        "put_entry": lambda: store.put_entry(make_entry(key)),
    }
    for name, call in calls.items():
        with pytest.raises(CacheUnsafePathError) as err:
            call()
        assert err.value.reason == REASON_UNSAFE_PATH, name
        assert victim_file.exists(), name
        assert victim_file.stat().st_mtime == before, name
    assert victim_file.read_bytes() == make_entry(key_of("victim")).to_canonical_bytes()
    assert not isinstance(CacheUnsafePathError("x"), CacheIntegrityError)


@needs_posix
@pytest.mark.parametrize("link", [ENTRIES_DIR, BLOBS_DIR])
def test_symlinked_top_level_directory_is_unsafe(
    ws: Path, store: LocalFsCacheStore, tmp_path: Path, link: str
) -> None:
    entry = make_entry(key_of("top"))
    ref = store.put_blob(io.BytesIO(b"top blob"), max_bytes=BLOB_BOUND)
    store.put_entry(entry)
    victim = _victim(tmp_path)
    shutil.copytree(root_of(ws) / link, victim / link)
    _replace_with_symlink(root_of(ws) / link, victim / link)
    listing = sorted(str(p) for p in victim.rglob("*"))
    with pytest.raises(CacheUnsafePathError):
        store.get_entry(entry.key) if link == ENTRIES_DIR else store.has_blob(ref.sha256)
    with pytest.raises(CacheUnsafePathError):
        store.delete_entry(entry.key) if link == ENTRIES_DIR else store.delete_blob(ref.sha256)
    assert sorted(str(p) for p in victim.rglob("*")) == listing


@needs_posix
def test_symlinked_blob_shard_is_unsafe_for_has_read_delete_and_put(
    ws: Path, store: LocalFsCacheStore, tmp_path: Path
) -> None:
    data = b"victim blob"
    ref = store.put_blob(io.BytesIO(data), max_bytes=BLOB_BOUND)
    victim = _victim(tmp_path)
    victim_blob = victim / ref.sha256
    victim_blob.write_bytes(data)
    os.utime(victim_blob, (VICTIM_PAYLOAD_MTIME, VICTIM_PAYLOAD_MTIME))
    _replace_with_symlink(blob_file(ws, ref.sha256).parent, victim)
    sink = io.BytesIO()
    for name, call in {
        "has_blob": lambda: store.has_blob(ref.sha256),
        "read_blob": lambda: store.read_blob(ref.sha256, sink, max_bytes=BLOB_BOUND),
        "delete_blob": lambda: store.delete_blob(ref.sha256),
        "put_blob": lambda: store.put_blob(io.BytesIO(data), max_bytes=BLOB_BOUND),
    }.items():
        with pytest.raises(CacheUnsafePathError) as err:
            call()
        assert err.value.reason == REASON_UNSAFE_PATH, name
    assert sink.getvalue() == b""
    assert victim_blob.read_bytes() == data
    assert victim_blob.stat().st_mtime == VICTIM_PAYLOAD_MTIME  # the dedupe utime never ran
    assert [p.name for p in victim.iterdir()] == [ref.sha256]
    assert tmp_files(ws) == []


@needs_posix
def test_symlinked_tmp_directory_refuses_a_write(
    ws: Path, store: LocalFsCacheStore, tmp_path: Path
) -> None:
    store.ensure_layout()
    victim = _victim(tmp_path)
    _replace_with_symlink(root_of(ws) / TMP_DIR, victim)
    with pytest.raises(CacheUnsafePathError):
        store.put_blob(io.BytesIO(b"x"), max_bytes=10)
    assert list(victim.iterdir()) == []


@needs_posix
def test_a_shard_that_appears_as_a_link_between_layout_and_write_is_unsafe(
    ws: Path, store: LocalFsCacheStore, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A planted link in the window after the per-operation check is still never followed."""
    entry = make_entry(key_of("race"))
    victim = _victim(tmp_path)
    shard = entry_file(ws, entry.key).parent
    real_ensure = LocalFsCacheStore.ensure_layout

    def ensure_then_plant(self: LocalFsCacheStore) -> None:
        real_ensure(self)
        shard.symlink_to(victim, target_is_directory=True)

    monkeypatch.setattr(LocalFsCacheStore, "ensure_layout", ensure_then_plant)
    with pytest.raises(CacheUnsafePathError):
        store.put_entry(entry)
    assert list(victim.iterdir()) == []


# ---------------------------------------------------------------------------- misc / threads
def test_concurrent_thread_puts_of_one_key_and_blob_stay_consistent(
    ws: Path, store: LocalFsCacheStore
) -> None:
    entry = make_entry(key_of("threads"))
    data = b"same bytes" * 100
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            for _ in range(10):
                store.put_blob(io.BytesIO(data), max_bytes=BLOB_BOUND)
                store.put_entry(entry)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(FIFO_TIMEOUT_SECONDS * 4)
    assert not errors
    assert store.get_entry(entry.key) is not None
    assert store.has_blob(sha_of(data))
    assert tmp_files(ws) == []


def test_corrupt_entry_is_distinct_from_a_missing_one(ws: Path, store: LocalFsCacheStore) -> None:
    key, path = _seed(ws, store, "distinct")
    path.unlink()
    assert store.get_entry(key) is None
    assert store.delete_entry(key) is False
    assert CacheBlobMissingError("a" * 64).detail == "a" * 64
