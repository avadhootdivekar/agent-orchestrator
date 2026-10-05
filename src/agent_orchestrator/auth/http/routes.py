"""Auth routes (HLD 2.4, 11.18; L4): the core HTTP contract of ``/api/auth/*``.

Owner: T-rpKCjP (after T-G7qByZ's minimal version this task is the file's only editor, HLD 11.18
ownership table). It provides ``install_auth_routes`` with the callable route-builder registry,
the core routes (E1 status, E9 keepalive, E10 logout), the local-password builder (E2 login, E8
password change) and the helpers every route module shares (``read_json_object``,
``require_str``, ``client_info``, ``user_payload``, ``rotate``, ``auth_error_handler``).
The second-factor routes (E3-E7) live in ``routes_second_factor.py`` and plug in through
:func:`register_route_builder`; the hub's routes live in ``hub_routes.py``.

Rules this module follows:

* **Flat routes only** (``app.add_api_route``): an included router is opaque to the middleware's
  route classification (HLD 13.1); :func:`assert_flat_auth_routes` enforces it.
* **R1a:** this file uses ``from __future__ import annotations``, so FastAPI resolves handler
  annotations from module globals. Every name a handler annotation uses (``Request``) is
  therefore imported at module scope, never only under ``TYPE_CHECKING`` (a missing one makes
  FastAPI treat the parameter as a query field and answer 422; ``test_routes_annotations.py``).
* **Errors:** handlers raise :class:`~agent_orchestrator.auth.errors.AuthError`;
  :func:`auth_error_handler` is the single place that renders it and logs 5xx once.
"""

from __future__ import annotations

import importlib
import ipaddress
import json
import logging
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.routing import APIRoute
from starlette.types import Scope

from ..audit import AuditEvent, AuditOutcome
from ..constants import (
    APP_STATE_AUTH_KEY,
    AUTH_ENROLL_BEGIN_PATH,
    AUTH_ENROLL_CONFIRM_PATH,
    AUTH_KEEPALIVE_PATH,
    AUTH_LOGIN_PATH,
    AUTH_LOGOUT_PATH,
    AUTH_PASSWORD_PATH,
    AUTH_STATUS_PATH,
    AUTH_TOTP_VERIFY_PATH,
    FORWARDED_HEADER,
    FORWARDING_HEADER_PREFIX,
    LOCAL_PROVIDER_ID,
    LOOPBACK_HOSTNAMES,
    MAX_LOGIN_PASSWORD_CHARS,
    MAX_PASSWORD_LENGTH,
    MAX_USERNAME_CHARS,
    SCOPE_PROOF_OK_KEY,
    SCOPE_SESSION_KEY,
)
from ..errors import AuthConfigError, AuthError, ErrorCode
from ..model import AuditEventName, SecondFactor, SessionState, TotpPolicy
from ..policy import AUTH_ROUTE_POLICIES, RouteKey
from ..provider import ClientInfo, VerifiedIdentity
from ..runtime import AuthRuntime, runtime_of
from ..seams import run_sync
from ..sessions import IssuedSession, SessionRecord
from ..store import format_timestamp
from ..throttle import canonical_client_key
from .responses import (
    CONTENT_TYPE_HEADER,
    JSON_CONTENT_TYPE,
    clear_cookie_header,
    error_bytes,
    error_headers,
    json_bytes,
    session_cookie_header,
)

logger = logging.getLogger(__name__)

