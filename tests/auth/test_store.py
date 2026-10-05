"""T-8NQP8J: ``auth/store.py`` (HLD 11.9, 12.1): snapshot, ``mutate``, every pure mutation,
``with_identity``, CAS, ``consume_*``, enrollment tokens, ``count_store_users``, ``repr`` hygiene.

Fixed clock (``FakeClock``) and seeded entropy throughout; the concurrency tests use real
``spawn`` processes against a real flock.
"""

from __future__ import annotations

import errno
import json
import multiprocessing
import os
import re
import stat
import threading
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from agent_orchestrator import fsutil
from agent_orchestrator.auth import constants
from agent_orchestrator.auth import store as store_mod
from agent_orchestrator.auth.errors import (
    StoreCorruptError,
    StoreLockTimeoutError,
    StoreUnavailableError,
)
from agent_orchestrator.auth.recovery import (
    generate_recovery_codes,
    new_recovery_records,
    normalize_recovery_code,
)
from agent_orchestrator.auth.store import (
    AlreadyEnrolledError,
    EnrollmentToken,
    NotEnrolledError,
    OutcomeKind,
    RecoveryCodeHash,
    RecoveryOutcome,
    StaleIdentityError,
    StoreMissingError,
    TooManyUsersError,
    TotpEnrollment,
    TotpOutcome,
    UserExistsError,
    UserNotFoundError,
    UserRecord,
    UserStore,
    UserStoreFile,
    add_user,
    bump_epoch,
    canonical_json,
    cas_mark_login,
    cas_rehash_password,
    consume_enrollment_token,
    consume_recovery,
    consume_totp,
    count_store_users,
    enroll_totp,
    format_timestamp,
    issue_enrollment_token,
    load_store_file,
    parse_timestamp,
    remove_totp,
    remove_user,
    replace_recovery_codes,
    set_password_hash,
    with_identity,
)
from agent_orchestrator.auth.totp import hotp, totp_step

from .helpers.core import FakeClock, SeededEntropy
from .helpers.store import (
    OTHER_PASSWORD_HASH,
    RFC_KEY,
    RFC_SECRET_B32,
    STORE_NOW,
    TEST_PASSWORD_HASH,
    add_user_worker,
    consume_totp_worker,
    enroll,
    make_store,
    make_store_paths,
    totp_code,
)

SPAWN = multiprocessing.get_context("spawn")
JOIN_TIMEOUT = 60.0
SCHEMA_PATH = Path(__file__).parent / "schemas" / "auth-users-v1.json"
SCHEMA = Draft202012Validator(json.loads(SCHEMA_PATH.read_text()))
# RFC 6238 appendix B time 1111111109 -> step 37037036; used so steps are large and unambiguous.
NOW_UNIX = 1_111_111_109
HEX32 = re.compile(r"^[0-9a-f]{32}$")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def raw_bytes(store: UserStore) -> bytes:
    return store.paths.users_file.read_bytes()


def raw_json(store: UserStore) -> dict[str, Any]:
    return json.loads(raw_bytes(store))  # type: ignore[no-any-return]


def assert_valid_schema(store: UserStore) -> None:
    errors = [e.message for e in SCHEMA.iter_errors(raw_json(store))]
    assert errors == []
    assert stat.S_IMODE(os.stat(store.paths.users_file).st_mode) == 0o600


def fresh_file(*names: str) -> UserStoreFile:
    f = UserStoreFile(store_id="a" * 32, created_at=format_timestamp(STORE_NOW))
    entropy = SeededEntropy(99)
    for name in names:
        add_user(f, name, TEST_PASSWORD_HASH, now=STORE_NOW, entropy=entropy)
    return f


def write_store_json(store: UserStore, data: object) -> None:
    store.paths.users_file.write_text(json.dumps(data))
    os.chmod(store.paths.users_file, 0o600)


def valid_dict(store: UserStore) -> dict[str, Any]:
    return raw_json(store)


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture()
def store(tmp_path: Path, clock: FakeClock) -> UserStore:
    return make_store(tmp_path, ["alice"], clock=clock)


def ident(store: UserStore, name: str = "alice") -> tuple[str, int]:
    rec = store.snapshot().users[name]
    return rec.user_id, rec.credential_epoch


# ---------------------------------------------------------------------------
# models, timestamps, canonical JSON
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_timestamp_round_trip_and_utc_conversion(self) -> None:
        from datetime import UTC, datetime, timezone

        assert format_timestamp(STORE_NOW) == "2026-01-01T00:00:00Z"
        plus2 = timezone(timedelta(hours=2))
        assert format_timestamp(datetime(2026, 1, 1, 2, 0, tzinfo=plus2)) == "2026-01-01T00:00:00Z"
        assert parse_timestamp("2026-01-01T00:00:00Z") == datetime(2026, 1, 1, tzinfo=UTC)
        assert parse_timestamp("not a date") is None
        assert parse_timestamp("2026-01-01T00:00:00+00:00") is None

    def test_naive_datetime_is_refused(self) -> None:
        from datetime import datetime

        with pytest.raises(ValueError, match="timezone-aware"):
            format_timestamp(datetime(2026, 1, 1))

    def test_canonical_json_form(self) -> None:
        out = canonical_json({"b": 1, "a": "é"})
        assert out == b'{\n  "a": "\\u00e9",\n  "b": 1\n}\n'

    def test_models_never_put_secrets_in_repr_or_str(self, store: UserStore) -> None:
        sentinels = ["SENTINEL-HASH-1", "SENTINEL-SECRET-2", "SENTINEL-SALT-3", "SENTINEL-DIGEST-4"]
        user = UserRecord(
            user_id="u" * 32,
            username="alice",
            password_hash=sentinels[0],
            created_at="t",
            updated_at="t",
            password_changed_at="t",
            totp=TotpEnrollment(secret_b32=sentinels[1], enrolled_at="t", last_used_step=1),
            recovery_codes=[RecoveryCodeHash(salt_hex=sentinels[2], hash_hex=sentinels[3])],
            enrollment_token=EnrollmentToken(
                salt_hex=sentinels[2], hash_hex=sentinels[3], expires_at="t"
            ),
        )
        whole = UserStoreFile(store_id="s", created_at="t", users={"alice": user})
        for model in (user, user.totp, user.recovery_codes[0], user.enrollment_token, whole):
            for text in (repr(model), str(model)):
                assert not any(s in text for s in sentinels)

    def test_real_store_snapshot_repr_has_no_secrets(self, tmp_path: Path) -> None:
        s = make_store(tmp_path, ["alice"])
        codes = enroll(s, "alice")
        text = repr(s.snapshot()) + str(s.snapshot())
        assert TEST_PASSWORD_HASH not in text and RFC_SECRET_B32 not in text
        assert not any(c.replace("-", "") in text for c in codes)


# ---------------------------------------------------------------------------
# snapshot, load_store_file, forward compatibility (AC-42)
# ---------------------------------------------------------------------------


