"""T-kwwJ82: route policy tables, cookie-only sets and the pure predicates (HLD 11.14)."""

from __future__ import annotations

import pytest

from agent_orchestrator.auth import constants
from agent_orchestrator.auth.errors import ErrorCode
from agent_orchestrator.auth.model import SessionState, TotpPolicy, TotpRequirement, denial_code
from agent_orchestrator.auth.policy import (
    AUTH_ROUTE_POLICIES,
    DASHBOARD_COOKIE_ONLY_NAVIGATION,
    DASHBOARD_ROUTE_POLICIES,
    HUB_COOKIE_ONLY_NAVIGATION,
    HUB_ROUTE_POLICIES,
    RoutePolicy,
    policy_allows,
    proof_required,
    totp_requirement,
)

PUBLIC = RoutePolicy.PUBLIC
PARTIAL_2F = RoutePolicy.PARTIAL_SECOND_FACTOR
ENROLL = RoutePolicy.ENROLLMENT
AUTHED = RoutePolicy.AUTHENTICATED
S_2F = SessionState.PARTIAL_SECOND_FACTOR
S_ENROLL = SessionState.PARTIAL_ENROLL
FULL = SessionState.FULL


def test_auth_table_has_exactly_the_six_entries() -> None:
    assert dict(AUTH_ROUTE_POLICIES) == {
        ("GET", constants.AUTH_STATUS_PATH): PUBLIC,
        ("POST", constants.AUTH_LOGIN_PATH): PUBLIC,
        ("POST", constants.AUTH_LOGOUT_PATH): PUBLIC,
        ("POST", constants.AUTH_TOTP_VERIFY_PATH): PARTIAL_2F,
        ("POST", constants.AUTH_ENROLL_BEGIN_PATH): ENROLL,
        ("POST", constants.AUTH_ENROLL_CONFIRM_PATH): ENROLL,
    }


def test_authenticated_auth_routes_are_absent_by_omission() -> None:
    for path in (
        constants.AUTH_KEEPALIVE_PATH,
        constants.AUTH_PASSWORD_PATH,
        constants.AUTH_TOTP_DISABLE_PATH,
        constants.AUTH_RECOVERY_CODES_PATH,
    ):
        assert not any(p == path for _, p in AUTH_ROUTE_POLICIES)


def test_dashboard_table_adds_exactly_four() -> None:
    extra = {k: v for k, v in DASHBOARD_ROUTE_POLICIES.items() if k not in AUTH_ROUTE_POLICIES}
    assert extra == {
        ("GET", constants.HEALTH_PATH): PUBLIC,
        ("GET", "/"): PUBLIC,
        ("GET", constants.SPA_FALLBACK_PATH): PUBLIC,
        ("MOUNT", constants.SPA_ASSETS_MOUNT_NAME): PUBLIC,
    }
    assert all(DASHBOARD_ROUTE_POLICIES[k] == v for k, v in AUTH_ROUTE_POLICIES.items())


def test_hub_table_adds_exactly_two_and_omits_index_and_service_status() -> None:
    extra = {k: v for k, v in HUB_ROUTE_POLICIES.items() if k not in AUTH_ROUTE_POLICIES}
    assert extra == {
        ("GET", constants.HUB_LOGIN_PATH): PUBLIC,
        ("GET", constants.HUB_ASSET_ROUTE_PATH): PUBLIC,
    }
    assert ("GET", "/") not in HUB_ROUTE_POLICIES
    assert not any(path == "/api/service/status" for _, path in HUB_ROUTE_POLICIES)


def test_tables_are_read_only() -> None:
    for table in (AUTH_ROUTE_POLICIES, DASHBOARD_ROUTE_POLICIES, HUB_ROUTE_POLICIES):
        with pytest.raises(TypeError):
            table[("GET", "/x")] = PUBLIC  # type: ignore[index]


def test_cookie_only_navigation_sets() -> None:
    assert DASHBOARD_COOKIE_ONLY_NAVIGATION == frozenset()
    assert HUB_COOKIE_ONLY_NAVIGATION == frozenset({("GET", "/")})