# --- Named constants (no magic literals) ---
CLEAR_SITE_DATA_HEADER = "Clear-Site-Data"
CLEAR_SITE_DATA_CACHE = '"cache"'  # the quotes are part of the header value (HLD 2.1)
HOST_HEADER = b"host"
ANONYMOUS_STATE = "anonymous"
JSON_MEDIA_TYPE = JSON_CONTENT_TYPE.decode("ascii")
MAX_KEY_ECHO_CHARS = 64  # a rejected request key is echoed at most this long (never a value)
BUILTIN_SECOND_FACTORS: tuple[str, ...] = (
    SecondFactor.TOTP.value,
    SecondFactor.RECOVERY_CODE.value,
)
FIELD_USERNAME = "username"
FIELD_PASSWORD = "password"
FIELD_CURRENT_PASSWORD = "current_password"
FIELD_NEW_PASSWORD = "new_password"
FIELD_EVERYWHERE = "everywhere"
# Codes whose 401 carries the ``WWW-Authenticate`` challenge (HLD 2.2): "no usable session".
_CHALLENGE_CODES = frozenset(
    {
        ErrorCode.NOT_AUTHENTICATED,
        ErrorCode.SECOND_FACTOR_REQUIRED,
        ErrorCode.ENROLLMENT_REQUIRED,
    }
)
INSECURE_LOGIN_WARNING = (
    "a login over plain HTTP from a non-loopback client succeeded: the connection is not "
    "encrypted, so passwords and session cookies can be read on the network. Serve the "
    "dashboard over HTTPS (for example behind a TLS-terminating proxy)."
)
PROXY_SUSPECTED_WARNING = (
    "requests from 127.0.0.1 carry proxy headers or a non-loopback Host, but no trusted proxy is "
    "configured: treating them as remote. Set AO_UI_AUTH_TRUSTED_PROXIES to the proxy address."
)

# The three second-factor routes exist only when a TOTP service is wired (HLD 13.2).
TOTP_ROUTE_KEYS: frozenset[RouteKey] = frozenset(
    {
        ("POST", AUTH_TOTP_VERIFY_PATH),
        ("POST", AUTH_ENROLL_BEGIN_PATH),
        ("POST", AUTH_ENROLL_CONFIRM_PATH),
    }
)

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


# ---------------------------------------------------------------------------------------------
# Route-builder registry (v2.1, design-review M2)
# ---------------------------------------------------------------------------------------------

RouteBuilder = Callable[[FastAPI, AuthRuntime], None]

# provider id -> builders, in registration order. A list: several modules contribute routes for
# one provider (the local-password builder, then the TOTP builder).
ROUTE_BUILDERS: dict[str, list[RouteBuilder]] = {}

# Modules imported for their ``register_route_builder`` side effect. ``routes.py`` imports them
# lazily inside ``install_auth_routes`` (never at the top), so the import cycle cannot bite.
_BUILTIN_ROUTE_MODULES: tuple[str, ...] = ("agent_orchestrator.auth.http.routes_second_factor",)


def register_route_builder(provider_id: str, fn: RouteBuilder) -> None:
    """Append ``fn`` to ``provider_id``'s builders. Registering the same ``fn`` twice is a no-op."""
    builders = ROUTE_BUILDERS.setdefault(provider_id, [])
    if fn not in builders:
        builders.append(fn)


class PasswordProvider(Protocol):
    """The login-side surface the local-password routes call (``LocalPasswordProvider``)."""

    async def authenticate(
        self, username_raw: str, password: str, client: ClientInfo
    ) -> VerifiedIdentity: ...

    async def change_password(
        self, session: SessionRecord, current: str, new: str, client: ClientInfo
    ) -> int: ...

    async def logout_everywhere(self, session: SessionRecord) -> int: ...


def _password_provider(runtime: AuthRuntime) -> PasswordProvider:
    # The registered builders are the local-password ones, so the provider has this surface.
    return cast("PasswordProvider", runtime.provider)


# ---------------------------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------------------------


def add_disabled_auth_routes(app: FastAPI) -> None:
    """With auth off, ``GET /api/auth/status`` is the only auth route (HLD 11.18)."""
    body = json_bytes(DISABLED_STATUS_BODY)

    async def auth_status() -> Response:
        return Response(content=body, media_type=JSON_MEDIA_TYPE)

    app.add_api_route(AUTH_STATUS_PATH, auth_status, methods=["GET"])


