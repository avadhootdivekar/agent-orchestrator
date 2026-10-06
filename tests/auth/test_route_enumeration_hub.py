"""T-KOv2qD: deny-by-default over the hub app (AC-17 hub part, S1; HLD 13.2, 20.3 #1).

The real ``build_hub_app(provider, auth=runtime)`` with ``totp=None``. Per HLD 13.2 (configuration
rule) the expected non-AUTHENTICATED set is the hub allowlist MINUS the three
``/api/auth/totp/*`` rows, which exist only with a second-factor service (T-KQ6ZrY's
``test_route_enumeration_full_config.py`` covers the hub with them). The allowlist is a literal
here, so a table edit that widens it fails this file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.auth.policy import (
    HUB_COOKIE_ONLY_NAVIGATION,
    HUB_ROUTE_POLICIES,
    RoutePolicy,
)
from tests.auth.helpers.core import FakeClock
from tests.auth.helpers.enumeration import (
    ENUMERATED_METHODS,
    anonymous_matrix,
    classify_request,
    concrete_path,
    non_authenticated_set,
    require_route_contexts,
    route_entries,
)
from tests.auth.helpers.hub import make_hub
from tests.auth.helpers.real_routes import Dash

require_route_contexts()  # skip the module, with a reason, on a FastAPI without the helper

# HLD 13.2, hub rows, WITHOUT the three TOTP rows (no second-factor service).
HUB_ALLOWLIST = {
    ("GET", "/api/auth/status"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("GET", "/login"),
    ("GET", "/auth-assets/{name}"),
}
TOTP_ROWS = {
    ("POST", "/api/auth/totp/verify"),
    ("POST", "/api/auth/totp/enroll/begin"),
    ("POST", "/api/auth/totp/enroll/confirm"),
}
# The authenticated hub surface the ticket names: index, status JSON and the FastAPI doc routes.
AUTHENTICATED_HUB_ROUTES = {
    ("GET", "/"),
    ("GET", "/api/service/status"),
    ("GET", "/docs"),
    ("GET", "/openapi.json"),
}
COOKIE_ONLY_ROUTE = ("GET", "/")


@pytest.fixture()
def hub(tmp_path: Path) -> Dash:
    dash = make_hub(tmp_path, FakeClock())
    assert dash.runtime.totp is None  # the configuration under test
    return dash


def test_allowlist_literal_has_the_documented_size() -> None:
    assert len(HUB_ALLOWLIST) == 5  # 8 hub rows minus the three TOTP rows


def test_non_authenticated_set_equals_the_hub_allowlist(hub: Dash) -> None:
    found = non_authenticated_set(hub.app, HUB_ROUTE_POLICIES, HUB_COOKIE_ONLY_NAVIGATION)
    assert found == HUB_ALLOWLIST


def test_no_stale_table_entries(hub: Dash) -> None:
    """Every hub policy-table entry (but the TOTP rows) matches a real route of the app."""
    entries = route_entries(hub.app)
    for key in HUB_ROUTE_POLICIES:
        method, path = key
        if key in TOTP_ROWS:
            assert not any(e.template == path for e in entries), key  # absent without a service
        else:
            assert any(e.template == path and method in e.methods for e in entries), key


def test_the_named_authenticated_routes_are_authenticated(hub: Dash) -> None:
    for method, template in AUTHENTICATED_HUB_ROUTES:
        _route, policy, _cookie_only = classify_request(
            hub.app, method, concrete_path(template), HUB_ROUTE_POLICIES, HUB_COOKIE_ONLY_NAVIGATION
        )
        assert policy is RoutePolicy.AUTHENTICATED, (method, template)


def test_the_hub_index_is_the_only_cookie_only_route(hub: Dash) -> None:
    assert HUB_COOKIE_ONLY_NAVIGATION == frozenset({COOKIE_ONLY_ROUTE})
    flagged = set()
    for entry in route_entries(hub.app):
        for method in ENUMERATED_METHODS:
            _route, _policy, cookie_only = classify_request(
                hub.app,
                method,
                concrete_path(entry.template),
                HUB_ROUTE_POLICIES,
                HUB_COOKIE_ONLY_NAVIGATION,
            )
            if cookie_only:
                flagged.add((method, entry.template))
    assert flagged == {COOKIE_ONLY_ROUTE}


def test_the_hub_registers_its_own_routes_flat(hub: Dash) -> None:
    templates = {e.template for e in route_entries(hub.app)}
    assert {"/", "/login", "/auth-assets/{name}", "/api/service/status"} <= templates
    assert not {"/redoc", "/docs/oauth2-redirect"} & templates  # security L2
    assert not any(t.startswith("/api/auth/totp") for t in templates)


def test_anonymous_requests_are_401_except_on_public_pairs(hub: Dash) -> None:
    public_templates = {template for _, template in HUB_ALLOWLIST}
    seen: set[str] = set()
    for method, template, status, body in anonymous_matrix(hub.client, hub.app):
        seen.add(template)
        if (method, template) in HUB_ALLOWLIST:
            assert status != 401, (method, template)
        elif template in public_templates:
            assert status in (401, 405), (method, template, status)  # another method: 405
        else:
            assert status == 401, (method, template, status)
            assert body is not None and body["code"] == "not_authenticated", (method, template)
    assert {template for _, template in HUB_ALLOWLIST} <= seen
    assert {"/", "/api/service/status", "/docs", "/openapi.json"} <= seen
