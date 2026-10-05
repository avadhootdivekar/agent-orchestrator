"""T-QJ1vyQ: Origin requirement and Fetch-Metadata checks (HLD 13.3 step 2; AC-23, S4)."""

from __future__ import annotations

import pytest

from agent_orchestrator.auth.constants import AUTH_LOGIN_PATH, AUTH_STATUS_PATH
from tests.auth.helpers.edge import (
    HTML_ACCEPT,
    NAVIGATION_HEADERS,
    PAGE_PATH,
    PROBE_PATH,
    PROBE_STATUS,
    TESTSERVER_ONLY,
    EdgeApp,
    build_edge_app,
)
from tests.auth.helpers.stub_runtime import StubRuntime

MUTATIONS = [
    ("POST", PROBE_PATH),
    ("PUT", PROBE_PATH),
    ("PATCH", PROBE_PATH),
    ("DELETE", PROBE_PATH),
    ("POST", AUTH_LOGIN_PATH),  # PUBLIC: the Origin rule covers it too
]
FOREIGN_ORIGINS = [
    "http://testserver:9999",  # another port
    "https://testserver",  # another scheme
    "http://evil.example",  # another host
    "null",
    "http://u@testserver",  # userinfo
]
LEGACY_REJECTION = "cross-origin request rejected"


@pytest.fixture(params=["allowlist", "allowlist_disabled"])
def edge(request: pytest.FixtureRequest, stub_runtime: StubRuntime) -> EdgeApp:
    """Both configurations: the host allowlist on (SecurityMiddleware first) and off."""
    allowed = TESTSERVER_ONLY if request.param == "allowlist" else None
    return build_edge_app(stub_runtime, allowed_hosts=allowed)


@pytest.mark.parametrize(("method", "path"), MUTATIONS)
def test_a_mutation_without_origin_is_403_origin_required(
    edge: EdgeApp, method: str, path: str
) -> None:
    _issued, headers = edge.login()
    headers.pop("Origin")
    response = edge.client.request(method, path, headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "origin_required"


@pytest.mark.parametrize(("method", "path"), MUTATIONS)
def test_a_same_origin_mutation_passes_the_middleware(
    edge: EdgeApp, method: str, path: str
) -> None:
    _issued, headers = edge.login()
    response = edge.client.request(method, path, headers=headers)
    assert response.status_code not in (401, 403, 413)
    if path == PROBE_PATH:
        assert response.status_code == PROBE_STATUS


@pytest.mark.parametrize("origin", FOREIGN_ORIGINS)
@pytest.mark.parametrize(("method", "path"), MUTATIONS)
def test_a_foreign_origin_is_403_from_the_layer_that_owns_it(
    stub_runtime: StubRuntime, origin: str, method: str, path: str
) -> None:
    # Allowlist on: the legacy SecurityMiddleware (outermost) answers first, in plain text.
    guarded = build_edge_app(stub_runtime, allowed_hosts=TESTSERVER_ONLY)
    _issued, headers = guarded.login()
    headers["Origin"] = origin
    response = guarded.client.request(method, path, headers=headers)
    assert response.status_code == 403
    assert response.text == LEGACY_REJECTION

    # Allowlist disabled: the legacy check passes everything, AuthMiddleware answers (JSON).
    open_edge = build_edge_app(stub_runtime, allowed_hosts=None)
    _issued, headers = open_edge.login()
    headers["Origin"] = origin
    response = open_edge.client.request(method, path, headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "origin_mismatch"
    assert response.headers["content-type"].startswith("application/json")


def test_a_csrf_denial_never_touches_the_session_or_cookie(edge: EdgeApp) -> None:
    issued, headers = edge.login()
    headers.pop("Origin")
    response = edge.client.post(PROBE_PATH, headers=headers)
    assert response.status_code == 403
    assert "set-cookie" not in response.headers
    assert edge.runtime.record_of(issued) is not None


# --- Fetch Metadata ---------------------------------------------------------------------------


@pytest.mark.parametrize("site", ["same-site", "cross-site"])
def test_a_cross_site_get_on_an_authenticated_route_is_403(edge: EdgeApp, site: str) -> None:
    _issued, headers = edge.login()
    headers["Sec-Fetch-Site"] = site
    response = edge.client.get("/api/runs", headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "cross_site_request"


@pytest.mark.parametrize("site", ["same-origin", "none", None])
def test_same_origin_none_or_absent_fetch_site_is_allowed(edge: EdgeApp, site: str | None) -> None:
    _issued, headers = edge.login()
    if site is None:
        headers.pop("Sec-Fetch-Site")
    else:
        headers["Sec-Fetch-Site"] = site
    assert edge.client.get("/api/runs", headers=headers).status_code == PROBE_STATUS


@pytest.mark.parametrize("site", ["same-site", "cross-site"])
def test_a_top_level_navigation_is_exempt_from_the_fetch_site_rule(
    edge: EdgeApp, site: str
) -> None:
    _issued, headers = edge.login()
    headers.update({**NAVIGATION_HEADERS, "Sec-Fetch-Site": site})
    response = edge.client.get(PAGE_PATH, headers=headers)
    assert response.status_code == 200  # not 403; the proof rule (AC 7) is satisfied here
    headers.pop("X-AO-Session-Proof")
    response = edge.client.get(PAGE_PATH, headers=headers)
    assert response.status_code == 401  # the exemption does not waive the proof
    assert response.json()["code"] == "not_authenticated"
    response = edge.client.get(PAGE_PATH, headers={**headers, **HTML_ACCEPT})
    assert response.status_code == 303


def test_navigation_exemption_needs_both_mode_and_dest(edge: EdgeApp) -> None:
    _issued, headers = edge.login()
    headers["Sec-Fetch-Site"] = "cross-site"
    for partial in (
        {"Sec-Fetch-Mode": "navigate"},
        {"Sec-Fetch-Dest": "document"},
        {"Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "document"},
        {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "iframe"},
    ):
        response = edge.client.get(PAGE_PATH, headers={**headers, **partial})
        assert response.status_code == 403, partial


def test_a_navigation_shape_never_exempts_a_mutation(edge: EdgeApp) -> None:
    _issued, headers = edge.login()
    headers.update({**NAVIGATION_HEADERS, "Sec-Fetch-Site": "cross-site"})
    response = edge.client.post(PROBE_PATH, headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "cross_site_request"


@pytest.mark.parametrize("site", ["same-site", "cross-site"])
def test_a_public_get_is_allowed_cross_site(edge: EdgeApp, site: str) -> None:
    for path in ("/api/health", AUTH_STATUS_PATH):
        response = edge.client.get(path, headers={"Sec-Fetch-Site": site})
        assert response.status_code == 200, path


@pytest.mark.parametrize(("method", "path"), MUTATIONS)
def test_a_cross_site_mutation_with_a_same_origin_origin_is_403(
    edge: EdgeApp, method: str, path: str
) -> None:
    _issued, headers = edge.login()
    headers["Sec-Fetch-Site"] = "cross-site"
    response = edge.client.request(method, path, headers=headers)
    assert response.status_code == 403
    assert response.json()["code"] == "cross_site_request"


def test_a_mutation_without_fetch_metadata_is_not_a_cross_site_denial(edge: EdgeApp) -> None:
    """Non-browser clients send no Sec-Fetch-* headers; the Origin rule still applies."""
    _issued, headers = edge.login()
    headers.pop("Sec-Fetch-Site")
    assert edge.client.post(PROBE_PATH, headers=headers).status_code == PROBE_STATUS