def install_auth_routes(app: FastAPI, runtime: AuthRuntime | None) -> None:
    """Record ``runtime`` on the app and register the auth routes. Must run BEFORE the SPA fallback.

    ``runtime is None`` -> only the disabled status route. Otherwise: import the built-in route
    modules, add the core routes, run the provider's builders in registration order, install the
    ``AuthError`` handler and prove every auth route is a direct member of the router.
    """
    setattr(app.state, APP_STATE_AUTH_KEY, runtime)
    if runtime is None:
        add_disabled_auth_routes(app)
        return
    for module in _BUILTIN_ROUTE_MODULES:
        importlib.import_module(module)  # idempotent: registers its builder once
    provider_id = runtime.provider.provider_id
    builders = ROUTE_BUILDERS.get(provider_id)
    if not builders:
        raise AuthConfigError(f"no route builder registered for provider '{provider_id}'")
    add_core_auth_routes(app, runtime)
    for builder in builders:
        builder(app, runtime)
    # Starlette types handlers as taking ``Exception``; it dispatches only AuthError to this one.
    app.add_exception_handler(AuthError, auth_error_handler)  # type: ignore[arg-type]
    # The second-factor routes exist only when the TOTP builder adds them (never without a TOTP
    # service): as a group they may be absent, but never partly present.
    assert_flat_auth_routes(app, optional=TOTP_ROUTE_KEYS)


def assert_flat_auth_routes(app: FastAPI, *, optional: Iterable[RouteKey] = ()) -> None:
    """Every ``AUTH_ROUTE_POLICIES`` key must be a DIRECT member of ``app.router.routes``.

    The middleware classifies by walking ``app.router.routes``; a route reached only through
    ``include_router`` is opaque there and would silently fall back to AUTHENTICATED (HLD 13.1).
    ``optional`` keys are a group that may be absent as a whole (the second-factor routes,
    which exist only with a TOTP service) but not partly. Raises ``RuntimeError`` naming the
    first missing key.
    """
    skipped = frozenset(optional)
    direct: set[RouteKey] = set()
    for route in app.router.routes:
        if isinstance(route, APIRoute):
            direct.update((method, route.path) for method in route.methods or ())
    group_present = bool(skipped & direct)  # a started group must be complete
    for key in sorted(AUTH_ROUTE_POLICIES):
        if key not in direct and (key not in skipped or group_present):
            raise RuntimeError(
                f"auth route {key[0]} {key[1]} is not a direct member of the app router "
                "(routes must be added with app.add_api_route, never include_router)"
            )


def add_core_auth_routes(app: FastAPI, runtime: AuthRuntime) -> None:
    """E1 status, E9 keepalive, E10 logout (provider independent)."""

    async def auth_status(request: Request) -> Response:
        session, proof_ok = _scope_session(request)
        info = client_info(request.scope, runtime)
        return _json(status_body(runtime, session, proof_ok, info))

    async def auth_keepalive(request: Request) -> Response:
        session = _require_full_session(request)
        times = runtime.sessions.times(session)
        return _json(
            {
                "idle_expires_at": format_timestamp(times.idle_expires_at),
                "absolute_expires_at": format_timestamp(times.absolute_expires_at),
                "idle_timeout_seconds": times.idle_timeout_seconds,
            }
        )

    async def auth_logout(request: Request) -> Response:
        data = await read_json_object(
            request, allowed=frozenset({FIELD_EVERYWHERE}), optional_body=True
        )
        everywhere = _optional_bool(data, FIELD_EVERYWHERE)
        session, proof_ok = _scope_session(request)
        info = client_info(request.scope, runtime)
        if session is not None and proof_ok:
            await _logout(runtime, session, info, everywhere=everywhere)
        # Always, and identical for every caller: 200, the clear-cookie header and the cache purge.
        response = _json({"state": ANONYMOUS_STATE})
        _append_header(response, clear_cookie_header(runtime.realm, secure=info.secure))
        response.headers[CLEAR_SITE_DATA_HEADER] = CLEAR_SITE_DATA_CACHE
        return response

    app.add_api_route(AUTH_STATUS_PATH, auth_status, methods=["GET"])
    app.add_api_route(AUTH_LOGOUT_PATH, auth_logout, methods=["POST"])
    app.add_api_route(AUTH_KEEPALIVE_PATH, auth_keepalive, methods=["POST"])


