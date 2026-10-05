"""The hub's own routes when auth is on (HLD 2.4 "Hub-only routes", 11.18; L4).

Owner: T-KOv2qD. ``service/hub.py`` keeps today's index for auth off and delegates here for auth
on, because a nested ``def index(request: Request)`` inside ``build_hub_app`` would get a 422:
that module uses ``from __future__ import annotations`` plus a lazy ``fastapi`` import, so
``Request`` would be unresolvable there.

* **R1a:** this file uses ``from __future__ import annotations``, so every name a handler
  annotation uses (``Request``) is imported at module scope.
* **Flat routes only** (``app.add_api_route``), like every other auth route (HLD 13.1).
* The policies live in ``HUB_ROUTE_POLICIES`` (``GET /login`` and ``GET /auth-assets/{name}`` are
  PUBLIC); ``GET /`` is AUTHENTICATED by omission and is the one cookie-only navigation route
  (``HUB_COOKIE_ONLY_NAVIGATION``, security M1). The anonymous 303 to ``/login`` is the
  middleware's decision, never this handler's.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response

from ..constants import HUB_ASSET_ROUTE_PATH, HUB_LOGIN_PATH
from ..principal import Principal, current_principal
from ..runtime import AuthRuntime
from .hub_page import HUB_ASSET_TYPES, read_hub_asset, render_login_page

NO_STORE_HEADERS: Final = {"Cache-Control": "no-store"}
INDEX_PATH: Final = "/"
NOT_FOUND_STATUS: Final = 404

# What the hub renders for the signed-in index: (status payload, principal) -> HTML.
RenderIndex = Callable[[dict[str, Any], Principal | None], str]
HubStatusProvider = Callable[[], dict[str, Any]]


def register_hub_auth_routes(
    app: FastAPI,
    runtime: AuthRuntime,
    *,
    status_provider: HubStatusProvider,
    render_index: RenderIndex,
) -> None:
    """Add ``GET /``, ``GET /login`` and ``GET /auth-assets/{name}`` to the hub app (flat)."""

    def hub_index(request: Request) -> HTMLResponse:
        # The middleware has already enforced FULL state and set the cookie-only principal.
        html = render_index(status_provider(), current_principal(request))
        return HTMLResponse(html, headers=NO_STORE_HEADERS)

    def hub_login_page() -> HTMLResponse:
        return HTMLResponse(render_login_page(), headers=NO_STORE_HEADERS)

    def hub_asset(name: str) -> Response:
        try:
            body = read_hub_asset(name)
        except KeyError:
            return Response(status_code=NOT_FOUND_STATUS)  # no detail: nothing to enumerate
        return Response(body, media_type=HUB_ASSET_TYPES[name])

    app.add_api_route(
        INDEX_PATH, hub_index, methods=["GET"], response_class=HTMLResponse, include_in_schema=False
    )
    app.add_api_route(
        HUB_LOGIN_PATH,
        hub_login_page,
        methods=["GET"],
        response_class=HTMLResponse,
        include_in_schema=False,
    )
    app.add_api_route(HUB_ASSET_ROUTE_PATH, hub_asset, methods=["GET"], include_in_schema=False)
