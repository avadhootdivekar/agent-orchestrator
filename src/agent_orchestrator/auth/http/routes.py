"""Auth routes (HLD 11.18; L4). MINIMAL version owned by T-G7qByZ; T-rpKCjP extends it.

After T-G7qByZ, T-rpKCjP is this file's only editor (HLD 11.18 ownership table). This version
provides exactly what the auth-off dashboard needs: ``install_auth_routes`` stores the runtime
on ``app.state`` and, with auth off, adds the one disabled ``GET /api/auth/status``.

Routes are added **flat** (``app.add_api_route``), never through ``include_router``: an included
router is opaque to the middleware's route classification (HLD 13.1).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import Response

from ..constants import APP_STATE_AUTH_KEY, AUTH_STATUS_PATH
from .responses import JSON_CONTENT_TYPE, json_bytes

# E1 body with auth off (HLD 2.4): a named constant so AC-2 can compare it byte-for-byte.
# Key order is part of the contract and identical in both modes.
DISABLED_STATUS_BODY: dict[str, object] = {
    "enabled": False,
    "state": "disabled",
    "user": None,
    "pending_username": None,
    "second_factors": None,
    "enrollment_token_required": None,
    "policy": None,
    "session": None,
    "transport": None,
}


def add_disabled_auth_routes(app: FastAPI) -> None:
    """With auth off, ``GET /api/auth/status`` is the only auth route (HLD 11.18)."""
    body = json_bytes(DISABLED_STATUS_BODY)

    async def auth_status() -> Response:
        return Response(content=body, media_type=JSON_CONTENT_TYPE.decode("ascii"))

    app.add_api_route(AUTH_STATUS_PATH, auth_status, methods=["GET"])


def install_auth_routes(app: FastAPI, runtime: Any | None) -> None:
    """Record ``runtime`` on the app and register the auth routes. Must run BEFORE the SPA fallback.

    ``runtime is None`` -> only the disabled status route.
    """
    setattr(app.state, APP_STATE_AUTH_KEY, runtime)
    if runtime is None:
        add_disabled_auth_routes(app)
        return
    # TODO(T-rpKCjP): the enabled branch -- import the built-in route modules, add the core routes
    # (status, logout, keepalive), run the provider's route builders, register the AuthError
    # handler and call assert_flat_auth_routes(app). Until then an enabled runtime fails closed
    # rather than serving an app on which nobody can sign in.
    raise NotImplementedError("auth routes for an enabled runtime land with T-rpKCjP")
