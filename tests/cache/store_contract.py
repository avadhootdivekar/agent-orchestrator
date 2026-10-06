"""Parametrizable contract suite for the `CacheStore` half (HLD 8.4.3; T-FJH6LI AC-10).

Not collected on its own (the file name does not start with `test_`). A test module subclasses
`CacheStoreContract` and overrides the `store` fixture:

    class TestInMemory(CacheStoreContract):
        @pytest.fixture
        def store(self) -> CacheStore:
            return InMemoryCacheStore()

T-U7ckfd reruns it against `LocalFsCacheStore` (a real tmp_path workspace). `CacheAdmin`
behaviour is NOT covered here (T-HjxNQ0). Everything below must hold for any correct store.
"""

from __future__ import annotations

import io

import pytest

from agent_orchestrator.cache.types import (
    CacheBlobMissingError,
    CacheEntry,
    CacheStore,
    CacheTooLargeError,
    PruneReport,
)
from tests.cache.fakes import key_of, make_entry, sha_of

BLOB_MAX_BYTES = 1024
INVALID_HEX_VALUES = [
    "",
    "../x",
    "../../etc/passwd",
    "A" * 64,
    "a" * 63,
    "a" * 65,
    "a" * 64 + "\n",
    "a" * 63 + "/",
    "a" * 63 + "\x00",
    "g" * 64,
    "a/" + "b" * 62,
]