def add_local_password_routes(app: FastAPI, runtime: AuthRuntime) -> None:
    """E2 login and E8 password change. The TOTP routes are a separate builder."""

    async def auth_login(request: Request) -> Response:
        data = await read_json_object(
            request,
            allowed=frozenset({FIELD_USERNAME, FIELD_PASSWORD}),
            required=frozenset({FIELD_USERNAME, FIELD_PASSWORD}),
        )
        username = _text(require_str(data, FIELD_USERNAME, min_len=1, max_len=MAX_USERNAME_CHARS))
        password = _text(require_str(data, FIELD_PASSWORD, max_len=MAX_LOGIN_PASSWORD_CHARS))
        session, proof_ok = _scope_session(request)
        info = client_info(request.scope, runtime)
        identity = await _password_provider(runtime).authenticate(username, password, info)
        full = identity.next_state is SessionState.FULL
        issued = runtime.sessions.issue(
            identity,
            identity.next_state,
            client_key=info.key,
            auth_method="password" if full else None,
            # D25: only a proof-verified session is retired; an unproven one just expires.
            replacing=session if proof_ok else None,
        )
        record = issued.record
        if full:
            await _audit(
                runtime,
                AuditEventName.LOGIN_SUCCESS,
                record,
                info,
                auth_method=record.auth_method,
            )
        _warn_insecure_login(runtime, info)
        return _session_response(runtime, issued, _login_body(runtime, issued), info)

    async def auth_password_change(request: Request) -> Response:
        data = await read_json_object(
            request,
            allowed=frozenset({FIELD_CURRENT_PASSWORD, FIELD_NEW_PASSWORD}),
            required=frozenset({FIELD_CURRENT_PASSWORD, FIELD_NEW_PASSWORD}),
        )
        current = _text(require_str(data, FIELD_CURRENT_PASSWORD, max_len=MAX_LOGIN_PASSWORD_CHARS))
        new = _text(require_str(data, FIELD_NEW_PASSWORD, max_len=MAX_LOGIN_PASSWORD_CHARS))
        session = _require_full_session(request)
        info = client_info(request.scope, runtime)
        epoch = await _password_provider(runtime).change_password(session, current, new, info)
        rotated = rotate(
            runtime,
            session,
            epoch=epoch,
            auth_method=session.auth_method,
            second_factor=session.second_factor,
            keep_absolute=True,
        )
        body = {
            "state": SessionState.FULL.api_state,
            "user": user_payload(runtime, rotated.record),
        }
        return _session_response(runtime, rotated, body, info)

    app.add_api_route(AUTH_LOGIN_PATH, auth_login, methods=["POST"])
    app.add_api_route(AUTH_PASSWORD_PATH, auth_password_change, methods=["POST"])


# Registered at import (HLD 11.18); ``routes_second_factor`` appends the TOTP builder after it.
register_route_builder(LOCAL_PROVIDER_ID, add_local_password_routes)


# ---------------------------------------------------------------------------------------------
# Shared helpers (also used by routes_second_factor.py)
# ---------------------------------------------------------------------------------------------


def user_payload(runtime: AuthRuntime, session: SessionRecord) -> dict[str, object]:
    """The E1 ``user`` object of a FULL session (HLD 2.4), key order as in the contract.

    Reads the store's current view of the account: an account removed meanwhile is a 401, so
    the caller never reports a user that no longer exists.
    """
    view = runtime.provider.user_view(session.username)
    if view is None:
        raise AuthError(ErrorCode.NOT_AUTHENTICATED)
    policy = runtime.settings.totp
    return {
        "username": session.username,
        "auth_method": session.auth_method,
        "roles": list(session.roles),
        "totp_enrolled": view.totp_enrolled,
        "recovery_codes_remaining": view.recovery_codes_remaining,
        "totp_required": view.totp_required,
        "can_enroll_totp": not view.totp_enrolled and policy is not TotpPolicy.OFF,
        "can_disable_totp": (
            view.totp_enrolled and policy is not TotpPolicy.REQUIRED and not view.totp_required
        ),
    }


def rotate(
    runtime: AuthRuntime,
    session: SessionRecord,
    *,
    epoch: int,
    auth_method: Any,
    second_factor: str,
    keep_absolute: bool,
) -> IssuedSession:
    """Replace ``session`` by a fresh FULL one for the post-change identity (HLD "Rotation").

    The new session carries the same ``user_id`` and the NEW credential epoch; every other
    session of the user in this realm is destroyed (other realms go stale through the epoch).
    """
    identity = VerifiedIdentity(
        session.user_id,
        session.username,
        session.roles,
        epoch,
        session.store_id,
        next_state=SessionState.FULL,
        provider=session.provider,
    )
    issued = runtime.sessions.issue(
        identity,
        SessionState.FULL,
        client_key=session.client_key,
        auth_method=auth_method,
        second_factor=second_factor,
        replacing=session,
        keep_absolute_deadline=keep_absolute,
    )
    runtime.sessions.destroy_user_sessions(
        session.user_id, except_session_id=issued.record.session_id
    )
    return issued


