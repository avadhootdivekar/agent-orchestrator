"""T-kwwJ82: session table, lifecycle, expiry, bounds and ``principal_for`` (HLD 11.10)."""

from __future__ import annotations

import base64
import hashlib
import hmac
from dataclasses import replace
from datetime import UTC, timedelta
from typing import Any

import pytest

from agent_orchestrator.auth import sessions
from agent_orchestrator.auth.constants import (
    MAX_ENROLL_CONFIRM_ATTEMPTS,
    MAX_SECOND_FACTOR_ATTEMPTS,
    MAX_SESSIONS_PER_USER,
    PARTIAL_SESSION_TTL_SECONDS,
    SESSION_PROOF_B64_CHARS,
    SESSION_TOKEN_B64_CHARS,
)
from agent_orchestrator.auth.model import SecondFactor, SessionState
from agent_orchestrator.auth.sessions import (
    InMemorySessionStore,
    IssuedSession,
    SessionManager,
    SessionRecord,
    amr_for,
)
from tests.auth.helpers.core import FakeClock, SeededEntropy
from tests.auth.helpers.sessions import (
    TEST_ABSOLUTE_SECONDS,
    TEST_IDLE_SECONDS,
    make_identity,
    make_manager,
)

FULL = SessionState.FULL
PARTIAL_2F = SessionState.PARTIAL_SECOND_FACTOR
PARTIAL_ENROLL = SessionState.PARTIAL_ENROLL
CLIENT = "203.0.113.9"


def issue_full(manager: SessionManager, **identity: Any) -> IssuedSession:
    return manager.issue(make_identity(**identity), FULL, client_key=CLIENT, auth_method="password")


def issue_partial(
    manager: SessionManager, state: SessionState = PARTIAL_2F, **identity: Any
) -> IssuedSession:
    return manager.issue(
        make_identity(next_state=state, **identity), state, client_key=CLIENT, auth_method=None
    )


def user(n: int) -> str:
    return f"{n:032x}"


class SpyStore(InMemorySessionStore):
    def __init__(self) -> None:
        super().__init__()
        self.puts = 0

    def put(self, record: SessionRecord) -> None:
        self.puts += 1
        super().put(record)


# --- AC-1: token and proof ---


def test_issue_returns_distinct_43_char_base64url_secrets_of_32_bytes() -> None:
    manager, _, _ = make_manager()
    issued = issue_full(manager)
    assert len(issued.token) == SESSION_TOKEN_B64_CHARS
    assert len(issued.proof) == SESSION_PROOF_B64_CHARS
    assert issued.token != issued.proof
    for secret in (issued.token, issued.proof):
        assert len(base64.urlsafe_b64decode(secret + "=")) == 32


def test_record_stores_sha256_digests() -> None:
    manager, _, _ = make_manager()
    issued = issue_full(manager)
    token_raw = base64.urlsafe_b64decode(issued.token + "=")
    proof_raw = base64.urlsafe_b64decode(issued.proof + "=")
    assert issued.record.token_hash == hashlib.sha256(token_raw).digest()
    assert issued.record.proof_hash == hashlib.sha256(proof_raw).digest()


def test_raw_secrets_never_appear_in_repr_or_fields() -> None:
    manager, _, _ = make_manager()
    issued = issue_full(manager)
    rec = issued.record
    rec.pending_totp_secret = b"pending-secret-bytes"
    for text in (repr(rec), repr(issued)):
        assert issued.token not in text
        assert issued.proof not in text
        assert "pending-secret-bytes" not in text
        assert str(rec.token_hash) not in text
        assert str(rec.proof_hash) not in text
    token_raw = base64.urlsafe_b64decode(issued.token + "=")
    proof_raw = base64.urlsafe_b64decode(issued.proof + "=")
    for value in vars(rec).values():
        assert value not in (issued.token, issued.proof, token_raw, proof_raw)


def test_session_id_is_32_hex_and_unique() -> None:
    manager, _, _ = make_manager()
    a, b = issue_full(manager), issue_full(manager)
    assert len(a.record.session_id) == 32
    int(a.record.session_id, 16)
    assert a.record.session_id != b.record.session_id


def test_issue_copies_identity_fields_and_realm() -> None:
    manager, clock, _ = make_manager(realm="ui:abcdef123456")
    identity = make_identity(roles=("admin", "ops"), credential_epoch=9, store_id="st")
    rec = manager.issue(identity, FULL, client_key=CLIENT, auth_method="password").record
    assert (rec.user_id, rec.username, rec.roles) == (identity.user_id, "alice", ("admin", "ops"))
    assert (rec.credential_epoch, rec.store_id, rec.provider) == (9, "st", identity.provider)
    assert (rec.realm, rec.client_key) == ("ui:abcdef123456", CLIENT)
    assert rec.created_at == clock.now_utc()
    assert isinstance(rec.roles, tuple)


