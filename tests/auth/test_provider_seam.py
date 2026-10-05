"""T-KQ6ZrY: the provider seam (AC-10; HLD 10.7, 11.15.1, OQ-10; cut-line #3 not taken).

A test-only redirect-shaped provider (``RedirectFakeProvider``, ``provider_id = "test-redirect"``)
is plugged in through the public seams only -- ``AuthProvider``, ``register_route_builder`` and
``build_auth_runtime(provider=)`` -- and signs a user in through an external-IdP-like redirect
and a **cross-site** callback. Sessions, middleware and ``Principal`` need no change, which an AST
check pins (they never import the local provider).

The app is composed explicitly, because ``create_app``'s policy table is fixed (OQ-10 (1)): the
callback and start routes are PUBLIC in this test's own table. A redirect callback cannot hand the
session proof to the SPA (OQ-10 (2)), so the test reads the proof the fake captured.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from starlette.testclient import TestClient

import agent_orchestrator.auth as auth_pkg
from agent_orchestrator.auth.constants import SESSION_PROOF_HEADER
from agent_orchestrator.auth.errors import AuthError, ErrorCode
from agent_orchestrator.auth.http.middleware import AuthMiddleware
from agent_orchestrator.auth.http.responses import session_cookie_header
from agent_orchestrator.auth.http.routes import (
    ROUTE_BUILDERS,
    install_auth_routes,
    register_route_builder,
)
from agent_orchestrator.auth.model import SessionState
from agent_orchestrator.auth.policy import DASHBOARD_ROUTE_POLICIES, RoutePolicy
from agent_orchestrator.auth.principal import Principal
from agent_orchestrator.auth.provider import AuthProvider, Revalidation, UserView, VerifiedIdentity
from agent_orchestrator.auth.runtime import AuthRuntime, Realm, build_auth_runtime
from tests.auth.helpers.core import FakeClock, SeededEntropy
from tests.auth.helpers.provider import make_settings

PROVIDER_ID = "test-redirect"
START_PATH = "/api/auth/test/start"
CALLBACK_PATH = "/api/auth/test/callback"
LOGIN_PATH = "/api/auth/login"
PROBE_PATH = "/api/probe"
IDP_AUTHORIZE_URL = "https://idp.example/authorize"
SEAM_USER_ID = "b" * 32
SEAM_USERNAME = "idp-user"
SEAM_PORT = 8801
AUTH_DIR = Path(auth_pkg.__file__ or "").parent
FORBIDDEN_IMPORT = "local_provider"
# Modules that must stay provider-agnostic (the session seam, D11).
SEAM_FILES = ("sessions.py", "http/middleware.py", "principal.py")


class RedirectFakeProvider(AuthProvider):
    """An external-IdP-shaped provider: no password, no store; identity comes from a callback."""

    provider_id = PROVIDER_ID

    def __init__(self) -> None:
        self.states: dict[str, str] = {}  # the state kept server-side between start and callback
        self.captured_proof: str | None = None  # what the SPA handoff would have to deliver

    def check_ready(self) -> None:
        return None

    def revalidate(self, user_id: str, credential_epoch: int) -> Revalidation:
        return Revalidation.VALID if user_id == SEAM_USER_ID else Revalidation.REVOKED

    def user_view(self, username: str) -> UserView | None:
        if username != SEAM_USERNAME:
            return None
        return UserView(SEAM_USER_ID, SEAM_USERNAME, (), False, None, False)

    def identity(self) -> VerifiedIdentity:
        return VerifiedIdentity(
            SEAM_USER_ID,
            SEAM_USERNAME,
            (),
            1,
            "idp-store",
            next_state=SessionState.FULL,
            provider=PROVIDER_ID,
        )


def make_route_builder(provider: RedirectFakeProvider) -> Any:
    """The provider's route builder: start (302 out), callback (303 back in) and a login stub."""

    def add_redirect_routes(app: FastAPI, runtime: AuthRuntime) -> None:
        async def start(request: Request) -> Response:
            state = f"state-{len(provider.states) + 1}"
            provider.states[state] = "pending"
            return RedirectResponse(f"{IDP_AUTHORIZE_URL}?state={state}", status_code=302)

        async def callback(request: Request) -> Response:
            state = request.query_params.get("state", "")
            if provider.states.pop(state, None) is None:
                raise AuthError(ErrorCode.INVALID_REQUEST)
            issued = runtime.sessions.issue(
                provider.identity(),
                SessionState.FULL,
                client_key="203.0.113.7",
                # AuthMethod is the local vocabulary; a real IdP provider would extend it (OQ-10).
                auth_method="password",
            )
            provider.captured_proof = issued.proof
            response = Response(status_code=303, headers={"Location": "/"})
            name, value = session_cookie_header(runtime.realm, issued.token, secure=False)
            response.headers.append(name.decode("ascii"), value.decode("latin-1"))
            return response

        async def login(request: Request) -> Response:
            # AUTH_ROUTE_POLICIES lists POST /api/auth/login, so every provider must expose it.
            raise AuthError(ErrorCode.INVALID_REQUEST, "This provider signs in by redirect.")

        app.add_api_route(START_PATH, start, methods=["GET"])
        app.add_api_route(CALLBACK_PATH, callback, methods=["GET"])
        app.add_api_route(LOGIN_PATH, login, methods=["POST"])

    return add_redirect_routes


