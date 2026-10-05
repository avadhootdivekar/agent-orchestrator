"""T-G7qByZ: ``AuthMiddleware`` behaviour (HLD 13.1, 13.3; AC-11, AC-15, AC-17, AC-23, AC-44 parts).

Every authenticated request sends the session proof (``same_origin_headers(client, proof)``) so
the tests keep passing when T-QJ1vyQ turns proof enforcement on. Where a test needs a request
*without* the proof it asserts only what holds both before and after that enforcement (no
principal, no slide), never the status code.
"""

from __future__ import annotations

import dataclasses
import inspect
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import agent_orchestrator.ui.app as ui_app
from agent_orchestrator.auth import constants
from agent_orchestrator.auth.constants import (
    AUTH_KEEPALIVE_PATH,
    SCOPE_PROOF_OK_KEY,
    WS_POLICY_VIOLATION,
)
from agent_orchestrator.auth.errors import AuthError, ErrorCode, StoreUnavailableError
from agent_orchestrator.auth.http.middleware import (
    AuthMiddleware,
    classify,
    is_api_path,
    route_path,
)
from agent_orchestrator.auth.http.responses import clear_cookie_header, error_body, error_bytes
from agent_orchestrator.auth.model import SessionState
from agent_orchestrator.auth.policy import (
    DASHBOARD_COOKIE_ONLY_NAVIGATION,
    DASHBOARD_ROUTE_POLICIES,
    HUB_COOKIE_ONLY_NAVIGATION,
    HUB_ROUTE_POLICIES,
    RouteKey,
    RoutePolicy,
)
from agent_orchestrator.auth.principal import (
    Principal,
    auth_enabled,
    current_principal,
    require_principal,
)
from agent_orchestrator.ui.security import SecurityMiddleware
from tests.auth.helpers.core import make_client, same_origin_headers
from tests.auth.helpers.enumeration import build_scope, classify_request
from tests.auth.helpers.stub_runtime import (
    StubRuntime,
    capture_scope,
    install_stub_auth_routes,
    make_stub_runtime,
)

PUBLIC_PROBE = "/api/whoami-public"
NAV_PAGE = "/nav"
PLAIN_PAGE = "/plain"
HTML = {"Accept": "text/html"}
NAV_HEADERS = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
PRINCIPAL_FIELD_ORDER = [
    "username",
    "auth_method",
    "roles",
    "user_id",
    "realm",
    "session_id",
    "amr",
    "auth_time",
    "provider",
]


# --- a probe app: the middleware over plain FastAPI routes that record what they see ---------


@dataclass
class Seen:
    principal: Principal | None
    auth_enabled: bool
    proof_ok: bool
    required: Principal | None
    required_error: ErrorCode | None


@dataclass
class Probe:
    app: FastAPI
    client: TestClient
    runtime: StubRuntime
    seen: list[Seen] = field(default_factory=list)

    def login(self, state: SessionState = SessionState.FULL, **kwargs: Any) -> tuple[Any, dict]:
        """A session (cookie set on the client) and the headers carrying its proof."""
        issued = self.runtime.issue(state, **kwargs)
        self.client.cookies.set(self.runtime.cookie_name, issued.token)
        return issued, same_origin_headers(self.client, issued.proof)