def test_issue_is_deterministic_for_a_fixed_seed() -> None:
    first = issue_full(make_manager(seed=3)[0])
    second = issue_full(make_manager(seed=3)[0])
    assert (first.token, first.proof) == (second.token, second.proof)
    assert first.record.session_id == second.record.session_id


def test_issue_rejects_full_without_auth_method() -> None:
    manager, _, store = make_manager()
    with pytest.raises(ValueError, match="auth_method"):
        manager.issue(make_identity(), FULL, client_key=CLIENT, auth_method=None)
    assert store.records() == []


# --- AC-2: lookup ---


def test_lookup_finds_the_issued_session() -> None:
    manager, _, _ = make_manager()
    issued = issue_full(manager)
    found = manager.lookup(issued.token)
    assert found is not None and found.session_id == issued.record.session_id


def test_lookup_ignores_a_record_issued_for_another_realm() -> None:
    """S3 / T-U2ERMo item 6: one store, two realms: a token only works in its own realm."""
    store = InMemorySessionStore()
    dashboard, _, _ = make_manager(store=store, realm="ui:abcdef123456")
    hub, _, _ = make_manager(store=store, realm="hub")
    issued = issue_full(dashboard)
    assert hub.lookup(issued.token) is None  # the hub does not honour a dashboard session
    found = dashboard.lookup(issued.token)  # ... which is left untouched in the store
    assert found is not None and found.realm == "ui:abcdef123456"


def test_a_foreign_realm_record_is_not_deleted_even_when_expired() -> None:
    store = InMemorySessionStore()
    dashboard, clock, _ = make_manager(store=store, realm="ui:abcdef123456")
    hub, _, _ = make_manager(store=store, realm="hub", clock=clock)
    issued = issue_full(dashboard)
    clock.advance(TEST_IDLE_SECONDS + 1)
    assert hub.lookup(issued.token) is None
    assert len(store.records()) == 1  # only its own realm may expire it
    assert dashboard.lookup(issued.token) is None
    assert store.records() == []


def test_lookup_returns_a_copy() -> None:
    manager, _, store = make_manager()
    issued = issue_full(manager)
    found = manager.lookup(issued.token)
    assert found is not None
    found.username = "mallory"
    again = manager.lookup(issued.token)
    assert again is not None and again.username == "alice"


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "",
        "short",
        "A" * (SESSION_TOKEN_B64_CHARS - 1),
        "A" * (SESSION_TOKEN_B64_CHARS + 1),
        "!" * SESSION_TOKEN_B64_CHARS,
        "+" * SESSION_TOKEN_B64_CHARS,  # standard-base64 alphabet, not url-safe
        "A" * (SESSION_TOKEN_B64_CHARS - 1) + "=",
        "é" * SESSION_TOKEN_B64_CHARS,
        "A" * (SESSION_TOKEN_B64_CHARS - 1) + "\n",
    ],
)
def test_lookup_rejects_malformed_values(bad: str | None) -> None:
    manager, _, _ = make_manager()
    issue_full(manager)
    assert manager.lookup(bad) is None


def test_lookup_rejects_non_canonical_encoding() -> None:
    manager, _, _ = make_manager()
    issued = issue_full(manager)
    # The last of 43 chars carries 4 padding bits that must be zero; flip one to forge an
    # alias that decodes to the same 32 bytes.
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    last = alphabet.index(issued.token[-1])
    alias = issued.token[:-1] + alphabet[last ^ 1]
    assert alias != issued.token
    assert manager.lookup(alias) is None


def test_lookup_unknown_token_and_proof_as_cookie_are_none() -> None:
    manager, _, _ = make_manager()
    issued = issue_full(manager)
    other_token = issue_full(make_manager(seed=99)[0]).token
    assert manager.lookup(other_token) is None
    assert manager.lookup(issued.proof) is None


def test_lookup_deletes_an_expired_session() -> None:
    manager, clock, store = make_manager()
    issued = issue_full(manager)
    clock.advance_mono(TEST_IDLE_SECONDS)
    assert manager.lookup(issued.token) is None
    assert store.get(issued.record.token_hash) is None
    assert store.records() == []


# --- AC-3: proof_matches ---