class TestSnapshot:
    def test_missing_file_is_an_empty_store(self, tmp_path: Path) -> None:
        s = make_store(tmp_path, create=False)
        snap = s.snapshot()
        assert snap.users == {} and snap.store_id == ""
        assert not s.exists()
        assert s.count_users() == 0
        assert s.user_by_id("0" * 32) is None

    def test_missing_directory_is_an_empty_store(self, tmp_path: Path) -> None:
        s = make_store(tmp_path, create=False)
        os.rmdir(s.paths.store_dir)
        assert s.snapshot().store_id == ""

    def test_loads_what_mutate_wrote(self, store: UserStore) -> None:
        snap = store.snapshot()
        assert list(snap.users) == ["alice"]
        assert snap.schema_version == 1 and HEX32.match(snap.store_id)
        assert store.exists() and store.count_users() == 1

    @pytest.mark.parametrize(
        "content",
        [b"{not json", b"", b"\xff\xfe\x00", b"[]", b'"string"', b"null"],
    )
    def test_invalid_json_or_shape_is_corrupt(self, store: UserStore, content: bytes) -> None:
        store.paths.users_file.write_bytes(content)
        with pytest.raises(StoreCorruptError):
            store.snapshot()
        assert store.count_users() is None

    def test_deeply_nested_json_is_corrupt_not_a_crash(self, store: UserStore) -> None:
        store.paths.users_file.write_text("[" * 10_000 + "]" * 10_000)
        with pytest.raises(StoreCorruptError, match="not valid JSON"):
            store.snapshot()

    @pytest.mark.parametrize("version", [None, "1", True, 0, -1])
    def test_missing_or_invalid_schema_version_is_corrupt(
        self, store: UserStore, version: object
    ) -> None:
        data = valid_dict(store)
        if version is None:
            del data["schema_version"]
        else:
            data["schema_version"] = version
        write_store_json(store, data)
        with pytest.raises(StoreCorruptError, match="schema_version"):
            store.snapshot()

    def test_newer_schema_version_says_upgrade_ao(self, store: UserStore) -> None:
        data = valid_dict(store)
        data["schema_version"] = 2
        write_store_json(store, data)
        with pytest.raises(StoreCorruptError, match="upgrade ao"):
            store.snapshot()

    def test_unknown_required_feature_says_upgrade_ao(self, store: UserStore) -> None:
        data = valid_dict(store)
        data["required_features"] = ["x"]
        write_store_json(store, data)
        with pytest.raises(StoreCorruptError, match="upgrade ao"):
            store.snapshot()

    def test_known_required_features_are_accepted(
        self, store: UserStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(store_mod, "KNOWN_STORE_FEATURES", frozenset({"x"}))
        data = valid_dict(store)
        data["required_features"] = ["x"]
        write_store_json(store, data)
        assert store.snapshot().required_features == ["x"]

    @pytest.mark.parametrize("features", ["x", [1], {"a": 1}])
    def test_malformed_required_features_is_corrupt(self, store: UserStore, features: Any) -> None:
        data = valid_dict(store)
        data["required_features"] = features
        write_store_json(store, data)
        with pytest.raises(StoreCorruptError, match="required_features"):
            store.snapshot()

    def test_validation_errors_carry_field_paths_but_never_values(self, store: UserStore) -> None:
        data = valid_dict(store)
        data["users"]["alice"]["credential_epoch"] = "SENTINEL-EPOCH-VALUE"
        data["users"]["alice"]["password_hash"] = 12345  # wrong type, value must not leak
        data["users"]["alice"]["totp"] = {"secret_b32": ["SENTINEL-SEED"], "algorithm": "SENTINEL"}
        write_store_json(store, data)
        with pytest.raises(StoreCorruptError) as err:
            store.snapshot()
        message = str(err.value)
        assert "users.alice.credential_epoch" in message
        assert "users.alice.password_hash" in message
        assert "SENTINEL" not in message and "12345" not in message
        assert "SENTINEL" not in repr(err.value)

    def test_unknown_fields_are_accepted_and_survive_a_rewrite_byte_for_byte(
        self, store: UserStore, clock: FakeClock
    ) -> None:
        data = valid_dict(store)
        data["future_top_level"] = {"nested": [1, 2, {"k": "v"}], "n": None}
        data["users"]["alice"]["future_user_field"] = ["x", {"y": 1}]
        data["users"]["alice"]["totp"] = None
        write_store_json(store, data)
        write_store_json(store, json.loads(canonical_json(data)))
        store.paths.users_file.write_bytes(canonical_json(data))
        assert store.snapshot().model_extra == {"future_top_level": data["future_top_level"]}

        clock.advance(60)
        store.mutate(
            lambda f: set_password_hash(f, "alice", OTHER_PASSWORD_HASH, now=clock.now_utc())
        )

        expected = json.loads(canonical_json(data))
        alice = expected["users"]["alice"]
        alice["password_hash"] = OTHER_PASSWORD_HASH
        alice["password_changed_at"] = alice["updated_at"] = format_timestamp(clock.now_utc())
        alice["credential_epoch"] += 1
        assert raw_bytes(store) == canonical_json(expected)

    def test_unchanged_file_is_not_reread_and_external_replace_is_seen(
        self, store: UserStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reads: list[Path] = []
        real = Path.read_bytes

        def counting(self: Path) -> bytes:
            reads.append(self)
            return real(self)

        store.snapshot()
        monkeypatch.setattr(Path, "read_bytes", counting)
        first = store.snapshot()
        assert store.snapshot() is first
        assert reads == []  # our own write already primed the cache

        data = valid_dict(store)  # the test's own read of the file is not the store's
        data["users"]["bob"] = {**data["users"]["alice"], "username": "bob", "user_id": "b" * 32}
        fsutil.atomic_write_bytes(store.paths.users_file, canonical_json(data))
        reads.clear()
        assert set(store.snapshot().users) == {"alice", "bob"}
        assert len(reads) == 1
        store.snapshot()
        assert len(reads) == 1

    def test_deleting_the_file_resets_to_empty(self, store: UserStore) -> None:
        assert store.count_users() == 1
        store.paths.users_file.unlink()
        assert store.snapshot().users == {} and store.count_users() == 0

    def test_read_oserror_is_store_unavailable(self, store: UserStore) -> None:
        if os.geteuid() == 0:
            pytest.skip("root reads mode-000 files")
        store.snapshot()
        os.chmod(store.paths.users_file, 0)
        # change the stat key so the cache is bypassed
        store._cache_key = None
        try:
            with pytest.raises(StoreUnavailableError):
                store.snapshot()
            assert store.count_users() is None
        finally:
            os.chmod(store.paths.users_file, 0o600)

    def test_stat_oserror_is_store_unavailable(self, store: UserStore) -> None:
        if os.geteuid() == 0:
            pytest.skip("root bypasses directory permissions")
        os.chmod(store.paths.store_dir, 0)
        try:
            with pytest.raises(StoreUnavailableError):
                store.snapshot()
            with pytest.raises(StoreUnavailableError):
                store.exists()
            assert store.count_users() is None
        finally:
            os.chmod(store.paths.store_dir, 0o700)

    def test_user_by_id_follows_the_current_snapshot(self, store: UserStore) -> None:
        user_id, _ = ident(store)
        rec = store.user_by_id(user_id)
        assert rec is not None and rec.username == "alice"
        assert store.user_by_id("f" * 32) is None
        store.mutate(lambda f: remove_user(f, "alice"))
        assert store.user_by_id(user_id) is None  # a removed user is not found

    def test_file_vanishing_between_stat_and_read_is_an_empty_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        s = make_store(tmp_path, ["alice"])
        fresh = UserStore(s.paths)  # no cache: forces the read

        def gone(path: Path) -> Any:
            raise FileNotFoundError

        monkeypatch.setattr(store_mod, "load_store_file", gone)
        assert fresh.snapshot().store_id == ""

    def test_load_store_file_missing_raises_filenotfound(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_store_file(tmp_path / "users.json")


# ---------------------------------------------------------------------------
# mutate (AC-13)
# ---------------------------------------------------------------------------


class TestMutate:
    def test_twenty_spawn_processes_adding_users_lose_nothing(self, tmp_path: Path) -> None:
        s = make_store(tmp_path)
        procs = [
            SPAWN.Process(
                target=add_user_worker,
                args=(str(s.paths.store_dir), str(s.paths.state_dir), f"u{i}", i + 1000),
            )
            for i in range(20)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=JOIN_TIMEOUT)
        assert [p.exitcode for p in procs] == [0] * 20
        users = s.snapshot().users
        assert sorted(users) == sorted(f"u{i}" for i in range(20))
        assert len({u.user_id for u in users.values()}) == 20
        assert_valid_schema(s)

    def test_threads_in_one_process_are_serialized_too(self, tmp_path: Path) -> None:
        s = make_store(tmp_path)
        errors: list[BaseException] = []

        def work(i: int) -> None:
            try:
                s.mutate(
                    lambda f: add_user(
                        f, f"t{i}", TEST_PASSWORD_HASH, now=STORE_NOW, entropy=SeededEntropy(i)
                    )
                )
            except BaseException as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=JOIN_TIMEOUT)
        assert errors == []
        assert len(s.snapshot().users) == 8

    def test_create_initializes_a_new_store(self, tmp_path: Path, clock: FakeClock) -> None:
        s = make_store(tmp_path, create=False, clock=clock)
        s.mutate(lambda f: None, create=True)
        data = raw_json(s)
        assert data["schema_version"] == 1
        assert HEX32.match(data["store_id"])
        assert data["created_at"] == "2026-01-01T00:00:00Z"
        assert data["users"] == {} and data["required_features"] == []
        assert_valid_schema(s)

    def test_create_false_on_a_missing_store_raises(self, tmp_path: Path) -> None:
        s = make_store(tmp_path, create=False)
        with pytest.raises(StoreMissingError):
            s.mutate(lambda f: None)
        assert not s.paths.users_file.exists()

    def test_missing_store_directory_raises_store_missing(self, tmp_path: Path) -> None:
        s = make_store(tmp_path, create=False)
        os.rmdir(s.paths.store_dir)
        for create in (False, True):
            with pytest.raises(StoreMissingError, match="does not exist"):
                s.mutate(lambda f: None, create=create)

    def test_every_written_file_is_0600_and_schema_valid(self, tmp_path: Path) -> None:
        import os as _os

        old = _os.umask(0o022)
        try:
            s = make_store(tmp_path, ["alice", "bob"])
            enroll(s, "alice")
            uid, _ = ident(s, "bob")
            s.mutate(
                lambda f: issue_enrollment_token(f, "bob", entropy=SeededEntropy(3), now=STORE_NOW)
            )
            s.mutate(lambda f: cas_mark_login(f, "bob", user_id=uid, epoch=1, now=STORE_NOW))
        finally:
            _os.umask(old)
        assert_valid_schema(s)
        assert not [p for p in s.paths.store_dir.iterdir() if p.name.endswith(".tmp")]

    def test_stale_temp_files_are_removed_while_the_lock_is_held(
        self, store: UserStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stale = store.paths.store_dir / ".users.json.crashed.tmp"
        stale.write_text("old hashes")
        seen: list[str] = []
        real = fsutil.remove_stale_temp_files

        def spy(path: Path) -> int:
            with pytest.raises(fsutil.LockTimeoutError):  # the lock is held by mutate right now
                with fsutil.FileLock(store.paths.users_lock, timeout=0.0):
                    pass
            seen.append(path.name)
            return real(path)

        monkeypatch.setattr(fsutil, "remove_stale_temp_files", spy)
        store.mutate(lambda f: None)
        assert seen == ["users.json"]
        assert not stale.exists()

    def test_lock_timeout_raises_store_lock_timeout(self, tmp_path: Path) -> None:
        s = make_store(tmp_path, ["alice"], lock_timeout=0.1)
        before = raw_bytes(s)
        with fsutil.FileLock(s.paths.users_lock, timeout=1.0):
            with pytest.raises(StoreLockTimeoutError, match="users.lock"):
                s.mutate(lambda f: bump_epoch(f, "alice", now=STORE_NOW))
        assert raw_bytes(s) == before

    def test_symlinked_lock_file_is_refused(self, tmp_path: Path) -> None:
        s = make_store(tmp_path, ["alice"])
        target = tmp_path / "elsewhere"
        target.write_text("x")
        s.paths.users_lock.unlink()
        s.paths.users_lock.symlink_to(target)
        with pytest.raises(StoreUnavailableError):
            s.mutate(lambda f: None)
        assert target.read_text() == "x"

    def test_returns_fn_result_and_refreshes_the_cache(self, store: UserStore) -> None:
        uid, epoch = ident(store)
        got = store.mutate(lambda f: bump_epoch(f, "alice", now=STORE_NOW))
        assert got == epoch + 1
        assert store.snapshot().users["alice"].credential_epoch == epoch + 1
        assert store.user_by_id(uid) is not None

    def test_unchanged_content_is_not_rewritten(self, store: UserStore) -> None:
        before = os.stat(store.paths.users_file)
        store.mutate(lambda f: None)
        after = os.stat(store.paths.users_file)
        assert (before.st_ino, before.st_mtime_ns) == (after.st_ino, after.st_mtime_ns)

    def test_fn_exception_propagates_and_writes_nothing(self, store: UserStore) -> None:
        before = raw_bytes(store)

        def bad(f: UserStoreFile) -> None:
            f.users["alice"].credential_epoch += 5
            raise UserExistsError("boom")

        with pytest.raises(UserExistsError):
            store.mutate(bad)
        assert raw_bytes(store) == before
        assert store.snapshot().users["alice"].credential_epoch == 1

    @pytest.mark.filterwarnings("ignore:Pydantic serializer warnings")
    def test_invalid_result_is_refused_before_writing(self, store: UserStore) -> None:
        before = raw_bytes(store)

        def corrupt(f: UserStoreFile) -> None:
            f.users["alice"].credential_epoch = "SENTINEL-NOT-AN-INT"  # type: ignore[assignment]

        with pytest.raises(StoreCorruptError, match="refusing to write") as err:
            store.mutate(corrupt)
        assert "SENTINEL" not in str(err.value)
        assert raw_bytes(store) == before

    def test_corrupt_store_is_never_overwritten(self, store: UserStore) -> None:
        store.paths.users_file.write_text("{broken")
        with pytest.raises(StoreCorruptError):
            store.mutate(lambda f: add_user(f, "x", "h", now=STORE_NOW, entropy=SeededEntropy(1)))
        assert store.paths.users_file.read_text() == "{broken"

    def test_write_failure_is_store_unavailable_and_keeps_the_original(
        self, store: UserStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        before = raw_bytes(store)

        def enospc(path: Path, data: bytes, **_k: Any) -> None:
            raise OSError(errno.ENOSPC, "no space")

        monkeypatch.setattr(fsutil, "atomic_write_bytes", enospc)
        with pytest.raises(StoreUnavailableError) as err:
            store.mutate(lambda f: bump_epoch(f, "alice", now=STORE_NOW))
        assert "no space" in (err.value.cause_for_log or "")
        assert "no space" not in str(err.value)  # never sent to the client
        assert raw_bytes(store) == before

    def test_default_clock_and_entropy_are_the_system_ones(self, tmp_path: Path) -> None:
        paths = make_store_paths(tmp_path)
        s = UserStore(paths)
        s.mutate(lambda f: None, create=True)
        assert HEX32.match(s.snapshot().store_id)
        assert parse_timestamp(s.snapshot().created_at) is not None


# ---------------------------------------------------------------------------
# user mutations (AC-8)
# ---------------------------------------------------------------------------


class TestUserMutations:
    def test_add_user_fields(self) -> None:
        f = fresh_file()
        rec = add_user(
            f,
            "carol",
            TEST_PASSWORD_HASH,
            now=STORE_NOW,
            entropy=SeededEntropy(5),
            totp_required=True,
        )
        assert HEX32.match(rec.user_id)
        assert rec.credential_epoch == 1 and rec.totp_required is True
        assert rec.created_at == rec.updated_at == rec.password_changed_at == "2026-01-01T00:00:00Z"
        assert rec.totp is None and rec.recovery_codes == [] and rec.enrollment_token is None
        assert rec.last_login_at is None and rec.roles == []
        assert f.users["carol"] is rec
        assert (
            add_user(f, "dave", "h", now=STORE_NOW, entropy=SeededEntropy(5)).totp_required is False
        )

    def test_duplicate_user(self) -> None:
        f = fresh_file("alice")
        with pytest.raises(UserExistsError):
            add_user(f, "alice", "h", now=STORE_NOW, entropy=SeededEntropy(1))
        assert len(f.users) == 1

    def test_too_many_users(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(store_mod, "MAX_USERS", 3)
        f = fresh_file("a", "b", "c")
        with pytest.raises(TooManyUsersError):
            add_user(f, "d", "h", now=STORE_NOW, entropy=SeededEntropy(1))
        assert len(f.users) == 3

    def test_real_max_users_boundary(self) -> None:
        f = fresh_file()
        entropy = SeededEntropy(3)
        for i in range(constants.MAX_USERS):
            add_user(f, f"u{i}", "h", now=STORE_NOW, entropy=entropy)
        with pytest.raises(TooManyUsersError):
            add_user(f, "one-more", "h", now=STORE_NOW, entropy=entropy)

    def test_remove_user_returns_id_and_readd_gets_a_new_id(self) -> None:
        f = fresh_file("alice")
        old_id = f.users["alice"].user_id
        assert remove_user(f, "alice") == old_id
        assert "alice" not in f.users
        new = add_user(f, "alice", "h", now=STORE_NOW, entropy=SeededEntropy(1234))
        assert new.user_id != old_id

    def test_remove_unknown_user(self) -> None:
        with pytest.raises(UserNotFoundError):
            remove_user(fresh_file(), "ghost")

    def test_set_password_hash_bumps_epoch_and_stamps(self) -> None:
        f = fresh_file("alice")
        later = STORE_NOW + timedelta(hours=1)
        assert set_password_hash(f, "alice", "new-hash", now=later) == 2
        rec = f.users["alice"]
        assert rec.password_hash == "new-hash" and rec.credential_epoch == 2
        assert rec.password_changed_at == rec.updated_at == "2026-01-01T01:00:00Z"
        assert rec.created_at == "2026-01-01T00:00:00Z"
        with pytest.raises(UserNotFoundError):
            set_password_hash(f, "ghost", "h", now=later)

    def test_bump_epoch(self) -> None:
        f = fresh_file("alice")
        assert bump_epoch(f, "alice", now=STORE_NOW) == 2
        assert bump_epoch(f, "alice", now=STORE_NOW) == 3
        with pytest.raises(UserNotFoundError):
            bump_epoch(f, "ghost", now=STORE_NOW)

    def test_enroll_remove_and_replace_each_bump_by_exactly_one(self) -> None:
        f = fresh_file("alice")
        rec = f.users["alice"]
        entropy = SeededEntropy(2)
        codes = generate_recovery_codes(entropy)
        epoch = enroll_totp(
            f, "alice", user_id=rec.user_id, epoch=1, secret_b32=RFC_SECRET_B32, step=5,
            records=new_recovery_records(codes, entropy), now=STORE_NOW, completes_login=False,
        )  # fmt: skip
        assert epoch == 2
        assert (
            replace_recovery_codes(f, "alice", new_recovery_records(codes, entropy), now=STORE_NOW)
            == 3
        )
        assert remove_totp(f, "alice", now=STORE_NOW, set_required=None) == 4
        assert rec.credential_epoch == 4

    def test_replace_recovery_codes_requires_enrollment_and_user(self) -> None:
        f = fresh_file("alice")
        with pytest.raises(NotEnrolledError):
            replace_recovery_codes(f, "alice", [], now=STORE_NOW)
        with pytest.raises(UserNotFoundError):
            replace_recovery_codes(f, "ghost", [], now=STORE_NOW)
        assert f.users["alice"].credential_epoch == 1

    def test_records_may_be_pairs_or_models_and_are_copied(self) -> None:
        f = fresh_file("alice")
        model = RecoveryCodeHash(
            salt_hex="a" * 32, hash_hex="b" * 64, used_at="2026-01-01T00:00:00Z"
        )
        enroll_totp(
            f, "alice", user_id=f.users["alice"].user_id, epoch=1, secret_b32=RFC_SECRET_B32,
            step=1, records=[("c" * 32, "d" * 64), model], now=STORE_NOW, completes_login=False,
        )  # fmt: skip
        codes = f.users["alice"].recovery_codes
        assert [c.salt_hex[0] for c in codes] == ["c", "a"]
        assert codes[1] == model and codes[1] is not model

    def test_remove_totp_matrix(self) -> None:
        # enrolled user: set_required True / False / None
        for set_required, expect_flag in ((True, True), (False, False), (None, False)):
            f = fresh_file("alice")
            uid = f.users["alice"].user_id
            enroll_totp(
                f, "alice", user_id=uid, epoch=1, secret_b32=RFC_SECRET_B32, step=1,
                records=[("a" * 32, "b" * 64)], now=STORE_NOW, completes_login=False,
            )  # fmt: skip
            assert remove_totp(f, "alice", now=STORE_NOW, set_required=set_required) == 3
            rec = f.users["alice"]
            assert rec.totp is None and rec.recovery_codes == []
            assert rec.totp_required is expect_flag

    def test_remove_totp_not_enrolled_raises_only_without_set_required(self) -> None:
        f = fresh_file("alice")
        with pytest.raises(NotEnrolledError):
            remove_totp(f, "alice", now=STORE_NOW, set_required=None)
        assert f.users["alice"].credential_epoch == 1
        # reset-2fa on a not-enrolled user: idempotent, sets the flag, bumps once
        assert remove_totp(f, "alice", now=STORE_NOW, set_required=True) == 2
        assert f.users["alice"].totp_required is True
        assert remove_totp(f, "alice", now=STORE_NOW, set_required=True) == 2  # nothing changed
        # disable-2fa on a not-enrolled user clears the flag
        assert remove_totp(f, "alice", now=STORE_NOW, set_required=False) == 3
        assert f.users["alice"].totp_required is False
        assert remove_totp(f, "alice", now=STORE_NOW, set_required=False) == 3

    def test_remove_totp_set_required_false_clears_the_enrollment_token(self) -> None:
        f = fresh_file("alice")
        issue_enrollment_token(f, "alice", entropy=SeededEntropy(1), now=STORE_NOW)
        assert remove_totp(f, "alice", now=STORE_NOW, set_required=True) == 2  # flag changed
        assert f.users["alice"].enrollment_token is not None  # token kept for True
        assert f.users["alice"].totp_required is True
        assert remove_totp(f, "alice", now=STORE_NOW, set_required=False) == 3
        assert f.users["alice"].enrollment_token is None

    def test_remove_totp_unknown_user(self) -> None:
        with pytest.raises(UserNotFoundError):
            remove_totp(fresh_file(), "ghost", now=STORE_NOW, set_required=True)


class TestWithIdentity:
    def test_matching_identity_runs_the_inner_mutation(self, store: UserStore) -> None:
        uid, epoch = ident(store)
        got = store.mutate(
            with_identity(
                lambda f: set_password_hash(f, "alice", OTHER_PASSWORD_HASH, now=STORE_NOW),
                username="alice", user_id=uid, epoch=epoch,
            )
        )  # fmt: skip
        assert got == epoch + 1
        assert store.snapshot().users["alice"].password_hash == OTHER_PASSWORD_HASH

    def test_stale_after_epoch_bump_leaves_the_file_byte_identical(self, store: UserStore) -> None:
        uid, epoch = ident(store)
        store.mutate(lambda f: set_password_hash(f, "alice", OTHER_PASSWORD_HASH, now=STORE_NOW))
        before = raw_bytes(store)
        ran: list[bool] = []

        def inner(f: UserStoreFile) -> int:
            ran.append(True)
            return bump_epoch(f, "alice", now=STORE_NOW)

        with pytest.raises(StaleIdentityError):
            store.mutate(with_identity(inner, username="alice", user_id=uid, epoch=epoch))
        assert ran == [] and raw_bytes(store) == before

    def test_stale_after_remove_and_readd(self, store: UserStore) -> None:
        uid, epoch = ident(store)
        store.mutate(lambda f: remove_user(f, "alice"))
        store.mutate(
            lambda f: add_user(
                f, "alice", TEST_PASSWORD_HASH, now=STORE_NOW, entropy=SeededEntropy(777)
            )
        )
        assert store.snapshot().users["alice"].credential_epoch == epoch  # same epoch, new id
        before = raw_bytes(store)
        with pytest.raises(StaleIdentityError):
            store.mutate(
                with_identity(
                    lambda f: bump_epoch(f, "alice", now=STORE_NOW),
                    username="alice", user_id=uid, epoch=epoch,
                )
            )  # fmt: skip
        assert raw_bytes(store) == before

    def test_unknown_user_is_stale(self) -> None:
        guarded = with_identity(lambda f: 1, username="ghost", user_id="x", epoch=1)
        with pytest.raises(StaleIdentityError):
            guarded(fresh_file())


# ---------------------------------------------------------------------------
# CAS (AC-5 / AC-39 store part)
# ---------------------------------------------------------------------------


class TestCas:
    def test_rehash_applies_without_an_epoch_bump(self, store: UserStore) -> None:
        uid, epoch = ident(store)
        changed = store.mutate(
            lambda f: cas_rehash_password(
                f, "alice", user_id=uid, epoch=epoch, old_hash=TEST_PASSWORD_HASH,
                new_hash=OTHER_PASSWORD_HASH, now=STORE_NOW,
            )
        )  # fmt: skip
        assert changed is True
        rec = store.snapshot().users["alice"]
        assert rec.password_hash == OTHER_PASSWORD_HASH and rec.credential_epoch == epoch
        assert rec.password_changed_at == "2026-01-01T00:00:00Z"

    def test_stale_cas_after_set_password_keeps_the_new_hash(self, store: UserStore) -> None:
        uid, epoch = ident(store)
        store.mutate(lambda f: set_password_hash(f, "alice", "$brand-new$", now=STORE_NOW))
        before = raw_bytes(store)
        changed = store.mutate(
            lambda f: cas_rehash_password(
                f, "alice", user_id=uid, epoch=epoch, old_hash=TEST_PASSWORD_HASH,
                new_hash=OTHER_PASSWORD_HASH, now=STORE_NOW,
            )
        )  # fmt: skip
        assert changed is False
        assert store.snapshot().users["alice"].password_hash == "$brand-new$"
        assert raw_bytes(store) == before

    def test_cas_fails_on_hash_mismatch_foreign_id_and_unknown_user(self) -> None:
        f = fresh_file("alice")
        rec = f.users["alice"]
        kwargs = dict(old_hash=TEST_PASSWORD_HASH, new_hash="n", now=STORE_NOW)
        assert not cas_rehash_password(
            f, "alice", user_id=rec.user_id, epoch=1, **{**kwargs, "old_hash": "other"}
        )
        assert not cas_rehash_password(f, "alice", user_id="f" * 32, epoch=1, **kwargs)
        assert not cas_rehash_password(f, "ghost", user_id=rec.user_id, epoch=1, **kwargs)
        assert rec.password_hash == TEST_PASSWORD_HASH

    def test_mark_login_sets_last_login_at(self, store: UserStore, clock: FakeClock) -> None:
        uid, epoch = ident(store)
        clock.advance(90)
        assert store.mutate(
            lambda f: cas_mark_login(f, "alice", user_id=uid, epoch=epoch, now=clock.now_utc())
        )
        assert store.snapshot().users["alice"].last_login_at == "2026-01-01T00:01:30Z"

    def test_mark_login_stale_epoch_or_foreign_id_changes_nothing(self, store: UserStore) -> None:
        uid, epoch = ident(store)
        before = raw_bytes(store)
        assert not store.mutate(
            lambda f: cas_mark_login(f, "alice", user_id=uid, epoch=epoch + 1, now=STORE_NOW)
        )
        assert not store.mutate(
            lambda f: cas_mark_login(f, "alice", user_id="f" * 32, epoch=epoch, now=STORE_NOW)
        )
        assert not store.mutate(
            lambda f: cas_mark_login(f, "ghost", user_id=uid, epoch=epoch, now=STORE_NOW)
        )
        assert store.snapshot().users["alice"].last_login_at is None
        assert raw_bytes(store) == before


# ---------------------------------------------------------------------------
# consume_totp (AC-10)
# ---------------------------------------------------------------------------


@pytest.fixture()
def enrolled(tmp_path: Path, clock: FakeClock) -> UserStore:
    """alice enrolled with the RFC seed and ``last_used_step == step(NOW_UNIX) - 1``."""
    s = make_store(tmp_path, ["alice"], clock=clock)
    enroll(s, "alice", now_unix=NOW_UNIX - 30)
    return s


def totp_consume(
    s: UserStore, code: str | None, *, now_unix: float = NOW_UNIX, **overrides: Any
) -> TotpOutcome:
    uid, epoch = ident(s)
    kw: dict[str, Any] = {"user_id": uid, "epoch": epoch, "now_unix": now_unix, **overrides}
    return s.mutate(lambda f: consume_totp(f, "alice", code, **kw))


class TestConsumeTotp:
    def test_rfc_vector_sanity(self) -> None:
        assert hotp(RFC_KEY, 1) == "287082"  # RFC 4226 appendix D
        assert totp_code(59) == "287082"

    def test_valid_code_is_accepted_and_the_step_recorded(self, enrolled: UserStore) -> None:
        step = totp_step(NOW_UNIX)
        assert enrolled.snapshot().users["alice"].totp.last_used_step == step - 1  # type: ignore[union-attr]
        outcome = totp_consume(enrolled, totp_code(NOW_UNIX))
        assert outcome == TotpOutcome(OutcomeKind.OK, step)
        assert enrolled.snapshot().users["alice"].totp.last_used_step == step  # type: ignore[union-attr]
        assert_valid_schema(enrolled)

    def test_same_code_again_is_replayed_and_nothing_is_written(self, enrolled: UserStore) -> None:
        code = totp_code(NOW_UNIX)
        assert totp_consume(enrolled, code).kind is OutcomeKind.OK
        before = raw_bytes(enrolled)
        assert totp_consume(enrolled, code) == TotpOutcome(OutcomeKind.REPLAYED)
        assert raw_bytes(enrolled) == before

    def test_earlier_step_inside_the_window_is_replayed_later_step_is_ok(
        self, enrolled: UserStore
    ) -> None:
        step = totp_step(NOW_UNIX)
        assert totp_consume(enrolled, hotp(RFC_KEY, step + 1)).step == step + 1  # clock skew +1
        assert totp_consume(enrolled, hotp(RFC_KEY, step)).kind is OutcomeKind.REPLAYED

    def test_wrong_code_is_invalid_and_none_is_invalid(self, enrolled: UserStore) -> None:
        step = totp_step(NOW_UNIX)
        valid = {hotp(RFC_KEY, s) for s in range(step - 1, step + 2)}
        wrong = next(f"{n:06d}" for n in range(1_000_000) if f"{n:06d}" not in valid)
        before = raw_bytes(enrolled)
        assert totp_consume(enrolled, wrong) == TotpOutcome(OutcomeKind.INVALID)
        assert totp_consume(enrolled, None) == TotpOutcome(OutcomeKind.INVALID)
        assert totp_consume(enrolled, hotp(RFC_KEY, step + 2)).kind is OutcomeKind.INVALID
        assert raw_bytes(enrolled) == before

    def test_stale_epoch_or_user_id_is_stale_with_no_write(self, enrolled: UserStore) -> None:
        uid, epoch = ident(enrolled)
        code = totp_code(NOW_UNIX)
        before = raw_bytes(enrolled)
        assert totp_consume(enrolled, code, epoch=epoch + 1).kind is OutcomeKind.STALE
        assert totp_consume(enrolled, code, user_id="f" * 32).kind is OutcomeKind.STALE
        stale = enrolled.mutate(
            lambda f: consume_totp(f, "ghost", code, user_id=uid, epoch=epoch, now_unix=NOW_UNIX)
        )
        assert stale.kind is OutcomeKind.STALE
        assert raw_bytes(enrolled) == before

    def test_not_enrolled_raises(self, store: UserStore) -> None:
        before = raw_bytes(store)
        with pytest.raises(NotEnrolledError):
            totp_consume(store, "123456")
        assert raw_bytes(store) == before

    def test_corrupt_secret_is_a_corrupt_store_error(self, enrolled: UserStore) -> None:
        data = raw_json(enrolled)
        data["users"]["alice"]["totp"]["secret_b32"] = "!!!not-base32!!!"
        write_store_json(enrolled, data)
        with pytest.raises(StoreCorruptError) as err:
            totp_consume(enrolled, "123456")
        assert "!!!" not in str(err.value)

    def test_two_processes_consuming_the_same_code_yield_exactly_one_ok(
        self, enrolled: UserStore
    ) -> None:
        uid, epoch = ident(enrolled)
        code = totp_code(NOW_UNIX)
        start, results = SPAWN.Event(), SPAWN.Queue()
        procs = [
            SPAWN.Process(
                target=consume_totp_worker,
                args=(
                    str(enrolled.paths.store_dir), str(enrolled.paths.state_dir), "alice",
                    uid, epoch, code, NOW_UNIX, start, results,
                ),
            )
            for _ in range(2)
        ]  # fmt: skip
        for p in procs:
            p.start()
        start.set()
        kinds = sorted(results.get(timeout=JOIN_TIMEOUT) for _ in procs)
        for p in procs:
            p.join(timeout=JOIN_TIMEOUT)
        assert [p.exitcode for p in procs] == [0, 0]
        assert kinds == ["ok", "replayed"]


# ---------------------------------------------------------------------------
# consume_recovery (AC-11)
# ---------------------------------------------------------------------------


class TestConsumeRecovery:
    def _consume(self, s: UserStore, code: str | None, **overrides: Any) -> RecoveryOutcome:
        uid, epoch = ident(s)
        kw: dict[str, Any] = {"user_id": uid, "epoch": epoch, "now": STORE_NOW, **overrides}
        normalized = normalize_recovery_code(code) if code is not None else None
        return s.mutate(lambda f: consume_recovery(f, "alice", normalized, **kw))

    def test_ok_then_invalid_on_reuse(self, tmp_path: Path) -> None:
        s = make_store(tmp_path, ["alice"])
        codes = enroll(s, "alice")
        assert self._consume(s, codes[3]) == RecoveryOutcome(OutcomeKind.OK, 9)
        used = [c for c in s.snapshot().users["alice"].recovery_codes if c.used_at]
        assert len(used) == 1 and used[0].used_at == "2026-01-01T00:00:00Z"
        before = raw_bytes(s)
        assert self._consume(s, codes[3]) == RecoveryOutcome(OutcomeKind.INVALID)
        assert raw_bytes(s) == before
        assert self._consume(s, codes[4]) == RecoveryOutcome(OutcomeKind.OK, 8)
        assert_valid_schema(s)

    def test_lowercase_and_unhyphenated_input_normalizes(self, tmp_path: Path) -> None:
        s = make_store(tmp_path, ["alice"])
        codes = enroll(s, "alice")
        assert self._consume(s, codes[0].lower().replace("-", "")).kind is OutcomeKind.OK

    def test_wrong_or_missing_code_is_invalid(self, tmp_path: Path) -> None:
        s = make_store(tmp_path, ["alice"])
        enroll(s, "alice")
        assert self._consume(s, "0000-0000-0000-0000").kind is OutcomeKind.INVALID
        assert self._consume(s, None).kind is OutcomeKind.INVALID

    def test_stale_epoch_is_stale_with_no_write(self, tmp_path: Path) -> None:
        s = make_store(tmp_path, ["alice"])
        codes = enroll(s, "alice")
        uid, epoch = ident(s)
        before = raw_bytes(s)
        assert self._consume(s, codes[0], epoch=epoch + 1) == RecoveryOutcome(OutcomeKind.STALE)
        assert self._consume(s, codes[0], user_id="f" * 32).kind is OutcomeKind.STALE
        assert raw_bytes(s) == before

    def test_not_enrolled_raises(self, store: UserStore) -> None:
        with pytest.raises(NotEnrolledError):
            self._consume(store, "0000-0000-0000-0000")

    def test_a_regenerated_set_invalidates_the_old_codes(self, tmp_path: Path) -> None:
        s = make_store(tmp_path, ["alice"])
        old = enroll(s, "alice")
        entropy = SeededEntropy(55)
        new = generate_recovery_codes(entropy)
        s.mutate(
            lambda f: replace_recovery_codes(
                f, "alice", new_recovery_records(new, entropy), now=STORE_NOW
            )
        )
        assert self._consume(s, old[0]).kind is OutcomeKind.INVALID
        assert self._consume(s, new[0]) == RecoveryOutcome(OutcomeKind.OK, 9)


# ---------------------------------------------------------------------------
# enrollment tokens (AC-12)
# ---------------------------------------------------------------------------

TOKEN_FORMAT = re.compile(r"^[0-9A-Z]{4}(-[0-9A-Z]{4}){3}$")


def issue(s: UserStore, clock: FakeClock, seed: int = 4) -> str:
    return s.mutate(
        lambda f: issue_enrollment_token(
            f, "alice", entropy=SeededEntropy(seed), now=clock.now_utc()
        )
    )


def consume_token(s: UserStore, clock: FakeClock, token: str | None, **overrides: Any) -> bool:
    uid, _ = ident(s)
    normalized = normalize_recovery_code(token) if token is not None else None
    kw: dict[str, Any] = {"user_id": uid, "now": clock.now_utc(), **overrides}
    return s.mutate(lambda f: consume_enrollment_token(f, "alice", normalized, **kw))


class TestEnrollmentTokens:
    def test_issue_returns_the_plaintext_once_and_stores_only_a_hash(
        self, store: UserStore, clock: FakeClock
    ) -> None:
        token = issue(store, clock)
        assert TOKEN_FORMAT.match(token)
        raw = raw_bytes(store).decode()
        assert token not in raw and token.replace("-", "") not in raw
        stored = raw_json(store)["users"]["alice"]["enrollment_token"]
        assert set(stored) == {"salt_hex", "hash_hex", "expires_at"}
        assert stored["expires_at"] == format_timestamp(
            clock.now_utc() + timedelta(seconds=constants.ENROLLMENT_TOKEN_TTL_SECONDS)
        )
        assert_valid_schema(store)

    def test_reissue_replaces_the_old_token(self, store: UserStore, clock: FakeClock) -> None:
        first = issue(store, clock, seed=4)
        second = issue(store, clock, seed=5)
        assert first != second
        assert consume_token(store, clock, first) is False
        assert consume_token(store, clock, second) is True

    def test_valid_consume_clears_the_token(self, store: UserStore, clock: FakeClock) -> None:
        token = issue(store, clock)
        clock.advance(10)
        assert consume_token(store, clock, token) is True
        assert store.snapshot().users["alice"].enrollment_token is None
        assert consume_token(store, clock, token) is False

    def test_wrong_token_or_missing_input_is_false_and_leaves_the_token(
        self, store: UserStore, clock: FakeClock
    ) -> None:
        token = issue(store, clock)
        before = raw_bytes(store)
        assert consume_token(store, clock, "0000-0000-0000-0000") is False
        assert consume_token(store, clock, None) is False
        assert raw_bytes(store) == before
        assert consume_token(store, clock, token) is True

    def test_expired_token_is_false_and_cleared(self, store: UserStore, clock: FakeClock) -> None:
        token = issue(store, clock)
        clock.advance(constants.ENROLLMENT_TOKEN_TTL_SECONDS + 1)
        assert consume_token(store, clock, token) is False
        assert store.snapshot().users["alice"].enrollment_token is None

    def test_expiry_instant_itself_is_expired_one_second_before_is_valid(
        self, store: UserStore, clock: FakeClock
    ) -> None:
        token = issue(store, clock)
        clock.advance(constants.ENROLLMENT_TOKEN_TTL_SECONDS - 1)
        assert consume_token(store, clock, token) is True
        token = issue(store, clock, seed=6)
        clock.advance(constants.ENROLLMENT_TOKEN_TTL_SECONDS)
        assert consume_token(store, clock, token) is False

    def test_wrong_user_id_is_false_and_unknown_user_is_false(
        self, store: UserStore, clock: FakeClock
    ) -> None:
        token = issue(store, clock)
        assert consume_token(store, clock, token, user_id="f" * 32) is False
        assert store.snapshot().users["alice"].enrollment_token is not None
        uid, _ = ident(store)
        normalized = normalize_recovery_code(token)
        assert (
            store.mutate(
                lambda f: consume_enrollment_token(
                    f, "ghost", normalized, user_id=uid, now=STORE_NOW
                )
            )
            is False
        )

    def test_unparseable_expiry_or_salt_fails_closed(
        self, store: UserStore, clock: FakeClock
    ) -> None:
        token = issue(store, clock)
        data = raw_json(store)
        data["users"]["alice"]["enrollment_token"]["salt_hex"] = "not-hex"
        write_store_json(store, data)
        assert consume_token(store, clock, token) is False
        data["users"]["alice"]["enrollment_token"]["expires_at"] = "garbage"
        write_store_json(store, data)
        assert consume_token(store, clock, token) is False  # cleared as expired
        assert store.snapshot().users["alice"].enrollment_token is None

    def test_issue_for_an_enrolled_user_raises(self, tmp_path: Path, clock: FakeClock) -> None:
        s = make_store(tmp_path, ["alice"], clock=clock)
        enroll(s, "alice")
        with pytest.raises(AlreadyEnrolledError):
            issue(s, clock)

    def test_issue_for_an_unknown_user_raises(self) -> None:
        with pytest.raises(UserNotFoundError):
            issue_enrollment_token(fresh_file(), "ghost", entropy=SeededEntropy(1), now=STORE_NOW)

    def test_enroll_totp_clears_any_token(self, store: UserStore, clock: FakeClock) -> None:
        issue(store, clock)
        assert store.snapshot().users["alice"].enrollment_token is not None
        enroll(store, "alice")
        assert store.snapshot().users["alice"].enrollment_token is None


# ---------------------------------------------------------------------------
# enroll_totp (AC-13)
# ---------------------------------------------------------------------------


class TestEnrollTotp:
    def _enroll(self, s: UserStore, **overrides: Any) -> int:
        uid, epoch = ident(s)
        kw: dict[str, Any] = {
            "user_id": uid, "epoch": epoch, "secret_b32": RFC_SECRET_B32, "step": 7,
            "records": [("a" * 32, "b" * 64)], "now": STORE_NOW, "completes_login": False,
            **overrides,
        }  # fmt: skip
        return s.mutate(lambda f: enroll_totp(f, "alice", **kw))

    def test_enrolls_and_bumps_the_epoch(self, store: UserStore) -> None:
        assert self._enroll(store) == 2
        rec = store.snapshot().users["alice"]
        assert rec.totp is not None
        assert (rec.totp.last_used_step, rec.totp.enrolled_at) == (7, "2026-01-01T00:00:00Z")
        assert rec.last_login_at is None
        assert_valid_schema(store)

    def test_completes_login_sets_last_login_at(self, store: UserStore) -> None:
        self._enroll(store, completes_login=True)
        assert store.snapshot().users["alice"].last_login_at == "2026-01-01T00:00:00Z"

    def test_stale_identity_raises(self, store: UserStore) -> None:
        uid, epoch = ident(store)
        before = raw_bytes(store)
        with pytest.raises(StaleIdentityError):
            self._enroll(store, epoch=epoch + 1)
        with pytest.raises(StaleIdentityError):
            self._enroll(store, user_id="f" * 32)
        with pytest.raises(StaleIdentityError):
            store.mutate(
                lambda f: enroll_totp(
                    f, "ghost", user_id=uid, epoch=epoch, secret_b32=RFC_SECRET_B32, step=1,
                    records=[], now=STORE_NOW, completes_login=False,
                )
            )  # fmt: skip
        assert raw_bytes(store) == before

    def test_already_enrolled_raises(self, store: UserStore) -> None:
        self._enroll(store)
        with pytest.raises(AlreadyEnrolledError):
            self._enroll(store)


# ---------------------------------------------------------------------------
# count_store_users (AC-14)
# ---------------------------------------------------------------------------


class TestCountStoreUsers:
    def test_missing_directory_and_missing_file_are_zero(self, tmp_path: Path) -> None:
        assert count_store_users(tmp_path / "nope") == 0
        (tmp_path / "empty").mkdir()
        assert count_store_users(tmp_path / "empty") == 0

    def test_store_dir_that_is_a_file_is_zero(self, tmp_path: Path) -> None:
        f = tmp_path / "afile"
        f.write_text("x")
        assert count_store_users(f) == 0

    def test_empty_store_is_zero_and_two_users_is_two(self, tmp_path: Path) -> None:
        s = make_store(tmp_path)
        assert count_store_users(s.paths.store_dir) == 0
        s.mutate(lambda f: add_user(f, "a", "h", now=STORE_NOW, entropy=SeededEntropy(1)))
        s.mutate(lambda f: add_user(f, "b", "h", now=STORE_NOW, entropy=SeededEntropy(2)))
        assert count_store_users(s.paths.store_dir) == 2

    def test_corrupt_and_unsupported_schema_are_none(self, store: UserStore) -> None:
        data = raw_json(store)
        store.paths.users_file.write_text("{oops")
        assert count_store_users(store.paths.store_dir) is None
        data["schema_version"] = 2
        write_store_json(store, data)
        assert count_store_users(store.paths.store_dir) is None

    def test_unreadable_file_is_none(self, store: UserStore) -> None:
        if os.geteuid() == 0:
            pytest.skip("root reads mode-000 files")
        os.chmod(store.paths.users_file, 0)
        try:
            assert count_store_users(store.paths.store_dir) is None
        finally:
            os.chmod(store.paths.users_file, 0o600)

    def test_unreadable_directory_is_none(self, store: UserStore) -> None:
        if os.geteuid() == 0:
            pytest.skip("root bypasses directory permissions")
        os.chmod(store.paths.store_dir, 0)
        try:
            assert count_store_users(store.paths.store_dir) is None
        finally:
            os.chmod(store.paths.store_dir, 0o700)

    def test_file_vanishing_between_stat_and_read_is_zero(
        self, store: UserStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def gone(path: Path) -> Any:
            raise FileNotFoundError

        monkeypatch.setattr(store_mod, "load_store_file", gone)
        assert count_store_users(store.paths.store_dir) == 0


def test_every_mutation_is_exercised_against_the_schema(tmp_path: Path, clock: FakeClock) -> None:
    """One store taken through every mutation; the schema holds after each one."""
    s = make_store(tmp_path, ["alice", "bob"], clock=clock)
    assert_valid_schema(s)
    steps = [
        lambda f: set_password_hash(f, "alice", OTHER_PASSWORD_HASH, now=clock.now_utc()),
        lambda f: bump_epoch(f, "alice", now=clock.now_utc()),
        lambda f: issue_enrollment_token(f, "alice", entropy=SeededEntropy(8), now=clock.now_utc()),
        lambda f: remove_totp(f, "bob", now=clock.now_utc(), set_required=True),
        lambda f: remove_user(f, "bob"),
    ]
    for step in steps:
        s.mutate(step)
        assert_valid_schema(s)
    enroll(s, "alice")
    assert_valid_schema(s)