def make_probe(
    runtime: StubRuntime,
    cookie_only: frozenset[RouteKey] = frozenset({("GET", NAV_PAGE)}),
) -> Probe:
    policies = {**DASHBOARD_ROUTE_POLICIES, ("GET", PUBLIC_PROBE): RoutePolicy.PUBLIC}
    app = FastAPI()
    app.add_middleware(
        AuthMiddleware, runtime=runtime, policies=policies, cookie_only_navigation=cookie_only
    )
    probe = Probe(app=app, client=make_client(app), runtime=runtime)

    def record(request: Request) -> dict[str, bool]:
        try:
            required, error = require_principal(request), None
        except AuthError as exc:
            required, error = None, exc.code
        probe.seen.append(
            Seen(
                current_principal(request),
                auth_enabled(request),
                getattr(request.state, SCOPE_PROOF_OK_KEY),
                required,
                error,
            )
        )
        return {"ok": True}

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.add_api_route("/api/whoami", record, methods=["GET", "POST"])
    app.add_api_route(PUBLIC_PROBE, record, methods=["GET"])
    app.add_api_route("/api/runs", record, methods=["GET"])
    app.add_api_route("/api/mutate", record, methods=["POST"])
    for page in (NAV_PAGE, PLAIN_PAGE):
        app.add_api_route(
            page,
            lambda: HTMLResponse("<p>page</p>"),
            methods=["GET"],
            response_class=HTMLResponse,
        )
    install_stub_auth_routes(app)
    return probe


@pytest.fixture()
def probe(stub_runtime: StubRuntime) -> Probe:
    return make_probe(stub_runtime)


def last_activity(runtime: StubRuntime, issued: Any) -> float:
    record = runtime.record_of(issued)
    assert record is not None
    return float(record.last_activity_mono)


# --- ordering and the API_PREFIX pin (AC-17 part) -------------------------------------------


def test_api_prefix_matches_the_dashboard_constant() -> None:
    assert constants.API_PREFIX == ui_app.API_PREFIX


def test_middleware_order_without_and_with_auth(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime
) -> None:
    for runtime in (None, stub_runtime):
        order = [m.cls for m in build_dashboard(runtime).user_middleware]
        assert order == [SecurityMiddleware, AuthMiddleware]


# --- construction checks (AC-44 part) --------------------------------------------------------


def _construct(cookie_only: frozenset[RouteKey], policies: Any = DASHBOARD_ROUTE_POLICIES) -> None:
    AuthMiddleware(
        lambda s, r, se: None,  # type: ignore[arg-type, return-value]
        runtime=None,
        policies=policies,
        cookie_only_navigation=cookie_only,
    )


@pytest.mark.parametrize(
    "key", [("GET", "/api/x"), ("POST", NAV_PAGE), ("GET", "/api"), ("HEAD", NAV_PAGE)]
)
def test_construction_rejects_api_or_non_get_cookie_only_keys(key: RouteKey) -> None:
    with pytest.raises(ValueError, match=re.escape(repr(key))):
        _construct(frozenset({key}))


def test_construction_rejects_a_key_present_in_the_policy_table() -> None:
    key = ("GET", "/")  # PUBLIC in the dashboard table
    with pytest.raises(ValueError, match=re.escape(repr(key))):
        _construct(frozenset({key}))


def test_construction_accepts_the_shipped_flag_sets() -> None:
    _construct(DASHBOARD_COOKIE_ONLY_NAVIGATION, DASHBOARD_ROUTE_POLICIES)
    _construct(HUB_COOKIE_ONLY_NAVIGATION, HUB_ROUTE_POLICIES)
    _construct(frozenset({("GET", NAV_PAGE)}))


# --- classify (AC-4) -------------------------------------------------------------------------


def _two_method_app() -> FastAPI:
    app = FastAPI()
    app.add_api_route("/p", lambda: None, methods=["POST", "GET"])
    return app


def test_partial_match_picks_the_first_sorted_method_with_an_entry() -> None:
    app = _two_method_app()
    table = {("GET", "/p"): RoutePolicy.PUBLIC}
    results = {classify_request(app, "DELETE", "/p", table)[1] for _ in range(50)}
    assert results == {RoutePolicy.PUBLIC}  # GET < POST and only GET has an entry
    only_post = {("POST", "/p"): RoutePolicy.PARTIAL_SECOND_FACTOR}
    assert classify_request(app, "DELETE", "/p", only_post)[1] is RoutePolicy.PARTIAL_SECOND_FACTOR
    both = {("GET", "/p"): RoutePolicy.PUBLIC, ("POST", "/p"): RoutePolicy.ENROLLMENT}
    assert classify_request(app, "DELETE", "/p", both)[1] is RoutePolicy.PUBLIC  # alphabetical
    assert classify_request(app, "DELETE", "/p", {})[1] is RoutePolicy.AUTHENTICATED