def test_proof_matches_only_the_issued_proof() -> None:
    manager, _, _ = make_manager()
    issued = issue_full(manager)
    other = issue_full(manager)
    assert manager.proof_matches(issued.record, issued.proof) is True
    assert manager.proof_matches(issued.record, other.proof) is False
    assert manager.proof_matches(issued.record, issued.token) is False


@pytest.mark.parametrize(
    "bad",
    [None, "", "x", "A" * (SESSION_PROOF_B64_CHARS + 1), "!" * SESSION_PROOF_B64_CHARS],
)
def test_proof_matches_rejects_malformed_headers(bad: str | None) -> None:
    manager, _, _ = make_manager()
    issued = issue_full(manager)
    assert manager.proof_matches(issued.record, bad) is False


def test_proof_matches_uses_compare_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    manager, _, _ = make_manager()
    issued = issue_full(manager)
    calls: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: Any, b: Any) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(sessions.hmac, "compare_digest", spy)
    assert manager.proof_matches(issued.record, issued.proof) is True
    assert len(calls) == 1
    # Even a wrong-but-well-formed value goes through the constant-time comparison.
    assert manager.proof_matches(issued.record, issued.token) is False
    assert len(calls) == 2


# --- AC-4: expiry on the single monotonic timeline ---


def test_full_session_idle_expiry_boundary() -> None:
    manager, clock, _ = make_manager()
    issued = issue_full(manager)
    clock.advance_mono(TEST_IDLE_SECONDS - 1)
    assert manager.lookup(issued.token) is not None
    clock.advance_mono(1)
    assert manager.lookup(issued.token) is None


def test_touch_extends_idle_expiry() -> None:
    manager, clock, _ = make_manager()
    issued = issue_full(manager)
    clock.advance_mono(TEST_IDLE_SECONDS - 1)
    record = manager.lookup(issued.token)
    assert record is not None
    manager.touch(record)
    clock.advance_mono(TEST_IDLE_SECONDS - 1)
    assert manager.lookup(issued.token) is not None
    clock.advance_mono(1)
    assert manager.lookup(issued.token) is None


def test_absolute_deadline_always_applies() -> None:
    manager, clock, _ = make_manager(idle_seconds=100, absolute_seconds=250)
    issued = issue_full(manager)
    for _ in range(2):  # keep the session active past two idle windows
        clock.advance_mono(99)
        record = manager.lookup(issued.token)
        assert record is not None
        manager.touch(record)
    clock.advance_mono(49)  # t = 247
    assert manager.lookup(issued.token) is not None
    clock.advance_mono(3)  # t = 250 = absolute deadline
    assert manager.lookup(issued.token) is None


@pytest.mark.parametrize("state", [PARTIAL_2F, PARTIAL_ENROLL])
def test_partial_session_expires_at_ttl_and_never_slides(state: SessionState) -> None:
    manager, clock, store = make_manager()
    issued = issue_partial(manager, state)
    clock.advance_mono(PARTIAL_SESSION_TTL_SECONDS - 1)
    record = manager.lookup(issued.token)
    assert record is not None
    manager.touch(record)  # must not extend
    assert store.get(issued.record.token_hash) == issued.record
    clock.advance_mono(1)
    assert manager.lookup(issued.token) is None


def test_partial_session_does_not_idle_out_before_its_ttl() -> None:
    manager, clock, _ = make_manager(idle_seconds=10)
    issued = issue_partial(manager)
    clock.advance_mono(100)  # far past the (FULL-only) idle timeout, inside the partial TTL
    assert manager.lookup(issued.token) is not None


def test_wall_clock_alone_never_expires_a_session() -> None:
    manager, clock, _ = make_manager()
    issued = issue_full(manager)
    clock.advance_wall(10 * TEST_IDLE_SECONDS)
    assert manager.lookup(issued.token) is not None


def test_monotonic_clock_alone_expires_a_session() -> None:
    manager, clock, _ = make_manager()
    issued = issue_full(manager)
    clock.advance_mono(TEST_IDLE_SECONDS)
    assert manager.lookup(issued.token) is None


# --- AC-5: rotation and identity ---


def test_rotation_retires_the_old_token_and_proof() -> None:
    manager, _, store = make_manager()
    old = issue_partial(manager)
    new = manager.issue(
        make_identity(),
        FULL,
        client_key=CLIENT,
        auth_method="password+totp",
        second_factor=SecondFactor.TOTP,
        replacing=old.record,
    )
    assert manager.lookup(old.token) is None
    assert store.get(old.record.token_hash) is None
    assert manager.lookup(new.token) is not None
    assert manager.proof_matches(new.record, old.proof) is False
    assert new.token != old.token and new.proof != old.proof


