"""T-QJ1vyQ: a cookie alone never yields a principal or slides (AC-44 dashboard part, S27, M1).

Security test gate 1. For every route context of the dashboard app, a valid FULL cookie and no
session proof must leave ``principal is None`` where the application sees it (a scope-capturing
shim around ``app.router``), must never reach ``SessionManager.principal_for`` and must not move
``last_activity_mono`` -- PUBLIC routes included. A synthetic app with the one flagged
cookie-only route proves the exception is real and slides only for a browser-attested
navigation.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI

from agent_orchestrator.auth.constants import SCOPE_PRINCIPAL_KEY
from agent_orchestrator.auth.policy import MOUNT_METHOD
from tests.auth.helpers.core import FakeClock, make_client, same_origin_headers
from tests.auth.helpers.edge import (
    NAV_PAGE_PATH,
    NAVIGATION_HEADERS,
    PAGE_PATH,
    EdgeApp,
    build_edge_app,
)
from tests.auth.helpers.enumeration import (
    ENUMERATED_METHODS,
    MUTATING,
    concrete_path,
    require_route_contexts,
    route_entries,
)
from tests.auth.helpers.stub_runtime import StubRuntime, capture_scope

require_route_contexts()  # skip the whole module, with a reason, on an older FastAPI

IDLE_STEP_SECONDS = 25.0


@pytest.fixture()
def principal_calls(stub_runtime: StubRuntime, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    calls: list[Any] = []
    real = stub_runtime.sessions.principal_for

    def spy(record: Any) -> Any:
        calls.append(record)
        return real(record)

    monkeypatch.setattr(stub_runtime.sessions, "principal_for", spy)
    return calls


def _last_activity(runtime: StubRuntime, issued: Any) -> float:
    record = runtime.record_of(issued)
    assert record is not None
    return float(record.last_activity_mono)


@pytest.mark.parametrize("built", [True, False])
def test_a_cookie_without_the_proof_yields_no_principal_on_any_dashboard_route(
    build_dashboard: Callable[..., FastAPI],
    stub_runtime: StubRuntime,
    fake_clock: FakeClock,
    principal_calls: list[Any],
    built: bool,
) -> None:
    app = build_dashboard(stub_runtime, built=built)
    capture = capture_scope(app)
    client = make_client(app)
    issued = stub_runtime.issue()
    client.cookies.set(stub_runtime.cookie_name, issued.token)
    headers = same_origin_headers(client)  # same-origin headers, but NO proof
    baseline = _last_activity(stub_runtime, issued)
    fake_clock.advance(IDLE_STEP_SECONDS)  # a slide would now be visible

    requests = 0
    for entry in route_entries(app):
        mount = entry.methods == (MOUNT_METHOD,)
        path = concrete_path(entry.template, mount=mount)
        for method in ENUMERATED_METHODS:
            kwargs: dict[str, Any] = {"headers": headers}
            if method in MUTATING:
                kwargs["json"] = {}
            client.request(method, path, **kwargs)
            requests += 1
            assert _last_activity(stub_runtime, issued) == baseline, (method, entry.template)
    assert requests >= 100
    assert capture.states, "no request reached the application at all"
    assert all(state[SCOPE_PRINCIPAL_KEY] is None for state in capture.states)
    assert principal_calls == []  # the principal was never even built
    assert _last_activity(stub_runtime, issued) == baseline


def test_public_routes_see_no_principal_even_with_a_valid_cookie(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime, fake_clock: FakeClock
) -> None:
    app = build_dashboard(stub_runtime, built=True)
    capture = capture_scope(app)
    client = make_client(app)
    issued = stub_runtime.issue()
    client.cookies.set(stub_runtime.cookie_name, issued.token)
    baseline = _last_activity(stub_runtime, issued)
    fake_clock.advance(IDLE_STEP_SECONDS)
    headers = same_origin_headers(client)
    for method, path in (
        ("GET", "/api/health"),
        ("GET", "/api/auth/status"),
        ("POST", "/api/auth/logout"),
        ("POST", "/api/auth/login"),
        ("GET", "/"),
    ):
        response = client.request(
            method, path, headers=headers, json={} if method == "POST" else None
        )
        assert response.status_code == 200, (method, path)
    assert len(capture.states) == 5
    assert all(state[SCOPE_PRINCIPAL_KEY] is None for state in capture.states)
    assert _last_activity(stub_runtime, issued) == baseline


# --- the one flagged route (synthetic app) ----------------------------------------------------


@pytest.fixture()
def flagged(stub_runtime: StubRuntime, principal_calls: list[Any]) -> tuple[EdgeApp, Any]:
    """The probe app with ``("GET", "/nav")`` flagged; a FULL cookie, no proof."""
    edge = build_edge_app(stub_runtime, allowed_hosts=None)
    issued, _headers = edge.login()
    return edge, issued


def _nav_get(edge: EdgeApp, extra: dict[str, str]) -> Any:
    return edge.client.get(NAV_PAGE_PATH, headers=extra)


def test_the_flagged_route_yields_a_principal_from_the_cookie_alone(
    flagged: tuple[EdgeApp, Any], principal_calls: list[Any]
) -> None:
    edge, issued = flagged
    capture = capture_scope(edge.app)
    assert _nav_get(edge, {}).status_code == 200
    (state,) = capture.states
    assert state[SCOPE_PRINCIPAL_KEY] is not None
    assert state[SCOPE_PRINCIPAL_KEY].session_id
    assert len(principal_calls) == 1
    assert edge.runtime.record_of(issued) is not None


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_a_browser_attested_navigation_slides_the_flagged_route(
    flagged: tuple[EdgeApp, Any], fake_clock: FakeClock, site: str
) -> None:
    edge, issued = flagged
    before = _last_activity(edge.runtime, issued)
    fake_clock.advance(IDLE_STEP_SECONDS)
    response = _nav_get(edge, {**NAVIGATION_HEADERS, "Sec-Fetch-Site": site})
    assert response.status_code == 200
    assert _last_activity(edge.runtime, issued) == before + IDLE_STEP_SECONDS


@pytest.mark.parametrize(
    "headers",
    [
        {},  # a script: no Fetch Metadata at all
        {"Sec-Fetch-Site": "same-origin"},  # fetch(), not a navigation
        {**NAVIGATION_HEADERS, "Sec-Fetch-Site": "cross-site"},  # a cross-site navigation
        {**NAVIGATION_HEADERS, "Sec-Fetch-Site": "same-site"},
        {**NAVIGATION_HEADERS},  # navigation shape but no Sec-Fetch-Site
        {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "same-origin"},  # no dest
        {"Sec-Fetch-Dest": "document", "Sec-Fetch-Site": "same-origin"},  # no mode
    ],
)
def test_the_flagged_route_does_not_slide_without_browser_attestation(
    flagged: tuple[EdgeApp, Any], fake_clock: FakeClock, headers: dict[str, str]
) -> None:
    edge, issued = flagged
    before = _last_activity(edge.runtime, issued)
    fake_clock.advance(IDLE_STEP_SECONDS)
    response = _nav_get(edge, headers)
    assert response.status_code == 200  # still served (the principal is set), just not slid
    assert _last_activity(edge.runtime, issued) == before


def test_the_unflagged_page_never_slides_or_yields_a_principal_without_the_proof(
    flagged: tuple[EdgeApp, Any], fake_clock: FakeClock
) -> None:
    edge, issued = flagged
    before = _last_activity(edge.runtime, issued)
    fake_clock.advance(IDLE_STEP_SECONDS)
    response = edge.client.get(
        PAGE_PATH, headers={**NAVIGATION_HEADERS, "Sec-Fetch-Site": "same-origin"}
    )
    assert response.status_code == 401
    assert _last_activity(edge.runtime, issued) == before
