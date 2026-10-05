"""A probe app for the CSRF / body-cap / proof tests (owner: T-QJ1vyQ; HLD sections 13.3, 20.2).

Plain FastAPI routes behind the real ``SecurityMiddleware`` (outer) and ``AuthMiddleware``
(inner), exactly in the production order. Test-only: never imported by ``src/``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from starlette.testclient import TestClient

from agent_orchestrator.auth.http.middleware import AuthMiddleware
from agent_orchestrator.auth.model import SessionState
from agent_orchestrator.auth.policy import (
    DASHBOARD_ROUTE_POLICIES,
    RouteKey,
    RoutePolicy,
)
from agent_orchestrator.ui.security import SecurityMiddleware
from tests.auth.helpers.core import make_client, same_origin_headers
from tests.auth.helpers.stub_runtime import StubRuntime, install_stub_auth_routes

PROBE_PATH = "/api/__probe"
ECHO_PATH = "/api/auth/__echo"
PAGE_PATH = "/page"  # AUTHENTICATED non-API page
NAV_PAGE_PATH = "/nav"  # the same, but flagged cookie-only navigation in the probe app
PROBE_STATUS = 204
HTML_ACCEPT = {"Accept": "text/html"}
NAVIGATION_HEADERS = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
TESTSERVER_ONLY = frozenset({"testserver"})


@dataclass
class EdgeApp:
    app: FastAPI
    client: TestClient
    runtime: StubRuntime
    echo_calls: list[bytes] = field(default_factory=list)

    def login(self, state: SessionState = SessionState.FULL) -> tuple[Any, dict[str, str]]:
        """A session (cookie set on the client) and same-origin headers carrying its proof."""
        issued = self.runtime.issue(state)
        self.client.cookies.set(self.runtime.cookie_name, issued.token)
        return issued, same_origin_headers(self.client, issued.proof)


def build_edge_app(
    runtime: StubRuntime,
    *,
    allowed_hosts: frozenset[str] | None = TESTSERVER_ONLY,
    cookie_only: frozenset[RouteKey] = frozenset({("GET", NAV_PAGE_PATH)}),
    extra_policies: Mapping[RouteKey, RoutePolicy] | None = None,
) -> EdgeApp:
    policies = {**DASHBOARD_ROUTE_POLICIES, ("POST", ECHO_PATH): RoutePolicy.PUBLIC}
    policies.update(extra_policies or {})
    app = FastAPI()
    app.add_middleware(
        AuthMiddleware, runtime=runtime, policies=policies, cookie_only_navigation=cookie_only
    )
    app.add_middleware(SecurityMiddleware, allowed_hosts=allowed_hosts)
    edge = EdgeApp(app=app, client=make_client(app), runtime=runtime)

    def probe() -> Response:
        return Response(status_code=PROBE_STATUS)

    async def echo(request: Request) -> Response:
        body = await request.body()
        edge.echo_calls.append(body)
        return Response(content=body, media_type="application/octet-stream")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.add_api_route(PROBE_PATH, probe, methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    app.add_api_route("/api/runs", probe, methods=["GET", "POST"])
    app.add_api_route(ECHO_PATH, echo, methods=["POST"])
    for page in (PAGE_PATH, NAV_PAGE_PATH):
        app.add_api_route(
            page, lambda: HTMLResponse("<p>page</p>"), methods=["GET"], response_class=HTMLResponse
        )
    install_stub_auth_routes(app)
    return edge