def test_full_to_full_rotation_keeps_absolute_deadline_and_auth_time() -> None:
    manager, clock, _ = make_manager()
    first = issue_full(manager)
    clock.advance(500)
    second = manager.issue(
        make_identity(),
        FULL,
        client_key=CLIENT,
        auth_method="password",
        replacing=first.record,
        keep_absolute_deadline=True,
    )
    assert second.record.absolute_deadline_mono == first.record.absolute_deadline_mono
    assert second.record.auth_time == first.record.auth_time
    assert second.record.created_mono == clock.monotonic()
    assert manager.lookup(first.token) is None


def test_full_to_full_rotation_without_keep_gets_fresh_deadline() -> None:
    manager, clock, _ = make_manager()
    first = issue_full(manager)
    clock.advance(500)
    second = manager.issue(
        make_identity(),
        FULL,
        client_key=CLIENT,
        auth_method="password",
        replacing=first.record,
    )
    assert second.record.absolute_deadline_mono == clock.monotonic() + TEST_ABSOLUTE_SECONDS
    assert second.record.auth_time == clock.now_utc()


def test_partial_to_full_gets_a_fresh_deadline_even_when_keep_requested() -> None:
    manager, clock, _ = make_manager()
    partial = issue_partial(manager)
    clock.advance(100)
    full = manager.issue(
        make_identity(),
        FULL,
        client_key=CLIENT,
        auth_method="password+totp",
        second_factor="totp",
        replacing=partial.record,
        keep_absolute_deadline=True,
    )
    assert full.record.absolute_deadline_mono == clock.monotonic() + TEST_ABSOLUTE_SECONDS
    assert full.record.auth_time == clock.now_utc()


def test_keep_without_replacing_is_ignored() -> None:
    manager, clock, _ = make_manager()
    issued = manager.issue(
        make_identity(),
        FULL,
        client_key=CLIENT,
        auth_method="password",
        keep_absolute_deadline=True,
    )
    assert issued.record.absolute_deadline_mono == clock.monotonic() + TEST_ABSOLUTE_SECONDS


def test_amr_for_values() -> None:
    assert amr_for("password", "none") == ("pwd",)
    assert amr_for("password+totp", SecondFactor.TOTP) == ("pwd", "otp", "mfa")
    assert amr_for("password+totp", "totp") == ("pwd", "otp", "mfa")
    assert amr_for("password+totp", SecondFactor.RECOVERY_CODE) == ("pwd", "rcv", "mfa")


@pytest.mark.parametrize(
    ("method", "factor"),
    [
        ("password", "totp"),
        ("password", "recovery_code"),
        ("password+totp", "none"),
        ("password+totp", "sms"),
        ("password", "bogus"),
    ],
)
def test_amr_for_rejects_inconsistent_pairs(method: Any, factor: str) -> None:
    with pytest.raises(ValueError, match="inconsistent"):
        amr_for(method, factor)


def test_issue_derives_amr_and_auth_time_for_each_login_kind() -> None:
    manager, clock, _ = make_manager()
    for method, factor, amr in [
        ("password", "none", ("pwd",)),
        ("password+totp", SecondFactor.TOTP, ("pwd", "otp", "mfa")),
        ("password+totp", SecondFactor.RECOVERY_CODE, ("pwd", "rcv", "mfa")),
    ]:
        rec = manager.issue(
            make_identity(),
            FULL,
            client_key=CLIENT,
            auth_method=method,  # type: ignore[arg-type]
            second_factor=factor,
        ).record
        assert rec.amr == amr
        assert rec.auth_method == method
        assert rec.second_factor == str(factor)
        assert rec.auth_time == clock.now_utc()


@pytest.mark.parametrize("state", [PARTIAL_2F, PARTIAL_ENROLL])
def test_partial_sessions_have_no_amr_auth_time_or_method(state: SessionState) -> None:
    manager, _, _ = make_manager()
    rec = manager.issue(
        make_identity(next_state=state),
        state,
        client_key=CLIENT,
        auth_method="password",  # a partial session never records a method
    ).record
    assert rec.amr == ()
    assert rec.auth_time is None
    assert rec.auth_method is None


def test_inconsistent_full_issue_raises_and_changes_nothing() -> None:
    manager, _, store = make_manager()
    with pytest.raises(ValueError, match="inconsistent"):
        manager.issue(
            make_identity(),
            FULL,
            client_key=CLIENT,
            auth_method="password+totp",
            second_factor="none",
        )
    assert store.records() == []


