"""T-G7qByZ: deny-by-default over every dashboard route (AC-17 dashboard, S1; HLD 13.2, 20.3 #1).

The app is the real ``create_app`` with ``install_stub_auth_routes`` standing in for the real
auth routes (T-rpKCjP adds ``test_route_enumeration_real_routes.py``). Built frontend and
unbuilt frontend are enumerated separately. The expected allowlist is a **literal** here, not
read back from ``policy.py``, so a table edit that widens it fails this file.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import APIRouter, FastAPI
from starlette.testclient import TestClient

from agent_orchestrator.auth.http.middleware import classify
from agent_orchestrator.auth.policy import (
    DASHBOARD_COOKIE_ONLY_NAVIGATION,
    DASHBOARD_ROUTE_POLICIES,
    MOUNT_METHOD,
    RoutePolicy,
)
from tests.auth.conftest import INDEX_HTML
from tests.auth.helpers.core import make_client, same_origin_headers
from tests.auth.helpers.enumeration import (
    anonymous_matrix,
    build_scope,
    classify_request,
    non_authenticated_set,
    require_route_contexts,
    route_entries,
)
from tests.auth.helpers.stub_runtime import StubRuntime, add_probe_route

require_route_contexts()  # skip the whole module, with a reason, on an older FastAPI

# HLD 13.2, dashboard rows: the complete non-AUTHENTICATED set.
AUTH_ALLOWLIST = {
    ("GET", "/api/auth/status"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
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
# The PUBLIC-policy subset: anonymous requests reach the application. (The partial-second-factor
# and enrollment routes are in the allowlist but still answer an anonymous caller 401.)
_PARTIAL_AND_ENROLL = {
    ("POST", "/api/auth/totp/verify"),
    ("POST", "/api/auth/totp/enroll/begin"),
    ("POST", "/api/auth/totp/enroll/confirm"),
}
# A GET to the /assets mount is the public pair (the mount's own key is MOUNT).
BUILT_PUBLIC_PAIRS = (BUILT_ALLOWLIST - _PARTIAL_AND_ENROLL - {(MOUNT_METHOD, "/assets")}) | {
    ("GET", "/assets")
}
UNBUILT_PUBLIC_PAIRS = UNBUILT_ALLOWLIST - _PARTIAL_AND_ENROLL
# The ten stub auth routes every enumerated app carries.
STUB_AUTH_TEMPLATES = 10


def test_allowlist_literals_have_the_documented_sizes() -> None:
    assert len(BUILT_ALLOWLIST) == 10 and len(UNBUILT_ALLOWLIST) == 8


@pytest.mark.parametrize(
    ("built", "allowlist"), [(True, BUILT_ALLOWLIST), (False, UNBUILT_ALLOWLIST)]
)
def test_non_authenticated_set_equals_the_allowlist(
    build_dashboard: Callable[..., FastAPI],
    stub_runtime: StubRuntime,
    built: bool,
    allowlist: set[tuple[str, str]],
) -> None:
    app = build_dashboard(stub_runtime, built=built)
    assert non_authenticated_set(app, DASHBOARD_ROUTE_POLICIES) == allowlist


@pytest.mark.parametrize("built", [True, False])
def test_anonymous_requests_are_401_except_on_public_pairs(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime, built: bool
) -> None:
    app = build_dashboard(stub_runtime, built=built)
    public_pairs = BUILT_PUBLIC_PAIRS if built else UNBUILT_PUBLIC_PAIRS
    public_templates = {template for _, template in public_pairs}
    seen: dict[str, int] = {}
    for method, template, status, body in anonymous_matrix(make_client(app), app):
        seen[template] = seen.get(template, 0) + 1
        if (method, template) in public_pairs:
            assert status != 401, (method, template)
        elif template in public_templates:
            # Another method on a PUBLIC template: PARTIAL gets that route's policy, then a 405
            # (or, for GET on a POST-only /api route, the guarded SPA fallback's 401).
            assert status in (401, 405), (method, template, status)
        else:
            assert status == 401, (method, template, status)
            assert body is not None and body["code"] == "not_authenticated", (method, template)
    assert all(count % 5 == 0 for count in seen.values())  # all five methods per route entry
    assert {template for _, template in AUTH_ALLOWLIST} <= set(seen)
    assert len([t for t in seen if t.startswith("/api/auth/")]) == STUB_AUTH_TEMPLATES


def test_public_get_pairs_are_served(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime
) -> None:
    client = make_client(build_dashboard(stub_runtime, built=True))
    assert client.get("/api/health").status_code == 200
    assert client.get("/").text == INDEX_HTML
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/api/auth/status").status_code == 200  # the stub handler (E1 is PUBLIC)


def test_every_policy_key_matches_a_route_of_the_built_app(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime
) -> None:
    entries = route_entries(build_dashboard(stub_runtime, built=True))
    for method, path in DASHBOARD_ROUTE_POLICIES:
        if method == MOUNT_METHOD:
            assert any(e.methods == (MOUNT_METHOD,) and e.name == path for e in entries), path
        else:
            assert any(e.template == path and method in e.methods for e in entries), (method, path)


def test_the_cookie_only_set_is_empty_for_the_dashboard() -> None:
    assert DASHBOARD_COOKIE_ONLY_NAVIGATION == frozenset()


def test_an_included_router_is_never_public(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime
) -> None:
    # Unbuilt: no catch-all fallback can mask the opaque router.
    app = build_dashboard(stub_runtime, built=False)
    router = APIRouter()
    router.add_api_route("/api/probe-included", lambda: {"ok": True}, methods=["GET"])
    # Same (method, path) as a PUBLIC table key, reached only through include_router:
    router.add_api_route("/api/health-included", lambda: {"ok": True}, methods=["GET"])
    app.include_router(router)

    route, policy, cookie_only = classify_request(
        app, "GET", "/api/probe-included", DASHBOARD_ROUTE_POLICIES
    )
    assert not hasattr(route, "path")  # FastAPI's opaque _IncludedRouter
    assert (policy, cookie_only) == (RoutePolicy.AUTHENTICATED, False)

    client = make_client(app)
    anonymous = client.get("/api/probe-included", headers=same_origin_headers(client))
    assert anonymous.status_code == 401
    issued = stub_runtime.issue()
    client.cookies.set(stub_runtime.cookie_name, issued.token)
    served = client.get("/api/probe-included", headers=same_origin_headers(client, issued.proof))
    assert served.status_code == 200  # the route works for an authenticated caller


def test_a_public_table_key_reached_through_include_router_stays_authenticated(
    stub_runtime: StubRuntime,
) -> None:
    app = FastAPI()
    router = APIRouter()
    router.add_api_route("/api/health", lambda: {"ok": True}, methods=["GET"])  # a PUBLIC key
    app.include_router(router)
    _route, policy, _ = classify(
        build_scope(app, "GET", "/api/health"), DASHBOARD_ROUTE_POLICIES, frozenset()
    )
    assert policy is RoutePolicy.AUTHENTICATED


def test_a_flat_probe_route_is_authenticated(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime
) -> None:
    app = build_dashboard(stub_runtime, built=True)
    add_probe_route(app, "/api/probe-flat", lambda: {"ok": True}, ["GET", "POST"])
    client = make_client(app)
    for method in ("GET", "POST"):
        kwargs = {"json": {}} if method == "POST" else {}
        response = client.request(
            method, "/api/probe-flat", headers=same_origin_headers(client), **kwargs
        )
        assert response.status_code == 401, method


def test_redoc_and_oauth_redirect_exist_only_with_auth_off(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime
) -> None:
    def paths(app: FastAPI) -> set[str]:
        return {getattr(r, "path", "") for r in app.router.routes}

    off, on = paths(build_dashboard(None)), paths(build_dashboard(stub_runtime))
    assert {"/redoc", "/docs/oauth2-redirect"} <= off
    assert not {"/redoc", "/docs/oauth2-redirect"} & on
    assert {"/api/docs", "/api/openapi.json"} <= on  # AUTHENTICATED, not removed


# --- /api guard and path probes (AC-5, dev-security #8) ---------------------------------------


def _authenticated(app: FastAPI, runtime: StubRuntime) -> tuple[TestClient, dict[str, str]]:
    client = make_client(app)
    issued = runtime.issue()
    client.cookies.set(runtime.cookie_name, issued.token)
    return client, same_origin_headers(client, issued.proof)


def test_an_unknown_api_path_is_401_anonymous_and_the_unchanged_404_when_authenticated(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime
) -> None:
    app = build_dashboard(stub_runtime, built=True)
    anonymous = make_client(app).get("/api/does-not-exist")
    assert anonymous.status_code == 401 and anonymous.json()["code"] == "not_authenticated"
    client, headers = _authenticated(app, stub_runtime)
    found = client.get("/api/does-not-exist", headers=headers)
    assert found.status_code == 404
    assert found.json() == {"detail": "no such endpoint: /api/does-not-exist"}


def test_an_anonymous_deep_link_gets_the_spa_shell(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime
) -> None:
    response = make_client(build_dashboard(stub_runtime, built=True)).get("/some/spa/route")
    assert response.status_code == 200 and response.text == INDEX_HTML


@pytest.mark.parametrize(
    "path", ["/api/x/", "//api/x", "/API/x", "/api%2Fx", "/api/auth/status/", "/Api/auth/login"]
)
def test_path_probes_never_reach_api_json(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime, path: str
) -> None:
    client = make_client(build_dashboard(stub_runtime, built=True))
    response = client.get("http://testserver" + path)
    assert response.status_code == 401 or response.text == INDEX_HTML, (path, response.text)


@pytest.mark.parametrize("path", ["/prefix/api/x", "/api/x", "/prefix/api/workspace", "/prefix"])
def test_a_root_path_app_never_serves_api_json_anonymously(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime, path: str
) -> None:
    app = build_dashboard(stub_runtime, built=True)
    client = TestClient(
        app, client=("127.0.0.1", 50000), root_path="/prefix", follow_redirects=False
    )
    response = client.get(path)
    assert response.status_code == 401 or response.text == INDEX_HTML, (path, response.text)
