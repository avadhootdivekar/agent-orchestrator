"""AC-10: the in-memory store passes the CacheStore contract; the other fakes are configurable."""

from __future__ import annotations

import io
import subprocess
from datetime import timedelta

import pytest

from agent_orchestrator.cache import constants as c
from agent_orchestrator.cache.types import (
    CacheAdmin,
    CacheStore,
    CacheUnsafePathError,
    UncacheableError,
)
from tests.cache.fakes import (
    FAKE_CLI_VERSION,
    FIXED_CREATED_AT,
    FakeRepoHeadReader,
    FakeVcsRunner,
    FakeWorktreeProbe,
    InMemoryCacheStore,
    fake_cli_version_of,
    key_of,
    make_entry,
    sha_of,
)
from tests.cache.store_contract import CacheStoreContract


@pytest.fixture(autouse=True)
def _cache_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AO_CACHE", raising=False)


class TestInMemoryCacheStoreContract(CacheStoreContract):
    @pytest.fixture
    def store(self) -> CacheStore:
        return InMemoryCacheStore()


def test_in_memory_store_implements_both_abcs() -> None:
    store = InMemoryCacheStore()
    assert isinstance(store, CacheStore) and isinstance(store, CacheAdmin)


def test_store_failures_are_configurable_and_calls_recorded() -> None:
    store = InMemoryCacheStore()
    store.fail_with("get_entry", CacheUnsafePathError(c.REASON_UNSAFE_PATH, "symlinked shard"))
    with pytest.raises(CacheUnsafePathError):
        store.get_entry(key_of("x"))
    assert store.called("get_entry") == 1
    store.fail_with("get_entry", None)
    assert store.get_entry(key_of("x")) is None
    assert store.called("delete_entry") == 0


def test_store_enforce_result_is_configurable() -> None:
    store = InMemoryCacheStore()
    assert store.maybe_enforce_limits(now=FIXED_CREATED_AT) is None


def test_admin_half_model_is_usable() -> None:
    store = InMemoryCacheStore(max_bytes=10**9, ttl_days=30)
    old = make_entry(key_of("old"), created_at=FIXED_CREATED_AT)
    new = make_entry(
        key_of("new"), outputs=(("n.txt", b"n"),), created_at=FIXED_CREATED_AT + timedelta(days=40)
    )
    for e, content in ((old, b"hello\n"), (new, b"n")):
        store.put_entry(e)
        store.put_blob(io.BytesIO(content), max_bytes=100)
    now = FIXED_CREATED_AT + timedelta(days=41)
    assert [i.key for i in store.iter_entries()] == sorted([old.key, new.key])
    assert store.stats(now=now).entries == 2 and store.stats(now=now).expired_entries == 1
    assert store.verify().ok is True
    dry = store.prune(now=now, max_bytes=None, ttl_days=30, dry_run=True)
    assert dry.total_removed == 1 and store.get_entry(old.key) is not None
    report = store.prune(now=now, max_bytes=None, ttl_days=30)
    assert report.removed_entries == {c.REASON_EXPIRED: 1} and report.removed_blobs == 1
    assert store.get_entry(old.key) is None and store.get_entry(new.key) is not None
    store.blobs[sha_of(b"n")] = b"tampered"
    assert [p.kind for p in store.verify().problems] == ["corrupt_blob"]
    assert store.clear().removed_entries == 1 and store.stats(now=now).entries == 0


def test_lru_prune_evicts_oldest_touched_first() -> None:
    store = InMemoryCacheStore()
    keys = [key_of(str(i)) for i in range(3)]
    for i, k in enumerate(keys):
        store.put_entry(make_entry(k, outputs=((f"o{i}", bytes([i]) * 50),)))
        store.put_blob(io.BytesIO(bytes([i]) * 50), max_bytes=100)
        store.touch_entry(k, FIXED_CREATED_AT + timedelta(hours=i))
    store.touch_entry(keys[0], FIXED_CREATED_AT + timedelta(hours=9))  # most recently used
    total = store.stats(now=FIXED_CREATED_AT).total_bytes
    report = store.prune(now=FIXED_CREATED_AT, max_bytes=int(total * 0.8), ttl_days=None)
    assert report.removed_entries == {c.REASON_EVICT_LRU: 1}
    assert store.get_entry(keys[1]) is None and store.get_entry(keys[0]) is not None


def test_repo_head_reader_returns_value_queue_or_raises() -> None:
    reader = FakeRepoHeadReader({"core": "a" * 40, "docs": "b" * 40})
    assert reader.read({"core": "/ws/core"}) == {"core": "a" * 40}
    assert reader.read({"core": "/p", "other": "/q"}) == {"core": "a" * 40}  # non-git: omitted
    reader.then({"core": "c" * 40})
    assert reader.read({"core": "/p"}) == {"core": "c" * 40}
    assert reader.call_count == 3
    reader.fail(UncacheableError(c.REASON_REPO_HEAD_UNAVAILABLE, "core"))
    with pytest.raises(UncacheableError):
        reader.read({"core": "/p"})
    reader.fail(None)
    assert reader.read({}) == {}


def test_worktree_probe_sequence_and_failure() -> None:
    first = frozenset({("/r", "a.py", "M", " ", 1, 2)})
    second = frozenset({("/r", "a.py", "M", " ", 9, 2)})
    probe = FakeWorktreeProbe(first, second)
    assert probe.snapshot({"c": "/r"}, "/ws", frozenset({"/ws/out"})) == first
    assert probe.snapshot({"c": "/r"}, "/ws", {"/ws/out"}) == second
    assert probe.snapshot({}, "/ws", frozenset()) == second  # the last one repeats
    assert probe.call_count == 3 and probe.calls[0][2] == frozenset({"/ws/out"})
    probe.fail(UncacheableError(c.REASON_REPO_WORKTREE_PROBE_FAILED, "boom"))
    with pytest.raises(UncacheableError):
        probe.snapshot({}, "/ws", frozenset())
    assert FakeWorktreeProbe().snapshot({}, "/ws", frozenset()) == frozenset()


def test_fake_cli_version_of_value_override_and_error() -> None:
    fn = fake_cli_version_of()
    assert fn("/usr/bin/claude") == FAKE_CLI_VERSION and fn.calls == [("/usr/bin/claude",)]
    assert fake_cli_version_of("2.1.0 (Claude Code)", versions={"/b": "9"})("/b") == "9"
    failing = fake_cli_version_of(error=UncacheableError(c.REASON_EXECUTOR_FINGERPRINT_UNAVAILABLE))
    with pytest.raises(UncacheableError):
        failing("/usr/bin/claude")


def test_fake_vcs_runner_scripts_results_and_records_calls() -> None:
    runner = FakeVcsRunner()
    runner.on("rev-parse", "HEAD", stdout=b"abc\n")
    runner.on("status", returncode=128, stderr=b"fatal")
    runner.on("fetch", raises=subprocess.TimeoutExpired("git", 1))
    cp = runner(["git", "-c", "x=y", "rev-parse", "HEAD"], cwd="/r", env={"A": "1"}, timeout=10)
    assert (cp.returncode, cp.stdout) == (0, b"abc\n")
    assert runner(["git", "status"], cwd="/r", env=None, timeout=1).returncode == 128
    with pytest.raises(subprocess.TimeoutExpired):
        runner(["git", "fetch"], cwd="/r", env=None, timeout=1)
    assert runner(["git", "log"], cwd="/r", env=None, timeout=1).returncode == 0  # unmatched
    assert runner.calls[0]["env"] == {"A": "1"} and runner.calls[0]["timeout"] == 10
    assert len(runner.calls) == 4