def test_classify_source_never_takes_first_over_the_methods_set() -> None:
    source = inspect.getsource(classify)
    assert "first(" not in source
    assert "sorted(" in source


def test_head_to_a_get_only_api_route_is_partial_public_then_405(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime
) -> None:
    app = build_dashboard(stub_runtime)
    route, policy, cookie_only = classify_request(
        app, "HEAD", "/api/health", DASHBOARD_ROUTE_POLICIES
    )
    assert route.path == "/api/health"
    assert (policy, cookie_only) == (RoutePolicy.PUBLIC, False)
    response = make_client(app).head("/api/health")
    assert response.status_code == 405


def test_framework_doc_routes_are_authenticated_when_auth_is_off(
    build_dashboard: Callable[..., FastAPI],
) -> None:
    app = build_dashboard(None)
    for method, path in (("HEAD", "/redoc"), ("GET", "/docs/oauth2-redirect")):
        route, policy, _ = classify_request(app, method, path, DASHBOARD_ROUTE_POLICIES)
        assert route is not None and route.path == path  # a Starlette Route: FULL match
        assert policy is RoutePolicy.AUTHENTICATED  # no table key for it


def test_framework_doc_routes_do_not_exist_when_auth_is_on(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime
) -> None:
    unbuilt = build_dashboard(stub_runtime, built=False)
    for method, path in (("HEAD", "/redoc"), ("GET", "/docs/oauth2-redirect")):
        route, policy, _ = classify_request(unbuilt, method, path, DASHBOARD_ROUTE_POLICIES)
        assert route is None
        assert policy is RoutePolicy.AUTHENTICATED
    built = build_dashboard(stub_runtime, built=True)
    route, policy, _ = classify_request(
        built, "GET", "/docs/oauth2-redirect", DASHBOARD_ROUTE_POLICIES
    )
    assert route is not None and route.path == "/{full_path:path}"  # only the PUBLIC SPA fallback
    assert policy is RoutePolicy.PUBLIC


def test_cookie_only_requires_a_full_match() -> None:
    app = FastAPI()
    app.add_api_route(NAV_PAGE, lambda: None, methods=["GET"])
    table = {("GET", NAV_PAGE): RoutePolicy.AUTHENTICATED}  # key resolves even when PARTIAL
    flagged = frozenset({("GET", NAV_PAGE)})
    assert classify_request(app, "GET", NAV_PAGE, table, flagged)[2] is True
    assert classify_request(app, "HEAD", NAV_PAGE, table, flagged)[2] is False  # PARTIAL
    assert classify_request(app, "GET", NAV_PAGE, table, frozenset())[2] is False
    assert classify_request(app, "GET", "/other", table, flagged)[2] is False


def test_route_path_strips_root_path_like_starlette() -> None:
    def rp(path: str, root: str) -> str:
        return route_path({"path": path, "root_path": root})

    assert rp("/prefix/api/x", "/prefix") == "/api/x"
    assert rp("/prefix", "/prefix") == "/"
    assert rp("/prefixed/api", "/prefix") == "/prefixed/api"  # not a path-segment prefix
    assert rp("/api/x", "/prefix") == "/api/x"
    assert rp("/api/x", "") == "/api/x"
    assert is_api_path("/api") and is_api_path("/api/x") and not is_api_path("/apix")
    assert build_scope(FastAPI(), "GET", "/x")["method"] == "GET"


# --- principal (AC-11, S30) ------------------------------------------------------------------


