"""T-rpKCjP: the core auth routes against HLD 2.2 / 2.4 (E1, E2, E8, E9, E10; AC-14, AC-22, AC-35).

The real ``create_app`` over a real ``AuthRuntime`` (``tests/auth/helpers/real_routes.py``):
every status and error code the contract lists for these routes is produced by at least one
case, JSON key sets and types are pinned, and the rotation / replay / proof rules of D25 hold.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from agent_orchestrator.auth.lockouts import LockoutKey
from agent_orchestrator.auth.model import TotpPolicy
from agent_orchestrator.auth.provider import Revalidation
from agent_orchestrator.auth.store import remove_user
from tests.auth.helpers import real_routes
from tests.auth.helpers.crypto import FastFakeHasher
from tests.auth.helpers.provider import PASSWORD
from tests.auth.helpers.real_routes import (
    DASH_PORT,
    DashFactory,
    enroll_totp_directly,
    json_body,
)

dash_factory = real_routes.dash_factory  # the pytest fixture (an assignment: no F811 clash)

LOGIN = "/api/auth/login"
LOGOUT = "/api/auth/logout"
STATUS = "/api/auth/status"
KEEPALIVE = "/api/auth/keepalive"
PASSWORD_PATH = "/api/auth/password"

USER_KEYS = [
    "username",
    "auth_method",
    "roles",
    "totp_enrolled",
    "recovery_codes_remaining",
    "totp_required",
    "can_enroll_totp",
    "can_disable_totp",
]
STATUS_KEYS = [
    "enabled",
    "state",
    "user",
    "pending_username",
    "second_factors",
    "enrollment_token_required",
    "policy",
    "session",
    "transport",
]
ERROR_KEYS = {"detail", "code"}
PROOF_RE = re.compile(r"^[A-Za-z0-9_-]{43}$")
TIMESTAMP_RE = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
NEW_PASSWORD = "an entirely new passphrase"


def assert_error(response: Any, status: int, code: str, *extra_keys: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = json_body(response)
    assert set(body) == ERROR_KEYS | set(extra_keys)
    assert body["code"] == code
    assert isinstance(body["detail"], str) and body["detail"]
    return body


# --- E2: login states -----------------------------------------------------------------------


def test_login_password_only_is_authenticated(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    response = dash.login()
    assert response.status_code == 200
    body = json_body(response)
    assert list(body) == ["state", "user", "session_proof"]
    assert body["state"] == "authenticated"
    assert list(body["user"]) == USER_KEYS
    assert PROOF_RE.fullmatch(body["session_proof"])
    assert body["user"] == {
        "username": "alice",
        "auth_method": "password",
        "roles": [],
        "totp_enrolled": False,
        "recovery_codes_remaining": None,
        "totp_required": False,
        "can_enroll_totp": True,
        "can_disable_totp": False,
    }
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 1 and cookies[0].startswith(f"ao_sid_{DASH_PORT}=")
    assert dash.protected().status_code == 200


def test_login_enrolled_user_needs_a_second_factor(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    dash.runtime.store.mutate(lambda f: enroll_totp_directly(f, "alice"))
    response = dash.login()
    assert response.status_code == 200
    body = json_body(response)
    assert list(body) == ["state", "second_factors", "session_proof"]
    assert body["state"] == "second_factor_required"
    assert body["second_factors"] == ["totp", "recovery_code"]
    assert dash.audit_events("auth.login.success") == []
    assert len(dash.audit_events("auth.login.second_factor_pending")) == 1
    assert dash.protected().status_code == 401  # a partial session opens nothing


def test_login_required_policy_and_not_enrolled_needs_enrollment(
    dash_factory: DashFactory,
) -> None:
    dash = dash_factory(totp=TotpPolicy.REQUIRED)
    body = json_body(dash.login())
    assert list(body) == ["state", "enrollment_token_required", "session_proof"]
    assert body["state"] == "enrollment_required"
    assert body["enrollment_token_required"] is True


def test_login_policy_off_with_totp_required_user_is_403(dash_factory: DashFactory) -> None:
    dash = dash_factory(totp=TotpPolicy.OFF)
    dash.runtime.store.mutate(lambda f: setattr(f.users["alice"], "totp_required", True))
    assert_error(dash.login(), 403, "totp_required")


# --- Contract: every status / code of E1, E2, E8, E9, E10 -----------------------------------


def test_login_invalid_request_variants(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    for body in (
        {"username": "alice"},  # missing password
        {"password": PASSWORD},  # missing username
        {"username": "", "password": "x"},  # empty username
        {"username": 5, "password": "x"},  # wrong type
        {"username": "a" * 65, "password": "x"},  # username over 64
        {"username": "alice", "password": "p" * 1025},  # password over 1024
    ):
        assert_error(dash.post(LOGIN, body), 400, "invalid_request")
    # the boundary values themselves are accepted by validation (then a plain 401)
    assert_error(
        dash.post(LOGIN, {"username": "a" * 64, "password": "p" * 1024}), 401, "invalid_credentials"
    )
    assert_error(
        dash.post(LOGIN, {"username": "alice", "password": ""}), 401, "invalid_credentials"
    )


def test_login_429_carries_retry_after(dash_factory: DashFactory) -> None:
    dash = dash_factory(lockout_threshold=2)
    for _ in range(2):
        assert_error(dash.login(password="wrong"), 401, "invalid_credentials")
    response = dash.login(password="wrong")
    body = assert_error(response, 429, "too_many_attempts", "retry_after_seconds")
    assert isinstance(body["retry_after_seconds"], int) and body["retry_after_seconds"] > 0
    assert response.headers["retry-after"] == str(body["retry_after_seconds"])


def test_login_503_busy_when_the_hash_queue_is_full(dash_factory: DashFactory) -> None:
    dash = dash_factory(hasher=FastFakeHasher(raise_busy=True))
    response = dash.login()
    body = assert_error(response, 503, "busy", "retry_after_seconds")
    assert body["retry_after_seconds"] == 1
    assert response.headers["retry-after"] == "1"


def test_login_503_store_unavailable_when_the_store_is_corrupt(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    dash.runtime.paths.users_file.write_text("{not json", encoding="utf-8")
    response = dash.login()
    assert_error(response, 503, "store_unavailable")
    assert response.headers["retry-after"] == "5"


def test_unauthenticated_calls_to_authenticated_routes_are_401(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    for path in (KEEPALIVE, PASSWORD_PATH):
        response = dash.post(path, {"current_password": "a", "new_password": "b"}, proof=None)
        assert_error(response, 401, "not_authenticated")
        assert response.headers["www-authenticate"].startswith("AO-Session realm=")


def test_password_change_error_contract(dash_factory: DashFactory) -> None:
    dash = dash_factory(lockout_threshold=3)
    dash.login()
    # 400 invalid_request: a missing field
    assert_error(dash.post(PASSWORD_PATH, {"current_password": PASSWORD}), 400, "invalid_request")
    # 400 password_policy: too short, with the violation list
    body = assert_error(
        dash.post(PASSWORD_PATH, {"current_password": PASSWORD, "new_password": "short"}),
        400,
        "password_policy",
        "violations",
    )
    assert body["violations"] == ["too_short"]
    # 401 invalid_credentials: a wrong current password (no challenge header on this one)
    response = dash.post(PASSWORD_PATH, {"current_password": "wrong", "new_password": NEW_PASSWORD})
    assert_error(response, 401, "invalid_credentials")
    assert "www-authenticate" not in response.headers
    # ... until the lockout (threshold 3) turns it into 429
    for _ in range(2):
        dash.post(PASSWORD_PATH, {"current_password": "wrong", "new_password": NEW_PASSWORD})
    response = dash.post(PASSWORD_PATH, {"current_password": "wrong", "new_password": NEW_PASSWORD})
    assert_error(response, 429, "too_many_attempts", "retry_after_seconds")
    assert response.headers["retry-after"]


def test_password_change_wrong_current_counts_a_lockout_failure(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    dash.login()
    user_id = dash.runtime.store.snapshot().users["alice"].user_id
    before = dash.runtime.lockouts.state(LockoutKey(user_id=user_id)).failures
    response = dash.post(PASSWORD_PATH, {"current_password": "wrong", "new_password": NEW_PASSWORD})
    assert_error(response, 401, "invalid_credentials")
    assert dash.runtime.lockouts.state(LockoutKey(user_id=user_id)).failures == before + 1


def test_error_503_on_password_change_when_the_store_is_unreadable(
    dash_factory: DashFactory,
) -> None:
    dash = dash_factory()
    dash.login()
    dash.runtime.paths.users_file.write_text("{not json", encoding="utf-8")
    # the middleware revalidates first: an unreadable store is a 503 that KEEPS the session
    response = dash.post(
        PASSWORD_PATH, {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )
    assert_error(response, 503, "store_unavailable")
    assert response.headers["retry-after"] == "5"


def test_keepalive_returns_the_session_times(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    dash.login()
    dash.clock.advance(100)
    response = dash.post(KEEPALIVE)
    assert response.status_code == 200
    body = json_body(response)
    assert list(body) == ["idle_expires_at", "absolute_expires_at", "idle_timeout_seconds"]
    assert body["idle_timeout_seconds"] == 1800
    assert TIMESTAMP_RE.fullmatch(body["idle_expires_at"])
    # the middleware slid the idle deadline to "now + 1800"; the absolute one did not move
    assert body["idle_expires_at"] == "2026-01-01T00:31:40Z"
    assert body["absolute_expires_at"] == "2026-01-01T12:00:00Z"


@pytest.mark.parametrize(
    "raw",
    [b"[1, 2]", b'"text"', b"{not json", b"\xff\xfe", b"[" * 10_000, b"[" * 5000 + b"]" * 5000],
)
def test_malformed_bodies_are_400(dash_factory: DashFactory, raw: bytes) -> None:
    dash = dash_factory()
    response = dash.post(LOGIN, raw=raw)
    assert_error(response, 400, "invalid_request")


def test_empty_login_body_is_400_but_empty_logout_body_is_fine(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    assert_error(dash.post(LOGIN, raw=b""), 400, "invalid_request")
    response = dash.post(LOGOUT, raw=b"")
    assert response.status_code == 200 and json_body(response) == {"state": "anonymous"}


def test_unknown_key_is_named_but_its_value_is_never_echoed(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    sentinel = "SENTINEL-VALUE-9f3a"
    body = assert_error(
        dash.post(LOGIN, {"username": "alice", "password": "x", "evil_key": sentinel}),
        400,
        "invalid_request",
    )
    assert "evil_key" in body["detail"]
    assert sentinel not in body["detail"]
    # a type error on a known field names the field, not what was sent
    body = assert_error(
        dash.post(LOGIN, {"username": sentinel, "password": 5}), 400, "invalid_request"
    )
    assert "password" in body["detail"] and sentinel not in body["detail"]


def test_a_very_long_unknown_key_is_truncated_in_the_message(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    body = assert_error(dash.post(LOGIN, {"k" * 5000: 1}), 400, "invalid_request")
    assert len(body["detail"]) < 200


def test_logout_body_validation(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    assert_error(dash.post(LOGOUT, {"everywhere": "yes"}), 400, "invalid_request")
    assert_error(dash.post(LOGOUT, {"surprise": True}), 400, "invalid_request")
    assert dash.post(LOGOUT, {"everywhere": False}).status_code == 200


# --- Uniform failure (S6, HTTP level) ---------------------------------------------------------


def test_unknown_user_and_wrong_password_are_indistinguishable(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    unknown = dash.login("nobody", "whatever")
    wrong = dash.login("alice", "wrong-password")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.content == wrong.content
    names = lambda r: {k.lower() for k in r.headers} - {"date"}  # noqa: E731
    assert names(unknown) == names(wrong)
    assert json_body(unknown)["code"] == "invalid_credentials"
    # a malformed name takes the same path
    malformed = dash.login("Bad Name!", "whatever")
    assert malformed.content == wrong.content and malformed.status_code == 401


# --- E1: status -------------------------------------------------------------------------------


def test_status_anonymous(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    response = dash.get(STATUS, proof=None)
    body = json_body(response)
    assert list(body) == STATUS_KEYS
    assert body["enabled"] is True and body["state"] == "anonymous"
    assert body["user"] is None and body["session"] is None
    assert body["pending_username"] is None and body["second_factors"] is None
    assert body["enrollment_token_required"] is None
    assert body["policy"] == {
        "totp": "optional",
        "min_password_length": 12,
        "max_password_length": 256,
    }
    assert body["transport"] == {
        "secure": False,
        "client_is_loopback": True,
        "proxy_suspected": False,
    }
    assert response.headers["cache-control"] == "no-store"


def test_status_authenticated_reports_user_session_and_transport(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    dash.login()
    body = dash.status()
    assert body["state"] == "authenticated"
    assert list(body["user"]) == USER_KEYS and body["user"]["username"] == "alice"
    assert body["pending_username"] is None and body["second_factors"] is None
    assert body["session"] == {
        "idle_timeout_seconds": 1800,
        "idle_expires_at": "2026-01-01T00:30:00Z",
        "absolute_expires_at": "2026-01-01T12:00:00Z",
    }


def test_status_second_factor_pending(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    dash.runtime.store.mutate(lambda f: enroll_totp_directly(f, "alice"))
    dash.login()
    body = dash.status()
    assert body["state"] == "second_factor_required"
    assert body["pending_username"] == "alice"
    assert body["second_factors"] == ["totp", "recovery_code"]
    assert body["user"] is None and body["enrollment_token_required"] is None
    assert body["session"]["idle_timeout_seconds"] == 300  # a partial session's TTL


def test_status_enrollment_pending(dash_factory: DashFactory) -> None:
    dash = dash_factory(totp=TotpPolicy.REQUIRED)
    dash.login()
    body = dash.status()
    assert body["state"] == "enrollment_required"
    assert body["pending_username"] == "alice"
    assert body["enrollment_token_required"] is True
    assert body["second_factors"] is None and body["user"] is None


@pytest.mark.parametrize("policy", list(TotpPolicy))
@pytest.mark.parametrize("enrolled", [False, True])
@pytest.mark.parametrize("required", [False, True])
def test_status_totp_capability_flags(
    dash_factory: DashFactory, policy: TotpPolicy, enrolled: bool, required: bool
) -> None:
    dash = dash_factory(totp=policy)

    def configure(f: Any) -> None:
        f.users["alice"].totp_required = required
        if enrolled:
            enroll_totp_directly(f, "alice")

    dash.runtime.store.mutate(configure)
    issued = dash.issue_full()
    dash.set_cookie(issued.token)
    user = json_body(dash.get(STATUS, proof=issued.proof))["user"]
    assert user["totp_enrolled"] is enrolled
    assert user["totp_required"] is required
    assert user["can_enroll_totp"] is (not enrolled and policy is not TotpPolicy.OFF)
    assert user["can_disable_totp"] is (
        enrolled and policy is not TotpPolicy.REQUIRED and not required
    )
    assert user["recovery_codes_remaining"] == (10 if enrolled else None)


def test_status_needs_the_proof_to_report_a_session(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    dash.login()
    for proof in (None, "A" * 43, "not-a-proof"):
        body = dash.status(proof=proof)
        assert body["state"] == "anonymous" and body["user"] is None and body["session"] is None
    # ... and the session itself is untouched by those looks
    assert dash.status()["state"] == "authenticated"


def test_status_never_slides_the_idle_deadline(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    dash.login()
    first = dash.status()["session"]
    dash.clock.advance(600)
    second = dash.status()["session"]
    assert second == first  # a status poll moves nothing
    dash.clock.advance(1300)  # 1900 s after login: idle (1800) has run out
    assert dash.protected().status_code == 401


def test_status_survives_the_account_disappearing(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    dash.login()
    dash.runtime.store.mutate(lambda f: remove_user(f, "alice"))
    # the middleware revalidates (REVOKED) and clears the cookie; status stays a 200
    body = dash.status()
    assert body["state"] == "anonymous"


# --- Rotation and replay (AC-14 route part) ---------------------------------------------------


def test_login_with_a_proven_session_destroys_it(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    dash.login()
    old_cookie, old_proof = dash.cookie_value, dash.proof
    assert dash.login().status_code == 200  # presents cookie + proof
    new_cookie, new_proof = dash.cookie_value, dash.proof
    assert (old_cookie, old_proof) != (new_cookie, new_proof)
    dash.set_cookie(old_cookie)
    assert dash.protected(proof=old_proof).status_code == 401
    dash.set_cookie(new_cookie)
    assert dash.protected(proof=new_proof).status_code == 200


def test_login_without_a_proof_leaves_the_presented_session_intact(
    dash_factory: DashFactory,
) -> None:
    dash = dash_factory()
    dash.login()
    old_cookie, old_proof = dash.cookie_value, dash.proof
    # a cookie but NO proof (D25): nothing may be destroyed on that say-so
    assert dash.login(proof=None, remember=False).status_code == 200
    dash.set_cookie(old_cookie)
    assert dash.protected(proof=old_proof).status_code == 200


def test_password_change_rotates_and_revokes_the_others(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    dash.login()
    old_cookie, old_proof = dash.cookie_value, dash.proof
    other = dash.new_browser()  # the same user, signed in from a second browser
    other_proof = json_body(dash.login(client=other, remember=False))["session_proof"]
    before = dash.status()["session"]
    epoch_before = dash.epoch()
    dash.clock.advance(100)

    response = dash.post(
        PASSWORD_PATH, {"current_password": PASSWORD, "new_password": NEW_PASSWORD}
    )

    assert response.status_code == 200
    body = json_body(response)
    assert list(body) == ["state", "user", "session_proof"] and body["state"] == "authenticated"
    assert list(body["user"]) == USER_KEYS
    new_proof = body["session_proof"]
    assert new_proof != old_proof and PROOF_RE.fullmatch(new_proof)
    new_cookie = response.cookies.get(dash.cookie_name)
    assert new_cookie and new_cookie != old_cookie
    assert dash.epoch() == epoch_before + 1
    assert len(dash.audit_events("auth.password.changed")) == 1
    # the old pair is dead, the user's other session is dead, the new pair works
    dash.set_cookie(old_cookie)
    assert dash.protected(proof=old_proof).status_code == 401
    assert dash.protected(proof=other_proof, client=other).status_code == 401
    dash.set_cookie(new_cookie)
    assert dash.protected(proof=new_proof).status_code == 200
    # the absolute deadline is kept (rotation is not a fresh login) ...
    assert (
        dash.get(STATUS, proof=new_proof).json()["session"]["absolute_expires_at"]
        == (before["absolute_expires_at"])
    )
    # ... and the new password is the one that works now
    assert dash.login(password=NEW_PASSWORD).status_code == 200
    assert dash.login(password=PASSWORD).status_code == 401


def test_logout_with_the_proof_kills_the_session(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    dash.login()
    cookie, proof = dash.cookie_value, dash.proof
    response = dash.post(LOGOUT, {})
    assert response.status_code == 200 and json_body(response) == {"state": "anonymous"}
    dash.set_cookie(cookie)  # replay the old pair
    assert dash.protected(proof=proof).status_code == 401
    assert [e["event"] for e in dash.audit_events("auth.logout", "auth.logout_all")] == [
        "auth.logout"
    ]


def test_logout_without_a_proof_clears_the_cookie_but_keeps_the_session(
    dash_factory: DashFactory,
) -> None:
    dash = dash_factory()
    dash.login()
    cookie, proof = dash.cookie_value, dash.proof
    response = dash.post(LOGOUT, {}, proof=None)
    assert response.status_code == 200 and json_body(response) == {"state": "anonymous"}
    assert response.headers["clear-site-data"] == '"cache"'
    assert response.headers.get_list("set-cookie") == [
        f"ao_sid_{DASH_PORT}=; Path=/; HttpOnly; SameSite=Strict; "
        "Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT"
    ]
    # the server session stays valid: a logout forged without the proof cannot sign anyone out
    dash.set_cookie(cookie)
    assert dash.protected(proof=proof).status_code == 200
    assert dash.audit_events("auth.logout", "auth.logout_all") == []


def test_logout_is_idempotent_for_an_anonymous_caller(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    response = dash.post(LOGOUT, {}, proof=None)
    assert response.status_code == 200 and json_body(response) == {"state": "anonymous"}
    assert response.headers["clear-site-data"] == '"cache"'
    assert len(response.headers.get_list("set-cookie")) == 1


# --- Logout everywhere --------------------------------------------------------------------------


def test_logout_everywhere_with_a_full_session_bumps_the_epoch(dash_factory: DashFactory) -> None:
    first = dash_factory(port=8765)
    second = dash_factory(port=8766, users=())
    first.login()
    second.login()
    epoch_before = first.epoch()
    response = first.post(LOGOUT, {"everywhere": True})
    assert response.status_code == 200
    assert first.epoch() == epoch_before + 1
    assert second.protected().status_code == 401  # the other instance's session is revoked too
    events = first.audit_events("auth.logout", "auth.logout_all")
    assert [e["event"] for e in events] == ["auth.logout_all"]
    assert events[0]["username"] == "alice" and events[0]["session_id"]


def test_logout_everywhere_with_a_partial_session_only_destroys_that_session(
    dash_factory: DashFactory,
) -> None:
    dash = dash_factory()
    dash.runtime.store.mutate(lambda f: enroll_totp_directly(f, "alice"))
    dash.login()  # a second-factor-pending session
    epoch_before = dash.epoch()
    response = dash.post(LOGOUT, {"everywhere": True})
    assert response.status_code == 200
    assert dash.epoch() == epoch_before  # a half-signed-in session cannot revoke the account
    assert dash.status(proof=dash.proof)["state"] == "anonymous"
    events = dash.audit_events("auth.logout", "auth.logout_all")
    assert [e["event"] for e in events] == ["auth.logout"]


def test_logout_everywhere_survives_an_already_revoked_identity(
    dash_factory: DashFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    dash = dash_factory()
    dash.login()
    # The epoch moves AFTER the middleware's check (a race): the identity-guarded bump finds a
    # stale identity. The caller still gets its 200 and the session is gone.
    monkeypatch.setattr(dash.runtime.provider, "revalidate", lambda *_: Revalidation.VALID)
    dash.runtime.store.mutate(lambda f: setattr(f.users["alice"], "credential_epoch", 9))
    response = dash.post(LOGOUT, {"everywhere": True})
    assert response.status_code == 200 and json_body(response) == {"state": "anonymous"}
    assert dash.session_store.records() == []
    assert [e["event"] for e in dash.audit_events("auth.logout", "auth.logout_all")] == [
        "auth.logout_all"
    ]


# --- Set-Cookie byte pins -----------------------------------------------------------------------


def test_login_set_cookie_is_byte_exact(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    response = dash.login()
    (cookie,) = response.headers.get_list("set-cookie")
    token = dash.cookie_value
    assert token and re.fullmatch(r"[A-Za-z0-9_-]{43}", token)
    assert cookie == f"ao_sid_{DASH_PORT}={token}; Path=/; HttpOnly; SameSite=Strict"
    assert "Domain" not in cookie and "Max-Age" not in cookie and "Secure" not in cookie


def test_login_over_https_uses_the_host_prefix_and_secure(dash_factory: DashFactory) -> None:
    dash = dash_factory(base_url="https://127.0.0.1:8765")
    response = dash.login()
    assert response.status_code == 200
    (cookie,) = response.headers.get_list("set-cookie")
    assert cookie.startswith(f"__Host-ao_sid_{DASH_PORT}=")
    assert cookie.endswith("; Path=/; HttpOnly; SameSite=Strict; Secure")
    cleared = dash.post(LOGOUT, {}).headers.get_list("set-cookie")
    assert cleared == [
        f"__Host-ao_sid_{DASH_PORT}=; Path=/; HttpOnly; SameSite=Strict; "
        "Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Secure"
    ]


def test_error_responses_never_set_a_cookie(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    response = dash.login(password="wrong")
    assert response.status_code == 401 and "set-cookie" not in response.headers


# --- Login audit and plumbing -------------------------------------------------------------------


def test_full_login_is_audited_with_the_new_session_id(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    dash.login()
    (event,) = dash.audit_events("auth.login.success")
    assert event["username"] == "alice" and event["auth_method"] == "password"
    (record,) = dash.session_store.records()
    assert event["session_id"] == record.session_id
