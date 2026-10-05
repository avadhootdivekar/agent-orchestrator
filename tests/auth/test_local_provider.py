"""``LocalPasswordProvider``: readiness, login, uniformity, revocation, re-auth (T-XchniS).

A real provider over real file-backed stores in a temp directory, the instant ``FastFakeHasher``
and a fixed clock (HLD 11.15.3-11.15.7). Spies count store/lockout/hash calls.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.auth.constants import LOCAL_PROVIDER_ID, MAX_USERNAME_CHARS
from agent_orchestrator.auth.errors import (
    AuthError,
    AuthNotReadyError,
    BusyError,
    ErrorCode,
    StoreLockTimeoutError,
    StoreUnavailableError,
    TooManyAttemptsError,
    UnsafePermissionsError,
)
from agent_orchestrator.auth.guard import AttemptGuard
from agent_orchestrator.auth.local_provider import LocalPasswordProvider, normalize_username
from agent_orchestrator.auth.lockouts import LockoutKey, LockoutPolicy, LockoutStore
from agent_orchestrator.auth.model import SessionState, TotpPolicy
from agent_orchestrator.auth.paths import StorePaths
from agent_orchestrator.auth.provider import AuthProvider, Revalidation
from agent_orchestrator.auth.store import (
    UserStore,
    add_user,
    bump_epoch,
    remove_user,
    set_password_hash,
)
from agent_orchestrator.auth.throttle import AddressThrottle, UsernameGates

from .helpers.core import run_async
from .helpers.crypto import FastFakeHasher
from .helpers.provider import (
    CLIENT,
    OTHER_CLIENT,
    PASSWORD,
    ProviderEnv,
    RehashingHasher,
    fake_hash,
    make_env,
)
from .helpers.store import enroll

NEW_PASSWORD = "an entirely different passphrase"


def spy(monkeypatch: pytest.MonkeyPatch, owner: Any, name: str) -> list[tuple[Any, ...]]:
    """Wrap ``owner.name`` (a class attribute) and return the list of call args."""
    calls: list[tuple[Any, ...]] = []
    original = getattr(owner, name)

    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(owner, name, wrapper)
    return calls


def login(env: ProviderEnv, name: str = "alice", password: str = PASSWORD, **kw: Any) -> Any:
    return run_async(env.provider.authenticate(name, password, kw.get("client", CLIENT)))


def code_of(call: Callable[[], Any]) -> ErrorCode:
    with pytest.raises(AuthError) as caught:
        call()
    return caught.value.code


def lockout_failures(env: ProviderEnv, user: str = "alice") -> int:
    user_id = env.store.snapshot().users[user].user_id
    return env.lockouts.state(LockoutKey(user_id=user_id)).failures


# --- check_ready --------------------------------------------------------------------------------


def fresh_provider(tmp_path: Path, **kw: Any) -> tuple[LocalPasswordProvider, ProviderEnv]:
    """A provider over a NOT-yet-initialized store (no users file, no state files)."""
    env = make_env(tmp_path, users=(), **kw)
    env.paths.users_file.unlink()
    env.paths.lockouts_file.unlink()
    return env.provider, env


def test_the_provider_id_and_the_abc() -> None:
    assert LocalPasswordProvider.provider_id == LOCAL_PROVIDER_ID
    assert issubclass(LocalPasswordProvider, AuthProvider)


def test_check_ready_missing_store_directory_gives_the_bootstrap_message(tmp_path: Path) -> None:
    provider, env = fresh_provider(tmp_path)
    env.paths.users_lock.unlink(missing_ok=True)
    env.paths.store_dir.rmdir()
    with pytest.raises(AuthNotReadyError) as caught:
        provider.check_ready()
    message = str(caught.value)
    assert "ao auth add-user <username>" in message
    assert f"--auth-dir {env.paths.store_dir}" in message  # not the XDG default
    assert str(env.paths.users_file) in message
    assert "ui.auth.enabled from cli" in message
    assert "--no-auth" in message and "AO_UI_AUTH=0" in message


def test_check_ready_missing_users_file_is_the_bootstrap_message(tmp_path: Path) -> None:
    provider, _ = fresh_provider(tmp_path)
    with pytest.raises(AuthNotReadyError, match="ao auth add-user"):
        provider.check_ready()


def test_the_bootstrap_message_omits_auth_dir_for_the_xdg_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_home = tmp_path / "cfg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    default_dir = config_home / "ao" / "auth"
    default_dir.mkdir(parents=True, mode=0o700)
    default_dir.chmod(0o700)
    env = make_env(tmp_path / "other")
    store = UserStore(StorePaths.at(default_dir, env.paths.state_dir))
    provider = LocalPasswordProvider(
        store, env.lockouts, env.guard, env.hasher, env.settings, env.audit, env.clock
    )
    with pytest.raises(AuthNotReadyError) as caught:
        provider.check_ready()  # the directory exists, users.json does not
    assert "--auth-dir" not in str(caught.value)
    assert "ao auth add-user <username>\n" in str(caught.value)


def test_check_ready_zero_users_names_the_bootstrap_command(tmp_path: Path) -> None:
    env = make_env(tmp_path, users=("alice",))
    env.store.mutate(lambda f: remove_user(f, "alice"))
    with pytest.raises(AuthNotReadyError, match="ao auth add-user"):
        env.provider.check_ready()


def test_check_ready_corrupt_store(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.paths.users_file.write_text("{not json")
    with pytest.raises(AuthNotReadyError, match="unusable"):
        env.provider.check_ready()


def test_check_ready_newer_schema_store(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    data = json.loads(env.paths.users_file.read_text())
    data["schema_version"] = 99
    env.paths.users_file.write_text(json.dumps(data))
    with pytest.raises(AuthNotReadyError, match="newer"):
        env.provider.check_ready()


def test_check_ready_unreadable_store_is_not_ready(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    original = env.store.snapshot

    def broken() -> Any:
        raise StoreUnavailableError(cause_for_log="cannot stat users file: boom")

    env.store.snapshot = broken  # type: ignore[method-assign]
    try:
        with pytest.raises(AuthNotReadyError, match="unreadable.*boom"):
            env.provider.check_ready()
    finally:
        env.store.snapshot = original  # type: ignore[method-assign]


def test_check_ready_corrupt_lockouts_names_the_unlock_command(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.paths.lockouts_file.write_text("{broken")
    with pytest.raises(AuthNotReadyError, match="ao auth unlock"):
        env.provider.check_ready()


@pytest.mark.parametrize(
    "damage",
    ["store_dir", "state_dir", "users_file", "symlinked_users", "open_parent"],
)
def test_check_ready_refuses_unsafe_permissions(tmp_path: Path, damage: str) -> None:
    env = make_env(tmp_path)
    env.provider.check_ready()  # healthy first: the lockout file exists too
    paths = env.paths
    if damage == "store_dir":
        paths.store_dir.chmod(0o755)
    elif damage == "state_dir":
        paths.state_dir.chmod(0o750)
    elif damage == "users_file":
        paths.users_file.chmod(0o644)
    elif damage == "symlinked_users":
        real = tmp_path / "real-users.json"
        paths.users_file.rename(real)
        paths.users_file.symlink_to(real)
    else:
        tmp_path.chmod(0o777)  # other-writable parent
    try:
        with pytest.raises(UnsafePermissionsError):
            env.provider.check_ready()
    finally:
        tmp_path.chmod(0o700)


def test_check_ready_passes_creates_the_state_dir_and_the_name_key_once(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    for leftover in list(env.paths.state_dir.glob("*")):
        leftover.unlink()
    env.paths.state_dir.rmdir()  # check_ready creates it again, 0700
    env.provider.check_ready()
    assert env.paths.state_dir.stat().st_mode & 0o777 == 0o700
    key = json.loads(env.paths.lockouts_file.read_text())["name_key_hex"]
    assert len(key) == 64
    before = env.paths.lockouts_file.stat().st_mtime_ns
    env.provider.check_ready()
    assert env.paths.lockouts_file.stat().st_mtime_ns == before  # a second call writes nothing
    assert json.loads(env.paths.lockouts_file.read_text())["name_key_hex"] == key


def test_a_group_writable_euid_owned_parent_warns_instead_of_refusing(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    tmp_path.chmod(0o770)
    try:
        env.provider.check_ready()  # does not raise
        warnings = env.provider.startup_warnings()
    finally:
        tmp_path.chmod(0o700)
    (notice,) = [w for w in warnings if "chmod g-w" in w]
    assert str(tmp_path.resolve()) in notice and "group-writable" in notice


# --- startup_warnings ---------------------------------------------------------------------------


def test_warns_about_enrolled_users_under_off(tmp_path: Path) -> None:
    env = make_env(tmp_path, users=("alice", "bob"), totp=TotpPolicy.OFF)
    enroll(env.store, "alice")
    (warning,) = env.provider.startup_warnings()
    assert warning == (
        "totp=off only disables new enrollment; 1 enrolled user(s) will still be asked for a code"
    )


def test_warns_about_blocked_users_and_names_the_remedy(tmp_path: Path) -> None:
    env = make_env(tmp_path, users=("alice", "bob"), totp=TotpPolicy.OFF)
    env.store.mutate(lambda f: setattr(f.users["bob"], "totp_required", True))
    (warning,) = env.provider.startup_warnings()
    assert "1 user(s) require two-factor" in warning
    assert "cannot log in" in warning and "ao auth disable-2fa" in warning


def test_an_enrolled_required_user_is_not_blocked_under_off(tmp_path: Path) -> None:
    env = make_env(tmp_path, totp=TotpPolicy.OFF)
    enroll(env.store, "alice")
    env.store.mutate(lambda f: setattr(f.users["alice"], "totp_required", True))
    warnings = env.provider.startup_warnings()
    assert len(warnings) == 1 and "will still be asked" in warnings[0]  # no "cannot log in"


def test_no_warnings_under_optional_with_private_parents(tmp_path: Path) -> None:
    env = make_env(tmp_path, totp=TotpPolicy.OPTIONAL)
    enroll(env.store, "alice")
    assert env.provider.startup_warnings() == []


def test_startup_warnings_tolerate_an_unreadable_store(tmp_path: Path) -> None:
    env = make_env(tmp_path, totp=TotpPolicy.OFF)
    env.paths.users_file.write_text("{not json")
    assert env.provider.startup_warnings() == []


# --- authenticate: uniformity (S6) ---------------------------------------------------------------


def test_unknown_and_wrong_password_are_indistinguishable_and_cost_the_same(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env(tmp_path)
    mutate_calls = spy(monkeypatch, UserStore, "mutate")
    failure_calls = spy(monkeypatch, LockoutStore, "record_failure")
    outcomes: list[AuthError] = []
    for name, password in (("ghost", "x"), ("alice", "wrong")):
        verifies = env.hasher.verify_calls
        writes = len(failure_calls)
        with pytest.raises(AuthError) as caught:
            login(env, name, password)
        outcomes.append(caught.value)
        assert env.hasher.verify_calls == verifies + 1  # no short-circuit before scrypt
        assert len(failure_calls) == writes + 1
    assert mutate_calls == []  # failed logins never touch users.json
    ghost, wrong = outcomes
    assert ghost.code is wrong.code is ErrorCode.INVALID_CREDENTIALS
    assert ghost.detail == wrong.detail and ghost.status == wrong.status == 401


def test_the_unknown_name_is_verified_against_the_dummy_hash(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    seen: list[str] = []
    original = env.hasher.verify

    async def recording(raw: str, encoded: str) -> bool:
        seen.append(encoded)
        return await original(raw, encoded)

    env.hasher.verify = recording  # type: ignore[method-assign]
    code_of(lambda: login(env, "ghost", PASSWORD))
    assert seen == [env.hasher.dummy_hash]


def test_unknown_correct_looking_password_never_authenticates(tmp_path: Path) -> None:
    class Always(FastFakeHasher):
        async def verify(self, raw: str, encoded: str) -> bool:
            self.verify_calls += 1
            return True  # even a hasher that accepts everything cannot admit a phantom

    env = make_env(tmp_path, hasher=Always())
    assert code_of(lambda: login(env, "ghost")) is ErrorCode.INVALID_CREDENTIALS


def test_phantom_lockout_key_is_the_keyed_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env(tmp_path)
    keys: list[LockoutKey] = []
    original = LockoutStore.record_failure

    def capture(self: LockoutStore, key: LockoutKey, policy: LockoutPolicy) -> Any:
        keys.append(key)
        return original(self, key, policy)

    monkeypatch.setattr(LockoutStore, "record_failure", capture)
    code_of(lambda: login(env, "ghost"))
    assert keys == [LockoutKey(phantom=env.lockouts.name_digest("ghost"))]
    assert keys[0].phantom != hashlib.sha256(b"ghost").hexdigest()
    (event,) = env.audit_events("auth.login.failure")
    assert event["username"] is None and event["user_id"] is None
    assert event["username_hash"] == env.lockouts.name_digest("ghost")[:16]


def test_malformed_names_share_one_bucket_and_still_make_one_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env(tmp_path)
    failure_calls = spy(monkeypatch, LockoutStore, "record_failure")
    for junk in ("Bad Name!", "x y", "", "../etc/passwd", "ünï\u0000"):
        code_of(lambda junk=junk: login(env, junk))
    assert len(failure_calls) == 5 and env.hasher.verify_calls == 5
    phantoms = json.loads(env.paths.lockouts_file.read_text())["phantoms"]
    assert list(phantoms) == [env.lockouts.name_digest("x y")]  # the single bucket
    assert phantoms[env.lockouts.name_digest("Bad Name!")]["failures"] == 5


def test_a_huge_junk_username_is_refused_like_any_other_unknown_name(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    assert code_of(lambda: login(env, "a" * 16_000)) is ErrorCode.INVALID_CREDENTIALS
    assert env.hasher.verify_calls == 1  # still exactly one (dummy) verify
    gate_keys = list(env.guard._gates._gates)  # the gate key is capped: junk cannot pin memory
    assert gate_keys and all(len(key) <= MAX_USERNAME_CHARS for key in gate_keys)


def test_login_names_are_normalized(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    assert normalize_username("  ＡＬＩＣＥ \n") == "alice"
    identity = login(env, "  ＡＬＩＣＥ ")
    assert identity.username == "alice"


def test_no_name_key_means_the_first_unknown_attempt_creates_one_lazily(tmp_path: Path) -> None:
    # check_ready() normally creates it; a deleted file self-heals once (LockoutStore's contract).
    env = make_env(tmp_path)
    env.paths.lockouts_file.unlink()
    code_of(lambda: login(env, "ghost"))
    assert json.loads(env.paths.lockouts_file.read_text())["name_key_hex"]


# --- authenticate: guard integration -----------------------------------------------------------


def test_address_throttle_refuses_without_hashing(tmp_path: Path) -> None:
    env = make_env(tmp_path, address_threshold=2)
    for _ in range(2):
        code_of(lambda: login(env, "alice", "wrong"))
    verifies = env.hasher.verify_calls
    with pytest.raises(TooManyAttemptsError):
        login(env, "alice", PASSWORD)
    assert env.hasher.verify_calls == verifies
    assert login(env, "alice", PASSWORD, client=OTHER_CLIENT).username == "alice"


def test_account_lockout_after_threshold_refuses_the_right_password(tmp_path: Path) -> None:
    env = make_env(tmp_path, threshold=3, address_threshold=50)
    for _ in range(3):
        code_of(lambda: login(env, "alice", "wrong"))
    verifies = env.hasher.verify_calls
    with pytest.raises(TooManyAttemptsError):
        login(env, "alice", PASSWORD)
    assert env.hasher.verify_calls == verifies
    assert len(env.audit_events("auth.lockout")) == 1


def test_busy_hasher_counts_an_address_failure_but_no_lockout(tmp_path: Path) -> None:
    env = make_env(tmp_path, hasher=FastFakeHasher(raise_busy=True), address_threshold=2)
    for _ in range(2):
        with pytest.raises(BusyError):
            login(env, "alice")
    assert lockout_failures(env) == 0
    with pytest.raises(TooManyAttemptsError):
        login(env, "alice")  # the flood was counted against the address


def test_two_provider_instances_share_the_account_lockout(tmp_path: Path) -> None:
    env = make_env(tmp_path, threshold=3, address_threshold=50)
    second_hasher = FastFakeHasher()
    other = second_provider(env, second_hasher)
    for _ in range(3):
        code_of(lambda: login(env, "alice", "wrong"))
    with pytest.raises(TooManyAttemptsError):
        run_async(other.authenticate("alice", PASSWORD, OTHER_CLIENT))
    assert second_hasher.verify_calls == 0


def second_provider(env: ProviderEnv, hasher: FastFakeHasher) -> LocalPasswordProvider:
    """A second provider (own lockout store, throttle, gates) over the same state directory."""
    lockouts = LockoutStore(env.paths, clock=env.clock)
    guard = AttemptGuard(
        address_throttle=AddressThrottle(50, clock=env.clock),
        gates=UsernameGates(),
        lockouts=lockouts,
        lockout_policy=LockoutPolicy(3, 30, 900),
        audit=env.audit,
        realm="ui:other",
        clock=env.clock,
    )
    return LocalPasswordProvider(
        env.store, lockouts, guard, hasher, env.settings, env.audit, env.clock
    )


# --- authenticate: next_state matrix (S9) -----------------------------------------------------


@pytest.mark.parametrize("policy", list(TotpPolicy))
def test_an_enrolled_user_is_always_challenged(tmp_path: Path, policy: TotpPolicy) -> None:
    env = make_env(tmp_path, totp=policy)
    enroll(env.store, "alice")
    identity = login(env)
    assert identity.next_state is SessionState.PARTIAL_SECOND_FACTOR


def test_not_enrolled_optional_is_a_full_login_that_resets_and_marks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env(tmp_path, totp=TotpPolicy.OPTIONAL)
    code_of(lambda: login(env, "alice", "wrong"))
    assert lockout_failures(env) == 1
    resets = spy(monkeypatch, LockoutStore, "reset")
    mutates = spy(monkeypatch, UserStore, "mutate")
    identity = login(env)
    assert identity.next_state is SessionState.FULL
    assert len(resets) == 1 and len(mutates) == 1  # one reset, one cas_mark_login
    assert lockout_failures(env) == 0
    assert env.store.snapshot().users["alice"].last_login_at == "2026-01-01T00:00:00Z"
    assert env.audit_events("auth.login.second_factor_pending") == []


@pytest.mark.parametrize(
    ("policy", "user_required"),
    [(TotpPolicy.REQUIRED, False), (TotpPolicy.OPTIONAL, True), (TotpPolicy.REQUIRED, True)],
)
def test_required_and_not_enrolled_goes_to_enrollment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, policy: TotpPolicy, user_required: bool
) -> None:
    env = make_env(tmp_path, totp=policy)
    env.store.mutate(lambda f: setattr(f.users["alice"], "totp_required", user_required))
    code_of(lambda: login(env, "alice", "wrong"))
    resets = spy(monkeypatch, LockoutStore, "reset")
    identity = login(env)
    assert identity.next_state is SessionState.PARTIAL_ENROLL
    assert resets == []
    assert lockout_failures(env) == 1  # NOT reset until the second factor succeeds
    assert env.store.snapshot().users["alice"].last_login_at is None


def test_partial_states_write_one_pending_event_each(tmp_path: Path) -> None:
    env = make_env(tmp_path, users=("alice", "bob"), totp=TotpPolicy.REQUIRED)
    enroll(env.store, "alice")
    login(env, "alice")
    login(env, "bob")
    pending = env.audit_events("auth.login.second_factor_pending")
    assert [(e["username"], e["details"]["second_factor"]) for e in pending] == [
        ("alice", "second_factor_required"),
        ("bob", "enrollment_required"),
    ]
    assert all(e["outcome"] == "info" and e["client_addr"] == CLIENT.key for e in pending)


def test_required_user_under_off_is_blocked_after_a_correct_password_only(tmp_path: Path) -> None:
    env = make_env(tmp_path, totp=TotpPolicy.OFF)
    env.store.mutate(lambda f: setattr(f.users["alice"], "totp_required", True))
    assert code_of(lambda: login(env, "alice", "wrong")) is ErrorCode.INVALID_CREDENTIALS
    with pytest.raises(AuthError) as caught:
        login(env)
    assert caught.value.code is ErrorCode.TOTP_REQUIRED and caught.value.status == 403


def test_identity_carries_the_snapshot_fields(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.store.mutate(lambda f: setattr(f.users["alice"], "roles", ["admin"]))
    rec = env.store.snapshot().users["alice"]
    identity = login(env)
    assert identity.user_id == rec.user_id and identity.roles == ("admin",)
    assert isinstance(identity.roles, tuple)
    assert identity.credential_epoch == rec.credential_epoch
    assert identity.store_id == env.store.snapshot().store_id
    assert identity.provider == LOCAL_PROVIDER_ID


# --- authenticate: revocation snapshot and CAS rehash --------------------------------------------


def test_epoch_comes_from_the_snapshot_of_the_verified_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env(tmp_path)
    env.store.mutate(lambda f: bump_epoch(f, "alice", now=env.clock.now_utc()))
    env.store.mutate(lambda f: bump_epoch(f, "alice", now=env.clock.now_utc()))
    rec = env.store.snapshot().users["alice"]
    assert rec.credential_epoch == 3
    original = env.hasher.verify

    async def straddling(raw: str, encoded: str) -> bool:
        # A password change lands while scrypt runs (epoch 3 -> 4).
        env.store.mutate(
            lambda f: set_password_hash(f, "alice", fake_hash("rotated"), now=env.clock.now_utc())
        )
        return await original(raw, encoded)

    env.hasher.verify = straddling  # type: ignore[method-assign]
    identity = login(env)
    assert identity.credential_epoch == 3  # the epoch of the verified hash, not the re-read one
    assert env.store.snapshot().users["alice"].credential_epoch == 4
    assert env.provider.revalidate(rec.user_id, 3) is Revalidation.REVOKED


def test_rehash_rewrites_the_hash_and_keeps_the_epoch(tmp_path: Path) -> None:
    env = make_env(tmp_path, hasher=RehashingHasher())
    before = env.store.snapshot().users["alice"]
    identity = login(env)
    after = env.store.snapshot().users["alice"]
    assert after.password_hash == fake_hash(PASSWORD) + RehashingHasher.marker
    assert after.credential_epoch == before.credential_epoch == identity.credential_epoch
    assert after.password_changed_at == before.password_changed_at  # no password change


def test_a_rehash_losing_the_race_to_a_password_change_is_skipped(tmp_path: Path) -> None:
    hasher = RehashingHasher()
    env = make_env(tmp_path, hasher=hasher)
    original_hash = hasher.hash

    async def racing(raw: str) -> str:
        env.store.mutate(  # set-password lands between the verify and the rehash
            lambda f: set_password_hash(f, "alice", fake_hash("newer"), now=env.clock.now_utc())
        )
        return await original_hash(raw)

    hasher.hash = racing  # type: ignore[method-assign]
    login(env)
    rec = env.store.snapshot().users["alice"]
    assert rec.password_hash == fake_hash("newer")  # the newer credential survives
    assert rec.credential_epoch == 2


def test_a_failed_login_never_rehashes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = make_env(tmp_path, hasher=RehashingHasher())
    mutates = spy(monkeypatch, UserStore, "mutate")
    code_of(lambda: login(env, "alice", "wrong"))
    code_of(lambda: login(env, "ghost", "wrong"))
    assert mutates == [] and env.hasher.hash_calls == 0


def test_revival_with_the_same_name_is_revoked(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    old = login(env)
    env.store.mutate(lambda f: remove_user(f, "alice"))
    env.store.mutate(
        lambda f: add_user(
            f, "alice", fake_hash(PASSWORD), now=env.clock.now_utc(), entropy=_Entropy()
        )
    )
    new = env.store.snapshot().users["alice"]
    assert new.user_id != old.user_id
    assert env.provider.revalidate(old.user_id, old.credential_epoch) is Revalidation.REVOKED
    assert env.provider.revalidate(new.user_id, new.credential_epoch) is Revalidation.VALID


class _Entropy:
    def token_bytes(self, n: int) -> bytes:
        return b"\x7f" * n


# --- revalidate and user_view -------------------------------------------------------------------


def test_revalidate_is_tri_state(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    env = make_env(tmp_path)
    rec = env.store.snapshot().users["alice"]
    assert env.provider.revalidate(rec.user_id, 1) is Revalidation.VALID
    assert env.provider.revalidate(rec.user_id, 2) is Revalidation.REVOKED
    assert env.provider.revalidate("f" * 32, 1) is Revalidation.REVOKED
    good = env.paths.users_file.read_bytes()
    env.paths.users_file.write_text("{not json")
    with caplog.at_level(logging.ERROR):
        assert env.provider.revalidate(rec.user_id, 1) is Revalidation.UNAVAILABLE
        assert env.provider.revalidate(rec.user_id, 1) is Revalidation.UNAVAILABLE
    assert len([r for r in caplog.records if "unreadable" in r.getMessage()]) == 1  # once
    env.paths.users_file.write_bytes(good)
    assert env.provider.revalidate(rec.user_id, 1) is Revalidation.VALID
    caplog.clear()
    env.paths.users_file.write_text("{not json again")
    with caplog.at_level(logging.ERROR):
        env.provider.revalidate(rec.user_id, 1)
    assert len(caplog.records) == 1  # a NEW outage logs again


@pytest.mark.parametrize("error", [StoreLockTimeoutError("held"), OSError("EIO")])
def test_revalidate_maps_lock_timeouts_and_os_errors_to_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    env = make_env(tmp_path)

    def broken(self: UserStore, user_id: str) -> Any:
        raise error

    monkeypatch.setattr(UserStore, "user_by_id", broken)
    assert env.provider.revalidate("a" * 32, 1) is Revalidation.UNAVAILABLE


def test_user_view_projects_counts_without_secrets(tmp_path: Path) -> None:
    env = make_env(tmp_path, users=("alice", "bob"))
    enroll(env.store, "alice")
    env.store.mutate(lambda f: setattr(f.users["bob"], "totp_required", True))
    alice = env.provider.user_view("alice")
    bob = env.provider.user_view("bob")
    assert alice is not None and bob is not None
    assert alice.totp_enrolled and alice.recovery_codes_remaining == 10 and not alice.totp_required
    assert not bob.totp_enrolled and bob.recovery_codes_remaining is None and bob.totp_required
    assert alice.user_id == env.store.snapshot().users["alice"].user_id
    assert env.provider.user_view("ghost") is None
    rendered = repr(alice)
    for secret in (fake_hash(PASSWORD), "GEZDGNBVGY3TQOJQ"):  # hash, RFC seed (base32 prefix)
        assert secret not in rendered
    assert "hash" not in rendered and "secret" not in rendered


def test_user_view_counts_only_unused_recovery_codes(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    enroll(env.store, "alice")
    env.store.mutate(
        lambda f: setattr(f.users["alice"].recovery_codes[0], "used_at", "2026-01-01T00:00:00Z")
    )
    view = env.provider.user_view("alice")
    assert view is not None and view.recovery_codes_remaining == 9


def test_a_corrupt_store_during_login_is_a_503_with_the_cause_for_the_log(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    env.paths.users_file.write_text("{not json")
    with pytest.raises(StoreUnavailableError) as caught:
        login(env)
    assert caught.value.status == 503 and "user store" in (caught.value.cause_for_log or "")
    assert env.hasher.verify_calls == 0


def test_a_user_store_lock_timeout_during_the_login_write_is_a_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env(tmp_path)

    def locked(self: UserStore, fn: Any, **kw: Any) -> Any:
        raise StoreLockTimeoutError("held")

    monkeypatch.setattr(UserStore, "mutate", locked)
    with pytest.raises(StoreUnavailableError):
        login(env)


# --- re-authentication (FR-18) -------------------------------------------------------------------


def reauth(env: ProviderEnv, session: Any, password: str = PASSWORD) -> None:
    run_async(env.provider.verify_current_password(session, password, CLIENT))


def test_a_wrong_current_password_is_counted_and_audited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env(tmp_path)
    session = env.session_for("alice")
    failure_calls = spy(monkeypatch, LockoutStore, "record_failure")
    assert code_of(lambda: reauth(env, session, "wrong")) is ErrorCode.INVALID_CREDENTIALS
    assert len(failure_calls) == 1
    (event,) = env.audit_events("auth.reauth.failure")
    assert event["username"] == "alice" and event["user_id"] == session.user_id


def test_a_correct_current_password_passes_silently(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    reauth(env, env.session_for("alice"))
    assert env.hasher.verify_calls == 1 and env.audit_lines() == []


def test_re_authentication_failures_lock_the_account(tmp_path: Path) -> None:
    env = make_env(tmp_path, threshold=2, address_threshold=50)
    session = env.session_for("alice")
    for _ in range(2):
        code_of(lambda: reauth(env, session, "wrong"))
    with pytest.raises(TooManyAttemptsError):
        reauth(env, session)
    with pytest.raises(TooManyAttemptsError):
        login(env)  # the same lockout counter as the login form


def test_a_stale_session_is_not_authenticated_and_never_hashes(tmp_path: Path) -> None:
    env = make_env(tmp_path, users=("alice", "bob"))
    gone = env.session_for("alice")
    env.store.mutate(lambda f: remove_user(f, "alice"))
    assert code_of(lambda: reauth(env, gone)) is ErrorCode.NOT_AUTHENTICATED
    bumped = env.session_for("bob")
    env.store.mutate(lambda f: bump_epoch(f, "bob", now=env.clock.now_utc()))
    assert code_of(lambda: reauth(env, bumped)) is ErrorCode.NOT_AUTHENTICATED
    assert env.hasher.verify_calls == 0 and env.audit_lines() == []


# --- change_password and logout_everywhere -------------------------------------------------------


def change(env: ProviderEnv, session: Any, current: str = PASSWORD, new: str = NEW_PASSWORD) -> int:
    return run_async(env.provider.change_password(session, current, new, CLIENT))


def test_change_password_success_bumps_the_epoch_and_audits(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    session = env.session_for("alice")
    epoch = change(env, session)
    rec = env.store.snapshot().users["alice"]
    assert epoch == rec.credential_epoch == 2
    assert rec.password_hash == fake_hash(NEW_PASSWORD)
    assert login(env, "alice", NEW_PASSWORD).credential_epoch == 2
    (event,) = env.audit_events("auth.password.changed")
    assert event["outcome"] == "success" and event["details"] == {"source": "web"}
    assert event["username"] == "alice" and event["user_id"] == session.user_id
    assert env.provider.revalidate(session.user_id, session.credential_epoch) is (
        Revalidation.REVOKED
    )


def test_change_password_with_a_wrong_current_password(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    assert code_of(lambda: change(env, env.session_for("alice"), "wrong")) is (
        ErrorCode.INVALID_CREDENTIALS
    )
    assert env.store.snapshot().users["alice"].credential_epoch == 1
    assert env.audit_events("auth.password.changed") == []


@pytest.mark.parametrize(
    ("new", "violation"),
    [("short", "too_short"), ("alice", "too_short"), ("x" * 20 + "\x01", "control")],
)
def test_change_password_policy_violations_are_listed(
    tmp_path: Path, new: str, violation: str
) -> None:
    env = make_env(tmp_path)
    session = env.session_for("alice")
    with pytest.raises(AuthError) as caught:
        change(env, session, new=new)
    assert caught.value.code is ErrorCode.PASSWORD_POLICY
    violations = caught.value.extra["violations"]
    assert isinstance(violations, list) and any(violation in str(v) for v in violations)
    assert env.store.snapshot().users["alice"].credential_epoch == 1
    assert NEW_PASSWORD not in repr(caught.value.extra)


def test_change_password_stale_identity_inside_the_mutate_writes_nothing(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    session = env.session_for("alice")
    original = env.hasher.hash

    async def racing(raw: str) -> str:
        # Another admin action bumps the epoch after the re-auth, before the write.
        env.store.mutate(lambda f: bump_epoch(f, "alice", now=env.clock.now_utc()))
        return await original(raw)

    env.hasher.hash = racing  # type: ignore[method-assign]
    assert code_of(lambda: change(env, session)) is ErrorCode.NOT_AUTHENTICATED
    rec = env.store.snapshot().users["alice"]
    assert rec.password_hash == fake_hash(PASSWORD) and rec.credential_epoch == 2
    assert env.audit_events("auth.password.changed") == []


def test_change_password_for_a_replaced_user_id_writes_nothing(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    session = env.session_for("alice")
    original = env.hasher.hash

    async def swap(raw: str) -> str:
        env.store.mutate(lambda f: remove_user(f, "alice"))
        env.store.mutate(
            lambda f: add_user(
                f, "alice", fake_hash("other"), now=env.clock.now_utc(), entropy=_Entropy()
            )
        )
        return await original(raw)

    env.hasher.hash = swap  # type: ignore[method-assign]
    assert code_of(lambda: change(env, session)) is ErrorCode.NOT_AUTHENTICATED
    assert env.store.snapshot().users["alice"].password_hash == fake_hash("other")


def test_logout_everywhere_bumps_the_epoch_under_the_identity_guard(tmp_path: Path) -> None:
    env = make_env(tmp_path)
    session = env.session_for("alice")
    assert run_async(env.provider.logout_everywhere(session)) == 2
    assert env.provider.revalidate(session.user_id, 1) is Revalidation.REVOKED
    assert code_of(lambda: run_async(env.provider.logout_everywhere(session))) is (
        ErrorCode.NOT_AUTHENTICATED
    )
    assert env.store.snapshot().users["alice"].credential_epoch == 2  # the stale call wrote nothing


# --- misc --------------------------------------------------------------------------------------


def test_the_provider_imports_no_web_framework() -> None:
    import agent_orchestrator.auth.local_provider as module

    source = Path(module.__file__).read_text()
    assert "fastapi" not in source and "starlette" not in source


def test_a_lockout_store_failure_while_keying_a_phantom_is_a_503(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make_env(tmp_path)

    def locked(self: LockoutStore, name: str) -> str:
        raise StoreLockTimeoutError("held")

    monkeypatch.setattr(LockoutStore, "name_digest", locked)
    with pytest.raises(StoreUnavailableError) as caught:
        login(env, "ghost")
    assert "lockout state" in (caught.value.cause_for_log or "")
    assert env.hasher.verify_calls == 0


def test_the_default_dir_comparison_survives_an_unresolvable_path(tmp_path: Path) -> None:
    from agent_orchestrator.auth.local_provider import _differs

    loop = tmp_path / "loop"
    loop.symlink_to(loop)  # a symlink loop: Path.resolve() raises on it
    assert _differs(loop, tmp_path / "elsewhere") is True
    assert _differs(tmp_path, tmp_path) is False
