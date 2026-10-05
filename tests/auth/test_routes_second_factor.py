"""T-KQ6ZrY: the second-factor routes E3-E7 against HLD 2.2 / 2.4 (AC-7, AC-8, AC-9, AC-22, AC-36,
AC-37, AC-43 route parts).

The real ``create_app`` over a real ``AuthRuntime`` **with** its ``LocalTotpService``
(``real_routes.build_dash(with_totp=True)``): a ``FakeClock``, the instant ``FastFakeHasher``, codes
computed from the known secret, and the latest session proof on every request.
"""

from __future__ import annotations

import base64
import dataclasses
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import Response
from starlette.testclient import TestClient

from agent_orchestrator.auth.constants import (
    LOCAL_PROVIDER_ID,
    SCOPE_PROOF_OK_KEY,
    SCOPE_SESSION_KEY,
)
from agent_orchestrator.auth.errors import StoreLockTimeoutError
from agent_orchestrator.auth.http import routes_second_factor
from agent_orchestrator.auth.http.routes import ROUTE_BUILDERS, install_auth_routes
from agent_orchestrator.auth.lockouts import LockoutKey
from agent_orchestrator.auth.model import SessionState, TotpPolicy
from agent_orchestrator.auth.store import (
    UserStoreFile,
    consume_totp,
    issue_enrollment_token,
)
from tests.auth.helpers import real_routes
from tests.auth.helpers.core import SeededEntropy
from tests.auth.helpers.provider import PASSWORD
from tests.auth.helpers.real_routes import Dash, DashFactory, enroll_totp_directly, json_body
from tests.auth.helpers.store import RFC_KEY, enroll, totp_code

dash_factory = real_routes.dash_factory  # the pytest fixture (an assignment: no F811 clash)

LOGIN = "/api/auth/login"
STATUS = "/api/auth/status"
VERIFY = "/api/auth/totp/verify"
BEGIN = "/api/auth/totp/enroll/begin"
CONFIRM = "/api/auth/totp/enroll/confirm"
DISABLE = "/api/auth/totp/disable"
REGEN = "/api/auth/totp/recovery-codes"
ALL_PATHS = (VERIFY, BEGIN, CONFIRM, DISABLE, REGEN)

STEP = 30  # TOTP_PERIOD_SECONDS
WRONG_PASSWORD = "not the password"
REMOTE_PEER = ("10.0.0.5", 1)
LOOPBACK_PEER = ("127.0.0.1", 1)
INSECURE_BASE = "http://testserver"
FOREIGN_HOST = "dash.example.com"
TOKEN_ENTROPY_SEED = 13
UNKNOWN_TOKEN = "AAAA-AAAA-AAAA-AAAA"  # well-formed, never issued
ERROR_KEYS = {"detail", "code"}
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


# --- harness helpers ------------------------------------------------------------------------


def totp_dash(dash_factory: DashFactory, **options: Any) -> Dash:
    """A dashboard whose runtime keeps its real TOTP service (the routes then exist)."""
    return dash_factory(with_totp=True, **options)


def code_now(dash: Dash, key: bytes = RFC_KEY, *, steps: int = 0) -> str:
    """The valid code at the fake time, shifted by whole 30 s steps."""
    return totp_code(dash.clock.now_utc().timestamp() + STEP * steps, key)


def wrong_code(dash: Dash) -> str:
    return code_now(dash, steps=50)  # far outside the accepted window


def next_step(dash: Dash) -> None:
    """Let the TOTP step move on, so the next code is not a replay of the last one used."""
    dash.clock.advance(STEP)


def enroll_alice(dash: Dash, username: str = "alice") -> list[str]:
    """Enroll ``username`` with the RFC seed straight in the store (before any login)."""
    return enroll(dash.runtime.store, username, now_unix=0)


def adopt(dash: Dash, response: Response) -> dict[str, Any]:
    """Follow a session-issuing 200: remember its proof and cookie."""
    assert response.status_code == 200, response.text
    body = json_body(response)
    dash.proof = body["session_proof"]
    dash.cookie_value = dash.client.cookies.get(dash.cookie_name)
    return body


def login_partial(dash: Dash) -> dict[str, Any]:
    """Log an enrolled alice in as far as the second-factor step."""
    body = json_body(dash.login())
    assert body["state"] == "second_factor_required"
    return body


def login_full_with_totp(dash: Dash) -> list[str]:
    """Enroll alice, log in with a TOTP code and move the clock on; returns her recovery codes."""
    codes = enroll_alice(dash)
    login_partial(dash)
    adopt(dash, dash.post(VERIFY, {"code": code_now(dash)}))
    next_step(dash)
    return codes


def login_full_plain(dash: Dash, username: str = "alice") -> None:
    """A password-only FULL session (alice not enrolled)."""
    body = json_body(dash.login(username))
    assert body["state"] == "authenticated"


def begin_voluntary(dash: Dash) -> dict[str, Any]:
    response = dash.post(BEGIN, {"current_password": PASSWORD})
    assert response.status_code == 200, response.text
    return json_body(response)


def key_of(begin_body: dict[str, Any]) -> bytes:
    return base64.b32decode(begin_body["secret"])


def enroll_via_routes(dash: Dash) -> tuple[bytes, list[str]]:
    """From a FULL password-only session: E4 + E5 with the matching code (adopts the new pair)."""
    key = key_of(begin_voluntary(dash))
    body = adopt(dash, dash.post(CONFIRM, {"code": code_now(dash, key)}))
    next_step(dash)
    return key, body["recovery_codes"]


def issue_token(dash: Dash, username: str = "alice") -> str:
    now = dash.clock.now_utc()
    token: str = dash.runtime.store.mutate(
        lambda f: issue_enrollment_token(
            f, username, entropy=SeededEntropy(TOKEN_ENTROPY_SEED), now=now
        )
    )
    return token


def failures_of(dash: Dash, username: str = "alice") -> int:
    user_id = dash.runtime.store.snapshot().users[username].user_id
    return dash.runtime.lockouts.state(LockoutKey(user_id=user_id)).failures


def set_totp_required(dash: Dash, username: str = "alice") -> None:
    def flag(f: UserStoreFile) -> None:
        f.users[username].totp_required = True

    dash.runtime.store.mutate(flag)


