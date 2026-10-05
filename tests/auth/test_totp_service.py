"""``LocalTotpService``: second-factor verify, enrollment, disable, regenerate (T-yfrfxv).

A real service over real file-backed stores, the instant ``FastFakeHasher``, a fixed clock and
codes computed from the known RFC seed at the fake time. Spies count store/lockout/guard calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest

from agent_orchestrator.auth.constants import (
    ENROLLMENT_TOKEN_TTL_SECONDS,
    RECOVERY_CODE_COUNT,
    TOTP_PERIOD_SECONDS,
)
from agent_orchestrator.auth.errors import (
    AuthError,
    BusyError,
    ErrorCode,
    StoreUnavailableError,
    TooManyAttemptsError,
)
from agent_orchestrator.auth.guard import AttemptGuard
from agent_orchestrator.auth.lockouts import LockoutStore
from agent_orchestrator.auth.model import SecondFactor, SessionState, TotpPolicy
from agent_orchestrator.auth.provider import AuthProvider, ClientInfo, Revalidation
from agent_orchestrator.auth.recovery import generate_recovery_codes, new_recovery_records
from agent_orchestrator.auth.runtime import Realm, build_auth_runtime
from agent_orchestrator.auth.store import (
    TotpEnrollment,
    UserStore,
    bump_epoch,
    replace_recovery_codes,
)
from agent_orchestrator.auth.totp import b32encode_secret
from agent_orchestrator.auth.totp_service import (
    EnrollmentChallenge,
    EnrollmentResult,
    LocalTotpService,
    SecondFactorResult,
    classify_code,
)

from .helpers.core import FakeClock, SeededEntropy, run_async
from .helpers.crypto import FastFakeHasher
from .helpers.provider import CLIENT, PASSWORD, fake_hash, make_settings
from .helpers.store import RFC_KEY, RFC_SECRET_B32, totp_code
from .helpers.totp_service import TotpEnv, code_of, error_of, make_totp_env, spy

LOOPBACK_HTTP = ClientInfo("127.0.0.1", True, False)
REMOTE_HTTPS = ClientInfo("203.0.113.9", False, True)
REMOTE_HTTP = ClientInfo("203.0.113.9", False, False)
WRONG_CODE = "000000"
WRONG_PASSWORD = "SENTINEL-wrong-password"
MIXED_RECOVERY_INPUT = "OIL0-1234-ABCD-EFGH"  # I, L, O normalize to 1, 1, 0
MIXED_RECOVERY_TYPED = "oil0 1234 abcd efgh"  # lower case, spaces


def _verify(t: TotpEnv, session: Any, **kw: Any) -> SecondFactorResult:
    kw.setdefault("client", CLIENT)
    return run_async(t.svc.verify_second_factor(session, **kw))


def _partial_session(t: TotpEnv) -> Any:
    return t.session(state=SessionState.PARTIAL_SECOND_FACTOR)


@pytest.fixture()
def enrolled(tmp_path: Path) -> TotpEnv:
    """alice enrolled with the RFC seed (epoch 2); a threshold high enough to keep counting."""
    t = make_totp_env(tmp_path, threshold=10)
    t.enrolled()
    return t


# -- classify_code -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("123456", (SecondFactor.TOTP, "123456")),
        ("123 456", (SecondFactor.TOTP, "123456")),
        (" 000000 ", (SecondFactor.TOTP, "000000")),
        ("abcd-efgh-jkmn-pqrs", (SecondFactor.RECOVERY_CODE, "ABCDEFGHJKMNPQRS")),
        ("oil0 1234 abcd efgh", (SecondFactor.RECOVERY_CODE, "0110" + "1234ABCDEFGH")),
        ("1234567890123456", (SecondFactor.RECOVERY_CODE, "1234567890123456")),
        ("12345", None),
        ("1234567", None),
        ("abcdef", None),
        ("", None),
        ("abcd-efgh-jkmn-pqr", None),
        ("abcd-efgh-jkmn-pqrU", None),  # U is not Crockford
        ("١٢٣٤٥٦", None),  # non-ASCII digits are not TOTP digits
    ],
)
def test_classify_code(raw: str, expected: tuple[SecondFactor, str] | None) -> None:
    assert classify_code(raw) == expected


# -- verify_second_factor: TOTP (AC 1) -----------------------------------------------------------


def test_a_valid_totp_code_succeeds_and_marks_the_login(
    enrolled: TotpEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _partial_session(enrolled)
    resets = spy(monkeypatch, LockoutStore, "reset")
    # Some earlier failures that the success must clear.
    with pytest.raises(AuthError):
        _verify(enrolled, session, code=WRONG_CODE)
    assert enrolled.env.lockouts.state(_key(session)).failures == 1
    result = _verify(enrolled, session, code=enrolled.code())
    assert result == SecondFactorResult(SecondFactor.TOTP, None)
    step = int(enrolled.now_unix() // TOTP_PERIOD_SECONDS)
    rec = enrolled.user()
    assert rec.totp.last_used_step == step
    assert rec.last_login_at is not None
    assert resets == [(session.user_id,)]
    assert enrolled.env.lockouts.state(_key(session)).failures == 0


def _key(session: Any) -> Any:
    from agent_orchestrator.auth.lockouts import LockoutKey

    return LockoutKey(user_id=session.user_id)


def test_a_replayed_code_is_rejected_with_reason_replayed_and_counted_once(
    enrolled: TotpEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _partial_session(enrolled)
    code = enrolled.code()
    _verify(enrolled, session, code=code)
    failures = spy(monkeypatch, LockoutStore, "record_failure")
    err = error_of(enrolled.svc.verify_second_factor(session, code=code, client=CLIENT))
    assert err.code is ErrorCode.INVALID_CODE
    assert err.extra == {"reason": "replayed"}
    assert len(failures) == 1


def test_a_code_from_an_earlier_step_after_a_later_one_is_a_replay(enrolled: TotpEnv) -> None:
    session = _partial_session(enrolled)
    earlier = enrolled.code(offset_steps=-1)
    _verify(enrolled, session, code=enrolled.code())  # the current step
    err = error_of(enrolled.svc.verify_second_factor(session, code=earlier, client=CLIENT))
    assert err.extra == {"reason": "replayed"}


def test_a_wrong_code_is_invalid_and_counted(
    enrolled: TotpEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    failures = spy(monkeypatch, LockoutStore, "record_failure")
    err = error_of(
        enrolled.svc.verify_second_factor(
            _partial_session(enrolled), code=WRONG_CODE, client=CLIENT
        )
    )
    assert err.code is ErrorCode.INVALID_CODE
    assert err.extra == {"reason": "invalid"}
    assert len(failures) == 1
    events = enrolled.env.audit_events("auth.second_factor.failure")
    assert [e["details"] for e in events] == [{"reason": "invalid"}]


@pytest.mark.parametrize("bad", ["12345", "abcdef", "", "1234567", "١٢٣٤٥٦"])
def test_a_malformed_code_is_counted_without_touching_the_store(
    enrolled: TotpEnv, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    session = _partial_session(enrolled)
    mutates = spy(monkeypatch, UserStore, "mutate")
    failures = spy(monkeypatch, LockoutStore, "record_failure")
    err = error_of(enrolled.svc.verify_second_factor(session, code=bad, client=CLIENT))
    assert err.code is ErrorCode.INVALID_CODE and err.extra == {"reason": "invalid"}
    assert mutates == []
    assert len(failures) == 1


def test_neither_code_nor_recovery_code_is_a_counted_invalid_attempt(
    enrolled: TotpEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    failures = spy(monkeypatch, LockoutStore, "record_failure")
    err = error_of(enrolled.svc.verify_second_factor(_partial_session(enrolled), client=CLIENT))
    assert err.code is ErrorCode.INVALID_CODE
    assert len(failures) == 1


def test_a_stale_session_is_not_authenticated_and_never_counted(
    enrolled: TotpEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _partial_session(enrolled)
    now = enrolled.env.clock.now_utc()
    enrolled.env.store.mutate(lambda f: bump_epoch(f, "alice", now=now))
    failures = spy(monkeypatch, LockoutStore, "record_failure")
    for kw in ({"code": enrolled.code()}, {"recovery_code": "0000-0000-0000-0000"}):
        err = error_of(enrolled.svc.verify_second_factor(session, client=CLIENT, **kw))
        assert err.code is ErrorCode.NOT_AUTHENTICATED
    assert failures == []
    assert enrolled.env.audit_events("auth.second_factor.failure") == []


def test_a_user_removed_under_the_session_is_not_authenticated(enrolled: TotpEnv) -> None:
    session = _partial_session(enrolled)
    from agent_orchestrator.auth.store import remove_user

    enrolled.env.store.mutate(lambda f: remove_user(f, "alice"))
    assert code_of(enrolled.svc.verify_second_factor(session, code="123456", client=CLIENT)) is (
        ErrorCode.NOT_AUTHENTICATED
    )


def test_a_session_whose_user_has_no_totp_is_not_authenticated(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)  # alice never enrolled
    session = _partial_session(t)
    assert code_of(t.svc.verify_second_factor(session, code="123456", client=CLIENT)) is (
        ErrorCode.NOT_AUTHENTICATED
    )
    assert code_of(
        t.svc.verify_second_factor(session, recovery_code="0000-0000-0000-0000", client=CLIENT)
    ) is (ErrorCode.NOT_AUTHENTICATED)


def test_a_locked_out_account_never_reaches_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = make_totp_env(tmp_path, threshold=2)
    t.enrolled()
    session = _partial_session(t)
    for _ in range(2):
        assert code_of(t.svc.verify_second_factor(session, code=WRONG_CODE, client=CLIENT)) is (
            ErrorCode.INVALID_CODE
        )
    mutates = spy(monkeypatch, UserStore, "mutate")
    with pytest.raises(TooManyAttemptsError):
        run_async(t.svc.verify_second_factor(session, code=t.code(), client=CLIENT))
    assert mutates == []


def test_a_lockout_is_cleared_by_a_successful_second_factor(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path, threshold=3)
    t.enrolled()
    session = _partial_session(t)
    for _ in range(2):
        code_of(t.svc.verify_second_factor(session, code=WRONG_CODE, client=CLIENT))
    _verify(t, session, code=t.code())
    assert t.env.lockouts.state(_key(session)).failures == 0


# -- verify_second_factor: recovery codes (AC 2) --------------------------------------------------


def test_a_recovery_code_is_accepted_once(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path, threshold=10)
    codes = t.enrolled()
    session = _partial_session(t)
    result = _verify(t, session, recovery_code=codes[0])
    assert result == SecondFactorResult(SecondFactor.RECOVERY_CODE, RECOVERY_CODE_COUNT - 1)
    assert t.user().last_login_at is not None
    err = error_of(t.svc.verify_second_factor(session, recovery_code=codes[0], client=CLIENT))
    assert err.code is ErrorCode.INVALID_CODE and err.extra == {"reason": "invalid"}
    # Another code still works and the count keeps falling.
    assert _verify(t, session, recovery_code=codes[1]).recovery_codes_remaining == 8


def test_recovery_codes_are_normalized(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    codes = t.enrolled()
    now = t.env.clock.now_utc()
    records = new_recovery_records([MIXED_RECOVERY_INPUT, *codes[1:]], SeededEntropy(3))
    t.env.store.mutate(lambda f: replace_recovery_codes(f, "alice", records, now=now))
    session = _partial_session(t)  # the replace bumped the epoch: a fresh session
    result = _verify(t, session, recovery_code=MIXED_RECOVERY_TYPED)
    assert result.method is SecondFactor.RECOVERY_CODE
    # The hyphenated upper-case display form is the same code and is now spent.
    assert code_of(
        t.svc.verify_second_factor(session, recovery_code=MIXED_RECOVERY_INPUT, client=CLIENT)
    ) is (ErrorCode.INVALID_CODE)


def test_a_malformed_recovery_code_is_counted_without_touching_the_store(
    enrolled: TotpEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    mutates = spy(monkeypatch, UserStore, "mutate")
    failures = spy(monkeypatch, LockoutStore, "record_failure")
    err = error_of(
        enrolled.svc.verify_second_factor(
            _partial_session(enrolled), recovery_code="not a code", client=CLIENT
        )
    )
    assert err.code is ErrorCode.INVALID_CODE
    assert mutates == [] and len(failures) == 1


# -- begin_enrollment (AC 3, 4, 5) ----------------------------------------------------------------


def test_begin_over_remote_http_is_refused_before_any_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = make_totp_env(tmp_path)
    mutates = spy(monkeypatch, UserStore, "mutate")
    attempts = spy(monkeypatch, AttemptGuard, "attempt")
    err = error_of(t.svc.begin_enrollment(t.session(), REMOTE_HTTP, current_password=PASSWORD))
    assert err.code is ErrorCode.INSECURE_TRANSPORT
    assert mutates == [] and attempts == []
    assert t.env.hasher.verify_calls == 0


def test_begin_is_allowed_over_loopback_http_and_remote_https(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    for client in (LOOPBACK_HTTP, REMOTE_HTTPS):
        challenge = run_async(
            t.svc.begin_enrollment(t.session(), client, current_password=PASSWORD)
        )
        assert isinstance(challenge, EnrollmentChallenge)


def test_begin_under_policy_off_is_refused(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path, totp=TotpPolicy.OFF)
    assert code_of(
        t.svc.begin_enrollment(t.session(), LOOPBACK_HTTP, current_password=PASSWORD)
    ) is (ErrorCode.TOTP_DISABLED_BY_POLICY)


def test_begin_for_an_enrolled_user_is_refused(enrolled: TotpEnv) -> None:
    assert code_of(
        enrolled.svc.begin_enrollment(enrolled.session(), LOOPBACK_HTTP, current_password=PASSWORD)
    ) is (ErrorCode.TOTP_ALREADY_ENROLLED)


def test_begin_with_a_stale_session_is_not_authenticated(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    session = t.session()
    now = t.env.clock.now_utc()
    t.env.store.mutate(lambda f: bump_epoch(f, "alice", now=now))
    assert code_of(t.svc.begin_enrollment(session, LOOPBACK_HTTP, current_password=PASSWORD)) is (
        ErrorCode.NOT_AUTHENTICATED
    )


def test_a_full_session_without_a_password_is_an_invalid_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = make_totp_env(tmp_path)
    failures = spy(monkeypatch, LockoutStore, "record_failure")
    assert code_of(t.svc.begin_enrollment(t.session(), LOOPBACK_HTTP)) is ErrorCode.INVALID_REQUEST
    assert failures == []


def test_a_full_session_with_a_wrong_password_is_counted_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = make_totp_env(tmp_path)
    failures = spy(monkeypatch, LockoutStore, "record_failure")
    err = code_of(
        t.svc.begin_enrollment(t.session(), LOOPBACK_HTTP, current_password=WRONG_PASSWORD)
    )
    assert err is ErrorCode.INVALID_CREDENTIALS
    assert len(failures) == 1
    assert len(t.env.audit_events("auth.reauth.failure")) == 1


def test_a_full_session_with_the_right_password_gets_a_challenge(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    challenge = run_async(
        t.svc.begin_enrollment(t.session(), LOOPBACK_HTTP, current_password=PASSWORD)
    )
    assert len(challenge.secret) == 20
    assert len(challenge.secret_b32) == 32
    assert challenge.secret_b32 == b32encode_secret(challenge.secret)
    assert challenge.issuer == "ao@test" and challenge.account == "alice"
    assert challenge.otpauth_uri.isascii()
    assert challenge.otpauth_uri.startswith("otpauth://totp/")
    assert quote("ao@test", safe="") in challenge.otpauth_uri
    assert "/ao%40test:alice?" in challenge.otpauth_uri
    assert f"secret={challenge.secret_b32}" in challenge.otpauth_uri
    assert (challenge.algorithm, challenge.digits, challenge.period) == ("SHA1", 6, 30)
    assert t.user().totp is None  # begin writes nothing for a FULL session


def test_two_begins_make_two_different_secrets(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    secrets = {
        run_async(
            t.svc.begin_enrollment(t.session(), LOOPBACK_HTTP, current_password=PASSWORD)
        ).secret
        for _ in range(2)
    }
    assert len(secrets) == 2


def _begin_with_token(t: TotpEnv, token: str | None, session: Any = None) -> EnrollmentChallenge:
    session = session or t.session(state=SessionState.PARTIAL_ENROLL)
    return run_async(t.svc.begin_enrollment(session, LOOPBACK_HTTP, enrollment_token=token))


def _assert_token_rejected(t: TotpEnv, token: str | None, session: Any = None) -> None:
    session = session or t.session(state=SessionState.PARTIAL_ENROLL)
    err = error_of(t.svc.begin_enrollment(session, LOOPBACK_HTTP, enrollment_token=token))
    assert err.code is ErrorCode.INVALID_CODE
    assert err.extra == {"reason": "invalid"}


@pytest.mark.parametrize("case", ["missing", "empty", "malformed", "wrong", "expired", "used"])
def test_a_bad_enrollment_token_is_one_counted_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    t = make_totp_env(tmp_path, threshold=10)
    token = t.issue_token()
    session = t.session(state=SessionState.PARTIAL_ENROLL)
    submitted: str | None
    if case == "missing":
        submitted = None
    elif case == "empty":
        submitted = ""
    elif case == "malformed":
        submitted = "short"
    elif case == "wrong":
        submitted = generate_recovery_codes(SeededEntropy(99), count=1)[0]
        assert submitted != token
    elif case == "expired":
        t.env.clock.advance(ENROLLMENT_TOKEN_TTL_SECONDS + 1)
        submitted = token
    else:
        _begin_with_token(t, token, session)  # consumed here
        submitted = token
    failures = spy(monkeypatch, LockoutStore, "record_failure")
    _assert_token_rejected(t, submitted, session)
    assert len(failures) == 1
    events = t.env.audit_events("auth.enrollment_token.failure")
    assert [e["details"] for e in events] == [{"reason": "invalid"}]


def test_a_missing_or_empty_token_writes_nothing_to_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = make_totp_env(tmp_path)
    t.issue_token()
    before = t.users_bytes()
    mutates = spy(monkeypatch, UserStore, "mutate")
    for token in (None, "", "short"):
        _assert_token_rejected(t, token)
    assert mutates == []
    assert t.users_bytes() == before


def test_a_wrong_token_leaves_the_real_token_usable(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path, threshold=10)
    token = t.issue_token()
    _assert_token_rejected(t, generate_recovery_codes(SeededEntropy(5), count=1)[0])
    assert isinstance(_begin_with_token(t, token), EnrollmentChallenge)


def test_a_valid_token_gives_a_challenge_and_is_consumed_by_begin(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    token = t.issue_token()
    assert t.user().enrollment_token is not None
    session = t.session(state=SessionState.PARTIAL_ENROLL)
    challenge = _begin_with_token(t, token, session)
    assert len(challenge.secret) == 20
    assert t.user().enrollment_token is None
    _assert_token_rejected(t, token, session)  # a second use fails
    assert t.env.lockouts.state(_key(session)).failures == 1


def test_a_lowercase_unseparated_token_is_normalized(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    token = t.issue_token()
    typed = token.replace("-", "").lower()
    assert isinstance(_begin_with_token(t, typed), EnrollmentChallenge)


def test_a_token_does_not_reset_the_lockout(tmp_path: Path) -> None:
    """Only a completed enrollment (confirm) clears the account's failures (S22)."""
    t = make_totp_env(tmp_path, threshold=10)
    token = t.issue_token()
    session = t.session(state=SessionState.PARTIAL_ENROLL)
    _assert_token_rejected(t, None, session)
    _begin_with_token(t, token, session)
    assert t.env.lockouts.state(_key(session)).failures == 1


