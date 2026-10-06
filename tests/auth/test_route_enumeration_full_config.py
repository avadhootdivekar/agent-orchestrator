"""T-KQ6ZrY: deny-by-default over BOTH apps in the FULL configuration (AC-17; HLD 13.2).

The configuration rule of 13.2: the three ``/api/auth/totp/*`` allowlist rows exist only with a
second-factor service. This file builds the dashboard (built and unbuilt) and the hub with a REAL
runtime whose ``totp`` is set, so the computed non-AUTHENTICATED set equals the full 13.2 table
(the literals below), every table entry matches a real route (no stale entry), the two
AUTHENTICATED second-factor routes (E6, E7) exist, and a partial session reaches exactly its own
step.

The hub's own rows (``GET /login``, ``GET /auth-assets/{name}``, the cookie-only index) belong to
``hub_routes.py`` and T-KOv2qD's ``test_route_enumeration_hub.py``; here the hub is the bare
auth-only app of ``real_routes`` (middleware + ``install_auth_routes``), so its expected set is
the six ``AUTH_ROUTE_POLICIES`` rows.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_orchestrator.auth.model import SessionState
from agent_orchestrator.auth.policy import (
    AUTH_ROUTE_POLICIES,
    DASHBOARD_ROUTE_POLICIES,
    HUB_ROUTE_POLICIES,
    MOUNT_METHOD,
    RouteKey,
)
from tests.auth.helpers import real_routes
from tests.auth.helpers.enumeration import (
    anonymous_matrix,
    non_authenticated_set,
    require_route_contexts,
    route_entries,
)
from tests.auth.helpers.real_routes import Dash, DashFactory

require_route_contexts()  # skip the module, with a reason, on a FastAPI without the helper

dash_factory = real_routes.dash_factory  # the pytest fixture (an assignment: no F811 clash)

TOTP_PREFIX = "/api/auth/totp/"
# HLD 13.2, the "both" rows: the whole auth allowlist WITH the three TOTP rows.
AUTH_ALLOWLIST: set[RouteKey] = {
    ("GET", "/api/auth/status"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/totp/verify"),
    ("POST", "/api/auth/totp/enroll/begin"),
    ("POST", "/api/auth/totp/enroll/confirm"),
}
DASHBOARD_BUILT: set[RouteKey] = AUTH_ALLOWLIST | {
    ("GET", "/api/health"),
    ("GET", "/"),
    ("GET", "/{full_path:path}"),
    (MOUNT_METHOD, "/assets"),
}
DASHBOARD_UNBUILT: set[RouteKey] = AUTH_ALLOWLIST | {("GET", "/api/health"), ("GET", "/")}
# Every /api/auth/* template of a TOTP-enabled app: the ten HLD 2.4 routes (E1-E10).
FULL_AUTH_TEMPLATES = {
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/keepalive",
    "/api/auth/password",
    "/api/auth/totp/verify",
    "/api/auth/totp/enroll/begin",
    "/api/auth/totp/enroll/confirm",
    "/api/auth/totp/disable",
    "/api/auth/totp/recovery-codes",
}
TOTP_TEMPLATES = {path for path in FULL_AUTH_TEMPLATES if path.startswith(TOTP_PREFIX)}
# What each partial state may reach among the second-factor routes.
OWN_STEP: dict[SessionState, set[RouteKey]] = {
    SessionState.PARTIAL_SECOND_FACTOR: {("POST", "/api/auth/totp/verify")},
    SessionState.PARTIAL_ENROLL: {
        ("POST", "/api/auth/totp/enroll/begin"),
        ("POST", "/api/auth/totp/enroll/confirm"),
    },
}
DENIAL = {
    SessionState.PARTIAL_SECOND_FACTOR: "second_factor_required",
    SessionState.PARTIAL_ENROLL: "enrollment_required",
}


def full_dash(dash_factory: DashFactory, *, built: bool = True, kind: str = "ui") -> Dash:
    dash = dash_factory(built_frontend=built, kind=kind, with_totp=True)
    assert dash.runtime.totp is not None  # the configuration under test
    return dash


@pytest.fixture(params=[True, False], ids=["built", "unbuilt"])
def built(request: pytest.FixtureRequest) -> bool:
    flag: bool = request.param
    return flag


def test_the_literals_are_the_documented_table() -> None:
    assert len(DASHBOARD_BUILT) == 10 and len(DASHBOARD_UNBUILT) == 8  # 13.2: dashboard rows
    assert AUTH_ALLOWLIST == set(AUTH_ROUTE_POLICIES) and len(AUTH_ALLOWLIST) == 6


def test_the_dashboard_set_equals_the_full_table(dash_factory: DashFactory, built: bool) -> None:
    dash = full_dash(dash_factory, built=built)
    expected = DASHBOARD_BUILT if built else DASHBOARD_UNBUILT
    assert non_authenticated_set(dash.app, DASHBOARD_ROUTE_POLICIES) == expected


def test_the_hub_set_includes_the_three_totp_rows(dash_factory: DashFactory) -> None:
    dash = full_dash(dash_factory, kind="hub")
    computed = non_authenticated_set(dash.app, HUB_ROUTE_POLICIES)
    assert computed == AUTH_ALLOWLIST  # the bare hub app: only the shared auth rows
    assert {key for key in HUB_ROUTE_POLICIES if key in AUTH_ALLOWLIST} == AUTH_ALLOWLIST


@pytest.mark.parametrize("kind", ["ui", "hub"])
def test_every_auth_policy_entry_matches_a_real_route(dash_factory: DashFactory, kind: str) -> None:
    """No stale entry: with a TOTP service the three configuration-dependent rows are live."""
    entries = route_entries(full_dash(dash_factory, kind=kind).app)
    for method, path in AUTH_ROUTE_POLICIES:
        assert any(e.template == path and method in e.methods for e in entries), (method, path)


def test_the_no_stale_check_covers_every_dashboard_entry(dash_factory: DashFactory) -> None:
    entries = route_entries(full_dash(dash_factory).app)
    for method, path in DASHBOARD_ROUTE_POLICIES:
        if method == MOUNT_METHOD:
            assert any(e.methods == (MOUNT_METHOD,) and e.name == path for e in entries), path
        else:
            assert any(e.template == path and method in e.methods for e in entries), (method, path)


@pytest.mark.parametrize("kind", ["ui", "hub"])
def test_the_full_auth_surface_is_exactly_the_ten_documented_routes(
    dash_factory: DashFactory, kind: str
) -> None:
    entries = route_entries(full_dash(dash_factory, kind=kind).app)
    assert {e.template for e in entries if e.template.startswith("/api/auth/")} == (
        FULL_AUTH_TEMPLATES
    )


def test_anonymous_requests_to_the_second_factor_routes_are_401(
    dash_factory: DashFactory, built: bool
) -> None:
    dash = full_dash(dash_factory, built=built)
    seen: set[str] = set()
    for method, template, status, body in anonymous_matrix(dash.client, dash.app):
        if template not in TOTP_TEMPLATES:
            continue
        seen.add(template)
        if method == "POST":
            assert status == 401, (method, template, status)
            assert body is not None and body["code"] == "not_authenticated", template
        else:
            assert status in (401, 405), (method, template, status)
    assert seen == TOTP_TEMPLATES


@pytest.mark.parametrize("state", list(DENIAL))
def test_a_partial_session_reaches_exactly_its_own_second_factor_step(
    dash_factory: DashFactory, state: SessionState
) -> None:
    dash = full_dash(dash_factory)
    for entry in route_entries(dash.app):
        if not entry.template.startswith(TOTP_PREFIX):
            continue
        for method in entry.methods:
            issued = dash.issue(state)  # fresh each time: a failed step may burn the session
            dash.set_cookie(issued.token)
            response = dash.client.request(
                method, entry.template, json={}, headers=dash.headers(issued.proof)
            )
            body: dict[str, Any] = response.json()
            if (method, entry.template) in OWN_STEP[state]:
                assert body["code"] != DENIAL[state], (method, entry.template, body)
            else:
                assert response.status_code == 401, (method, entry.template)
                assert body["code"] == DENIAL[state], (method, entry.template, body)


def test_a_full_session_reaches_the_authenticated_second_factor_routes(
    dash_factory: DashFactory,
) -> None:
    dash = full_dash(dash_factory)
    issued = dash.issue_full()
    dash.set_cookie(issued.token)
    for path in ("/api/auth/totp/disable", "/api/auth/totp/recovery-codes"):
        response = dash.post(path, {}, proof=issued.proof)
        assert response.status_code == 400, path  # past the gate: a body-schema error
        assert response.json()["code"] == "invalid_request"