async def read_json_object(
    request: Request,
    *,
    allowed: frozenset[str],
    required: frozenset[str] = frozenset(),
    optional_body: bool = False,
) -> dict[str, Any]:
    """The request body as a JSON object restricted to ``allowed`` keys (``invalid_request`` else).

    An empty body is ``{}`` when ``optional_body`` (E10) and an error otherwise. Malformed JSON,
    pathological nesting (``RecursionError``), a non-object, an unknown key or a missing
    required key are all 400. Messages name a key, never a value.
    """
    raw = await request.body()
    if not raw.strip():
        if optional_body:
            data: dict[str, Any] = {}
        else:
            raise AuthError(ErrorCode.INVALID_REQUEST, "The request body is required.")
    else:
        try:
            parsed = json.loads(raw)
        except (ValueError, RecursionError) as exc:
            raise AuthError(
                ErrorCode.INVALID_REQUEST, "The request body is not valid JSON."
            ) from exc
        if not isinstance(parsed, dict):
            raise AuthError(ErrorCode.INVALID_REQUEST, "The request body must be a JSON object.")
        data = parsed
    for key in data:
        if key not in allowed:
            raise AuthError(ErrorCode.INVALID_REQUEST, f"Unknown field: {_echo_key(key)}.")
    for key in sorted(required):
        if key not in data:
            raise AuthError(ErrorCode.INVALID_REQUEST, f"Missing field: {_echo_key(key)}.")
    return data


def require_str(
    data: Mapping[str, Any],
    key: str,
    *,
    min_len: int = 0,
    max_len: int,
    optional: bool = False,
) -> str | None:
    """``data[key]`` as a ``min_len..max_len`` character string (``None``: optional and absent)."""
    if key not in data:
        if optional:
            return None
        raise AuthError(ErrorCode.INVALID_REQUEST, f"Missing field: {_echo_key(key)}.")
    value = data[key]
    if not isinstance(value, str):
        raise AuthError(ErrorCode.INVALID_REQUEST, f"Field {_echo_key(key)} must be a string.")
    if len(value) > max_len:
        raise AuthError(
            ErrorCode.INVALID_REQUEST, f"Field {_echo_key(key)} is too long (at most {max_len})."
        )
    if len(value) < min_len:
        raise AuthError(
            ErrorCode.INVALID_REQUEST, f"Field {_echo_key(key)} is too short (at least {min_len})."
        )
    return value


def client_info(scope: Scope, runtime: AuthRuntime) -> ClientInfo:
    """Who is calling and over what (HLD 11.18, security M2; ``ClientInfo`` is T-XchniS's shape).

    ``is_loopback`` needs a loopback peer, a loopback ``Host`` AND no forwarding header: a
    reverse proxy on the same machine looks like a loopback peer, and trusting that would let
    any remote user pass for local. ``proxy_suspected`` flags exactly that unconfigured case.
    """
    client = scope.get("client")
    peer = client[0] if client else None
    peer_loopback = _is_loopback_peer(peer)
    host = _host_name(scope)
    forwarded = _has_forwarding_header(scope)
    is_loopback = peer_loopback and host in LOOPBACK_HOSTNAMES and not forwarded
    proxy_suspected = (
        not runtime.settings.trusted_proxies
        and peer_loopback
        and (forwarded or host not in LOOPBACK_HOSTNAMES)
    )
    if proxy_suspected and not runtime.proxy_suspected_warned:
        runtime.proxy_suspected_warned = True
        logger.warning(PROXY_SUSPECTED_WARNING)
    return ClientInfo(
        key=canonical_client_key(peer),
        is_loopback=is_loopback,
        secure=scope.get("scheme") == "https",
        proxy_suspected=proxy_suspected,
    )