def assert_error(response: Response, status: int, code: str, *extra: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body = json_body(response)
    assert set(body) == ERROR_KEYS | set(extra), body
    assert body["code"] == code
    assert isinstance(body["detail"], str) and body["detail"]
    return body


def assert_rotated(dash: Dash, response: Response, *, old_proof: str | None) -> dict[str, Any]:
    """A 200 that issued a NEW cookie and proof; the old pair no longer opens anything."""
    old_cookie = dash.cookie_value
    assert response.status_code == 200, response.text
    body = json_body(response)
    assert body["session_proof"] != old_proof
    new_cookie = dash.client.cookies.get(dash.cookie_name)
    assert new_cookie and new_cookie != old_cookie
    dash.proof, dash.cookie_value = body["session_proof"], new_cookie
    assert dash.protected().status_code == 200
    # the replaced pair is dead (cookie + proof together, and the old cookie with the new proof)
    assert old_cookie is not None
    dash.set_cookie(old_cookie)
    assert dash.protected(proof=old_proof).status_code == 401
    assert dash.protected(proof=body["session_proof"]).status_code == 401
    dash.set_cookie(new_cookie)
    return body


def other_browser_full(dash: Dash, username: str = "alice") -> Callable[[], Response]:
    """A second FULL session of ``username`` in the realm; returns a probe for it."""
    issued = dash.issue_full(username)
    browser = dash.new_browser()
    dash.set_cookie(issued.token, client=browser)
    return lambda: dash.protected(proof=issued.proof, client=browser)


# --- the contract table (AC 1) --------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    endpoint: str
    status: int
    code: str | None  # None: a 200
    scenario: Callable[[DashFactory, pytest.MonkeyPatch], Response]
    extra: tuple[str, ...] = ()


CASES: list[Case] = []


def case(
    endpoint: str, status: int, code: str | None = None, *extra: str
) -> Callable[[Callable[[DashFactory, pytest.MonkeyPatch], Response]], Callable[..., Response]]:
    def register(
        fn: Callable[[DashFactory, pytest.MonkeyPatch], Response],
    ) -> Callable[..., Response]:
        CASES.append(Case(endpoint, status, code, fn, extra))
        return fn

    return register


def store_down(dash: Dash, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every store write now times out on the lock (the revalidating snapshot still reads)."""

    def locked(*args: Any, **kwargs: Any) -> Any:
        raise StoreLockTimeoutError("locked")

    monkeypatch.setattr(dash.runtime.store, "mutate", locked)


def lock_account(dash: Dash, path: str, body: dict[str, Any]) -> Response:
    """Fail twice (threshold 2), then send the same request again: the account is locked out."""
    for _ in range(2):
        dash.post(path, body)
    return dash.post(path, body)


# E3
@case(VERIFY, 200)
def _e3_ok(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    enroll_alice(dash)
    login_partial(dash)
    return dash.post(VERIFY, {"code": code_now(dash)})


@case(VERIFY, 400, "invalid_request")
def _e3_both_fields(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    enroll_alice(dash)
    login_partial(dash)
    return dash.post(VERIFY, {"code": "123456", "recovery_code": UNKNOWN_TOKEN})


@case(VERIFY, 401, "invalid_code", "reason", "attempts_remaining")
def _e3_wrong(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    enroll_alice(dash)
    login_partial(dash)
    return dash.post(VERIFY, {"code": wrong_code(dash)})


@case(VERIFY, 401, "not_authenticated")
def _e3_no_session(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    return totp_dash(factory).post(VERIFY, {"code": "123456"}, proof=None)


@case(VERIFY, 401, "enrollment_required")
def _e3_from_enrollment_session(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory, totp=TotpPolicy.REQUIRED)
    assert json_body(dash.login())["state"] == "enrollment_required"
    return dash.post(VERIFY, {"code": "123456"})


@case(VERIFY, 409, "already_authenticated")
def _e3_full(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_plain(dash)
    return dash.post(VERIFY, {"code": "123456"})


@case(VERIFY, 429, "too_many_attempts", "retry_after_seconds")
def _e3_locked(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory, lockout_threshold=2)
    enroll_alice(dash)
    login_partial(dash)
    return lock_account(dash, VERIFY, {"code": wrong_code(dash)})


@case(VERIFY, 503, "store_unavailable")
def _e3_store_down(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    enroll_alice(dash)
    login_partial(dash)
    store_down(dash, mp)
    return dash.post(VERIFY, {"code": code_now(dash)})


# E4
@case(BEGIN, 200)
def _e4_voluntary(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_plain(dash)
    return dash.post(BEGIN, {"current_password": PASSWORD})


@case(BEGIN, 200)
def _e4_forced(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory, totp=TotpPolicy.REQUIRED)
    token = issue_token(dash)
    dash.login()
    return dash.post(BEGIN, {"enrollment_token": token})


@case(BEGIN, 400, "invalid_request")
def _e4_missing_password(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_plain(dash)
    return dash.post(BEGIN, {})


@case(BEGIN, 401, "invalid_credentials")
def _e4_wrong_password(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_plain(dash)
    return dash.post(BEGIN, {"current_password": WRONG_PASSWORD})


@case(BEGIN, 401, "invalid_code", "reason")
def _e4_wrong_token(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory, totp=TotpPolicy.REQUIRED)
    dash.login()
    return dash.post(BEGIN, {"enrollment_token": UNKNOWN_TOKEN})


@case(BEGIN, 403, "insecure_transport")
def _e4_insecure(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory, peer=REMOTE_PEER, base_url=INSECURE_BASE)
    login_full_plain(dash)
    return dash.post(BEGIN, {"current_password": PASSWORD})


@case(BEGIN, 403, "totp_disabled_by_policy")
def _e4_policy_off(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory, totp=TotpPolicy.OFF)
    login_full_plain(dash)
    return dash.post(BEGIN, {"current_password": PASSWORD})


@case(BEGIN, 409, "totp_already_enrolled")
def _e4_already_enrolled(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_with_totp(dash)
    return dash.post(BEGIN, {"current_password": PASSWORD})


@case(BEGIN, 429, "too_many_attempts", "retry_after_seconds")
def _e4_locked(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory, lockout_threshold=2)
    login_full_plain(dash)
    return lock_account(dash, BEGIN, {"current_password": WRONG_PASSWORD})


@case(BEGIN, 503, "store_unavailable")
def _e4_store_down(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory, totp=TotpPolicy.REQUIRED)
    token = issue_token(dash)
    dash.login()
    store_down(dash, mp)
    return dash.post(BEGIN, {"enrollment_token": token})


# E5
@case(CONFIRM, 200)
def _e5_ok(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_plain(dash)
    key = key_of(begin_voluntary(dash))
    return dash.post(CONFIRM, {"code": code_now(dash, key)})


@case(CONFIRM, 400, "invalid_request")
def _e5_missing_code(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_plain(dash)
    begin_voluntary(dash)
    return dash.post(CONFIRM, {})


@case(CONFIRM, 401, "invalid_code", "reason", "attempts_remaining")
def _e5_wrong(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_plain(dash)
    begin_voluntary(dash)
    return dash.post(CONFIRM, {"code": "000000"})


@case(CONFIRM, 403, "insecure_transport")
def _e5_insecure(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory, peer=REMOTE_PEER, base_url=INSECURE_BASE)
    login_full_plain(dash)
    return dash.post(CONFIRM, {"code": "123456"})  # transport first: not even a 409


@case(CONFIRM, 403, "totp_disabled_by_policy")
def _e5_policy_off(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_plain(dash)
    key = key_of(begin_voluntary(dash))
    # The policy flips while an enrollment is pending (a restart with ``off``).
    off = dataclasses.replace(dash.runtime.settings, totp=TotpPolicy.OFF)
    mp.setattr(dash.runtime, "settings", off)
    mp.setattr(dash.runtime.totp, "_settings", off)
    return dash.post(CONFIRM, {"code": code_now(dash, key)})


@case(CONFIRM, 409, "totp_already_enrolled")
def _e5_already_enrolled(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_plain(dash)
    key = key_of(begin_voluntary(dash))
    # The same account enrolls elsewhere (e.g. the CLI) while this enrollment is pending.
    dash.runtime.store.mutate(lambda f: enroll_totp_directly(f, "alice"))
    return dash.post(CONFIRM, {"code": code_now(dash, key)})


@case(CONFIRM, 409, "no_pending_enrollment")
def _e5_no_pending(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_plain(dash)
    return dash.post(CONFIRM, {"code": "123456"})


@case(CONFIRM, 503, "store_unavailable")
def _e5_store_down(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_plain(dash)
    key = key_of(begin_voluntary(dash))
    store_down(dash, mp)
    return dash.post(CONFIRM, {"code": code_now(dash, key)})


# E6
@case(DISABLE, 200)
def _e6_ok(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_with_totp(dash)
    return dash.post(DISABLE, {"current_password": PASSWORD, "code": code_now(dash)})


@case(DISABLE, 400, "invalid_request")
def _e6_missing_code(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_with_totp(dash)
    return dash.post(DISABLE, {"current_password": PASSWORD})


@case(DISABLE, 401, "invalid_credentials")
def _e6_wrong_password(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_with_totp(dash)
    return dash.post(DISABLE, {"current_password": WRONG_PASSWORD, "code": code_now(dash)})


@case(DISABLE, 401, "invalid_code", "reason")
def _e6_wrong_code(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_with_totp(dash)
    return dash.post(DISABLE, {"current_password": PASSWORD, "code": wrong_code(dash)})


@case(DISABLE, 403, "totp_required")
def _e6_required(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory, totp=TotpPolicy.REQUIRED)
    login_full_with_totp(dash)
    return dash.post(DISABLE, {"current_password": PASSWORD, "code": code_now(dash)})


@case(DISABLE, 409, "totp_not_enrolled")
def _e6_not_enrolled(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_plain(dash)
    return dash.post(DISABLE, {"current_password": PASSWORD, "code": "123456"})


@case(DISABLE, 429, "too_many_attempts", "retry_after_seconds")
def _e6_locked(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory, lockout_threshold=2)
    login_full_with_totp(dash)
    return lock_account(dash, DISABLE, {"current_password": WRONG_PASSWORD, "code": "123456"})


@case(DISABLE, 503, "store_unavailable")
def _e6_store_down(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_with_totp(dash)
    store_down(dash, mp)
    return dash.post(DISABLE, {"current_password": PASSWORD, "code": code_now(dash)})


# E7
@case(REGEN, 200)
def _e7_ok(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_with_totp(dash)
    return dash.post(REGEN, {"current_password": PASSWORD, "code": code_now(dash)})


@case(REGEN, 400, "invalid_request")
def _e7_unknown_key(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_with_totp(dash)
    return dash.post(REGEN, {"current_password": PASSWORD, "code": "1", "extra": 1})


@case(REGEN, 401, "invalid_credentials")
def _e7_wrong_password(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_with_totp(dash)
    return dash.post(REGEN, {"current_password": WRONG_PASSWORD, "code": code_now(dash)})


@case(REGEN, 401, "invalid_code", "reason")
def _e7_wrong_code(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_with_totp(dash)
    return dash.post(REGEN, {"current_password": PASSWORD, "code": wrong_code(dash)})


@case(REGEN, 403, "insecure_transport")
def _e7_insecure(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory, peer=REMOTE_PEER, base_url=INSECURE_BASE)
    login_full_with_totp(dash)
    return dash.post(REGEN, {"current_password": PASSWORD, "code": code_now(dash)})


@case(REGEN, 409, "totp_not_enrolled")
def _e7_not_enrolled(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_plain(dash)
    return dash.post(REGEN, {"current_password": PASSWORD, "code": "123456"})


@case(REGEN, 429, "too_many_attempts", "retry_after_seconds")
def _e7_locked(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory, lockout_threshold=2)
    login_full_with_totp(dash)
    return lock_account(dash, REGEN, {"current_password": WRONG_PASSWORD, "code": "123456"})


@case(REGEN, 503, "store_unavailable")
def _e7_store_down(factory: DashFactory, mp: pytest.MonkeyPatch) -> Response:
    dash = totp_dash(factory)
    login_full_with_totp(dash)
    store_down(dash, mp)
    return dash.post(REGEN, {"current_password": PASSWORD, "code": code_now(dash)})


# The (endpoint, status, code) rows HLD 2.4 lists for E3-E7 (the middleware's own 403 origin
# rows and 413 are covered by test_csrf / test_body_cap).
HLD_MATRIX = {
    (VERIFY, 200, None),
    (VERIFY, 400, "invalid_request"),
    (VERIFY, 401, "invalid_code"),
    (VERIFY, 401, "not_authenticated"),
    (VERIFY, 409, "already_authenticated"),
    (VERIFY, 429, "too_many_attempts"),
    (VERIFY, 503, "store_unavailable"),
    (BEGIN, 200, None),
    (BEGIN, 401, "invalid_credentials"),
    (BEGIN, 401, "invalid_code"),
    (BEGIN, 403, "insecure_transport"),
    (BEGIN, 403, "totp_disabled_by_policy"),
    (BEGIN, 409, "totp_already_enrolled"),
    (BEGIN, 429, "too_many_attempts"),
    (BEGIN, 503, "store_unavailable"),
    (CONFIRM, 200, None),
    (CONFIRM, 401, "invalid_code"),
    (CONFIRM, 403, "insecure_transport"),
    (CONFIRM, 403, "totp_disabled_by_policy"),
    (CONFIRM, 409, "totp_already_enrolled"),
    (CONFIRM, 409, "no_pending_enrollment"),
    (DISABLE, 200, None),
    (DISABLE, 401, "invalid_credentials"),
    (DISABLE, 401, "invalid_code"),
    (DISABLE, 403, "totp_required"),
    (DISABLE, 409, "totp_not_enrolled"),
    (DISABLE, 429, "too_many_attempts"),
    (DISABLE, 503, "store_unavailable"),
    (REGEN, 200, None),
    (REGEN, 401, "invalid_credentials"),
    (REGEN, 401, "invalid_code"),
    (REGEN, 403, "insecure_transport"),
    (REGEN, 409, "totp_not_enrolled"),
    (REGEN, 429, "too_many_attempts"),
    (REGEN, 503, "store_unavailable"),
}


def test_the_table_covers_every_documented_status_and_code() -> None:
    produced = {(c.endpoint, c.status, c.code) for c in CASES}
    assert HLD_MATRIX <= produced, sorted(HLD_MATRIX - produced, key=str)


@pytest.mark.parametrize(
    "c",
    CASES,
    ids=[
        f"{k.endpoint.rsplit('/', 1)[-1]}-{k.status}-{k.code or 'ok'}-{i}"
        for i, k in enumerate(CASES)
    ],
)
def test_contract_conformance(
    c: Case, dash_factory: DashFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = c.scenario(dash_factory, monkeypatch)
    if c.code is not None:
        assert_error(response, c.status, c.code, *c.extra)
        return
    assert response.status_code == c.status, response.text
    body = json_body(response)
    expected = {
        VERIFY: ["state", "user", "used_recovery_code", "session_proof"],
        BEGIN: ["secret", "otpauth_uri", "issuer", "account", "algorithm", "digits", "period"],
        CONFIRM: ["state", "user", "recovery_codes", "session_proof"],
        DISABLE: ["state", "user", "session_proof"],
        REGEN: ["recovery_codes", "user", "session_proof"],
    }[c.endpoint]
    assert list(body) == expected
    if "user" in body:
        assert list(body["user"]) == USER_KEYS
    if "recovery_codes" in body:
        assert len(body["recovery_codes"]) == 10
        assert all(isinstance(code, str) for code in body["recovery_codes"])
    if c.endpoint == BEGIN:
        assert body["algorithm"] == "SHA1" and body["digits"] == 6 and body["period"] == 30
        assert body["issuer"] == "ao@test" and body["account"] == "alice"
        assert body["otpauth_uri"].startswith("otpauth://totp/")
        assert f"secret={body['secret']}" in body["otpauth_uri"]
    if c.endpoint == VERIFY:
        assert body["state"] == "authenticated" and body["used_recovery_code"] is False


# --- AC 2: TOTP login ------------------------------------------------------------------------


def test_totp_login_rotates_the_cookie_and_the_proof(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory)
    enroll_alice(dash)
    partial = login_partial(dash)
    partial_proof = partial["session_proof"]
    partial_cookie = dash.client.cookies.get(dash.cookie_name)
    dash.proof = partial_proof
    assert dash.protected().status_code == 401  # a partial session opens nothing

    response = dash.post(VERIFY, {"code": code_now(dash)})
    body = assert_rotated(dash, response, old_proof=partial_proof)
    assert body["state"] == "authenticated"
    assert dash.client.cookies.get(dash.cookie_name) != partial_cookie
    assert body["user"]["auth_method"] == "password+totp"
    assert body["user"]["totp_enrolled"] is True
    assert len(dash.audit_events("auth.login.success")) == 1
    assert dash.audit_events("auth.login.success")[0]["auth_method"] == "password+totp"
    assert dash.audit_events("auth.recovery_code.used") == []


def test_totp_verify_from_a_full_session_is_409(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory)
    login_full_with_totp(dash)
    response = dash.post(VERIFY, {"code": code_now(dash)})
    assert_error(response, 409, "already_authenticated")
    assert dash.protected().status_code == 200  # the session survives the mistake


def test_the_verify_session_gets_a_fresh_absolute_deadline(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory)
    enroll_alice(dash)
    login_partial(dash)
    dash.clock.advance(100)  # partial sessions live 300 s
    adopt(dash, dash.post(VERIFY, {"code": code_now(dash)}))
    session = dash.status()["session"]
    assert session["absolute_expires_at"] == "2026-01-01T12:01:40Z"  # now + 43 200 s


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"code": "123456", "recovery_code": UNKNOWN_TOKEN},
        {"code": 123456},
        {"recovery_code": None},
        {"code": "123456", "extra": 1},
        {"code": "x" * 65},
    ],
    ids=["empty", "both", "non-string", "null", "unknown-key", "too-long"],
)
def test_malformed_verify_bodies_are_400_and_never_counted(
    dash_factory: DashFactory, body: dict[str, Any]
) -> None:
    dash = totp_dash(dash_factory)
    enroll_alice(dash)
    login_partial(dash)
    before = failures_of(dash)
    assert_error(dash.post(VERIFY, body), 400, "invalid_request")
    assert failures_of(dash) == before
    failed = dash.post(VERIFY, {"code": wrong_code(dash)})
    assert json_body(failed)["attempts_remaining"] == 4  # the 400s did not use the budget


def test_verify_requires_the_proof(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory)
    enroll_alice(dash)
    login_partial(dash)
    response = dash.post(VERIFY, {"code": code_now(dash)}, proof=None)
    assert_error(response, 401, "not_authenticated")
    assert dash.audit_events("auth.login.success") == []


# --- AC 3: cross-realm replay ----------------------------------------------------------------


def test_a_code_used_in_one_realm_is_a_replay_in_the_other(dash_factory: DashFactory) -> None:
    a = totp_dash(dash_factory, port=8765, base_url="http://127.0.0.1:8765")
    b = totp_dash(dash_factory, port=8766, base_url="http://127.0.0.1:8766", users=())
    enroll_alice(a)
    login_partial(a)
    login_partial(b)
    code = code_now(a)
    adopt(a, a.post(VERIFY, {"code": code}))

    replay = b.post(VERIFY, {"code": code})
    body = assert_error(replay, 401, "invalid_code", "reason", "attempts_remaining")
    assert body["reason"] == "replayed"
    assert body["attempts_remaining"] == 4

    next_step(b)  # the next step's code goes through on the other realm
    assert b.post(VERIFY, {"code": code_now(b)}).status_code == 200


# --- AC 4: attempts --------------------------------------------------------------------------


def test_five_wrong_codes_destroy_the_partial_session_and_lock_the_account(
    dash_factory: DashFactory,
) -> None:
    dash = totp_dash(dash_factory)
    enroll_alice(dash)
    login_partial(dash)
    for expected in (4, 3, 2, 1):
        body = assert_error(
            dash.post(VERIFY, {"code": wrong_code(dash)}),
            401,
            "invalid_code",
            "reason",
            "attempts_remaining",
        )
        assert body["attempts_remaining"] == expected
    last = dash.post(VERIFY, {"code": wrong_code(dash)})
    body = assert_error(last, 401, "invalid_code", "reason", "attempts_remaining")
    assert body["attempts_remaining"] == 0
    cleared = [c for c in last.headers.get_list("set-cookie") if "Max-Age=0" in c]
    assert len(cleared) == 1 and cleared[0].startswith(f"{dash.cookie_name}=;")

    # the session is gone: even the right code is a plain 401 now
    assert_error(dash.post(VERIFY, {"code": code_now(dash)}), 401, "not_authenticated")
    # five failures reached the account lockout: a fresh login is refused while it runs
    assert failures_of(dash) == 5
    login = dash.login()
    assert_error(login, 429, "too_many_attempts", "retry_after_seconds")
    assert login.headers["retry-after"]


def test_failed_attempts_before_the_limit_keep_the_cookie(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory)
    enroll_alice(dash)
    login_partial(dash)
    response = dash.post(VERIFY, {"code": wrong_code(dash)})
    assert response.headers.get_list("set-cookie") == []
    assert dash.post(VERIFY, {"code": code_now(dash)}).status_code == 200


def test_five_wrong_enrollment_codes_discard_the_pending_secret(
    dash_factory: DashFactory,
) -> None:
    dash = totp_dash(dash_factory)
    login_full_plain(dash)
    begin_voluntary(dash)
    for expected in (4, 3, 2, 1, 0):
        body = assert_error(
            dash.post(CONFIRM, {"code": "000000"}),
            401,
            "invalid_code",
            "reason",
            "attempts_remaining",
        )
        assert body["attempts_remaining"] == expected
    assert_error(dash.post(CONFIRM, {"code": "000000"}), 409, "no_pending_enrollment")
    # the session itself is untouched; a new begin starts a fresh counter and can succeed
    key = key_of(begin_voluntary(dash))
    assert dash.post(CONFIRM, {"code": code_now(dash, key)}).status_code == 200


def test_a_wrong_confirm_code_does_not_touch_the_account_lockout(
    dash_factory: DashFactory,
) -> None:
    dash = totp_dash(dash_factory)
    login_full_plain(dash)
    begin_voluntary(dash)
    before = failures_of(dash)
    dash.post(CONFIRM, {"code": "000000"})
    assert failures_of(dash) == before  # the session counts these, not the guard


def test_a_new_begin_resets_the_confirm_counter(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory)
    login_full_plain(dash)
    begin_voluntary(dash)
    for _ in range(3):
        dash.post(CONFIRM, {"code": "000000"})
    begin_voluntary(dash)
    body = json_body(dash.post(CONFIRM, {"code": "000000"}))
    assert body["attempts_remaining"] == 4


# --- AC 5: recovery login --------------------------------------------------------------------


def test_recovery_code_login(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory)
    codes = enroll_alice(dash)
    partial = login_partial(dash)
    response = dash.post(VERIFY, {"recovery_code": codes[0].lower().replace("-", " ")})
    body = assert_rotated(dash, response, old_proof=partial["session_proof"])
    assert body["used_recovery_code"] is True
    assert body["user"]["recovery_codes_remaining"] == 9
    (used,) = dash.audit_events("auth.recovery_code.used")
    assert used["details"]["recovery_codes_remaining"] == 9
    assert len(dash.audit_events("auth.login.success")) == 1
    record = dash.runtime.sessions.lookup(dash.cookie_value)
    assert record is not None
    assert dash.runtime.sessions.principal_for(record).amr == ("pwd", "rcv", "mfa")

    # a used code is spent: reusing it on a new login is an invalid_code
    other = dash.new_browser()
    assert json_body(dash.login(client=other, remember=False))["state"] == "second_factor_required"
    reuse = dash.post(
        VERIFY,
        {"recovery_code": codes[0]},
        client=other,
        proof=json_body(dash.login(client=other, remember=False))["session_proof"],
    )
    body = assert_error(reuse, 401, "invalid_code", "reason", "attempts_remaining")
    assert body["reason"] == "invalid"


def test_a_totp_login_has_the_otp_amr(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory)
    login_full_with_totp(dash)
    record = dash.runtime.sessions.lookup(dash.cookie_value)
    assert record is not None
    assert dash.runtime.sessions.principal_for(record).amr == ("pwd", "otp", "mfa")


# --- AC 6: forced enrollment -----------------------------------------------------------------


def forced(dash_factory: DashFactory, **options: Any) -> Dash:
    dash = totp_dash(dash_factory, totp=TotpPolicy.REQUIRED, **options)
    return dash


def test_forced_enrollment_state_confines_the_session(dash_factory: DashFactory) -> None:
    dash = forced(dash_factory)
    body = json_body(dash.login())
    assert body["state"] == "enrollment_required"
    assert_error(dash.protected(), 401, "enrollment_required")
    assert_error(dash.post(VERIFY, {"code": "123456"}), 401, "enrollment_required")
    assert_error(
        dash.post(DISABLE, {"current_password": "x", "code": "1"}), 401, "enrollment_required"
    )
    assert_error(
        dash.post(REGEN, {"current_password": "x", "code": "1"}), 401, "enrollment_required"
    )


def _missing(dash: Dash) -> dict[str, Any]:
    return {}


def _wrong(dash: Dash) -> dict[str, Any]:
    issue_token(dash)
    return {"enrollment_token": UNKNOWN_TOKEN}


def _expired(dash: Dash) -> dict[str, Any]:
    token = issue_token(dash)
    dash.clock.advance(3601)  # ENROLLMENT_TOKEN_TTL_SECONDS + 1
    return {"enrollment_token": token}


def _used(dash: Dash) -> dict[str, Any]:
    token = issue_token(dash)
    assert dash.post(BEGIN, {"enrollment_token": token}).status_code == 200
    return {"enrollment_token": token}


@pytest.mark.parametrize("make_body", [_missing, _wrong, _expired, _used])
def test_a_bad_enrollment_token_is_a_uniform_counted_failure(
    dash_factory: DashFactory, make_body: Callable[[Dash], dict[str, Any]]
) -> None:
    dash = forced(dash_factory)
    dash.login()  # the partial session is issued BEFORE the clock moves on
    body = make_body(dash)
    if make_body is _expired:
        dash.login()  # a fresh session: the first one outlived its 300 s TTL
    before = failures_of(dash)
    response = dash.post(BEGIN, body)
    detail = assert_error(response, 401, "invalid_code", "reason")
    assert detail["reason"] == "invalid"
    assert failures_of(dash) == before + 1  # through the AttemptGuard, one failure each time
    assert dash.audit_events("auth.enrollment_token.failure")
    assert dash.runtime.store.snapshot().users["alice"].totp is None
    # uniform: every kind of failure has the same bytes
    assert response.content == json.dumps(UNIFORM_TOKEN_FAILURE, separators=(",", ":")).encode()


UNIFORM_TOKEN_FAILURE: dict[str, Any] = {
    "detail": "Invalid or already used code.",
    "code": "invalid_code",
    "reason": "invalid",
}


def test_a_valid_token_begins_enrollment_exactly_once(dash_factory: DashFactory) -> None:
    dash = forced(dash_factory)
    token = issue_token(dash)
    dash.login()
    first = dash.post(BEGIN, {"enrollment_token": token})
    assert first.status_code == 200
    assert set(json_body(first)) >= {"secret", "otpauth_uri"}
    assert_error(dash.post(BEGIN, {"enrollment_token": token}), 401, "invalid_code", "reason")


def test_forced_enrollment_completes_the_login(dash_factory: DashFactory) -> None:
    dash = forced(dash_factory)
    token = issue_token(dash)
    partial_proof = json_body(dash.login())["session_proof"]
    dash.proof = partial_proof
    probe = other_browser_full(dash)  # another session of the same user in this realm
    assert probe().status_code == 200
    key = key_of(json_body(dash.post(BEGIN, {"enrollment_token": token})))

    response = dash.post(CONFIRM, {"code": code_now(dash, key)})
    body = assert_rotated(dash, response, old_proof=partial_proof)
    assert body["state"] == "authenticated"
    assert len(body["recovery_codes"]) == 10
    assert body["user"]["totp_enrolled"] is True
    assert body["user"]["auth_method"] == "password+totp"
    assert probe().status_code == 401  # the user's other sessions in the realm are revoked
    (login,) = dash.audit_events("auth.login.success")
    assert login["auth_method"] == "password+totp"
    assert len(dash.audit_events("auth.totp.enrolled")) == 1
    assert dash.epoch() == 2
    # a fresh absolute deadline: login completion is not a keep-deadline rotation
    assert dash.status()["session"]["absolute_expires_at"] == "2026-01-01T12:00:00Z"


def test_the_forced_enrollment_resets_the_lockout_counter(dash_factory: DashFactory) -> None:
    dash = forced(dash_factory)
    token = issue_token(dash)
    dash.login()
    dash.post(BEGIN, {"enrollment_token": UNKNOWN_TOKEN})
    assert failures_of(dash) == 1
    key = key_of(json_body(dash.post(BEGIN, {"enrollment_token": token})))
    dash.post(CONFIRM, {"code": code_now(dash, key)})
    assert failures_of(dash) == 0


# --- AC 7: voluntary enrollment --------------------------------------------------------------


def test_voluntary_enrollment_requires_the_current_password(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory)
    login_full_plain(dash)
    assert_error(dash.post(BEGIN, {}), 400, "invalid_request")
    assert_error(dash.post(BEGIN, {"enrollment_token": UNKNOWN_TOKEN}), 400, "invalid_request")
    before = failures_of(dash)
    assert_error(dash.post(BEGIN, {"current_password": WRONG_PASSWORD}), 401, "invalid_credentials")
    assert failures_of(dash) == before + 1  # counted
    assert dash.post(BEGIN, {"current_password": PASSWORD}).status_code == 200


def test_a_forced_session_may_not_use_the_password_field(dash_factory: DashFactory) -> None:
    dash = forced(dash_factory)
    dash.login()
    assert_error(dash.post(BEGIN, {"current_password": PASSWORD}), 400, "invalid_request")


def test_voluntary_enrollment_keeps_the_deadline_and_bumps_the_epoch(
    dash_factory: DashFactory,
) -> None:
    dash = totp_dash(dash_factory)
    login_full_plain(dash)
    deadline = dash.status()["session"]["absolute_expires_at"]
    epoch = dash.epoch()
    dash.clock.advance(600)
    old_proof = dash.proof
    key = key_of(begin_voluntary(dash))
    response = dash.post(CONFIRM, {"code": code_now(dash, key)})
    body = assert_rotated(dash, response, old_proof=old_proof)
    assert len(body["recovery_codes"]) == 10
    assert dash.epoch() == epoch + 1
    assert dash.status()["session"]["absolute_expires_at"] == deadline
    assert body["user"]["totp_enrolled"] is True
    assert body["user"]["recovery_codes_remaining"] == 10
    assert body["user"]["can_disable_totp"] is True
    # voluntary enrollment is not a login: no new login event beyond the password login
    assert len(dash.audit_events("auth.login.success")) == 1


def test_voluntary_enrollment_revokes_the_other_sessions(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory)
    login_full_plain(dash)
    probe = other_browser_full(dash)
    assert probe().status_code == 200
    enroll_via_routes(dash)
    assert probe().status_code == 401


def test_the_new_secret_can_log_in_afterwards(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory)
    login_full_plain(dash)
    key, _codes = enroll_via_routes(dash)
    other = dash.new_browser()
    partial = json_body(dash.login(client=other, remember=False))
    assert partial["state"] == "second_factor_required"
    response = dash.post(
        VERIFY, {"code": code_now(dash, key)}, client=other, proof=partial["session_proof"]
    )
    assert response.status_code == 200, response.text


# --- AC 8: sticky TOTP -----------------------------------------------------------------------


def test_an_enrolled_user_is_still_challenged_when_the_policy_goes_off(
    dash_factory: DashFactory,
) -> None:
    first = totp_dash(dash_factory)
    enroll_alice(first)
    off = dataclasses.replace(first.runtime.settings, totp=TotpPolicy.OFF)
    dash = totp_dash(
        dash_factory, settings=off, users=(), port=8766, base_url="http://127.0.0.1:8766"
    )
    body = json_body(dash.login())
    assert body["state"] == "second_factor_required"
    assert_error(dash.get("/api/runs"), 401, "second_factor_required")
    adopt(dash, dash.post(VERIFY, {"code": code_now(dash)}))
    assert dash.protected().status_code == 200
    # ... but under ``off`` nobody may START an enrollment
    assert_error(dash.post(BEGIN, {"current_password": PASSWORD}), 403, "totp_disabled_by_policy")
    assert_error(dash.post(CONFIRM, {"code": "123456"}), 409, "no_pending_enrollment")


# --- AC 9: disable ---------------------------------------------------------------------------


def test_disable_is_refused_when_policy_requires_totp(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory, totp=TotpPolicy.REQUIRED)
    login_full_with_totp(dash)
    assert_error(
        dash.post(DISABLE, {"current_password": PASSWORD, "code": code_now(dash)}),
        403,
        "totp_required",
    )
    assert dash.runtime.store.snapshot().users["alice"].totp is not None


def test_disable_is_refused_for_a_totp_required_user(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory)
    set_totp_required(dash)
    login_full_with_totp(dash)
    assert_error(
        dash.post(DISABLE, {"current_password": PASSWORD, "code": code_now(dash)}),
        403,
        "totp_required",
    )


def test_a_failed_reauth_is_counted_and_audited(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory)
    login_full_with_totp(dash)
    before = failures_of(dash)
    assert_error(
        dash.post(DISABLE, {"current_password": WRONG_PASSWORD, "code": code_now(dash)}),
        401,
        "invalid_credentials",
    )
    assert failures_of(dash) == before + 1
    assert_error(
        dash.post(DISABLE, {"current_password": PASSWORD, "code": wrong_code(dash)}),
        401,
        "invalid_code",
        "reason",
    )
    assert failures_of(dash) == before + 2
    assert dash.audit_events("auth.reauth.failure")
    assert dash.audit_events("auth.second_factor.failure")
    assert dash.runtime.store.snapshot().users["alice"].totp is not None


@pytest.mark.parametrize("use_recovery_code", [False, True], ids=["totp-code", "recovery-code"])
def test_disable_succeeds_with_either_kind_of_code(
    dash_factory: DashFactory, use_recovery_code: bool
) -> None:
    dash = totp_dash(dash_factory)
    codes = login_full_with_totp(dash)
    probe = other_browser_full(dash)
    assert probe().status_code == 200
    deadline = dash.status()["session"]["absolute_expires_at"]
    old_proof = dash.proof
    code = codes[0] if use_recovery_code else code_now(dash)
    response = dash.post(DISABLE, {"current_password": PASSWORD, "code": code})
    body = assert_rotated(dash, response, old_proof=old_proof)
    assert list(body) == ["state", "user", "session_proof"]
    assert body["user"]["totp_enrolled"] is False
    assert body["user"]["auth_method"] == "password"
    assert body["user"]["can_enroll_totp"] is True
    assert probe().status_code == 401  # other sessions are revoked
    assert dash.status()["session"]["absolute_expires_at"] == deadline
    assert dash.runtime.store.snapshot().users["alice"].totp is None
    assert len(dash.audit_events("auth.totp.disabled")) == 1
    record = dash.runtime.sessions.lookup(dash.cookie_value)
    assert record is not None
    assert dash.runtime.sessions.principal_for(record).amr == ("pwd",)


def test_disable_then_login_needs_no_second_factor(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory)
    login_full_with_totp(dash)
    adopt(dash, dash.post(DISABLE, {"current_password": PASSWORD, "code": code_now(dash)}))
    other = dash.new_browser()
    assert json_body(dash.login(client=other, remember=False))["state"] == "authenticated"


# --- AC 10: regenerate -----------------------------------------------------------------------


def test_regenerate_replaces_the_codes_and_rotates(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory)
    old_codes = login_full_with_totp(dash)
    probe = other_browser_full(dash)
    deadline = dash.status()["session"]["absolute_expires_at"]
    old_proof = dash.proof
    response = dash.post(REGEN, {"current_password": PASSWORD, "code": code_now(dash)})
    body = assert_rotated(dash, response, old_proof=old_proof)
    new_codes = body["recovery_codes"]
    assert len(new_codes) == 10 and not set(new_codes) & set(old_codes)
    assert body["user"]["recovery_codes_remaining"] == 10
    assert body["user"]["auth_method"] == "password+totp"  # the login method is kept
    assert probe().status_code == 401
    assert dash.status()["session"]["absolute_expires_at"] == deadline
    assert len(dash.audit_events("auth.recovery_codes.regenerated")) == 1

    # an old code is dead at the next login step; a new one works
    other = dash.new_browser()
    proof = json_body(dash.login(client=other, remember=False))["session_proof"]
    old = dash.post(VERIFY, {"recovery_code": old_codes[1]}, client=other, proof=proof)
    assert_error(old, 401, "invalid_code", "reason", "attempts_remaining")
    fresh = dash.post(VERIFY, {"recovery_code": new_codes[0]}, client=other, proof=proof)
    assert fresh.status_code == 200


def test_regenerate_accepts_a_recovery_code_as_the_second_proof(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory)
    codes = login_full_with_totp(dash)
    response = dash.post(REGEN, {"current_password": PASSWORD, "code": codes[2]})
    assert response.status_code == 200, response.text
    assert json_body(response)["user"]["recovery_codes_remaining"] == 10


# --- AC 11: insecure_transport ---------------------------------------------------------------


def transport_calls(dash: Dash, headers: dict[str, str] | None = None) -> dict[str, int]:
    """E4, E5 and E7 from a FULL (enrolled) session; returns path -> status."""
    results = {}
    results[BEGIN] = dash.post(BEGIN, {"current_password": PASSWORD}, headers=headers).status_code
    results[CONFIRM] = dash.post(CONFIRM, {"code": "123456"}, headers=headers).status_code
    results[REGEN] = dash.post(
        REGEN, {"current_password": PASSWORD, "code": "123456"}, headers=headers
    ).status_code
    return results


def test_e4_e5_e7_refuse_a_remote_client_over_plain_http(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory, peer=REMOTE_PEER, base_url=INSECURE_BASE)
    login_full_with_totp(dash)
    for path in (BEGIN, CONFIRM, REGEN):
        body = {"current_password": PASSWORD, "code": code_now(dash)}
        payload = (
            {"code": "123456"}
            if path == CONFIRM
            else ({"current_password": PASSWORD} if path == BEGIN else body)
        )
        response = dash.post(path, payload)
        assert_error(response, 403, "insecure_transport")
    # nothing was spent: no failure counted, no secret generated
    assert failures_of(dash) == 0
    record = dash.runtime.sessions.lookup(dash.cookie_value)
    assert record is not None and record.pending_totp_secret is None


def test_a_remote_client_may_still_log_in_over_plain_http(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory, peer=REMOTE_PEER, base_url=INSECURE_BASE)
    enroll_alice(dash)
    login_partial(dash)
    adopt(dash, dash.post(VERIFY, {"code": code_now(dash)}))
    # E6 is not a secret-delivering route: it is not in the transport rule
    next_step(dash)
    assert (
        dash.post(DISABLE, {"current_password": PASSWORD, "code": code_now(dash)}).status_code
        == 200
    )


def test_a_loopback_client_is_allowed_over_plain_http(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory, peer=LOOPBACK_PEER)
    login_full_plain(dash)
    assert dash.post(BEGIN, {"current_password": PASSWORD}).status_code == 200
    assert dash.post(CONFIRM, {"code": "000000"}).status_code == 401  # reached the service


def test_a_remote_client_is_allowed_over_https(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory, peer=REMOTE_PEER, base_url="https://testserver")
    login_full_with_totp(dash)
    assert dash.post(BEGIN, {"current_password": PASSWORD}).status_code == 409  # already enrolled
    response = dash.post(REGEN, {"current_password": PASSWORD, "code": code_now(dash)})
    assert response.status_code == 200, response.text


FORWARDED_CASES: list[dict[str, str]] = [
    {"X-Forwarded-For": "203.0.113.9"},
    {"Forwarded": "for=203.0.113.9"},
    {"X-Forwarded-Proto": "http"},
    {"Host": FOREIGN_HOST, "Origin": f"http://{FOREIGN_HOST}"},
]


@pytest.mark.parametrize(
    "headers",
    FORWARDED_CASES,
    ids=["x-forwarded-for", "forwarded", "x-forwarded-proto", "foreign-host"],
)
def test_a_loopback_peer_behind_a_proxy_counts_as_remote(
    dash_factory: DashFactory, monkeypatch: pytest.MonkeyPatch, headers: dict[str, str]
) -> None:
    monkeypatch.setenv("AO_UI_ALLOWED_HOSTS", f"testserver,{FOREIGN_HOST}")
    dash = totp_dash(dash_factory, peer=LOOPBACK_PEER)
    login_full_with_totp(dash)
    statuses = transport_calls(dash, headers)
    assert statuses == {BEGIN: 403, CONFIRM: 403, REGEN: 403}, statuses
    # the identical calls without the proxy hint reach the service
    assert transport_calls(dash)[BEGIN] != 403


def test_trusted_proxy_with_https_passes(dash_factory: DashFactory) -> None:
    """A configured proxy terminates TLS; uvicorn then reports https (simulated by the base URL)."""
    dash = totp_dash(
        dash_factory,
        peer=LOOPBACK_PEER,
        base_url="https://127.0.0.1:8765",
        trusted_proxies=("127.0.0.1",),
    )
    login_full_with_totp(dash)
    headers = {"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https"}
    assert dash.post(BEGIN, {"current_password": PASSWORD}, headers=headers).status_code == 409


# --- AC 12: E4 hygiene -----------------------------------------------------------------------


def test_the_pending_secret_lives_only_in_the_session(
    dash_factory: DashFactory, caplog: pytest.LogCaptureFixture
) -> None:
    dash = totp_dash(dash_factory)
    login_full_plain(dash)
    response = dash.post(BEGIN, {"current_password": PASSWORD})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers.get_list("set-cookie") == []  # E4 does not rotate
    body = json_body(response)
    assert "session_proof" not in body
    secret = base64.b32decode(body["secret"])
    record = dash.runtime.sessions.lookup(dash.cookie_value)
    assert record is not None and record.pending_totp_secret == secret
    assert dash.runtime.store.snapshot().users["alice"].totp is None
    assert body["secret"] not in dash.runtime.paths.users_file.read_text()
    assert body["secret"] not in dash.runtime.paths.audit_file.read_text()
    assert body["secret"] not in caplog.text


def test_begin_again_replaces_the_pending_secret(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory)
    login_full_plain(dash)
    first = key_of(begin_voluntary(dash))
    second = key_of(begin_voluntary(dash))
    assert first != second
    assert dash.post(CONFIRM, {"code": code_now(dash, first)}).status_code == 401
    assert dash.post(CONFIRM, {"code": code_now(dash, second)}).status_code == 200


def test_recovery_codes_are_displayed_once_and_never_logged_or_audited(
    dash_factory: DashFactory, caplog: pytest.LogCaptureFixture
) -> None:
    dash = totp_dash(dash_factory)
    login_full_plain(dash)
    _key, codes = enroll_via_routes(dash)
    status = dash.status()
    assert "recovery_codes" not in status["user"]  # the status carries a count, never codes
    audit = dash.runtime.paths.audit_file.read_text()
    users = dash.runtime.paths.users_file.read_text()
    for code in codes:
        assert code not in audit and code not in users and code not in caplog.text
        assert code.replace("-", "") not in users
    assert status["user"]["recovery_codes_remaining"] == 10


def test_secret_bearing_responses_are_not_cacheable(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory)
    login_full_with_totp(dash)
    response = dash.post(REGEN, {"current_password": PASSWORD, "code": code_now(dash)})
    assert response.headers["cache-control"] == "no-store"


# --- partial-session confinement and proof (S10, S22, D25) -----------------------------------


@pytest.mark.parametrize("path", [BEGIN, CONFIRM, DISABLE, REGEN])
def test_a_second_factor_pending_session_reaches_only_its_own_step(
    dash_factory: DashFactory, path: str
) -> None:
    dash = totp_dash(dash_factory)
    enroll_alice(dash)
    login_partial(dash)
    response = dash.post(path, {"current_password": PASSWORD, "code": "123456"})
    assert_error(response, 401, "second_factor_required")


@pytest.mark.parametrize("path", ALL_PATHS)
def test_every_route_requires_a_session_and_the_proof(dash_factory: DashFactory, path: str) -> None:
    dash = totp_dash(dash_factory)
    assert_error(dash.post(path, {}, proof=None), 401, "not_authenticated")
    login_full_plain(dash)
    assert_error(dash.post(path, {}, proof=None), 401, "not_authenticated")
    assert_error(dash.post(path, {}, proof="A" * 43), 401, "not_authenticated")


def test_a_full_session_cannot_use_the_enrollment_token(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory)
    token = issue_token(dash)
    login_full_plain(dash)
    assert_error(dash.post(BEGIN, {"enrollment_token": token}), 400, "invalid_request")


def test_a_stale_session_is_401_not_a_guess(dash_factory: DashFactory) -> None:
    """The credential changed under the session (CLI reset): nothing is counted, 401."""
    dash = totp_dash(dash_factory)
    enroll_alice(dash)
    login_partial(dash)
    user = dash.runtime.store.snapshot().users["alice"]

    def bump(f: UserStoreFile) -> None:
        f.users["alice"].credential_epoch += 1

    dash.runtime.store.mutate(bump)
    assert user.credential_epoch + 1 == dash.epoch()
    before = failures_of(dash)
    assert_error(dash.post(VERIFY, {"code": code_now(dash)}), 401, "not_authenticated")
    assert failures_of(dash) == before


def test_a_verify_that_loses_the_race_to_a_revocation_destroys_the_partial_session(
    dash_factory: DashFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The epoch moves between the middleware's revalidation and the consume (D10)."""
    dash = totp_dash(dash_factory)
    enroll_alice(dash)
    login_partial(dash)
    real = consume_totp

    def racing(f: UserStoreFile, *args: Any, **kwargs: Any) -> Any:
        f.users["alice"].credential_epoch += 1
        return real(f, *args, **kwargs)

    monkeypatch.setattr("agent_orchestrator.auth.totp_service.consume_totp", racing)
    response = dash.post(VERIFY, {"code": code_now(dash)})
    assert_error(response, 401, "not_authenticated")
    cleared = [c for c in response.headers.get_list("set-cookie") if "Max-Age=0" in c]
    assert len(cleared) == 1
    assert dash.runtime.sessions.lookup(dash.cookie_value) is None


def test_a_store_outage_keeps_the_partial_session_and_logs_once(
    dash_factory: DashFactory, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    dash = totp_dash(dash_factory)
    enroll_alice(dash)
    login_partial(dash)
    with monkeypatch.context() as patch:
        store_down(dash, patch)
        response = dash.post(VERIFY, {"code": code_now(dash)})
    assert_error(response, 503, "store_unavailable")
    assert response.headers["retry-after"] == "5"
    errors = [
        r
        for r in caplog.records
        if r.levelname == "ERROR" and "store_unavailable" in r.getMessage()
    ]
    assert len(errors) == 1
    # the session was kept and the failure not counted against the attempts
    ok = dash.post(VERIFY, {"code": code_now(dash)})
    assert ok.status_code == 200, ok.text


# --- AC 15: registration ---------------------------------------------------------------------


def direct_routes(dash: Dash) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for route in dash.app.router.routes:
        if isinstance(route, APIRoute):
            for method in route.methods or ():
                counts[(method, route.path)] = counts.get((method, route.path), 0) + 1
    return counts


def test_the_five_routes_are_flat_and_registered_once_per_app(dash_factory: DashFactory) -> None:
    first = totp_dash(dash_factory)
    second = totp_dash(dash_factory, users=(), port=8766, base_url="http://127.0.0.1:8766")
    for dash in (first, second):
        counts = direct_routes(dash)
        for path in ALL_PATHS:
            assert counts.get(("POST", path)) == 1, path
    builders = ROUTE_BUILDERS[LOCAL_PROVIDER_ID]
    assert builders.count(routes_second_factor.add_totp_routes) == 1  # two apps, one registration


def test_without_a_totp_service_none_of_the_routes_exists(dash_factory: DashFactory) -> None:
    dash = dash_factory()  # the harness default: totp=None
    assert dash.runtime.totp is None
    counts = direct_routes(dash)
    assert not any(path in {p for _, p in counts} for path in ALL_PATHS)
    assert dash.post(VERIFY, {}, proof=None).status_code in (401, 404)


def test_the_registered_builder_is_a_noop_for_a_provider_without_totp(
    dash_factory: DashFactory,
) -> None:
    from fastapi import FastAPI

    dash = dash_factory()
    app = FastAPI()
    routes_second_factor.add_totp_routes(app, dash.runtime)  # runtime.totp is None
    assert app.router.routes == [r for r in app.router.routes if not isinstance(r, APIRoute)]


# --- misc: session-state edge ----------------------------------------------------------------


def test_e3_with_every_state_is_decided_by_state(dash_factory: DashFactory) -> None:
    """A FULL session gets 409; only a second-factor-pending one gets past the state gate."""
    dash = totp_dash(dash_factory)
    enroll_alice(dash)
    issued = dash.issue(SessionState.PARTIAL_SECOND_FACTOR)
    dash.set_cookie(issued.token)
    response = dash.post(VERIFY, {"code": code_now(dash)}, proof=issued.proof)
    assert response.status_code == 200, response.text


# --- defence in depth: the handlers re-check what the middleware decides --------------------


def test_a_second_install_of_the_builder_adds_no_duplicate_route(dash_factory: DashFactory) -> None:
    dash = totp_dash(dash_factory)
    routes_second_factor.add_totp_routes(dash.app, dash.runtime)  # a re-imported module's builder
    counts = direct_routes(dash)
    assert all(counts[("POST", path)] == 1 for path in ALL_PATHS)


class InjectSession:
    """Test-only ASGI wrapper standing in for ``AuthMiddleware``: it plants a chosen session."""

    def __init__(self, app: Any, session: Any, *, proof_ok: bool = True) -> None:
        self.app, self.session, self.proof_ok = app, session, proof_ok

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            state = scope.setdefault("state", {})
            state[SCOPE_SESSION_KEY] = self.session
            state[SCOPE_PROOF_OK_KEY] = self.proof_ok
        await self.app(scope, receive, send)


def payload_for(path: str) -> dict[str, str]:
    """A body that passes the schema of ``path`` (so the session checks are what answers)."""
    if path == CONFIRM:
        return {"code": "123456"}
    return {"code": "123456", "current_password": PASSWORD}


def bare_app(dash: Dash, session: Any, *, proof_ok: bool = True) -> TestClient:
    """The real routes WITHOUT the middleware, so a handler sees exactly the planted session."""
    app = FastAPI()
    app.add_middleware(InjectSession, session=session, proof_ok=proof_ok)
    install_auth_routes(app, dash.runtime)
    return TestClient(app, base_url="http://127.0.0.1:8765", client=LOOPBACK_PEER)


@pytest.mark.parametrize(
    ("path", "state", "code"),
    [
        (BEGIN, SessionState.PARTIAL_SECOND_FACTOR, "second_factor_required"),
        (CONFIRM, SessionState.PARTIAL_SECOND_FACTOR, "second_factor_required"),
        (VERIFY, SessionState.PARTIAL_ENROLL, "enrollment_required"),
    ],
)
def test_a_handler_refuses_a_state_the_middleware_would_have_refused(
    dash_factory: DashFactory, path: str, state: SessionState, code: str
) -> None:
    dash = totp_dash(dash_factory)
    client = bare_app(dash, dash.issue(state).record)
    response = client.post(path, json=payload_for(path))
    assert_error(response, 401, code)


@pytest.mark.parametrize("path", ALL_PATHS)
def test_a_handler_without_a_proven_session_is_401(dash_factory: DashFactory, path: str) -> None:
    dash = totp_dash(dash_factory)
    record = dash.issue_full().record
    for session, proof_ok in ((None, True), (record, False)):
        client = bare_app(dash, session, proof_ok=proof_ok)
        response = client.post(path, json=payload_for(path))
        assert_error(response, 401, "not_authenticated")
