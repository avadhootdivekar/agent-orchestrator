"""``lockouts.py``: the pure policy, the cross-process store, phantoms and keyed digests.

Fixed clocks and seeded entropy throughout; the concurrency test uses real ``spawn`` processes
against a real flock (T-CsT5gk AC 1-4, HLD 11.11, 12.1b).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import multiprocessing
import os
import stat
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from agent_orchestrator import fsutil
from agent_orchestrator.auth import constants
from agent_orchestrator.auth import lockouts as lockouts_mod
from agent_orchestrator.auth.errors import (
    StoreCorruptError,
    StoreLockTimeoutError,
    StoreUnavailableError,
)
from agent_orchestrator.auth.lockouts import (
    LockoutFile,
    LockoutKey,
    LockoutPolicy,
    LockoutState,
    LockoutStore,
    load_lockout_file,
)
from agent_orchestrator.auth.store import format_timestamp

from .helpers.core import FakeClock, SeededEntropy
from .helpers.state import (
    NEVER_LOCK_POLICY,
    OTHER_USER_ID,
    USER_ID,
    make_lockout_store,
    record_failure_worker,
)
from .helpers.store import make_store

SPAWN = multiprocessing.get_context("spawn")
JOIN_TIMEOUT = 60.0
SCHEMA = Draft202012Validator(
    json.loads((Path(__file__).parent / "schemas" / "auth-lockouts-v1.json").read_text())
)
POLICY = LockoutPolicy(
    threshold=constants.DEFAULT_LOCKOUT_THRESHOLD,
    base_seconds=constants.DEFAULT_LOCKOUT_BASE_SECONDS,
    max_seconds=constants.DEFAULT_LOCKOUT_MAX_SECONDS,
)
ACCOUNT = LockoutKey(user_id=USER_ID)


def raw_json(store: LockoutStore) -> dict[str, Any]:
    data = json.loads(store._paths.lockouts_file.read_bytes())
    assert isinstance(data, dict)
    return data


def assert_valid_schema(store: LockoutStore) -> None:
    assert [e.message for e in SCHEMA.iter_errors(raw_json(store))] == []
    assert stat.S_IMODE(os.stat(store._paths.lockouts_file).st_mode) == 0o600


# ---------------------------------------------------------------------------
# LockoutPolicy (AC 1)
# ---------------------------------------------------------------------------


class TestPolicy:
    def test_defaults_match_constants(self) -> None:
        assert (POLICY.threshold, POLICY.base_seconds, POLICY.max_seconds) == (5, 30, 900)
        assert POLICY.reset_after_seconds == constants.LOCKOUT_RESET_AFTER_SECONDS

    def test_backoff_table_from_the_hld_worked_example(self) -> None:
        clock = FakeClock()
        state = LockoutState()
        seen: list[int | None] = []
        for _ in range(10):
            state = POLICY.register_failure(state, clock.now_utc())
            wait = POLICY.retry_after(state, clock.now_utc())
            seen.append(wait)
            clock.advance(wait or 0)  # wait out the lock before the next attempt
        assert seen == [None, None, None, None, 30, 60, 120, 240, 480, 900]
        state = POLICY.register_failure(state, clock.now_utc())
        assert state.failures == 11
        assert POLICY.retry_after(state, clock.now_utc()) == 900

    def test_register_success_resets_everything(self) -> None:
        assert POLICY.register_success() == LockoutState()

    def test_failure_after_reset_window_counts_as_first(self) -> None:
        clock = FakeClock()
        state = LockoutState()
        for _ in range(4):
            state = POLICY.register_failure(state, clock.now_utc())
        clock.advance(constants.LOCKOUT_RESET_AFTER_SECONDS + 1)
        assert POLICY.effective_failures(state, clock.now_utc()) == 0
        again = POLICY.register_failure(state, clock.now_utc())
        assert again.failures == 1 and again.locked_until is None

    def test_failure_exactly_at_the_reset_boundary_still_counts(self) -> None:
        clock = FakeClock()
        state = POLICY.register_failure(LockoutState(), clock.now_utc())
        clock.advance(constants.LOCKOUT_RESET_AFTER_SECONDS)
        assert POLICY.effective_failures(state, clock.now_utc()) == 1

    def test_retry_after_is_none_once_the_lock_expired(self) -> None:
        clock = FakeClock()
        state = LockoutState(failures=5, locked_until=format_timestamp(clock.now_utc()))
        assert POLICY.retry_after(state, clock.now_utc()) is None
        assert POLICY.retry_after(LockoutState(), clock.now_utc()) is None

    def test_retry_after_rounds_up_partial_seconds(self) -> None:
        clock = FakeClock()
        state = LockoutState(
            failures=5, locked_until=format_timestamp(clock.now_utc() + timedelta(seconds=10))
        )
        clock.advance(0.25)
        assert POLICY.retry_after(state, clock.now_utc()) == 10  # 9.75 s left -> 10

    def test_lock_is_never_shortened_by_second_resolution(self) -> None:
        clock = FakeClock()
        clock.advance(0.4)  # sub-second wall time
        state = LockoutState(failures=4, last_failure_at=format_timestamp(clock.now_utc()))
        state = POLICY.register_failure(state, clock.now_utc())
        wait = POLICY.retry_after(state, clock.now_utc())
        assert wait is not None and 30 <= wait <= 31  # 30.4 s rounds up, never down

    def test_huge_failure_count_is_capped_not_overflowed(self) -> None:
        clock = FakeClock()
        state = LockoutState(failures=10**9, last_failure_at=format_timestamp(clock.now_utc()))
        state = POLICY.register_failure(state, clock.now_utc())
        assert POLICY.retry_after(state, clock.now_utc()) == 900

    def test_unparseable_timestamps_fail_closed_or_neutral(self) -> None:
        clock = FakeClock()
        garbled = LockoutState(failures=3, last_failure_at="yesterday", locked_until="soon")
        assert POLICY.effective_failures(garbled, clock.now_utc()) == 3  # count kept
        assert POLICY.retry_after(garbled, clock.now_utc()) is None

    def test_register_failure_returns_a_copy_and_keeps_unknown_fields(self) -> None:
        clock = FakeClock()
        old = LockoutState.model_validate({"failures": 1, "future_field": "kept"})
        new = POLICY.register_failure(old, clock.now_utc())
        assert old.failures == 1 and new.failures == 2
        assert new.model_dump()["future_field"] == "kept"

    def test_below_threshold_clears_a_stale_lock(self) -> None:
        clock = FakeClock()
        stale = LockoutState(
            failures=9, last_failure_at="2020-01-01T00:00:00Z", locked_until="2020-01-01T00:10:00Z"
        )
        new = POLICY.register_failure(stale, clock.now_utc())
        assert new.failures == 1 and new.locked_until is None


class TestLockoutKey:
    def test_exactly_one_of_the_two(self) -> None:
        assert LockoutKey(user_id="u").phantom is None
        assert LockoutKey(phantom="p").user_id is None
        with pytest.raises(ValueError, match="exactly one"):
            LockoutKey()
        with pytest.raises(ValueError, match="exactly one"):
            LockoutKey(user_id="u", phantom="p")


# ---------------------------------------------------------------------------
# LockoutStore (AC 2)
# ---------------------------------------------------------------------------


class TestStore:
    def test_missing_file_is_empty(self, tmp_path: Path) -> None:
        store = make_lockout_store(tmp_path)
        assert store.state(ACCOUNT) == LockoutState()
        assert store.state(LockoutKey(phantom="a" * 64)) == LockoutState()
        assert not store._paths.lockouts_file.exists()  # a read never creates the file
        store.check_readable()  # a missing file is readable

    def test_record_failure_persists_in_the_state_dir_at_0600(self, tmp_path: Path) -> None:
        users = make_store(tmp_path, ["alice"])
        users_bytes = users.paths.users_file.read_bytes()
        users_mtime = os.stat(users.paths.users_file).st_mtime_ns
        store = LockoutStore(users.paths, clock=FakeClock(), entropy=SeededEntropy(1))

        new = store.record_failure(ACCOUNT, POLICY)

        assert new.failures == 1
        assert store.state(ACCOUNT).failures == 1
        assert store._paths.lockouts_file.parent == users.paths.state_dir
        assert stat.S_IMODE(os.stat(users.paths.state_dir).st_mode) == 0o700
        assert_valid_schema(store)
        assert users.paths.users_file.read_bytes() == users_bytes
        assert os.stat(users.paths.users_file).st_mtime_ns == users_mtime

    def test_state_returns_a_copy(self, tmp_path: Path) -> None:
        store = make_lockout_store(tmp_path)
        store.record_failure(ACCOUNT, POLICY)
        store.state(ACCOUNT).failures = 99
        assert store.state(ACCOUNT).failures == 1

    def test_two_realms_sharing_a_state_dir_share_counts(self, tmp_path: Path) -> None:
        clock = FakeClock()
        a = make_lockout_store(tmp_path, clock=clock)
        b = LockoutStore(a._paths, clock=clock, entropy=SeededEntropy(2))
        for _ in range(3):
            a.record_failure(ACCOUNT, POLICY)
        for _ in range(2):
            b.record_failure(ACCOUNT, POLICY)
        assert POLICY.retry_after(a.state(ACCOUNT), clock.now_utc()) == 30
        assert POLICY.retry_after(b.state(ACCOUNT), clock.now_utc()) == 30

    def test_reset_clears_the_entry_and_is_a_no_write_when_clear(self, tmp_path: Path) -> None:
        store = make_lockout_store(tmp_path)
        store.record_failure(ACCOUNT, POLICY)
        store.record_failure(LockoutKey(user_id=OTHER_USER_ID), POLICY)

        store.reset(USER_ID)

        assert store.state(ACCOUNT) == LockoutState()
        assert store.state(LockoutKey(user_id=OTHER_USER_ID)).failures == 1
        before = os.stat(store._paths.lockouts_file)
        store.reset(USER_ID)  # already clear
        store.reset("f" * 32)  # never seen
        after = os.stat(store._paths.lockouts_file)
        assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)

    def test_reset_on_a_missing_file_does_not_create_it(self, tmp_path: Path) -> None:
        store = make_lockout_store(tmp_path)
        store.reset(USER_ID)
        assert not store._paths.lockouts_file.exists()

    def test_forget_removes_the_key(self, tmp_path: Path) -> None:
        store = make_lockout_store(tmp_path)
        store.record_failure(ACCOUNT, POLICY)
        store.forget(USER_ID)
        assert USER_ID not in raw_json(store)["accounts"]
        store.forget(USER_ID)  # idempotent

    def test_unknown_fields_survive_a_rewrite(self, tmp_path: Path) -> None:
        store = make_lockout_store(tmp_path)
        store.record_failure(ACCOUNT, POLICY)
        data = raw_json(store)
        data["from_the_future"] = {"x": 1}
        data["accounts"][USER_ID]["note"] = "kept"
        store._paths.lockouts_file.write_text(json.dumps(data))
        os.chmod(store._paths.lockouts_file, 0o600)

        store.record_failure(ACCOUNT, POLICY)

        again = raw_json(store)
        assert again["from_the_future"] == {"x": 1}
        assert again["accounts"][USER_ID]["note"] == "kept"
        assert again["accounts"][USER_ID]["failures"] == 2
        assert_valid_schema(store)

    def test_changes_by_another_process_are_seen_through_the_stat_cache(
        self, tmp_path: Path
    ) -> None:
        a = make_lockout_store(tmp_path)
        b = LockoutStore(a._paths, clock=FakeClock(), entropy=SeededEntropy(3))
        assert a.state(ACCOUNT).failures == 0  # primes a's cache with the "missing" snapshot
        b.record_failure(ACCOUNT, POLICY)
        assert a.state(ACCOUNT).failures == 1
        b.record_failure(ACCOUNT, POLICY)
        assert a.state(ACCOUNT).failures == 2

    def test_unchanged_file_is_not_reparsed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = make_lockout_store(tmp_path)
        store.record_failure(ACCOUNT, POLICY)
        fresh = LockoutStore(store._paths, clock=FakeClock())
        calls = 0
        real = lockouts_mod.load_lockout_file

        def counting(path: Path) -> LockoutFile:
            nonlocal calls
            calls += 1
            return real(path)

        monkeypatch.setattr(lockouts_mod, "load_lockout_file", counting)
        for _ in range(3):
            fresh.state(ACCOUNT)
        assert calls == 1

    def test_file_removed_between_stat_and_read_is_empty(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = make_lockout_store(tmp_path)
        store.record_failure(ACCOUNT, POLICY)
        fresh = LockoutStore(store._paths, clock=FakeClock())

        def gone(path: Path) -> LockoutFile:
            raise FileNotFoundError

        monkeypatch.setattr(lockouts_mod, "load_lockout_file", gone)
        assert fresh.state(ACCOUNT) == LockoutState()

    def test_missing_state_directory_is_created_at_0700(self, tmp_path: Path) -> None:
        store = make_lockout_store(tmp_path)
        os.rmdir(store._paths.state_dir)
        store.record_failure(ACCOUNT, POLICY)
        assert stat.S_IMODE(os.stat(store._paths.state_dir).st_mode) == 0o700
        assert store.state(ACCOUNT).failures == 1

    def test_lock_timeout_is_a_domain_error(self, tmp_path: Path) -> None:
        store = make_lockout_store(tmp_path, lock_timeout=0.05)
        with fsutil.FileLock(store._paths.lockouts_lock, timeout=1.0):
            with pytest.raises(StoreLockTimeoutError):
                store.record_failure(ACCOUNT, POLICY)

    def test_unwritable_state_is_unavailable(self, tmp_path: Path) -> None:
        store = make_lockout_store(tmp_path)
        os.rmdir(store._paths.state_dir)
        store._paths.state_dir.write_text("a file, not a directory")
        with pytest.raises(StoreUnavailableError):
            store.record_failure(ACCOUNT, POLICY)

    def test_unreadable_file_is_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = make_lockout_store(tmp_path)
        store.record_failure(ACCOUNT, POLICY)
        fresh = LockoutStore(store._paths, clock=FakeClock())

        def deny(self: Path) -> bytes:
            raise PermissionError("no")

        monkeypatch.setattr(Path, "read_bytes", deny)
        with pytest.raises(StoreUnavailableError):
            fresh.state(ACCOUNT)

    def test_stat_failure_is_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = make_lockout_store(tmp_path)

        def boom(path: Any, *a: Any, **k: Any) -> Any:
            raise PermissionError("no")

        monkeypatch.setattr(lockouts_mod.os, "stat", boom)
        with pytest.raises(StoreUnavailableError):
            store.state(ACCOUNT)

    def test_four_spawn_processes_lose_no_updates(self, tmp_path: Path) -> None:
        store = make_lockout_store(tmp_path)
        procs = [
            SPAWN.Process(
                target=record_failure_worker,
                args=(str(store._paths.store_dir), str(store._paths.state_dir), USER_ID, 25, i),
            )
            for i in range(4)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=JOIN_TIMEOUT)
        assert [p.exitcode for p in procs] == [0] * 4
        assert store.state(ACCOUNT).failures == 100
        assert_valid_schema(store)
        assert NEVER_LOCK_POLICY.threshold > 100


# ---------------------------------------------------------------------------
# Phantoms (AC 3)
# ---------------------------------------------------------------------------


class TestPhantoms:
    def test_phantom_keys_live_under_phantoms(self, tmp_path: Path) -> None:
        store = make_lockout_store(tmp_path)
        key = LockoutKey(phantom=store.name_digest("ghost"))
        store.record_failure(key, POLICY)
        data = raw_json(store)
        assert list(data["phantoms"]) == [key.phantom] and data["accounts"] == {}
        assert store.state(key).failures == 1
        assert_valid_schema(store)

    def test_default_cap_is_4096(self) -> None:
        assert constants.PHANTOM_LOCKOUT_MAX_ENTRIES == 4096
        assert lockouts_mod.PHANTOM_LOCKOUT_MAX_ENTRIES == 4096

    def test_fourth_phantom_evicts_the_oldest(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(lockouts_mod, "PHANTOM_LOCKOUT_MAX_ENTRIES", 3)
        clock = FakeClock()
        store = make_lockout_store(tmp_path, clock=clock)
        keys = [LockoutKey(phantom=format(i, "064x")) for i in range(4)]
        for key in keys[:3]:
            store.record_failure(key, POLICY)
            clock.advance(10)
        # Refresh the first one: now keys[1] is the oldest.
        store.record_failure(keys[0], POLICY)
        clock.advance(10)
        store.record_failure(keys[3], POLICY)
        survivors = [str(keys[i].phantom) for i in (0, 2, 3)]
        assert sorted(raw_json(store)["phantoms"]) == sorted(survivors)

    def test_the_newest_phantom_survives_a_timestamp_tie(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(lockouts_mod, "PHANTOM_LOCKOUT_MAX_ENTRIES", 2)
        store = make_lockout_store(tmp_path)  # FakeClock never advances: every timestamp ties
        keys = [LockoutKey(phantom=format(i, "064x")) for i in (7, 1, 3)]
        for key in keys:
            store.record_failure(key, POLICY)
        phantoms = raw_json(store)["phantoms"]
        assert len(phantoms) == 2 and keys[2].phantom in phantoms

    def test_accounts_are_never_evicted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(lockouts_mod, "PHANTOM_LOCKOUT_MAX_ENTRIES", 2)
        clock = FakeClock()
        store = make_lockout_store(tmp_path, clock=clock)
        for i in range(5):
            store.record_failure(LockoutKey(user_id=format(i, "032x")), POLICY)
            clock.advance(1)
        for i in range(5):
            store.record_failure(LockoutKey(phantom=format(i, "064x")), POLICY)
            clock.advance(1)
        data = raw_json(store)
        assert len(data["accounts"]) == 5 and len(data["phantoms"]) == 2


# ---------------------------------------------------------------------------
# Keyed digests (AC 3b, security L1)
# ---------------------------------------------------------------------------


class TestNameDigest:
    def test_ensure_name_key_writes_once(self, tmp_path: Path) -> None:
        store = make_lockout_store(tmp_path)
        store.ensure_name_key()
        data = raw_json(store)
        assert len(data["name_key_hex"]) == 64 and data["accounts"] == {} and data["phantoms"] == {}
        assert_valid_schema(store)
        before = os.stat(store._paths.lockouts_file)
        store.ensure_name_key()
        after = os.stat(store._paths.lockouts_file)
        assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)

    def test_ensure_name_key_keeps_existing_counters(self, tmp_path: Path) -> None:
        store = make_lockout_store(tmp_path)
        data = LockoutFile(accounts={USER_ID: LockoutState(failures=2)})
        store._paths.lockouts_file.write_text(json.dumps(data.model_dump(mode="json")))
        os.chmod(store._paths.lockouts_file, 0o600)  # a v2 file without the key stays valid
        store.ensure_name_key()
        assert store.state(ACCOUNT).failures == 2
        assert raw_json(store)["name_key_hex"] is not None

    def test_digest_is_hmac_sha256_of_the_name_under_the_key(self, tmp_path: Path) -> None:
        store = make_lockout_store(tmp_path)
        store.ensure_name_key()
        key = bytes.fromhex(raw_json(store)["name_key_hex"])
        digest = store.name_digest("ghost")
        assert len(digest) == 64 and int(digest, 16) >= 0
        assert digest == hmac.new(key, b"ghost", hashlib.sha256).hexdigest()
        assert digest != hashlib.sha256(b"ghost").hexdigest()
        assert store.name_digest("ghost") == digest  # stable
        assert store.name_digest("ghost2") != digest

    def test_different_keys_give_different_digests(self, tmp_path: Path) -> None:
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        one = make_lockout_store(tmp_path / "a", entropy=SeededEntropy(1))
        two = make_lockout_store(tmp_path / "b", entropy=SeededEntropy(2))
        assert one.name_digest("ghost") != two.name_digest("ghost")

    @pytest.mark.parametrize(
        "bad", ["Bad Name!", "x" * 100, "a b", "", "UPPER", "tab\tname", "ok\n"]
    )
    def test_every_malformed_name_shares_one_bucket(self, tmp_path: Path, bad: str) -> None:
        store = make_lockout_store(tmp_path)
        bucket = store.name_digest("Bad Name!")
        assert store.name_digest(bad) == bucket
        key = bytes.fromhex(raw_json(store)["name_key_hex"])
        assert (
            bucket
            == hmac.new(key, constants.INVALID_USERNAME_BUCKET.encode(), hashlib.sha256).hexdigest()
        )
        assert store.name_digest("valid.name-1") != bucket

    def test_missing_key_at_runtime_is_created_once_and_logged_once(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        store = make_lockout_store(tmp_path)
        store.ensure_name_key()
        old = store.name_digest("ghost")
        store._paths.lockouts_file.unlink()  # state is "safe to lose": the key rotates
        with caplog.at_level(logging.DEBUG):
            new = store.name_digest("ghost")
            again = store.name_digest("ghost")
        assert new == again and new != old
        assert [r.levelno for r in caplog.records if r.name == lockouts_mod.__name__] == [
            logging.WARNING
        ]

    def test_first_digest_without_ensure_creates_the_key(self, tmp_path: Path) -> None:
        store = make_lockout_store(tmp_path)
        assert len(store.name_digest("ghost")) == 64
        assert raw_json(store)["name_key_hex"] is not None

    def test_the_key_never_appears_in_repr_or_logs(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        store = make_lockout_store(tmp_path)
        with caplog.at_level(logging.DEBUG):
            store.ensure_name_key()
            store.name_digest("ghost")
            store._paths.lockouts_file.write_text("{not json")
            store.reset(USER_ID, repair_corrupt=True)
            key_hex = raw_json(store)["name_key_hex"]
        loaded = load_lockout_file(store._paths.lockouts_file)
        assert key_hex not in repr(loaded) and "name_key_hex" not in repr(loaded)
        assert key_hex not in caplog.text

    def test_bad_key_format_is_corrupt(self, tmp_path: Path) -> None:
        store = make_lockout_store(tmp_path)
        store.ensure_name_key()
        data = raw_json(store)
        data["name_key_hex"] = "not-hex"
        store._paths.lockouts_file.write_text(json.dumps(data))
        with pytest.raises(StoreCorruptError, match="invalid content"):
            store.check_readable()

    def test_corrupt_file_makes_digests_unavailable(self, tmp_path: Path) -> None:
        store = make_lockout_store(tmp_path)
        store._paths.lockouts_file.write_text("{")
        with pytest.raises(StoreUnavailableError):
            store.name_digest("ghost")
        with pytest.raises(StoreUnavailableError):
            store.ensure_name_key()


# ---------------------------------------------------------------------------
# Corruption fails closed (AC 4)
# ---------------------------------------------------------------------------

CORRUPT_CONTENTS = [
    b"{not json",
    b"\xff\xfe\x00",
    b"[]",
    b'{"accounts": {}, "phantoms": {}}',  # no schema_version
    b'{"schema_version": true, "accounts": {}, "phantoms": {}}',
    b'{"schema_version": 2, "accounts": {}, "phantoms": {}}',  # from a newer ao
    b'{"schema_version": 1, "accounts": {"u": {"failures": "many"}}, "phantoms": {}}',
    b'{"schema_version": 1, "accounts": {"u": {"failures": -1}}, "phantoms": {}}',
]


class TestCorruption:
    @pytest.mark.parametrize("content", CORRUPT_CONTENTS)
    def test_fails_closed_on_every_path(self, tmp_path: Path, content: bytes) -> None:
        store = make_lockout_store(tmp_path)
        store._paths.lockouts_file.write_bytes(content)
        with pytest.raises(StoreUnavailableError):
            store.state(ACCOUNT)
        with pytest.raises(StoreUnavailableError):
            store.record_failure(ACCOUNT, POLICY)
        with pytest.raises(StoreUnavailableError):
            store.reset(USER_ID)
        with pytest.raises(StoreUnavailableError):
            store.forget(USER_ID)
        with pytest.raises(StoreCorruptError):
            store.check_readable()
        assert store._paths.lockouts_file.read_bytes() == content  # never silently rewritten

    def test_the_corrupt_error_message_has_no_values(self, tmp_path: Path) -> None:
        store = make_lockout_store(tmp_path)
        secret = "ab" * 32
        store._paths.lockouts_file.write_text(
            json.dumps(
                {"schema_version": 1, "name_key_hex": secret + "zz", "accounts": {}, "phantoms": {}}
            )
        )
        with pytest.raises(StoreCorruptError) as info:
            store.check_readable()
        assert secret not in str(info.value)

    def test_repair_rewrites_a_valid_empty_file_and_logs_one_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        store = make_lockout_store(tmp_path)
        store._paths.lockouts_file.write_text("{not json")
        with caplog.at_level(logging.DEBUG):
            store.reset(USER_ID, repair_corrupt=True)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        data = raw_json(store)
        assert data["accounts"] == {} and data["phantoms"] == {} and len(data["name_key_hex"]) == 64
        assert_valid_schema(store)
        assert store.state(ACCOUNT) == LockoutState()
        store.check_readable()

    def test_repair_flag_on_a_healthy_file_is_a_normal_reset(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        store = make_lockout_store(tmp_path)
        store.record_failure(ACCOUNT, POLICY)
        with caplog.at_level(logging.DEBUG):
            store.reset(USER_ID, repair_corrupt=True)
        assert store.state(ACCOUNT) == LockoutState()
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]

    def test_repair_does_not_mask_an_unreadable_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = make_lockout_store(tmp_path)
        store.record_failure(ACCOUNT, POLICY)

        def deny(self: Path) -> bytes:
            raise PermissionError("no")

        monkeypatch.setattr(Path, "read_bytes", deny)
        with pytest.raises(StoreUnavailableError):
            store.reset(USER_ID, repair_corrupt=True)

    def test_a_new_store_object_sees_the_repair(self, tmp_path: Path) -> None:
        store = make_lockout_store(tmp_path)
        store._paths.lockouts_file.write_text("garbage")
        store.reset(USER_ID, repair_corrupt=True)
        other = LockoutStore(store._paths, clock=FakeClock())
        other.record_failure(ACCOUNT, POLICY)
        assert other.state(ACCOUNT).failures == 1
        assert format_timestamp(FakeClock().now_utc()) == other.state(ACCOUNT).last_failure_at