async def auth_error_handler(request: Request, exc: AuthError) -> Response:
    """Render an :class:`AuthError` with the same bytes the middleware's ``deny`` produces.

    The one place a 5xx is logged (ERROR, once, with ``cause_for_log`` and the exception chain);
    ``cause_for_log`` is never part of the response.
    """
    if exc.status >= 500:
        logger.error(
            "auth request failed with %s (%s): %s",
            exc.code.value,
            request.url.path,
            exc.cause_for_log or exc.detail,
            exc_info=exc,
        )
    runtime = runtime_of(request.app)
    headers = [(CONTENT_TYPE_HEADER, JSON_CONTENT_TYPE)]
    if runtime is not None:
        headers = error_headers(exc, runtime.realm, www_authenticate=exc.code in _CHALLENGE_CODES)
    return Response(
        content=error_bytes(exc),
        status_code=exc.status,
        headers={name.decode("ascii"): value.decode("latin-1") for name, value in headers},
    )


# ---------------------------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------------------------


def status_body(
    runtime: AuthRuntime, session: SessionRecord | None, proof_ok: bool, info: ClientInfo
) -> dict[str, object]:
    """The E1 body (HLD 2.4): non-anonymous only for a proof-verified session."""
    settings = runtime.settings
    active = session if proof_ok else None
    user: dict[str, object] | None = None
    if active is not None and active.state is SessionState.FULL:
        try:
            user = user_payload(runtime, active)
        except AuthError:  # the account vanished a moment after revalidation
            active = None
    partial = active is not None and active.state is not SessionState.FULL
    times = runtime.sessions.times(active) if active is not None else None
    return {
        "enabled": True,
        "state": active.state.api_state if active is not None else ANONYMOUS_STATE,
        "user": user,
        "pending_username": active.username if active is not None and partial else None,
        "second_factors": (
            list(BUILTIN_SECOND_FACTORS)
            if active is not None and active.state is SessionState.PARTIAL_SECOND_FACTOR
            else None
        ),
        "enrollment_token_required": (
            True if active is not None and active.state is SessionState.PARTIAL_ENROLL else None
        ),
        "policy": {
            "totp": settings.totp.value,
            "min_password_length": settings.min_password_length,
            "max_password_length": MAX_PASSWORD_LENGTH,
        },
        "session": (
            {
                "idle_timeout_seconds": times.idle_timeout_seconds,
                "idle_expires_at": format_timestamp(times.idle_expires_at),
                "absolute_expires_at": format_timestamp(times.absolute_expires_at),
            }
            if times is not None
            else None
        ),
        "transport": {
            "secure": info.secure,
            "client_is_loopback": info.is_loopback,
            "proxy_suspected": info.proxy_suspected,
        },
    }


def _login_body(runtime: AuthRuntime, issued: IssuedSession) -> dict[str, object]:
    record = issued.record
    if record.state is SessionState.FULL:
        return {"state": record.state.api_state, "user": user_payload(runtime, record)}
    if record.state is SessionState.PARTIAL_SECOND_FACTOR:
        return {"state": record.state.api_state, "second_factors": list(BUILTIN_SECOND_FACTORS)}
    return {"state": record.state.api_state, "enrollment_token_required": True}


async def _logout(
    runtime: AuthRuntime, session: SessionRecord, info: ClientInfo, *, everywhere: bool
) -> None:
    """Destroy a proof-verified session; ``everywhere`` on a FULL one also bumps the epoch."""
    event = AuditEventName.LOGOUT
    if everywhere and session.state is SessionState.FULL:
        try:
            await _password_provider(runtime).logout_everywhere(session)
        except AuthError as exc:
            # An already-revoked identity is what logging out everywhere wants; any other
            # failure (a 503) leaves the session intact so the user can retry.
            if exc.code is not ErrorCode.NOT_AUTHENTICATED:
                raise
        event = AuditEventName.LOGOUT_ALL
    runtime.sessions.destroy(session)
    await _audit(runtime, event, session, info, auth_method=session.auth_method)