# --- AC-6: every mutation ends in put() ---


def test_each_mutation_does_exactly_one_put() -> None:
    store = SpyStore()
    manager, _, _ = make_manager(store=store)
    full = issue_full(manager).record
    partial = issue_partial(manager).record
    for action, record in [
        (manager.touch, full),
        (manager.record_second_factor_failure, partial),
        (lambda r: manager.set_pending_secret(r, b"s"), partial),
        (manager.record_confirm_failure, partial),
        (manager.clear_pending, partial),
    ]:
        before = store.puts
        action(record)
        assert store.puts == before + 1


def test_mutating_a_get_copy_does_not_change_the_stored_record() -> None:
    store = InMemorySessionStore()
    manager, _, _ = make_manager(store=store)
    issued = issue_full(manager)
    copy = store.get(issued.record.token_hash)
    assert copy is not None
    copy.username = "mallory"
    copy.second_factor_failures = 99
    stored = store.get(issued.record.token_hash)
    assert stored is not None
    assert stored.username == "alice" and stored.second_factor_failures == 0
    snapshot = store.records()
    snapshot[0].username = "mallory"
    assert store.records()[0].username == "alice"


def test_put_stores_a_copy_not_the_callers_object() -> None:
    store = InMemorySessionStore()
    manager, _, _ = make_manager(store=store)
    record = issue_full(manager).record
    record.username = "changed-after-put"
    stored = store.get(record.token_hash)
    assert stored is not None and stored.username == "alice"


def test_mutation_is_persisted_for_later_lookups() -> None:
    manager, clock, _ = make_manager()
    issued = issue_full(manager)
    clock.advance_mono(60)
    record = manager.lookup(issued.token)
    assert record is not None
    manager.touch(record)
    again = manager.lookup(issued.token)
    assert again is not None and again.last_activity_mono == clock.monotonic()


def test_mutating_a_destroyed_session_does_not_resurrect_it() -> None:
    store = SpyStore()
    manager, _, _ = make_manager(store=store)
    issued = issue_full(manager)
    stale = issued.record
    manager.destroy(stale)
    puts = store.puts
    manager.touch(stale)
    manager.set_pending_secret(stale, b"s")
    manager.clear_pending(stale)
    assert manager.lookup(issued.token) is None
    assert store.records() == []
    assert store.puts == puts


def test_touch_on_a_partial_session_is_a_noop() -> None:
    store = SpyStore()
    manager, clock, _ = make_manager(store=store)
    issued = issue_partial(manager)
    puts = store.puts
    clock.advance_mono(10)
    manager.touch(issued.record)
    assert store.puts == puts


# --- AC-7: attempt counters ---


def test_second_factor_failures_count_down_and_destroy_at_zero() -> None:
    manager, _, store = make_manager()
    issued = issue_partial(manager)
    assert MAX_SECOND_FACTOR_ATTEMPTS == 5
    record = manager.lookup(issued.token)
    assert record is not None
    results = []
    for _ in range(MAX_SECOND_FACTOR_ATTEMPTS):
        record = manager.lookup(issued.token) or record
        results.append(manager.record_second_factor_failure(record))
    assert results == [4, 3, 2, 1, 0]
    assert manager.lookup(issued.token) is None
    assert store.records() == []


def test_second_factor_failure_count_is_persisted() -> None:
    manager, _, _ = make_manager()
    issued = issue_partial(manager)
    manager.record_second_factor_failure(issued.record)
    again = manager.lookup(issued.token)
    assert again is not None and again.second_factor_failures == 1


def test_confirm_failures_clear_the_pending_secret_at_the_limit() -> None:
    manager, _, _ = make_manager()
    issued = issue_partial(manager, PARTIAL_ENROLL)
    record = issued.record
    manager.set_pending_secret(record, b"totp-secret")
    remaining = []
    for _ in range(MAX_ENROLL_CONFIRM_ATTEMPTS):
        current = manager.lookup(issued.token)
        assert current is not None
        remaining.append(manager.record_confirm_failure(current))
        if len(remaining) < MAX_ENROLL_CONFIRM_ATTEMPTS:
            assert manager.lookup(issued.token).pending_totp_secret == b"totp-secret"  # type: ignore[union-attr]
    assert remaining == [4, 3, 2, 1, 0]
    final = manager.lookup(issued.token)
    assert final is not None  # the session itself survives; only the pending secret is dropped
    assert final.pending_totp_secret is None