@pytest.mark.parametrize(
    ("flagged", "table"),
    [
        (DASHBOARD_COOKIE_ONLY_NAVIGATION, DASHBOARD_ROUTE_POLICIES),
        (HUB_COOKIE_ONLY_NAVIGATION, HUB_ROUTE_POLICIES),
    ],
)
def test_cookie_only_keys_are_get_non_api_and_not_in_the_table(
    flagged: frozenset[tuple[str, str]], table: object
) -> None:
    for method, path in flagged:
        assert method == "GET"
        assert not path.startswith(constants.API_PREFIX)
        assert (method, path) not in table  # type: ignore[operator]  # => AUTHENTICATED


# The 4x4 table of HLD 11.14: None == no session; a value is the expected denial code.
ALLOW = None
CELLS = [
    (PUBLIC, None, ALLOW),
    (PUBLIC, S_2F, ALLOW),
    (PUBLIC, S_ENROLL, ALLOW),
    (PUBLIC, FULL, ALLOW),
    (PARTIAL_2F, None, ErrorCode.NOT_AUTHENTICATED),
    (PARTIAL_2F, S_2F, ALLOW),
    (PARTIAL_2F, S_ENROLL, ErrorCode.ENROLLMENT_REQUIRED),
    (PARTIAL_2F, FULL, ALLOW),
    (ENROLL, None, ErrorCode.NOT_AUTHENTICATED),
    (ENROLL, S_2F, ErrorCode.SECOND_FACTOR_REQUIRED),
    (ENROLL, S_ENROLL, ALLOW),
    (ENROLL, FULL, ALLOW),
    (AUTHED, None, ErrorCode.NOT_AUTHENTICATED),
    (AUTHED, S_2F, ErrorCode.SECOND_FACTOR_REQUIRED),
    (AUTHED, S_ENROLL, ErrorCode.ENROLLMENT_REQUIRED),
    (AUTHED, FULL, ALLOW),
]


@pytest.mark.parametrize(("policy", "state", "denial"), CELLS)
def test_policy_allows_matrix(
    policy: RoutePolicy, state: SessionState | None, denial: ErrorCode | None
) -> None:
    assert policy_allows(policy, state) is (denial is None)
    if denial is not None:
        assert denial_code(state) is denial


def test_matrix_covers_every_policy_and_state() -> None:
    assert {(p, s) for p, s, _ in CELLS} == {
        (p, s) for p in RoutePolicy for s in (None, *SessionState)
    }


@pytest.mark.parametrize("policy", [PARTIAL_2F, ENROLL, AUTHED])
def test_proof_required_for_every_non_public_policy(policy: RoutePolicy) -> None:
    assert proof_required(policy, cookie_only_navigation=False) is True


def test_proof_not_required_for_public() -> None:
    assert proof_required(PUBLIC, cookie_only_navigation=False) is False
    assert proof_required(PUBLIC, cookie_only_navigation=True) is False


def test_proof_not_required_for_flagged_navigation() -> None:
    assert proof_required(AUTHED, cookie_only_navigation=True) is False


@pytest.mark.parametrize(
    ("policy", "user_required", "expected"),
    [
        (TotpPolicy.OFF, False, TotpRequirement.NONE),
        (TotpPolicy.OPTIONAL, False, TotpRequirement.NONE),
        (TotpPolicy.REQUIRED, False, TotpRequirement.ENROLL_ALLOWED),
        (TotpPolicy.REQUIRED, True, TotpRequirement.ENROLL_ALLOWED),
        (TotpPolicy.OPTIONAL, True, TotpRequirement.ENROLL_ALLOWED),
        (TotpPolicy.OFF, True, TotpRequirement.BLOCKED),
    ],
)
def test_totp_requirement(
    policy: TotpPolicy, user_required: bool, expected: TotpRequirement
) -> None:
    assert totp_requirement(policy, user_required) is expected