class CacheStoreContract:
    """Behaviour every `CacheStore` implementation must show."""

    @pytest.fixture(autouse=True)
    def _cache_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("AO_CACHE", raising=False)

    @pytest.fixture
    def store(self) -> CacheStore:  # overridden by the concrete test class
        raise NotImplementedError("subclass must provide the `store` fixture")

    # ------------------------------------------------------------------ pre-flight
    def test_check_on_a_fresh_store_passes(self, store: CacheStore) -> None:
        store.check()
        assert isinstance(store.root, str) and store.root

    # ------------------------------------------------------------------ entries
    def test_get_missing_entry_is_none(self, store: CacheStore) -> None:
        assert store.get_entry(key_of("absent")) is None

    def test_put_then_get_round_trips(self, store: CacheStore) -> None:
        entry = make_entry(key_of("a"))
        written = store.put_entry(entry)
        assert written == len(entry.to_canonical_bytes()) > 0
        got = store.get_entry(entry.key)
        assert isinstance(got, CacheEntry)
        assert got.to_canonical_bytes() == entry.to_canonical_bytes()

    def test_put_same_key_overwrites(self, store: CacheStore) -> None:
        key = key_of("same")
        store.put_entry(make_entry(key, cost_usd=1.0))
        store.put_entry(make_entry(key, cost_usd=2.0))
        got = store.get_entry(key)
        assert got is not None and got.usage.cost_usd == 2.0

    def test_entries_are_independent_by_key(self, store: CacheStore) -> None:
        a, b = key_of("a"), key_of("b")
        store.put_entry(make_entry(a))
        assert store.get_entry(b) is None
        store.put_entry(make_entry(b, outputs=(("x.txt", b"x"),)))
        assert store.get_entry(a) is not None and store.get_entry(b) is not None

    def test_put_entry_too_large_raises(self, store: CacheStore) -> None:
        # 300 distinct 4000-char paths: a valid model whose canonical bytes exceed 1 MiB.
        big = make_entry(
            key_of("big"), outputs=[(f"{i:04d}" + "p" * 4000, b"x") for i in range(300)]
        )
        with pytest.raises(CacheTooLargeError):
            store.put_entry(big)
        assert store.get_entry(big.key) is None

    def test_touch_existing_and_missing_entry(self, store: CacheStore) -> None:
        entry = make_entry(key_of("t"))
        store.put_entry(entry)
        store.touch_entry(entry.key, entry.created_at)
        store.touch_entry(key_of("never-stored"), entry.created_at)  # no-op, no error
        assert store.get_entry(entry.key) is not None

    def test_delete_entry_true_then_false(self, store: CacheStore) -> None:
        entry = make_entry(key_of("d"))
        store.put_entry(entry)
        assert store.delete_entry(entry.key) is True
        assert store.get_entry(entry.key) is None
        assert store.delete_entry(entry.key) is False

    # ------------------------------------------------------------------ blobs
    def test_blob_round_trip(self, store: CacheStore) -> None:
        data = b"payload bytes\x00\xff"
        ref = store.put_blob(io.BytesIO(data), max_bytes=BLOB_MAX_BYTES)
        assert (ref.sha256, ref.size, ref.new) == (sha_of(data), len(data), True)
        assert store.has_blob(ref.sha256) is True
        out = io.BytesIO()
        assert store.read_blob(ref.sha256, out, max_bytes=BLOB_MAX_BYTES) == len(data)
        assert out.getvalue() == data

    def test_blob_dedupe_reports_not_new(self, store: CacheStore) -> None:
        data = b"same content"
        first = store.put_blob(io.BytesIO(data), max_bytes=BLOB_MAX_BYTES)
        second = store.put_blob(io.BytesIO(data), max_bytes=BLOB_MAX_BYTES)
        assert first.sha256 == second.sha256
        assert (first.new, second.new) == (True, False)

    def test_empty_blob(self, store: CacheStore) -> None:
        ref = store.put_blob(io.BytesIO(b""), max_bytes=BLOB_MAX_BYTES)
        assert ref.sha256 == sha_of(b"") and ref.size == 0
        out = io.BytesIO()
        assert store.read_blob(ref.sha256, out, max_bytes=BLOB_MAX_BYTES) == 0

    def test_blob_exactly_at_the_bound_is_accepted(self, store: CacheStore) -> None:
        data = b"b" * BLOB_MAX_BYTES
        assert store.put_blob(io.BytesIO(data), max_bytes=BLOB_MAX_BYTES).size == len(data)

    def test_blob_over_the_bound_is_refused_and_not_kept(self, store: CacheStore) -> None:
        data = b"b" * (BLOB_MAX_BYTES + 1)
        with pytest.raises(CacheTooLargeError):
            store.put_blob(io.BytesIO(data), max_bytes=BLOB_MAX_BYTES)
        assert store.has_blob(sha_of(data)) is False

    def test_read_missing_blob_raises(self, store: CacheStore) -> None:
        assert store.has_blob(sha_of(b"nope")) is False
        with pytest.raises(CacheBlobMissingError):
            store.read_blob(sha_of(b"nope"), io.BytesIO(), max_bytes=BLOB_MAX_BYTES)

    def test_read_blob_signals_overflow_past_max_bytes(self, store: CacheStore) -> None:
        data = b"z" * 100
        ref = store.put_blob(io.BytesIO(data), max_bytes=BLOB_MAX_BYTES)
        out = io.BytesIO()
        copied = store.read_blob(ref.sha256, out, max_bytes=40)
        # The caller must be able to see "more than expected" (it then treats the blob as corrupt).
        assert 40 < copied <= 41
        assert out.getvalue() == data[:copied]

    def test_delete_blob_true_then_false(self, store: CacheStore) -> None:
        ref = store.put_blob(io.BytesIO(b"gone"), max_bytes=BLOB_MAX_BYTES)
        assert store.delete_blob(ref.sha256) is True
        assert store.has_blob(ref.sha256) is False
        assert store.delete_blob(ref.sha256) is False

    # ------------------------------------------------------------------ validation (M-2)
    @pytest.mark.parametrize("bad", INVALID_HEX_VALUES)
    def test_invalid_key_is_a_value_error(self, store: CacheStore, bad: str) -> None:
        with pytest.raises(ValueError):
            store.get_entry(bad)
        with pytest.raises(ValueError):
            store.delete_entry(bad)
        with pytest.raises(ValueError):
            store.touch_entry(bad, make_entry().created_at)

    @pytest.mark.parametrize("bad", INVALID_HEX_VALUES)
    def test_invalid_sha_is_a_value_error(self, store: CacheStore, bad: str) -> None:
        with pytest.raises(ValueError):
            store.has_blob(bad)
        with pytest.raises(ValueError):
            store.read_blob(bad, io.BytesIO(), max_bytes=BLOB_MAX_BYTES)
        with pytest.raises(ValueError):
            store.delete_blob(bad)

    # ------------------------------------------------------------------ limits
    def test_maybe_enforce_limits_is_none_or_a_report(self, store: CacheStore) -> None:
        result = store.maybe_enforce_limits(now=make_entry().created_at)
        assert result is None or isinstance(result, PruneReport)