def test_full_session_with_proof_yields_the_principal_contract(probe: Probe) -> None:
    _issued, headers = probe.login()
    response = probe.client.get("/api/whoami", headers=headers)
    assert response.status_code == 200
    (seen,) = probe.seen
    p = seen.principal
    assert p is not None
    assert [f.name for f in dataclasses.fields(p)] == PRINCIPAL_FIELD_ORDER
    assert isinstance(p.roles, list) and p.roles == []
    hash(p)  # a frozen dataclass holding a list is still hashable
    assert isinstance(p.amr, tuple)
    assert p.auth_time.tzinfo is not None and p.auth_time.utcoffset().total_seconds() == 0  # type: ignore[union-attr]
    assert re.fullmatch(r"ui:[0-9a-f]{12}", p.realm)
    assert seen.auth_enabled is True and seen.proof_ok is True
    assert seen.required is p or seen.required == p  # require_principal returns the principal


def test_two_requests_on_one_session_never_share_the_roles_list(probe: Probe) -> None:
    issued, headers = probe.login(roles=("viewer",))
    probe.client.get("/api/whoami", headers=headers)
    probe.client.get("/api/whoami", headers=headers)
    p1, p2 = (s.principal for s in probe.seen)
    assert p1 is not None and p2 is not None
    assert p1.roles == ["viewer"] and p1.roles is not p2.roles
    p1.roles.append("x")
    assert p2.roles == ["viewer"]
    assert probe.runtime.record_of(issued).roles == ("viewer",)  # type: ignore[union-attr]


def test_current_principal_is_the_object_in_request_state(probe: Probe) -> None:
    _issued, headers = probe.login()
    probe.client.get("/api/whoami", headers=headers)
    (seen,) = probe.seen
    assert seen.principal is not None and seen.required == seen.principal


@pytest.mark.parametrize("state", [SessionState.PARTIAL_SECOND_FACTOR, SessionState.PARTIAL_ENROLL])
def test_partial_session_has_no_principal_and_require_principal_raises(
    probe: Probe, state: SessionState
) -> None:
    _issued, headers = probe.login(state)
    response = probe.client.get(PUBLIC_PROBE, headers=headers)  # PUBLIC admits every state
    assert response.status_code == 200
    (seen,) = probe.seen
    assert seen.principal is None
    assert seen.required is None and seen.required_error is ErrorCode.NOT_AUTHENTICATED
    assert seen.auth_enabled is True


# --- revalidation (tri-state) ----------------------------------------------------------------


def test_bumped_epoch_revokes_the_session_and_clears_the_cookie(probe: Probe) -> None:
    issued, headers = probe.login()
    assert probe.client.get("/api/whoami", headers=headers).status_code == 200
    probe.runtime.provider.bump_epoch()
    response = probe.client.get("/api/whoami", headers=headers)
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"
    cleared = clear_cookie_header(probe.runtime.realm, secure=False)[1].decode()
    assert response.headers["set-cookie"] == cleared
    assert probe.runtime.record_of(issued) is None


def test_unavailable_store_answers_503_and_keeps_the_session(probe: Probe) -> None:
    issued, headers = probe.login()
    probe.runtime.provider.corrupt()
    response = probe.client.get("/api/whoami", headers=headers)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert response.content == error_bytes(StoreUnavailableError())
    assert "set-cookie" not in response.headers
    assert probe.runtime.record_of(issued) is not None
    probe.runtime.provider.restore()
    assert probe.client.get("/api/whoami", headers=headers).status_code == 200


# --- cookies (S26) ---------------------------------------------------------------------------


def test_duplicate_realm_cookie_is_no_session_and_warns_once(
    probe: Probe, caplog: pytest.LogCaptureFixture
) -> None:
    issued, headers = probe.login()
    probe.client.cookies.clear()
    name = probe.runtime.cookie_name
    doubled = {**headers, "Cookie": f"{name}={issued.token}; {name}={issued.token}"}
    with caplog.at_level(logging.WARNING, logger="agent_orchestrator.auth.http.middleware"):
        first = probe.client.get("/api/whoami", headers=doubled)
        second = probe.client.get("/api/whoami", headers=doubled)
    assert first.status_code == second.status_code == 401
    assert "set-cookie" not in first.headers  # tossing must not log the real user out
    assert probe.runtime.record_of(issued) is not None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert issued.token not in warnings[0].getMessage()