def test_set_pending_secret_resets_confirm_failures() -> None:
    manager, _, _ = make_manager()
    issued = issue_partial(manager, PARTIAL_ENROLL)
    manager.set_pending_secret(issued.record, b"one")
    manager.record_confirm_failure(issued.record)
    manager.record_confirm_failure(issued.record)
    assert issued.record.pending_confirm_failures == 2
    manager.set_pending_secret(issued.record, b"two")
    stored = manager.lookup(issued.token)
    assert stored is not None
    assert (stored.pending_totp_secret, stored.pending_confirm_failures) == (b"two", 0)


def test_clear_pending_drops_secret_and_counter() -> None:
    manager, _, _ = make_manager()
    issued = issue_partial(manager, PARTIAL_ENROLL)
    manager.set_pending_secret(issued.record, b"x")
    manager.record_confirm_failure(issued.record)
    manager.clear_pending(issued.record)
    stored = manager.lookup(issued.token)
    assert stored is not None
    assert (stored.pending_totp_secret, stored.pending_confirm_failures) == (None, 0)


def test_counters_follow_patched_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sessions, "MAX_SECOND_FACTOR_ATTEMPTS", 2)
    manager, _, _ = make_manager()
    issued = issue_partial(manager)
    assert manager.record_second_factor_failure(issued.record) == 1
    assert manager.record_second_factor_failure(issued.record) == 0
    assert manager.lookup(issued.token) is None


# --- AC-8: bounds ---


def test_per_user_cap_evicts_that_users_oldest_session() -> None:
    manager, clock, _ = make_manager()
    other = issue_full(manager, user_id=user(2))
    issued = []
    for _ in range(MAX_SESSIONS_PER_USER):
        clock.advance(1)
        issued.append(issue_full(manager))
    assert all(manager.lookup(i.token) for i in issued)
    clock.advance(1)
    newest = issue_full(manager)  # the 33rd for this user_id
    assert manager.lookup(issued[0].token) is None
    assert all(manager.lookup(i.token) is not None for i in issued[1:])
    assert manager.lookup(newest.token) is not None
    assert manager.lookup(other.token) is not None  # another user is untouched


def test_per_user_eviction_is_by_last_activity_not_creation() -> None:
    manager, clock, _ = make_manager()
    first = issue_full(manager)
    clock.advance(1)
    rest = [issue_full(manager) for _ in range(MAX_SESSIONS_PER_USER - 1)]
    clock.advance(1)
    record = manager.lookup(first.token)
    assert record is not None
    manager.touch(record)  # `first` is now the most recently active
    clock.advance(1)
    issue_full(manager)
    assert manager.lookup(first.token) is not None
    assert manager.lookup(rest[0].token) is None


def test_partial_cap_evicts_the_oldest_partial(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sessions, "MAX_PARTIAL_SESSIONS", 3)
    manager, clock, store = make_manager()
    full = issue_full(manager, user_id=user(1))
    partials = []
    for n in range(3):
        clock.advance(1)
        partials.append(issue_partial(manager, user_id=user(10 + n)))
    clock.advance(1)
    newest = issue_partial(manager, user_id=user(20))
    assert manager.lookup(partials[0].token) is None
    assert all(manager.lookup(p.token) is not None for p in partials[1:])
    assert manager.lookup(newest.token) is not None
    assert manager.lookup(full.token) is not None  # FULL sessions are not partial-capped
    assert sum(1 for r in store.records() if r.state is not FULL) == 3


def test_partial_cap_never_exceeded_over_many_issues(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sessions, "MAX_PARTIAL_SESSIONS", 4)
    manager, clock, store = make_manager()
    for n in range(20):
        clock.advance(1)
        issue_partial(manager, user_id=user(n))
        assert sum(1 for r in store.records() if r.state is not FULL) <= 4


def test_total_cap_evicts_the_oldest_last_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sessions, "MAX_SESSIONS_TOTAL", 4)
    manager, clock, store = make_manager()
    items = []
    for n in range(4):
        clock.advance(1)
        items.append(issue_full(manager, user_id=user(n)))
    clock.advance(1)
    record = manager.lookup(items[0].token)
    assert record is not None
    manager.touch(record)  # items[0] is now the freshest; items[1] is the stalest
    clock.advance(1)
    newest = issue_full(manager, user_id=user(9))
    assert manager.lookup(items[1].token) is None
    assert all(manager.lookup(i.token) is not None for i in (items[0], items[2], items[3]))
    assert manager.lookup(newest.token) is not None
    assert len(store.records()) == 4


