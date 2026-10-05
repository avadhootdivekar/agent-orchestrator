"""T-rpKCjP: deny-by-default over the dashboard WITH THE REAL core auth routes (AC-17 part).

The companion of T-G7qByZ's stub-based ``test_route_enumeration_dashboard.py`` (which stays
unchanged and green): the same enumeration, but ``create_app`` runs the real
``install_auth_routes`` over a real runtime with ``totp=None``. Per HLD 13.2 (configuration rule)
the expected non-AUTHENTICATED set is the allowlist MINUS the three ``/api/auth/totp/*`` rows,
which exist only with a second-factor service (T-KQ6ZrY's full-configuration file covers them).
The allowlist is a literal here, so a table edit that widens it fails this file.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from agent_orchestrator.auth.model import SessionState
from agent_orchestrator.auth.policy import (
    DASHBOARD_COOKIE_ONLY_NAVIGATION,
    DASHBOARD_ROUTE_POLICIES,
    MOUNT_METHOD,
)
from tests.auth.conftest import INDEX_HTML
from tests.auth.helpers import real_routes
from tests.auth.helpers.enumeration import (
    ENUMERATED_METHODS,
    MUTATING,
    anonymous_matrix,
    concrete_path,
    non_authenticated_set,
    require_route_contexts,
    route_entries,
)
from tests.auth.helpers.real_routes import Dash, DashFactory

require_route_contexts()  # skip the module, with a reason, on a FastAPI without the helper

dash_factory = real_routes.dash_factory  # the pytest fixture (an assignment: no F811 clash)

# HLD 13.2, dashboard rows, WITHOUT the three TOTP rows (no second-factor service).
AUTH_ALLOWLIST = {
    ("GET", "/api/auth/status"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
}
TOTP_ROWS = {
    ("POST", "/api/auth/totp/verify"),
    ("POST", "/api/auth/totp/enroll/begin"),
    ("POST", "/api/auth/totp/enroll/confirm"),
}
BUILT_ALLOWLIST = AUTH_ALLOWLIST | {
    ("GET", "/api/health"),
    ("GET", "/"),
    ("GET", "/{full_path:path}"),
    (MOUNT_METHOD, "/assets"),
}
UNBUILT_ALLOWLIST = AUTH_ALLOWLIST | {("GET", "/api/health"), ("GET", "/")}
PUBLIC_PAIRS_BUILT = (BUILT_ALLOWLIST - {(MOUNT_METHOD, "/assets")}) | {("GET", "/assets")}
PUBLIC_PAIRS_UNBUILT = set(UNBUILT_ALLOWLIST)
# The real core routes of the dashboard, and only those, live under /api/auth.
REAL_AUTH_TEMPLATES = {
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/auth/keepalive",
    "/api/auth/password",
}
PARTIAL_CASES = [
    (SessionState.PARTIAL_SECOND_FACTOR, "second_factor_required"),
    (SessionState.PARTIAL_ENROLL, "enrollment_required"),
]


def real_dashboard(dash_factory: DashFactory, built: bool) -> Dash:
    dash = dash_factory(built_frontend=built)
    assert dash.runtime.totp is None  # the configuration under test
    return dash


@pytest.fixture(params=[True, False], ids=["built", "unbuilt"])
def built(request: pytest.FixtureRequest) -> bool:
    flag: bool = request.param
    return flag


def test_allowlist_literals_have_the_documented_sizes() -> None:
    assert len(BUILT_ALLOWLIST) == 7 and len(UNBUILT_ALLOWLIST) == 5  # 10 and 8 minus the TOTP rows


def test_non_authenticated_set_equals_the_allowlist(dash_factory: DashFactory, built: bool) -> None:
    dash = real_dashboard(dash_factory, built)
    expected = BUILT_ALLOWLIST if built else UNBUILT_ALLOWLIST
    assert non_authenticated_set(dash.app, DASHBOARD_ROUTE_POLICIES) == expected


def test_no_stale_table_entries(dash_factory: DashFactory) -> None:
    """Every policy-table entry (but the TOTP rows) matches a real route of the built app."""
    entries = route_entries(real_dashboard(dash_factory, True).app)
    for key in DASHBOARD_ROUTE_POLICIES:
        method, path = key
        if key in TOTP_ROWS:
            assert not any(e.template == path for e in entries), key  # absent without a service
        elif method == MOUNT_METHOD:
            assert any(e.methods == (MOUNT_METHOD,) and e.name == path for e in entries), path
        else:
            assert any(e.template == path and method in e.methods for e in entries), key


def test_the_real_core_auth_routes_are_exactly_the_documented_ones(
    dash_factory: DashFactory, built: bool
) -> None:
    entries = route_entries(real_dashboard(dash_factory, built).app)
    assert {e.template for e in entries if e.template.startswith("/api/auth/")} == (
        REAL_AUTH_TEMPLATES
    )


def test_anonymous_requests_are_401_except_on_public_pairs(
    dash_factory: DashFactory, built: bool
) -> None:
    dash = real_dashboard(dash_factory, built)
    public_pairs = PUBLIC_PAIRS_BUILT if built else PUBLIC_PAIRS_UNBUILT
    public_templates = {template for _, template in public_pairs}
    for method, template, status, body in anonymous_matrix(dash.client, dash.app):
        if (method, template) in public_pairs:
            assert status != 401, (method, template)
        elif template in public_templates:
            assert status in (401, 405), (method, template, status)  # another method: 405
        else:
            assert status == 401, (method, template, status)
            assert body is not None and body["code"] == "not_authenticated", (method, template)


def test_public_pairs_are_served(dash_factory: DashFactory) -> None:
    client = real_dashboard(dash_factory, True).client
    assert client.get("/api/health").status_code == 200
    assert client.get("/").text == INDEX_HTML
    assert client.get("/assets/app.js").status_code == 200
    status = client.get("/api/auth/status")
    assert status.status_code == 200 and status.json()["enabled"] is True


def partial_matrix(
    dash: Dash, state: SessionState
) -> Iterator[tuple[str, str, int, dict[str, Any] | None]]:
    """Every method on every route with a FRESH partial session and its proof per request.

    A fresh session each time, because the PUBLIC logout route legitimately destroys the session
    it is called with (the proof matches), which would turn the rest of the matrix anonymous.
    """
    for entry in route_entries(dash.app):
        path = concrete_path(entry.template, mount=entry.methods == (MOUNT_METHOD,))
        for method in ENUMERATED_METHODS:
            issued = dash.issue(state)
            dash.set_cookie(issued.token)
            kwargs: dict[str, Any] = {"headers": dash.headers(issued.proof)}
            if method in MUTATING:
                kwargs["json"] = {}
            response = dash.client.request(method, path, **kwargs)
            try:
                body = response.json()
            except ValueError:
                body = None
            yield method, entry.template, response.status_code, body


@pytest.mark.parametrize(("state", "code"), PARTIAL_CASES)
def test_a_partial_session_is_confined_on_the_real_routes(
    dash_factory: DashFactory, built: bool, state: SessionState, code: str
) -> None:
    dash = real_dashboard(dash_factory, built)
    public_pairs = PUBLIC_PAIRS_BUILT if built else PUBLIC_PAIRS_UNBUILT
    public_templates = {template for _, template in public_pairs}
    for method, template, status, body in partial_matrix(dash, state):
        if (method, template) in public_pairs:
            assert status != 401, (method, template)
        elif template in public_templates:
            assert status in (401, 405), (method, template, status)
            if status == 401:
                assert body is not None and body["code"] == code, (method, template)
        else:
            # keepalive, password and every other route: the partial state's own denial code
            assert status == 401, (method, template, status)
            assert body is not None and body["code"] == code, (method, template)


def test_the_cookie_only_set_is_empty_for_the_dashboard() -> None:
    assert DASHBOARD_COOKIE_ONLY_NAVIGATION == frozenset()


def test_a_full_session_reaches_every_authenticated_core_route(dash_factory: DashFactory) -> None:
    dash = real_dashboard(dash_factory, True)
    assert dash.login().status_code == 200
    assert dash.status()["state"] == "authenticated"
    assert dash.post("/api/auth/keepalive").status_code == 200
    assert dash.protected().status_code == 200