def test_unknown_token_is_401_with_a_clear_cookie(probe: Probe) -> None:
    probe.client.cookies.set(probe.runtime.cookie_name, "A" * 43)
    response = probe.client.get("/api/whoami", headers=same_origin_headers(probe.client))
    assert response.status_code == 401
    cleared = clear_cookie_header(probe.runtime.realm, secure=False)[1].decode()
    assert response.headers["set-cookie"] == cleared


def test_anonymous_html_navigation_is_redirected_to_the_login_path(probe: Probe) -> None:
    response = probe.client.get(PLAIN_PAGE, headers=HTML)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert response.headers["x-frame-options"] == "DENY"
    # an API path never redirects, and a non-HTML client gets the 401 envelope
    assert probe.client.get("/api/whoami", headers=HTML).status_code == 401
    assert probe.client.get(PLAIN_PAGE).status_code == 401


def test_hub_realm_redirects_to_its_login_page(tmp_path: Any, fake_clock: Any) -> None:
    hub = make_probe(make_stub_runtime(tmp_path, clock=fake_clock, kind="hub"))
    response = hub.client.get(PLAIN_PAGE, headers=HTML)
    assert (response.status_code, response.headers["location"]) == (303, "/login")


# --- sliding (AC-15 part, S20) ---------------------------------------------------------------


def test_get_polling_never_slides_the_session(probe: Probe) -> None:
    runtime = probe.runtime
    issued, headers = probe.login()
    idle = runtime.idle_seconds
    runtime.clock.advance(idle - 1)
    assert probe.client.get("/api/runs", headers=headers).status_code == 200
    assert last_activity(runtime, issued) == issued.record.last_activity_mono
    runtime.clock.advance(idle - 1)  # 2*(idle-1) after login: expired, polling did not extend it
    assert probe.client.get("/api/runs", headers=headers).status_code == 401


@pytest.mark.parametrize(
    ("method", "path"), [("POST", AUTH_KEEPALIVE_PATH), ("POST", "/api/mutate")]
)
def test_keepalive_and_mutations_with_the_proof_slide(probe: Probe, method: str, path: str) -> None:
    runtime = probe.runtime
    issued, headers = probe.login()
    runtime.clock.advance(runtime.idle_seconds - 1)
    assert probe.client.request(method, path, headers=headers, json={}).status_code == 200
    assert last_activity(runtime, issued) == runtime.clock.monotonic()
    runtime.clock.advance(runtime.idle_seconds - 1)  # alive only because of the slide
    assert probe.client.get("/api/runs", headers=headers).status_code == 200


def test_a_mutation_without_the_proof_does_not_slide(probe: Probe) -> None:
    runtime = probe.runtime
    issued, headers = probe.login()
    runtime.clock.advance(100)
    no_proof = {k: v for k, v in headers.items() if k.lower() != "x-ao-session-proof"}
    probe.client.post("/api/mutate", headers=no_proof, json={})  # status: pre/post enforcement
    assert last_activity(runtime, issued) == issued.record.last_activity_mono
    assert all(s.principal is None for s in probe.seen)
    assert all(s.proof_ok is False for s in probe.seen)