def test_expired_sessions_are_garbage_collected_on_issue() -> None:
    manager, clock, store = make_manager()
    stale = [issue_full(manager, user_id=user(n)) for n in range(3)]
    clock.advance_mono(TEST_IDLE_SECONDS)
    issue_full(manager, user_id=user(50))
    remaining = store.records()
    assert [r.user_id for r in remaining] == [user(50)]
    assert all(store.get(s.record.token_hash) is None for s in stale)


def test_expired_sessions_do_not_count_toward_the_per_user_cap() -> None:
    manager, clock, _ = make_manager()
    for _ in range(MAX_SESSIONS_PER_USER):
        issue_full(manager)
    clock.advance_mono(TEST_IDLE_SECONDS)
    fresh = issue_full(manager)
    assert manager.lookup(fresh.token) is not None


# --- AC-9: destroy_user_sessions ---


def test_destroy_user_sessions_except_one_returns_the_count() -> None:
    manager, _, _ = make_manager()
    keep = issue_full(manager)
    gone = [issue_full(manager), issue_partial(manager), issue_full(manager)]
    bystander = issue_full(manager, user_id=user(2))
    count = manager.destroy_user_sessions(
        keep.record.user_id, except_session_id=keep.record.session_id
    )
    assert count == 3
    assert manager.lookup(keep.token) is not None
    assert manager.lookup(bystander.token) is not None
    assert all(manager.lookup(g.token) is None for g in gone)


def test_destroy_user_sessions_without_exception_removes_all() -> None:
    manager, _, store = make_manager()
    issue_full(manager)
    issue_full(manager)
    assert manager.destroy_user_sessions(make_identity().user_id) == 2
    assert store.records() == []


def test_destroy_user_sessions_is_keyed_by_user_id_not_username() -> None:
    manager, _, _ = make_manager()
    old_alice = issue_full(manager, user_id=user(1), username="alice")
    new_alice = issue_full(manager, user_id=user(2), username="alice")  # removed, re-added
    assert manager.destroy_user_sessions(user(1)) == 1
    assert manager.lookup(old_alice.token) is None
    assert manager.lookup(new_alice.token) is not None


def test_destroy_user_sessions_with_nothing_to_do_returns_zero() -> None:
    manager, _, _ = make_manager()
    issue_full(manager)
    assert manager.destroy_user_sessions(user(77)) == 0


def test_destroy_removes_the_session() -> None:
    manager, _, _ = make_manager()
    issued = issue_full(manager)
    manager.destroy(issued.record)
    manager.destroy(issued.record)  # idempotent
    assert manager.lookup(issued.token) is None


# --- AC-10: principal_for ---


def test_principal_for_carries_the_records_values() -> None:
    manager, clock, _ = make_manager(realm="hub")
    record = issue_full(manager, roles=("admin", "ops")).record
    p = manager.principal_for(record)
    assert p.username == "alice"
    assert p.auth_method == "password"
    assert p.roles == list(record.roles) == ["admin", "ops"]
    assert isinstance(p.roles, list)
    assert (p.user_id, p.realm, p.session_id) == (record.user_id, "hub", record.session_id)
    assert p.amr == ("pwd",) and isinstance(p.amr, tuple)
    assert p.auth_time == clock.now_utc() and p.auth_time.tzinfo is UTC
    assert p.provider == record.provider


def test_principal_for_empty_roles_is_an_empty_list() -> None:
    manager, _, _ = make_manager()
    p = manager.principal_for(issue_full(manager).record)
    assert p.roles == []
    assert isinstance(p.roles, list)


def test_principal_for_totp_login_carries_mfa_amr() -> None:
    manager, _, _ = make_manager()
    rec = manager.issue(
        make_identity(),
        FULL,
        client_key=CLIENT,
        auth_method="password+totp",
        second_factor=SecondFactor.RECOVERY_CODE,
    ).record
    p = manager.principal_for(rec)
    assert p.auth_method == "password+totp"
    assert p.amr == ("pwd", "rcv", "mfa")


def test_principal_roles_never_alias_across_calls_or_the_record() -> None:
    manager, _, store = make_manager()
    issued = issue_full(manager, roles=("admin",))
    record = issued.record
    p1 = manager.principal_for(record)
    p2 = manager.principal_for(record)
    assert p1.roles is not p2.roles
    p1.roles.append("x")
    assert p2.roles == ["admin"]
    assert manager.principal_for(record).roles == ["admin"]
    assert record.roles == ("admin",)
    stored = store.get(record.token_hash)
    assert stored is not None and stored.roles == ("admin",)
    assert manager.principal_for(stored).roles == ["admin"]