async def _audit(
    runtime: AuthRuntime,
    event: AuditEventName,
    session: SessionRecord,
    info: ClientInfo,
    *,
    auth_method: str | None,
) -> None:
    await run_sync(
        runtime.audit.record,
        AuditEvent(
            event,
            AuditOutcome.SUCCESS,
            username=session.username,
            user_id=session.user_id,
            realm=runtime.realm.id,
            client_addr=info.key,
            session_id=session.session_id,
            auth_method=auth_method,
        ),
    )


def _warn_insecure_login(runtime: AuthRuntime, info: ClientInfo) -> None:
    """One WARNING per process for the first successful plain-HTTP login from a remote client."""
    if not info.secure and not info.is_loopback and not runtime.first_insecure_login_warned:
        runtime.first_insecure_login_warned = True
        logger.warning(INSECURE_LOGIN_WARNING)


def _session_response(
    runtime: AuthRuntime, issued: IssuedSession, body: Mapping[str, object], info: ClientInfo
) -> Response:
    """200 for a session-issuing endpoint: the body plus ``session_proof`` and the cookie."""
    response = _json({**body, "session_proof": issued.proof})
    _append_header(response, session_cookie_header(runtime.realm, issued.token, secure=info.secure))
    return response


def _json(payload: dict[str, object], status: int = 200) -> Response:
    return Response(content=json_bytes(payload), status_code=status, media_type=JSON_MEDIA_TYPE)


def _append_header(response: Response, header: tuple[bytes, bytes]) -> None:
    name, value = header
    response.headers.append(name.decode("ascii"), value.decode("latin-1"))


def _scope_session(request: Request) -> tuple[SessionRecord | None, bool]:
    """``(session, proof_ok)`` as set by ``AuthMiddleware`` (always present in the scope state)."""
    state = request.state
    session: SessionRecord | None = getattr(state, SCOPE_SESSION_KEY, None)
    return session, bool(getattr(state, SCOPE_PROOF_OK_KEY, False))


def _require_full_session(request: Request) -> SessionRecord:
    """The FULL session of an AUTHENTICATED route. The middleware guarantees it; re-check anyway."""
    session, proof_ok = _scope_session(request)
    if session is None or not proof_ok or session.state is not SessionState.FULL:
        raise AuthError(ErrorCode.NOT_AUTHENTICATED)
    return session


def _optional_bool(data: Mapping[str, Any], key: str) -> bool:
    value = data.get(key, False)
    if not isinstance(value, bool):
        raise AuthError(ErrorCode.INVALID_REQUEST, f"Field {_echo_key(key)} must be a boolean.")
    return value


def _text(value: str | None) -> str:
    """Narrow ``require_str``'s ``str | None`` for a field that is not optional."""
    if value is None:  # unreachable: require_str raises for a missing non-optional field
        raise AuthError(ErrorCode.INVALID_REQUEST)
    return value


def _echo_key(key: str) -> str:
    """A request key quoted for an error message: bounded and escaped, never a value."""
    shown = key if len(key) <= MAX_KEY_ECHO_CHARS else key[:MAX_KEY_ECHO_CHARS] + "..."
    return json.dumps(shown, ensure_ascii=True)


def _is_loopback_peer(peer: str | None) -> bool:
    if not peer:
        return False
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback


def _host_name(scope: Scope) -> str:
    """The ``Host`` header's hostname: lower-case, brackets and port stripped ("" if absent)."""
    for name, value in scope.get("headers", ()):
        if name == HOST_HEADER:
            try:
                return urlsplit("//" + value.decode("latin-1")).hostname or ""
            except ValueError:
                return ""
    return ""


def _has_forwarding_header(scope: Scope) -> bool:
    forwarding = FORWARDED_HEADER.encode("ascii")
    prefix = FORWARDING_HEADER_PREFIX.encode("ascii")
    return any(
        name == forwarding or name.startswith(prefix) for name, _ in scope.get("headers", ())
    )


__all__ = [
    "DISABLED_STATUS_BODY",
    "ROUTE_BUILDERS",
    "RouteBuilder",
    "add_core_auth_routes",
    "add_disabled_auth_routes",
    "add_local_password_routes",
    "assert_flat_auth_routes",
    "auth_error_handler",
    "client_info",
    "install_auth_routes",
    "read_json_object",
    "register_route_builder",
    "require_str",
    "rotate",
    "user_payload",
]