@pytest.mark.parametrize(
    ("site", "slides"),
    [
        ("same-origin", True),
        ("none", True),
        ("same-site", False),
        ("cross-site", False),
        (None, False),
    ],
)
def test_flagged_navigation_slides_only_when_browser_attested(
    probe: Probe, site: str | None, slides: bool
) -> None:
    runtime = probe.runtime
    issued, _ = probe.login()
    runtime.clock.advance(100)
    headers = {**NAV_HEADERS, **HTML}
    if site is not None:
        headers["Sec-Fetch-Site"] = site
    response = probe.client.get(NAV_PAGE, headers=headers)  # cookie only: no proof
    assert response.status_code == 200
    expected = runtime.clock.monotonic() if slides else issued.record.last_activity_mono
    assert last_activity(runtime, issued) == expected
    # the flagged route yields a principal from the cookie alone, whichever the slide outcome
    assert probe.runtime.record_of(issued) is not None


def test_flagged_route_without_navigation_headers_does_not_slide(probe: Probe) -> None:
    runtime = probe.runtime
    issued, _ = probe.login()
    runtime.clock.advance(100)
    probe.client.get(
        NAV_PAGE, headers={"Sec-Fetch-Site": "same-origin"}
    )  # fetch(), not a navigation
    assert last_activity(runtime, issued) == issued.record.last_activity_mono


def test_navigation_to_an_unflagged_page_never_slides(probe: Probe) -> None:
    runtime = probe.runtime
    issued, headers = probe.login()
    runtime.clock.advance(100)
    response = probe.client.get(
        PLAIN_PAGE, headers={**headers, **NAV_HEADERS, **HTML, "Sec-Fetch-Site": "same-origin"}
    )
    assert response.status_code == 200  # the proof is also present
    assert last_activity(runtime, issued) == issued.record.last_activity_mono


@pytest.mark.parametrize("state", [SessionState.PARTIAL_SECOND_FACTOR, SessionState.PARTIAL_ENROLL])
def test_partial_sessions_never_slide(probe: Probe, state: SessionState) -> None:
    runtime = probe.runtime
    issued, headers = probe.login(state)
    runtime.clock.advance(100)
    for path in ("/api/auth/totp/verify", "/api/auth/totp/enroll/begin"):
        probe.client.post(path, headers=headers, json={})
    probe.client.post(AUTH_KEEPALIVE_PATH, headers=headers, json={})
    assert last_activity(runtime, issued) == issued.record.last_activity_mono


# --- no principal or slide without the proof on PUBLIC routes (AC-44 part, S27) ---------------