def test_principal_for_does_not_alias_a_mutated_lookup_copy() -> None:
    manager, _, _ = make_manager()
    issued = issue_full(manager, roles=("admin",))
    p = manager.principal_for(manager.lookup(issued.token))  # type: ignore[arg-type]
    p.roles.clear()
    again = manager.lookup(issued.token)
    assert again is not None and again.roles == ("admin",)


def test_principal_for_a_partial_record_raises() -> None:
    manager, _, _ = make_manager()
    for state in (PARTIAL_2F, PARTIAL_ENROLL):
        with pytest.raises(ValueError, match="FULL"):
            manager.principal_for(issue_partial(manager, state).record)


def test_principal_for_a_full_record_missing_method_or_time_raises() -> None:
    manager, _, _ = make_manager()
    record = issue_full(manager).record
    with pytest.raises(ValueError, match="auth_method"):
        manager.principal_for(replace(record, auth_method=None))
    with pytest.raises(ValueError, match="auth_time"):
        manager.principal_for(replace(record, auth_time=None))


# --- AC-11: times() ---


def test_times_for_a_full_session() -> None:
    manager, clock, _ = make_manager()
    issued = issue_full(manager)
    clock.advance(100)
    times = manager.times(issued.record)
    now = clock.now_utc()
    assert times.idle_timeout_seconds == TEST_IDLE_SECONDS
    assert times.idle_expires_at == now + timedelta(seconds=TEST_IDLE_SECONDS - 100)
    assert times.absolute_expires_at == now + timedelta(seconds=TEST_ABSOLUTE_SECONDS - 100)
    assert times.idle_expires_at.tzinfo is not None
    assert times.absolute_expires_at.utcoffset() == timedelta(0)


def test_times_idle_is_capped_by_the_absolute_deadline() -> None:
    manager, clock, _ = make_manager(idle_seconds=1000, absolute_seconds=600)
    issued = issue_full(manager)
    times = manager.times(issued.record)
    assert (
        times.idle_expires_at
        == times.absolute_expires_at
        == clock.now_utc() + timedelta(seconds=600)
    )


def test_times_follow_touch() -> None:
    manager, clock, _ = make_manager()
    issued = issue_full(manager)
    clock.advance(300)
    manager.touch(issued.record)
    times = manager.times(issued.record)
    assert times.idle_expires_at == clock.now_utc() + timedelta(seconds=TEST_IDLE_SECONDS)


@pytest.mark.parametrize("state", [PARTIAL_2F, PARTIAL_ENROLL])
def test_times_for_a_partial_session(state: SessionState) -> None:
    manager, clock, _ = make_manager()
    issued = issue_partial(manager, state)
    clock.advance(40)
    times = manager.times(issued.record)
    expected = clock.now_utc() + timedelta(seconds=PARTIAL_SESSION_TTL_SECONDS - 40)
    assert times.idle_expires_at == times.absolute_expires_at == expected
    assert times.idle_timeout_seconds == PARTIAL_SESSION_TTL_SECONDS


def test_times_use_the_wall_clock_only_as_a_label() -> None:
    manager, clock, _ = make_manager()
    issued = issue_full(manager)
    clock.advance_wall(5000)  # wall jump: projections shift with it, expiry does not
    assert manager.lookup(issued.token) is not None
    assert manager.times(issued.record).idle_expires_at == clock.now_utc() + timedelta(
        seconds=TEST_IDLE_SECONDS
    )


# --- store seam ---


def test_in_memory_store_basic_semantics() -> None:
    store = InMemorySessionStore()
    manager, _, _ = make_manager(store=store)
    a, b = issue_full(manager), issue_full(manager)
    assert [r.session_id for r in store.records()] == [a.record.session_id, b.record.session_id]
    assert store.get(b"\x00" * 32) is None
    store.delete(b"\x00" * 32)  # deleting an unknown hash is a no-op
    assert len(store.records()) == 2


def test_manager_uses_the_injected_entropy_and_clock() -> None:
    clock = FakeClock()
    manager = SessionManager(
        InMemorySessionStore(),
        realm="hub",
        idle_seconds=60,
        absolute_seconds=120,
        clock=clock,
        entropy=SeededEntropy(1),
    )
    rec = issue_full(manager).record
    assert rec.created_mono == clock.monotonic()
    assert rec.absolute_deadline_mono == clock.monotonic() + 120