@pytest.fixture()
def provider() -> Iterator[RedirectFakeProvider]:
    fake = RedirectFakeProvider()
    register_route_builder(PROVIDER_ID, make_route_builder(fake))
    try:
        yield fake
    finally:
        ROUTE_BUILDERS.pop(PROVIDER_ID, None)  # no leak into other tests


def build_app(tmp_path: Path, provider: RedirectFakeProvider) -> tuple[FastAPI, AuthRuntime]:
    """``FastAPI()`` + ``AuthMiddleware`` (own policy table) + ``install_auth_routes`` + a probe."""
    runtime = build_auth_runtime(
        make_settings(tmp_path),
        Realm("ui", SEAM_PORT, tmp_path),
        clock=FakeClock(),
        entropy=SeededEntropy(5),
        provider=provider,
    )
    policies = {
        **DASHBOARD_ROUTE_POLICIES,
        ("GET", START_PATH): RoutePolicy.PUBLIC,
        ("GET", CALLBACK_PATH): RoutePolicy.PUBLIC,
    }
    app = FastAPI()
    app.add_middleware(AuthMiddleware, runtime=runtime, policies=policies)
    install_auth_routes(app, runtime)

    async def probe(request: Request) -> dict[str, Any]:
        principal: Principal = request.state.principal
        return {
            "provider": principal.provider,
            "username": principal.username,
            "amr": list(principal.amr),
        }

    app.add_api_route(PROBE_PATH, probe, methods=["GET"])  # AUTHENTICATED by omission
    return app, runtime


def test_a_redirect_provider_signs_in_across_sites(
    tmp_path: Path, provider: RedirectFakeProvider
) -> None:
    app, runtime = build_app(tmp_path, provider)
    assert runtime.totp is None  # a provider without a local second factor
    client = TestClient(app, base_url=f"http://127.0.0.1:{SEAM_PORT}", follow_redirects=False)

    start = client.get(START_PATH)
    assert start.status_code == 302
    location = start.headers["location"]
    assert location.startswith(IDP_AUTHORIZE_URL + "?state=")
    state = location.split("state=", 1)[1]

    # an anonymous caller has no session yet
    assert client.get(PROBE_PATH).status_code == 401

    # the IdP sends the browser back: a cross-site top-level navigation
    callback = client.get(
        CALLBACK_PATH,
        params={"state": state},
        headers={
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-User": "?1",
        },
    )
    assert callback.status_code == 303
    assert callback.headers["location"] == "/"
    cookie_name = runtime.realm.cookie_name(secure=False)
    assert client.cookies.get(cookie_name)
    assert provider.captured_proof is not None

    # the state is single use
    replay = client.get(CALLBACK_PATH, params={"state": state})
    assert replay.status_code == 400

    probe = client.get(PROBE_PATH, headers={SESSION_PROOF_HEADER: provider.captured_proof})
    assert probe.status_code == 200, probe.text
    assert probe.json() == {"provider": PROVIDER_ID, "username": SEAM_USERNAME, "amr": ["pwd"]}
    # the cookie alone is not enough (D25): the proof the redirect cannot deliver is required
    assert client.get(PROBE_PATH).status_code == 401


def test_the_seam_needs_no_provider_specific_code_in_the_session_layer(
    tmp_path: Path, provider: RedirectFakeProvider
) -> None:
    app, runtime = build_app(tmp_path, provider)
    # no TOTP routes exist for a provider without a second factor
    paths = {getattr(route, "path", "") for route in app.router.routes}
    assert not any(p.startswith("/api/auth/totp/") for p in paths)
    assert START_PATH in paths and CALLBACK_PATH in paths
    assert runtime.provider is provider


@pytest.mark.parametrize("relative", SEAM_FILES)
def test_the_seam_modules_never_import_the_local_provider(relative: str) -> None:
    tree = ast.parse((AUTH_DIR / relative).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    assert not [name for name in imported if FORBIDDEN_IMPORT in name.split(".")], relative


def test_an_unregistered_provider_is_refused_at_install(tmp_path: Path) -> None:
    """The seam is explicit: a provider with no route builder cannot start an app."""
    from agent_orchestrator.auth.errors import AuthConfigError

    fake = RedirectFakeProvider()
    runtime = build_auth_runtime(
        make_settings(tmp_path),
        Realm("ui", SEAM_PORT, tmp_path),
        clock=FakeClock(),
        entropy=SeededEntropy(5),
        provider=fake,
    )
    assert PROVIDER_ID not in ROUTE_BUILDERS
    with pytest.raises(AuthConfigError, match=PROVIDER_ID):
        install_auth_routes(FastAPI(), runtime)