@pytest.mark.parametrize(("method", "path"), [("POST", "/api/auth/logout"), ("GET", "/api/health")])
def test_public_routes_with_a_cookie_but_no_proof_get_no_principal_and_no_slide(
    build_dashboard: Callable[..., FastAPI],
    stub_runtime: StubRuntime,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    app = build_dashboard(stub_runtime)
    capture = capture_scope(app)
    principal_for_calls: list[object] = []
    real = stub_runtime.sessions.principal_for
    monkeypatch.setattr(
        stub_runtime.sessions,
        "principal_for",
        lambda record: principal_for_calls.append(record) or real(record),
    )
    client = make_client(app)
    issued = stub_runtime.issue()
    client.cookies.set(stub_runtime.cookie_name, issued.token)
    stub_runtime.clock.advance(100)

    no_proof = same_origin_headers(client)
    kwargs: dict[str, Any] = {"json": {}} if method == "POST" else {}
    response = client.request(method, path, headers=no_proof, **kwargs)

    assert response.status_code == 200
    assert capture.states, "the request must reach the application"
    assert all(state["principal"] is None for state in capture.states)
    assert all(state["auth_proof_ok"] is False for state in capture.states)
    assert principal_for_calls == []
    assert last_activity(stub_runtime, issued) == issued.record.last_activity_mono
    # still a valid session for a following request that carries the proof
    follow = client.get("/api/workspace", headers=same_origin_headers(client, issued.proof))
    assert follow.status_code == 200
    assert capture.states[-1]["principal"] is not None


# --- headers (AC-23 part) --------------------------------------------------------------------


def test_security_headers_on_deny_and_pass_through_responses(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime
) -> None:
    client = make_client(build_dashboard(stub_runtime))
    issued = stub_runtime.issue()
    realm_id = stub_runtime.realm.id

    denied = client.get("/api/workspace", headers=same_origin_headers(client))
    assert denied.status_code == 401
    assert denied.headers["x-frame-options"] == "DENY"
    assert denied.headers["cache-control"] == "no-store"
    assert denied.headers["www-authenticate"] == f'AO-Session realm="{realm_id}"'
    assert denied.content == error_body(ErrorCode.NOT_AUTHENTICATED)

    client.cookies.set(stub_runtime.cookie_name, issued.token)
    ok_headers = same_origin_headers(client, issued.proof)
    ok = client.get("/api/workspace", headers=ok_headers)
    assert ok.status_code == 200
    assert (ok.headers["x-frame-options"], ok.headers["cache-control"]) == ("DENY", "no-store")
    health = client.get("/api/health")  # PUBLIC but under /api
    assert health.headers["cache-control"] == "no-store"

    stub_runtime.provider.corrupt()
    unavailable = client.get("/api/workspace", headers=ok_headers)
    assert unavailable.status_code == 503
    assert unavailable.headers["x-frame-options"] == "DENY"
    assert unavailable.headers["cache-control"] == "no-store"
    assert "www-authenticate" not in unavailable.headers


def test_the_public_spa_shell_is_framed_denied_but_cacheable(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime
) -> None:
    client = make_client(build_dashboard(stub_runtime))
    for path in ("/", "/some/deep/link", "/assets/app.js"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["x-frame-options"] == "DENY"
        assert "cache-control" not in response.headers


def test_a_clear_cookie_is_not_added_when_the_app_sets_the_cookie_itself(
    stub_runtime: StubRuntime,
) -> None:
    own = f"{stub_runtime.cookie_name}=fresh; Path=/"
    app = FastAPI()
    policies = {**DASHBOARD_ROUTE_POLICIES, ("GET", "/api/set-own"): RoutePolicy.PUBLIC}
    app.add_middleware(AuthMiddleware, runtime=stub_runtime, policies=policies)

    @app.get("/api/set-own")
    def set_own() -> JSONResponse:
        response = JSONResponse({"ok": True})
        response.headers["set-cookie"] = own
        return response

    client = make_client(app)
    client.cookies.set(stub_runtime.cookie_name, "A" * 43)  # unknown token: would be cleared
    response = client.get("/api/set-own")
    assert response.status_code == 200
    assert response.headers.get_list("set-cookie") == [own]


# --- proof observation (AC-15 of the ticket) -------------------------------------------------


def test_proof_ok_is_observed_in_request_state(probe: Probe) -> None:
    issued, headers = probe.login()
    probe.client.get(PUBLIC_PROBE, headers=headers)
    wrong = {**headers, "X-AO-Session-Proof": "B" * 43}
    probe.client.get(PUBLIC_PROBE, headers=wrong)
    none = {k: v for k, v in headers.items() if k != "X-AO-Session-Proof"}
    probe.client.get(PUBLIC_PROBE, headers=none)
    probe.client.cookies.clear()
    probe.client.get(PUBLIC_PROBE)  # no session at all
    assert [s.proof_ok for s in probe.seen] == [True, False, False, False]
    assert [s.principal is not None for s in probe.seen] == [True, False, False, False]
    assert issued.record is not None


# --- websocket (AC-16) -----------------------------------------------------------------------


def test_websocket_connections_are_closed_with_policy_violation(probe: Probe) -> None:
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with probe.client.websocket_connect("/api/ws"):
            pass
    assert excinfo.value.code == WS_POLICY_VIOLATION == 1008


# --- lifespan --------------------------------------------------------------------------------


def test_lifespan_passes_through(stub_runtime: StubRuntime) -> None:
    probe = make_probe(stub_runtime)
    with probe.client:  # runs the lifespan startup and shutdown through the middleware
        assert probe.client.get("/api/health").status_code == 200