def test_begin_on_a_partial_second_factor_session_is_denied(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    session = t.session(state=SessionState.PARTIAL_SECOND_FACTOR)
    assert code_of(t.svc.begin_enrollment(session, LOOPBACK_HTTP, current_password=PASSWORD)) is (
        ErrorCode.SECOND_FACTOR_REQUIRED
    )


def test_begin_surfaces_a_busy_hasher_without_a_lockout_entry(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path, hasher=FastFakeHasher(raise_busy=True))
    session = t.session()
    with pytest.raises(BusyError):
        run_async(t.svc.begin_enrollment(session, LOOPBACK_HTTP, current_password=PASSWORD))
    assert t.env.lockouts.state(_key(session)).failures == 0
    assert t.env.audit_events("auth.reauth.failure") == []


# -- confirm_enrollment (AC 6) --------------------------------------------------------------------


def _confirm(t: TotpEnv, session: Any, code: str, client: ClientInfo = LOOPBACK_HTTP) -> Any:
    return run_async(t.svc.confirm_enrollment(session, code, client))


def _pending(t: TotpEnv, state: SessionState = SessionState.FULL) -> Any:
    session = t.session(state=state)
    session.pending_totp_secret = RFC_KEY
    return session


def test_confirm_over_remote_http_is_refused(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    assert code_of(t.svc.confirm_enrollment(_pending(t), t.code(), REMOTE_HTTP)) is (
        ErrorCode.INSECURE_TRANSPORT
    )


def test_confirm_under_policy_off_is_refused(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path, totp=TotpPolicy.OFF)
    assert code_of(t.svc.confirm_enrollment(_pending(t), t.code(), LOOPBACK_HTTP)) is (
        ErrorCode.TOTP_DISABLED_BY_POLICY
    )


def test_confirm_without_a_pending_secret_is_an_invalid_request(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    assert code_of(t.svc.confirm_enrollment(t.session(), t.code(), LOOPBACK_HTTP)) is (
        ErrorCode.INVALID_REQUEST
    )


@pytest.mark.parametrize("bad", [WRONG_CODE, "12345", "abcdef", ""])
def test_a_wrong_confirm_code_changes_nothing(tmp_path: Path, bad: str) -> None:
    t = make_totp_env(tmp_path)
    session = _pending(t)
    before = t.users_bytes()
    err = error_of(t.svc.confirm_enrollment(session, bad, LOOPBACK_HTTP))
    assert err.code is ErrorCode.INVALID_CODE and err.extra == {"reason": "invalid"}
    assert t.users_bytes() == before
    assert t.env.audit_events("auth.totp.enrolled") == []
    # The service does not count: the route owns the pending-attempt counter.
    assert t.env.lockouts.state(_key(session)).failures == 0
    assert session.pending_confirm_failures == 0 and session.pending_totp_secret == RFC_KEY


def test_a_correct_confirm_enrolls_and_returns_ten_codes(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    session = _pending(t)
    token = t.issue_token()  # a stray token is cleared by enrollment
    assert token
    result = _confirm(t, session, t.code())
    assert isinstance(result, EnrollmentResult)
    assert result.credential_epoch == session.credential_epoch + 1
    assert len(result.recovery_codes) == RECOVERY_CODE_COUNT == len(set(result.recovery_codes))
    rec = t.user()
    assert rec.totp.secret_b32 == RFC_SECRET_B32
    assert rec.totp.last_used_step == int(t.now_unix() // TOTP_PERIOD_SECONDS)
    assert rec.credential_epoch == result.credential_epoch
    assert rec.enrollment_token is None
    assert len(rec.recovery_codes) == RECOVERY_CODE_COUNT
    assert rec.last_login_at is None  # a FULL session already logged in
    events = t.env.audit_events("auth.totp.enrolled")
    assert len(events) == 1 and events[0]["details"] == {"source": "web"}
    assert events[0]["username"] == "alice"


def test_the_code_just_used_to_confirm_cannot_be_replayed_at_login(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    code = t.code()
    _confirm(t, _pending(t), code)
    session = _partial_session(t)  # fresh session at the new epoch
    err = error_of(t.svc.verify_second_factor(session, code=code, client=CLIENT))
    assert err.extra == {"reason": "replayed"}


def test_a_code_one_step_off_is_accepted_by_the_window(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    result = _confirm(t, _pending(t), t.code(offset_steps=-1))
    assert result.credential_epoch == 2
    assert t.user().totp.last_used_step == int(t.now_unix() // 30) - 1


def test_a_confirmed_recovery_code_logs_in_afterwards(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    result = _confirm(t, _pending(t), t.code())
    session = _partial_session(t)
    verified = _verify(t, session, recovery_code=result.recovery_codes[3])
    assert verified.recovery_codes_remaining == RECOVERY_CODE_COUNT - 1


def test_a_forced_enrollment_confirm_completes_the_login(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = make_totp_env(tmp_path, threshold=10)
    token = t.issue_token()
    session = t.session(state=SessionState.PARTIAL_ENROLL)
    challenge = _begin_with_token(t, token, session)
    session.pending_totp_secret = challenge.secret
    # A failed attempt that confirm must clear.
    _assert_token_rejected(t, None, session)
    assert t.env.lockouts.state(_key(session)).failures == 1
    resets = spy(monkeypatch, LockoutStore, "reset")
    result = _confirm(t, session, totp_code(t.now_unix(), challenge.secret))
    assert resets == [(session.user_id,)]
    assert t.env.lockouts.state(_key(session)).failures == 0
    rec = t.user()
    assert rec.last_login_at is not None
    assert rec.credential_epoch == result.credential_epoch
    assert rec.totp is not None and rec.enrollment_token is None


def test_a_full_session_confirm_does_not_touch_the_lockout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = make_totp_env(tmp_path)
    resets = spy(monkeypatch, LockoutStore, "reset")
    _confirm(t, _pending(t), t.code())
    assert resets == []


def test_confirm_with_a_stale_epoch_is_not_authenticated(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    session = _pending(t)
    now = t.env.clock.now_utc()
    t.env.store.mutate(lambda f: bump_epoch(f, "alice", now=now))
    before = t.users_bytes()
    assert code_of(t.svc.confirm_enrollment(session, t.code(), LOOPBACK_HTTP)) is (
        ErrorCode.NOT_AUTHENTICATED
    )
    assert t.users_bytes() == before
    assert t.env.audit_events("auth.totp.enrolled") == []


def test_a_concurrent_enrollment_is_already_enrolled(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    session = _pending(t)

    def enroll_elsewhere(f: Any) -> None:  # same identity and epoch: only totp changes
        f.users["alice"].totp = TotpEnrollment(
            secret_b32="A" * 32, enrolled_at="2026-01-01T00:00:00Z", last_used_step=0
        )

    t.env.store.mutate(enroll_elsewhere)
    assert code_of(t.svc.confirm_enrollment(session, t.code(), LOOPBACK_HTTP)) is (
        ErrorCode.TOTP_ALREADY_ENROLLED
    )
    assert t.env.audit_events("auth.totp.enrolled") == []
    assert t.user().totp.secret_b32 == "A" * 32  # the winner is untouched


# -- disable_totp (AC 7) --------------------------------------------------------------------------


def _disable(t: TotpEnv, session: Any, password: str, code: str) -> int:
    return run_async(t.svc.disable_totp(session, password, code, CLIENT))


def test_disable_is_refused_under_policy_required_before_any_password_work(
    tmp_path: Path,
) -> None:
    t = make_totp_env(tmp_path, totp=TotpPolicy.REQUIRED)
    t.enrolled()
    assert code_of(t.svc.disable_totp(t.session(), PASSWORD, t.code(), CLIENT)) is (
        ErrorCode.TOTP_REQUIRED
    )
    assert t.env.hasher.verify_calls == 0
    assert t.user().totp is not None


def test_disable_is_refused_for_a_totp_required_user(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    t.enrolled()

    def require(f: Any) -> None:
        f.users["alice"].totp_required = True

    t.env.store.mutate(require)
    assert code_of(t.svc.disable_totp(t.session(), WRONG_PASSWORD, WRONG_CODE, CLIENT)) is (
        ErrorCode.TOTP_REQUIRED
    )
    assert t.env.hasher.verify_calls == 0


def test_disable_is_refused_for_a_required_user_under_policy_off(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path, totp=TotpPolicy.OFF)
    t.enrolled()

    def require(f: Any) -> None:
        f.users["alice"].totp_required = True

    t.env.store.mutate(require)
    assert code_of(t.svc.disable_totp(t.session(), PASSWORD, t.code(), CLIENT)) is (
        ErrorCode.TOTP_REQUIRED
    )


def test_disable_when_not_enrolled(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    assert code_of(t.svc.disable_totp(t.session(), PASSWORD, "123456", CLIENT)) is (
        ErrorCode.TOTP_NOT_ENROLLED
    )
    assert t.env.hasher.verify_calls == 0


def test_disable_with_a_wrong_password_burns_no_code(
    enrolled: TotpEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    mutates = spy(monkeypatch, UserStore, "mutate")
    assert code_of(
        enrolled.svc.disable_totp(enrolled.session(), WRONG_PASSWORD, enrolled.code(), CLIENT)
    ) is (ErrorCode.INVALID_CREDENTIALS)
    assert mutates == []
    assert enrolled.user().totp.last_used_step == 1  # untouched


@pytest.mark.parametrize("bad", [WRONG_CODE, "nonsense", "", "0000-0000-0000-0000"])
def test_disable_with_a_wrong_code(
    enrolled: TotpEnv, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    failures = spy(monkeypatch, LockoutStore, "record_failure")
    err = error_of(enrolled.svc.disable_totp(enrolled.session(), PASSWORD, bad, CLIENT))
    assert err.code is ErrorCode.INVALID_CODE and err.extra == {"reason": "invalid"}
    assert len(failures) == 1
    assert enrolled.user().totp is not None


def test_disable_with_a_replayed_code(enrolled: TotpEnv) -> None:
    session = enrolled.session()
    code = enrolled.code()
    _verify(enrolled, _partial_session(enrolled), code=code)  # spends the step
    err = error_of(enrolled.svc.disable_totp(session, PASSWORD, code, CLIENT))
    assert err.code is ErrorCode.INVALID_CODE and err.extra == {"reason": "replayed"}


def test_disable_with_a_totp_code(enrolled: TotpEnv) -> None:
    session = enrolled.session()
    epoch = _disable(enrolled, session, PASSWORD, enrolled.code())
    rec = enrolled.user()
    assert epoch == session.credential_epoch + 1 == rec.credential_epoch
    assert rec.totp is None and rec.recovery_codes == []
    assert rec.totp_required is False
    events = enrolled.env.audit_events("auth.totp.disabled")
    assert len(events) == 1 and events[0]["details"] == {"source": "web"}


def test_disable_with_a_recovery_code(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    codes = t.enrolled()
    session = t.session()
    epoch = _disable(t, session, PASSWORD, codes[2])
    rec = t.user()
    assert epoch == rec.credential_epoch == session.credential_epoch + 1
    assert rec.totp is None and rec.recovery_codes == []
    assert len(t.env.audit_events("auth.totp.disabled")) == 1


def test_disable_does_not_reset_the_lockout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = make_totp_env(tmp_path, threshold=10)
    t.enrolled()
    resets = spy(monkeypatch, LockoutStore, "reset")
    _disable(t, t.session(), PASSWORD, t.code())
    assert resets == []


def test_disable_with_a_stale_session(enrolled: TotpEnv) -> None:
    session = enrolled.session()
    now = enrolled.env.clock.now_utc()
    enrolled.env.store.mutate(lambda f: bump_epoch(f, "alice", now=now))
    assert code_of(enrolled.svc.disable_totp(session, PASSWORD, enrolled.code(), CLIENT)) is (
        ErrorCode.NOT_AUTHENTICATED
    )
    assert enrolled.user().totp is not None


def test_a_disable_racing_a_credential_change_is_stale_and_not_counted(
    enrolled: TotpEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The epoch moves after the guarded code check: the identity guard refuses the write."""
    session = enrolled.session()
    original = AttemptGuard.attempt

    async def attempt_then_bump(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = await original(self, *args, **kwargs)
        if kwargs.get("failure_event") == "auth.second_factor.failure":
            now = enrolled.env.clock.now_utc()
            enrolled.env.store.mutate(lambda f: bump_epoch(f, "alice", now=now))
        return result

    monkeypatch.setattr(AttemptGuard, "attempt", attempt_then_bump)
    assert code_of(enrolled.svc.disable_totp(session, PASSWORD, enrolled.code(), CLIENT)) is (
        ErrorCode.NOT_AUTHENTICATED
    )
    assert enrolled.user().totp is not None


def test_the_not_enrolled_race_is_mapped_for_disable_and_regenerate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = make_totp_env(tmp_path)  # alice is not enrolled, but the pre-check is made to pass
    session = t.session()
    rec = t.user().model_copy(
        update={
            "totp": TotpEnrollment(secret_b32=RFC_SECRET_B32, enrolled_at="x", last_used_step=0)
        }
    )
    monkeypatch.setattr(t.svc, "_current_record", lambda s: rec)
    assert code_of(t.svc.disable_totp(session, PASSWORD, "123456", CLIENT)) is (
        ErrorCode.TOTP_NOT_ENROLLED
    )
    assert code_of(t.svc.regenerate_recovery_codes(session, PASSWORD, "123456", LOOPBACK_HTTP)) is (
        ErrorCode.TOTP_NOT_ENROLLED
    )


# -- regenerate_recovery_codes (AC 8) -------------------------------------------------------------


def test_regenerate_over_remote_http_is_refused(
    enrolled: TotpEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = spy(monkeypatch, AttemptGuard, "attempt")
    assert code_of(
        enrolled.svc.regenerate_recovery_codes(
            enrolled.session(), PASSWORD, enrolled.code(), REMOTE_HTTP
        )
    ) is (ErrorCode.INSECURE_TRANSPORT)
    assert attempts == []


def test_regenerate_when_not_enrolled(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    assert code_of(
        t.svc.regenerate_recovery_codes(t.session(), PASSWORD, "123456", LOOPBACK_HTTP)
    ) is (ErrorCode.TOTP_NOT_ENROLLED)


def test_regenerate_with_a_wrong_password_or_code(enrolled: TotpEnv) -> None:
    session = enrolled.session()
    assert code_of(
        enrolled.svc.regenerate_recovery_codes(
            session, WRONG_PASSWORD, enrolled.code(), LOOPBACK_HTTP
        )
    ) is (ErrorCode.INVALID_CREDENTIALS)
    assert code_of(
        enrolled.svc.regenerate_recovery_codes(session, PASSWORD, WRONG_CODE, LOOPBACK_HTTP)
    ) is (ErrorCode.INVALID_CODE)
    assert enrolled.user().credential_epoch == session.credential_epoch


def test_regenerate_replaces_every_code(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path, threshold=30, address_threshold=100)
    old_codes = t.enrolled()
    session = t.session()
    epoch, new_codes = run_async(
        t.svc.regenerate_recovery_codes(session, PASSWORD, t.code(), LOOPBACK_HTTP)
    )
    assert epoch == session.credential_epoch + 1 == t.user().credential_epoch
    assert len(new_codes) == RECOVERY_CODE_COUNT
    assert not set(new_codes) & set(old_codes)
    events = t.env.audit_events("auth.recovery_codes.regenerated")
    assert len(events) == 1 and events[0]["details"] == {"source": "web"}
    fresh = _partial_session(t)
    for old in old_codes:
        assert code_of(t.svc.verify_second_factor(fresh, recovery_code=old, client=CLIENT)) is (
            ErrorCode.INVALID_CODE
        )
    assert _verify(t, fresh, recovery_code=new_codes[0]).recovery_codes_remaining == 9
    # Regeneration keeps TOTP itself enrolled.
    assert t.user().totp is not None


def test_regenerate_with_a_recovery_code_spends_that_code_then_replaces_all(
    tmp_path: Path,
) -> None:
    t = make_totp_env(tmp_path)
    old_codes = t.enrolled()
    _, new_codes = run_async(
        t.svc.regenerate_recovery_codes(t.session(), PASSWORD, old_codes[0], LOOPBACK_HTTP)
    )
    assert len(new_codes) == RECOVERY_CODE_COUNT
    assert all(c.used_at is None for c in t.user().recovery_codes)


# -- sticky TOTP (AC 9) ---------------------------------------------------------------------------


def test_an_enrolled_user_completes_the_second_factor_under_policy_off(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path, totp=TotpPolicy.OFF)
    codes = t.enrolled()
    session = _partial_session(t)
    assert _verify(t, session, code=t.code()).method is SecondFactor.TOTP
    assert _verify(t, session, recovery_code=codes[0]).method is SecondFactor.RECOVERY_CODE
    assert code_of(
        t.svc.begin_enrollment(t.session(), LOOPBACK_HTTP, current_password=PASSWORD)
    ) is (ErrorCode.TOTP_DISABLED_BY_POLICY)


def test_an_enrolled_user_may_disable_under_policy_off_when_not_required(tmp_path: Path) -> None:
    """Sticky enrollment does not make TOTP required: ``off`` + no per-user flag = optional."""
    t = make_totp_env(tmp_path, totp=TotpPolicy.OFF)
    t.enrolled()
    assert _disable(t, t.session(), PASSWORD, t.code()) == 3


# -- store failures, secrets, wiring --------------------------------------------------------------


def test_a_store_lock_timeout_is_a_503_with_the_cause_for_the_log(
    enrolled: TotpEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_orchestrator.auth.errors import StoreLockTimeoutError

    def timeout(self: Any, fn: Any, **kw: Any) -> Any:
        raise StoreLockTimeoutError("busy")

    monkeypatch.setattr(UserStore, "mutate", timeout)
    err = error_of(
        enrolled.svc.verify_second_factor(_partial_session(enrolled), code="123456", client=CLIENT)
    )
    assert isinstance(err, StoreUnavailableError)
    assert err.cause_for_log is not None and "busy" in err.cause_for_log
    assert enrolled.env.audit_events("auth.second_factor.failure") == []


def test_a_corrupt_store_is_a_503_on_the_session_check(enrolled: TotpEnv) -> None:
    session = enrolled.session()
    enrolled.env.paths.users_file.write_text("{not json", encoding="utf-8")
    with pytest.raises(StoreUnavailableError):
        run_async(enrolled.svc.begin_enrollment(session, LOOPBACK_HTTP, current_password=PASSWORD))


def test_result_reprs_hold_no_secret(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path)
    challenge = run_async(
        t.svc.begin_enrollment(t.session(), LOOPBACK_HTTP, current_password=PASSWORD)
    )
    session = t.session()
    session.pending_totp_secret = challenge.secret
    result = run_async(
        t.svc.confirm_enrollment(session, totp_code(t.now_unix(), challenge.secret), LOOPBACK_HTTP)
    )
    sensitive = [challenge.secret_b32, challenge.secret.hex(), *result.recovery_codes]
    sensitive += [c.replace("-", "") for c in result.recovery_codes]
    for obj in (challenge, result, SecondFactorResult(SecondFactor.TOTP, 3)):
        text = repr(obj)
        assert not any(s in text for s in sensitive)
        assert "otpauth" not in text
    assert repr(challenge.secret) not in repr(challenge)
    assert "alice" in repr(challenge)  # the non-secret parts remain useful


def test_the_audit_log_never_sees_a_code_token_secret_or_password(tmp_path: Path) -> None:
    t = make_totp_env(tmp_path, threshold=20)
    codes = t.enrolled()
    token_env = make_totp_env(tmp_path / "second", threshold=20)
    token = token_env.issue_token()
    sensitive: list[str] = [WRONG_PASSWORD, WRONG_CODE, "not a code", token, *codes]
    sensitive += [t.code(), RFC_SECRET_B32]
    partial = _partial_session(t)
    for kw in ({"code": WRONG_CODE}, {"recovery_code": "not a code"}):
        code_of(t.svc.verify_second_factor(partial, client=CLIENT, **kw))
    _verify(t, partial, code=t.code())
    _verify(t, partial, recovery_code=codes[0])
    code_of(t.svc.begin_enrollment(t.session(), LOOPBACK_HTTP, current_password=WRONG_PASSWORD))
    code_of(t.svc.disable_totp(t.session(), PASSWORD, WRONG_CODE, CLIENT))
    _, new_codes = run_async(
        t.svc.regenerate_recovery_codes(t.session(), PASSWORD, codes[1], LOOPBACK_HTTP)
    )
    sensitive += new_codes
    # The enrollment-token and confirm paths, on the second env.
    sess = token_env.session(state=SessionState.PARTIAL_ENROLL)
    _assert_token_rejected(token_env, token[::-1], sess)
    challenge = _begin_with_token(token_env, token, sess)
    sess.pending_totp_secret = challenge.secret
    confirmed = run_async(
        token_env.svc.confirm_enrollment(
            sess, totp_code(token_env.now_unix(), challenge.secret), LOOPBACK_HTTP
        )
    )
    sensitive += [challenge.secret_b32, challenge.otpauth_uri, *confirmed.recovery_codes]
    for env in (t, token_env):
        text = env.env.paths.audit_file.read_text(encoding="utf-8")
        assert text
        for secret in sensitive:
            assert secret not in text
            assert secret.replace("-", "") not in text


def test_build_auth_runtime_wires_the_service_over_the_shared_graph(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    runtime = build_auth_runtime(
        settings,
        Realm("ui", 8765, tmp_path),
        clock=FakeClock(),
        entropy=SeededEntropy(1),
        hasher=FastFakeHasher(),
    )
    assert isinstance(runtime.totp, LocalTotpService)
    assert runtime.totp._guard is runtime.provider.guard
    assert runtime.totp._store is runtime.store
    assert runtime.totp._lockouts is runtime.lockouts
    assert runtime.totp._audit is runtime.audit
    assert runtime.totp._clock is runtime.clock
    assert runtime.totp._provider is runtime.provider


def test_the_wired_service_works_end_to_end_on_the_runtime_stores(tmp_path: Path) -> None:
    from agent_orchestrator.auth.store import add_user

    clock = FakeClock()
    runtime = build_auth_runtime(
        make_settings(tmp_path),
        Realm("hub", 8770),
        clock=clock,
        entropy=SeededEntropy(1),
        hasher=FastFakeHasher(),
    )
    runtime.store.mutate(
        lambda f: add_user(
            f, "bob", fake_hash(PASSWORD), now=clock.now_utc(), entropy=SeededEntropy(2)
        ),
        create=True,
    )
    assert runtime.totp is not None
    from agent_orchestrator.auth.provider import VerifiedIdentity

    rec = runtime.store.snapshot().users["bob"]
    identity = VerifiedIdentity(
        rec.user_id, "bob", (), rec.credential_epoch, "s", SessionState.FULL
    )
    session = runtime.sessions.issue(
        identity, SessionState.FULL, client_key="c", auth_method="password"
    )
    challenge = run_async(
        runtime.totp.begin_enrollment(session.record, LOOPBACK_HTTP, current_password=PASSWORD)
    )
    assert len(challenge.secret) == 20


class _OtherProvider(AuthProvider):
    provider_id = "other"

    def check_ready(self) -> None:
        return None

    def revalidate(self, user_id: str, credential_epoch: int) -> Revalidation:
        return Revalidation.VALID

    def user_view(self, username: str) -> None:
        return None


def test_a_provider_without_a_local_second_factor_gets_no_service(tmp_path: Path) -> None:
    runtime = build_auth_runtime(
        make_settings(tmp_path),
        Realm("hub", 8770),
        clock=FakeClock(),
        entropy=SeededEntropy(1),
        provider=_OtherProvider(),
    )
    assert runtime.totp is None


@pytest.mark.parametrize("flow", ["disable", "regenerate"])
def test_totp_vanishing_after_the_code_check_is_not_enrolled(
    enrolled: TotpEnv, monkeypatch: pytest.MonkeyPatch, flow: str
) -> None:
    """TOTP removed (epoch unchanged) between the guarded code check and the write."""
    session = enrolled.session()
    original = AttemptGuard.attempt

    async def attempt_then_unenroll(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = await original(self, *args, **kwargs)
        if kwargs.get("failure_event") == "auth.second_factor.failure":

            def drop(f: Any) -> None:
                f.users["alice"].totp = None

            enrolled.env.store.mutate(drop)
        return result

    monkeypatch.setattr(AttemptGuard, "attempt", attempt_then_unenroll)
    if flow == "disable":
        call = enrolled.svc.disable_totp(session, PASSWORD, enrolled.code(), CLIENT)
    else:
        call = enrolled.svc.regenerate_recovery_codes(
            session, PASSWORD, enrolled.code(), LOOPBACK_HTTP
        )
    assert code_of(call) is ErrorCode.TOTP_NOT_ENROLLED
