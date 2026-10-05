"""T-G7qByZ: with auth off the dashboard is unchanged (AC-2 part, S17, NFR-1; HLD 20.3 #14).

The existing ``tests/ui`` suite is the main regression gate and passes unmodified. This file
adds what that suite cannot see: response header *names* and bodies against a hard-coded
pre-change snapshot, the OpenAPI path-key diff (exactly ``/api/auth/status``), the E1 disabled
body byte-for-byte, and the "no auth effect" scope contract.

The header snapshot was taken against the integration baseline (the parent of the auth epic's
first ``create_app`` edit). HLD 16 X4: if the approvals epic's ``X-Frame-Options`` line in
``ui/security.py`` merges first, it becomes part of that baseline and is added here at merge time.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable

import pytest
from fastapi import FastAPI, Request

import agent_orchestrator.ui.app as ui_app
from agent_orchestrator._version import get_version_string
from agent_orchestrator.auth.constants import APP_STATE_AUTH_KEY, SCOPE_PROOF_OK_KEY
from agent_orchestrator.auth.http.middleware import AuthMiddleware
from agent_orchestrator.auth.http.routes import DISABLED_STATUS_BODY
from agent_orchestrator.auth.principal import auth_enabled, current_principal, require_principal
from agent_orchestrator.ui.security import SecurityMiddleware
from tests.auth.conftest import INDEX_HTML
from tests.auth.helpers.core import make_client
from tests.auth.helpers.stub_runtime import add_probe_route

SECURITY_HEADERS = {
    "cross-origin-opener-policy",
    "cross-origin-resource-policy",
    "referrer-policy",
    "x-content-type-options",
}
JSON_HEADERS = {"content-length", "content-type"} | SECURITY_HEADERS
SPA_HEADERS = {
    "accept-ranges",
    "content-length",
    "content-security-policy",
    "content-type",
    "etag",
    "last-modified",
} | SECURITY_HEADERS

PRE_CHANGE_OPENAPI_PATHS = {
    "/api/files",
    "/api/files/content",
    "/api/files/html",
    "/api/general-instructions",
    "/api/health",
    "/api/launches",
    "/api/launches/{launch_id}",
    "/api/runs",
    "/api/runs/stats",
    "/api/runs/{run_id}",
    "/api/runs/{run_id}/activity",
    "/api/runs/{run_id}/cancel",
    "/api/runs/{run_id}/feedback",
    "/api/runs/{run_id}/graph",
    "/api/runs/{run_id}/log",
    "/api/runs/{run_id}/resume",
    "/api/runs/{run_id}/signals",
    "/api/runs/{run_id}/summary",
    "/api/templates",
    "/api/templates/{name}/instances",
    "/api/usage",
    "/api/workflows",
    "/api/workspace",
}
E1_DISABLED_BYTES = (
    b'{"enabled":false,"state":"disabled","user":null,"pending_username":null,'
    b'"second_factors":null,"enrollment_token_required":null,"policy":null,"session":null,'
    b'"transport":null}'
)


def test_response_header_names_are_unchanged(build_dashboard: Callable[..., FastAPI]) -> None:
    client = make_client(build_dashboard(None))
    health, runs, shell = client.get("/api/health"), client.get("/api/runs"), client.get("/")
    assert {k.lower() for k in health.headers} == JSON_HEADERS
    assert {k.lower() for k in runs.headers} == JSON_HEADERS
    assert {k.lower() for k in shell.headers} == SPA_HEADERS
    for response in (health, runs, shell):
        assert "x-frame-options" not in response.headers  # auth adds neither header when off
        assert "cache-control" not in response.headers


def test_response_bodies_are_unchanged(build_dashboard: Callable[..., FastAPI]) -> None:
    client = make_client(build_dashboard(None))
    assert client.get("/api/health").json() == {"status": "ok", "version": get_version_string()}
    assert client.get("/api/runs").json() == []
    assert client.get("/").text == INDEX_HTML


def test_status_is_the_disabled_body_byte_for_byte(build_dashboard: Callable[..., FastAPI]) -> None:
    response = make_client(build_dashboard(None)).get("/api/auth/status")
    assert response.status_code == 200
    assert response.content == E1_DISABLED_BYTES
    assert response.headers["content-type"] == "application/json"
    assert list(DISABLED_STATUS_BODY) == [
        "enabled",
        "state",
        "user",
        "pending_username",
        "second_factors",
        "enrollment_token_required",
        "policy",
        "session",
        "transport",
    ]


def test_openapi_differs_by_exactly_the_status_route(
    build_dashboard: Callable[..., FastAPI],
) -> None:
    paths = set(make_client(build_dashboard(None)).get("/api/openapi.json").json()["paths"])
    assert paths - PRE_CHANGE_OPENAPI_PATHS == {"/api/auth/status"}
    assert PRE_CHANGE_OPENAPI_PATHS - paths == set()


def test_framework_doc_routes_still_exist(build_dashboard: Callable[..., FastAPI]) -> None:
    app = build_dashboard(None)
    client = make_client(app)
    assert client.get("/redoc").status_code == 200
    assert client.get("/docs/oauth2-redirect").status_code == 200
    assert app.redoc_url == "/redoc"
    assert app.swagger_ui_oauth2_redirect_url == "/docs/oauth2-redirect"


def test_auth_on_drops_exactly_those_two_framework_routes(
    build_dashboard: Callable[..., FastAPI], stub_runtime: object
) -> None:
    app = build_dashboard(stub_runtime)  # type: ignore[arg-type]
    assert app.redoc_url is None and app.swagger_ui_oauth2_redirect_url is None
    assert app.docs_url == "/api/docs" and app.openapi_url == "/api/openapi.json"


def test_middleware_stack_and_auth_state_when_off(build_dashboard: Callable[..., FastAPI]) -> None:
    app = build_dashboard(None)
    assert [m.cls for m in app.user_middleware] == [SecurityMiddleware, AuthMiddleware]
    assert getattr(app.state, APP_STATE_AUTH_KEY) is None
    assert app.user_middleware[1].kwargs["runtime"] is None


def test_create_app_auth_is_keyword_only_and_defaults_to_off() -> None:
    parameter = inspect.signature(ui_app.create_app).parameters["auth"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is None


def test_a_probe_sees_no_principal_and_auth_disabled(
    build_dashboard: Callable[..., FastAPI],
) -> None:
    app = build_dashboard(None)
    seen: list[tuple[object, bool, object, bool]] = []

    def probe(request: Request) -> dict[str, bool]:
        seen.append(
            (
                current_principal(request),
                auth_enabled(request),
                require_principal(request),
                getattr(request.state, SCOPE_PROOF_OK_KEY),
            )
        )
        return {"ok": True}

    add_probe_route(app, "/api/probe", probe, ["GET"])
    client = make_client(app)
    # Cookies, proofs and cross-site hints are inert with auth off.
    response = client.get(
        "/api/probe",
        headers={
            "Cookie": "ao_sid_8765=" + "A" * 43 + "; ao_sid_8765=" + "B" * 43,
            "X-AO-Session-Proof": "C" * 43,
            "Sec-Fetch-Site": "cross-site",
        },
    )
    assert response.status_code == 200
    assert seen == [(None, False, None, False)]
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize("path", ["/api/runs", "/api/workspace", "/api/health", "/"])
def test_auth_off_never_denies_or_redirects(
    build_dashboard: Callable[..., FastAPI], path: str
) -> None:
    client = make_client(build_dashboard(None))
    response = client.get(path, headers={"Accept": "text/html"})
    assert response.status_code == 200
