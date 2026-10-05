"""``AuthMiddleware``: deny-by-default request gate (HLD 11.17, 13.1 and 13.3; L4).

A pure ASGI middleware. It classifies each request through the app's **policy table** (one
explicit table per app, ``auth/policy.py``; anything absent is AUTHENTICATED), reads the realm
cookie, looks the session up, computes the session-proof state, revalidates, decides, then
injects the principal and slides the idle timer **only when the request is attested** (HLD 13.3
steps 8-9, security M1), and finally adds the response headers.

Task split (HLD 11.17): T-G7qByZ owns the preamble, steps 1, 4, 5 (computing), 6-10 and
``deny()``. T-QJ1vyQ owns ``_check_csrf`` (step 2), ``_cap_auth_body`` (step 3) and the
enforcement branch inside ``_check_proof`` (step 5).

Auth off (``runtime is None``) is a pure pass-through: nothing is read, added or denied, so the
app is byte-identical to an app without this middleware (NFR-1, S17).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.routing import BaseRoute, Match, Mount
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ...ui.security import MUTATING_METHODS
from ..constants import (
    API_PREFIX,
    AUTH_API_PREFIX,
    AUTH_KEEPALIVE_PATH,
    HUB_LOGIN_PATH,
    MAX_AUTH_BODY_BYTES,
    SCOPE_AUTH_ENABLED_KEY,
    SCOPE_PRINCIPAL_KEY,
    SCOPE_PROOF_OK_KEY,
    SCOPE_SESSION_KEY,
    SESSION_PROOF_HEADER,
    WS_POLICY_VIOLATION,
)
from ..errors import AuthError, ErrorCode, StoreUnavailableError
from ..model import SessionState, denial_code
from ..policy import (
    MOUNT_METHOD,
    RouteKey,
    RoutePolicy,
    policy_allows,
    proof_required,
)
from ..provider import Revalidation
from ..sessions import SessionRecord
from .origin import origin_matches_host
from .responses import (
    clear_cookie_header,
    error_bytes,
    error_headers,
    parse_realm_cookie,
)

if TYPE_CHECKING:  # annotation only: keeps this module's import graph unchanged
    from ..runtime import AuthRuntime

logger = logging.getLogger(__name__)

# --- Named constants (no magic literals) ---
FRAME_OPTIONS_HEADER = "X-Frame-Options"
FRAME_OPTIONS_DENY = "DENY"
CACHE_CONTROL_HEADER = "Cache-Control"
CACHE_CONTROL_NO_STORE = "no-store"
SET_COOKIE = "Set-Cookie"
FETCH_SITE_HEADER = "sec-fetch-site"
FETCH_MODE_HEADER = "sec-fetch-mode"
FETCH_DEST_HEADER = "sec-fetch-dest"
FETCH_MODE_NAVIGATE = "navigate"
FETCH_DEST_DOCUMENT = "document"
ORIGIN_HEADER = "origin"
HOST_HEADER = "host"
# Fetch-Metadata ``Sec-Fetch-Site`` values that are not cross-site (HLD 13.3 step 2).
FETCH_SITE_NOT_CROSS = frozenset({"same-origin", "none"})
HTTP_REQUEST_MESSAGE = "http.request"
HTTP_DISCONNECT_MESSAGE = "http.disconnect"
# A browser-attested top-level navigation: same-origin or typed into the address bar / bookmark.
FETCH_SITE_BROWSER_NAV = frozenset({"same-origin", "none"})
NAVIGATION_METHODS = frozenset({"GET", "HEAD"})
HTML_MEDIA_TYPE = "text/html"
REDIRECT_STATUS = 303
UNAUTHORIZED_STATUS = 401
LOCATION_HEADER = b"location"
CONTENT_LENGTH_HEADER = b"content-length"


# --- Step 1: route classification (HLD 13.1) ---


def route_path(scope: Scope) -> str:
    """The path as the router sees it: ``root_path``-safe, the rule Starlette itself uses."""
    path: str = scope["path"]
    root: str = scope.get("root_path", "")
    if not root or not path.startswith(root):
        return path
    if path == root:
        return "/"
    return path[len(root) :] if path[len(root)] == "/" else path


def is_api_path(path: str) -> bool:
    return path == API_PREFIX or path.startswith(API_PREFIX + "/")


def classify(
    scope: Scope,
    policies: Mapping[RouteKey, RoutePolicy],
    cookie_only_navigation: frozenset[RouteKey],
) -> tuple[BaseRoute | None, RoutePolicy, bool]:
    """``(route, policy, cookie_only)`` for a request, by a policy-table lookup on the route.

    Walks ``router.routes`` in the order Starlette dispatches (first FULL match wins; the first
    PARTIAL is the fallback, which later becomes a 405). Anything unknown -- no match, an opaque
    included router -- is AUTHENTICATED. ``cookie_only`` is true only for a FULL match whose key
    is in ``cookie_only_navigation`` (v2.1, security M1).
    """
    router = scope["app"].router
    full: BaseRoute | None = None
    partial: BaseRoute | None = None
    for route in router.routes:
        match, _ = route.matches(scope)
        if match == Match.FULL:
            full = route
            break
        if match == Match.PARTIAL and partial is None:
            partial = route
    chosen = full or partial
    key: RouteKey | None = None
    policy = RoutePolicy.AUTHENTICATED
    if isinstance(chosen, Mount):
        policy = policies.get((MOUNT_METHOD, chosen.name or ""), RoutePolicy.AUTHENTICATED)
    elif chosen is not None and hasattr(chosen, "path"):
        template: str = chosen.path
        if full is not None:
            key = (scope["method"], template)
        else:
            # PARTIAL (path matched, method did not -> 405 later). Deterministic: never iterate
            # the methods *set* in hash order; take the alphabetically first one with an entry.
            hits = [
                (method, template)
                for method in sorted(getattr(chosen, "methods", None) or ())
                if (method, template) in policies
            ]
            key = hits[0] if hits else None
        policy = policies.get(key, RoutePolicy.AUTHENTICATED) if key else RoutePolicy.AUTHENTICATED
    # else: an opaque router (e.g. FastAPI's _IncludedRouter) stays AUTHENTICATED: never public.
    if (
        is_api_path(route_path(scope))
        and policy is not RoutePolicy.AUTHENTICATED
        and not str(getattr(chosen, "path", "")).startswith(API_PREFIX + "/")
    ):
        policy = RoutePolicy.AUTHENTICATED  # the SPA fallback can never open /api/*
    cookie_only = full is not None and key is not None and key in cookie_only_navigation
    return chosen, policy, cookie_only


# --- Steps 2 and 3: CSRF / Fetch Metadata and the auth body cap (HLD 13.3, T-QJ1vyQ) ---


def _check_csrf(
    scope: Scope, headers: Headers, policy: RoutePolicy, *, navigation: bool, sfs: str | None
) -> AuthError | None:
    """HLD 13.3 step 2: Origin and Fetch-Metadata checks, for every route (PUBLIC included).

    A mutation needs an ``Origin`` equal to the request's own origin (S4). Independently, a
    ``Sec-Fetch-Site`` other than same-origin/none is refused on any non-PUBLIC route or any
    mutation, except a top-level GET/HEAD navigation (``navigation``). Runs before the session
    lookup, so a denial never touches a session.
    """
    mutating = scope["method"] in MUTATING_METHODS
    if mutating:
        origin = headers.get(ORIGIN_HEADER)
        if origin is None:
            return AuthError(ErrorCode.ORIGIN_REQUIRED)
        if not origin_matches_host(
            origin, scheme=scope.get("scheme", "http"), host_header=headers.get(HOST_HEADER, "")
        ):
            return AuthError(ErrorCode.ORIGIN_MISMATCH)
    if (
        sfs is not None
        and sfs not in FETCH_SITE_NOT_CROSS
        and (policy is not RoutePolicy.PUBLIC or mutating)
        and not navigation
    ):
        return AuthError(ErrorCode.CROSS_SITE_REQUEST)
    return None


def _replay(body: bytes, receive: Receive, disconnected: bool) -> Receive:
    """A ``receive`` that serves the buffered ``body`` once, then defers to the real one."""
    pending: list[Message] = (
        [{"type": HTTP_DISCONNECT_MESSAGE}]
        if disconnected
        else [{"type": HTTP_REQUEST_MESSAGE, "body": body, "more_body": False}]
    )

    async def replay() -> Message:
        if pending:
            return pending.pop(0)
        return await receive()

    return replay


async def _cap_auth_body(
    scope: Scope, receive: Receive, rpath: str
) -> tuple[Receive, AuthError | None]:
    """HLD 13.3 step 3: the ``MAX_AUTH_BODY_BYTES`` cap on mutating ``/api/auth/*`` bodies.

    A declared ``Content-Length`` over the cap is refused outright; otherwise the body is
    buffered (a chunked body over the cap is refused as soon as it crosses it) and replayed to
    the app as one message, byte-identical. Every other path is returned untouched.
    """
    if scope["method"] not in MUTATING_METHODS or not rpath.startswith(AUTH_API_PREFIX + "/"):
        return receive, None
    declared = Headers(scope=scope).get("content-length")
    if declared is not None and declared.strip().isdigit():
        if int(declared) > MAX_AUTH_BODY_BYTES:
            return receive, AuthError(ErrorCode.BODY_TOO_LARGE)
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == HTTP_DISCONNECT_MESSAGE:
            return _replay(b"", receive, True), None
        chunk: bytes = message.get("body", b"")
        size += len(chunk)
        if size > MAX_AUTH_BODY_BYTES:
            return receive, AuthError(ErrorCode.BODY_TOO_LARGE)
        chunks.append(chunk)
        if not message.get("more_body", False):
            return _replay(b"".join(chunks), receive, False), None


@dataclass(frozen=True)
class ProofCheck:
    """Step 5 result. ``session`` is what the rest of the pipeline sees."""

    session: SessionRecord | None
    proof_ok: bool  # the session proof header matched this session
    attested: bool  # proof_ok, or the flagged cookie-only navigation
    browser_nav: bool  # a browser-attested top-level navigation


def _check_proof(
    runtime: AuthRuntime,
    session: SessionRecord | None,
    headers: Headers,
    *,
    policy: RoutePolicy,
    cookie_only: bool,
    navigation: bool,
    sfs: str | None,
) -> ProofCheck:
    """HLD 13.3 step 5: compute ``proof_ok`` / ``attested`` / ``browser_nav`` and enforce them.

    Enforcement (D25, security M1): on a proof-required route (every non-PUBLIC route except the
    flagged cookie-only navigation) a session that is not ``attested`` becomes ``None`` for THIS
    request only -- it is never destroyed and the cookie is never cleared, so a request that
    merely lacks the proof cannot log the victim out. PUBLIC routes keep the session in scope.
    """
    proof_ok = session is not None and runtime.sessions.proof_matches(
        session, headers.get(SESSION_PROOF_HEADER)
    )
    attested = proof_ok or cookie_only
    browser_nav = navigation and sfs in FETCH_SITE_BROWSER_NAV
    if (
        session is not None
        and not attested
        and proof_required(policy, cookie_only_navigation=cookie_only)
    ):
        session = None
    return ProofCheck(session, proof_ok, attested, browser_nav)


# --- Middleware ---


@dataclass
class _Ctx:
    """Per-request facts the send wrapper needs when the response starts."""

    rpath: str
    policy: RoutePolicy
    secure: bool
    clear_cookie: bool = False


class AuthMiddleware:
    """Pure-ASGI authentication gate. See the module docstring and HLD 13.3."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        runtime: AuthRuntime | None,
        policies: Mapping[RouteKey, RoutePolicy],
        cookie_only_navigation: frozenset[RouteKey] = frozenset(),
    ) -> None:
        for method, path in sorted(cookie_only_navigation):
            if method != "GET" or is_api_path(path):
                raise ValueError(
                    f"cookie_only_navigation key {(method, path)!r} "
                    f"must be a GET outside {API_PREFIX}"
                )
            if (method, path) in policies:
                raise ValueError(
                    f"cookie_only_navigation key {(method, path)!r} must be AUTHENTICATED "
                    "(absent from the policy table)"
                )
        self.app = app
        self._runtime = runtime
        self._policies = policies
        self._cookie_only_navigation = cookie_only_navigation
        # One WARNING per middleware instance (one per app, hence per process in practice).
        self._warned_duplicate_cookie = False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        runtime = self._runtime
        state: dict[str, Any] = scope.setdefault("state", {})
        state[SCOPE_PRINCIPAL_KEY] = None
        state[SCOPE_SESSION_KEY] = None
        state[SCOPE_PROOF_OK_KEY] = False
        state[SCOPE_AUTH_ENABLED_KEY] = runtime is not None
        if runtime is None:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": WS_POLICY_VIOLATION})
            return
        await self._handle_http(runtime, state, scope, receive, send)

    async def _handle_http(
        self,
        runtime: AuthRuntime,
        state: dict[str, Any],
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        headers = Headers(scope=scope)
        method: str = scope["method"]
        rpath = route_path(scope)
        sfs = headers.get(FETCH_SITE_HEADER)
        navigation = (
            method in NAVIGATION_METHODS
            and headers.get(FETCH_MODE_HEADER) == FETCH_MODE_NAVIGATE
            and headers.get(FETCH_DEST_HEADER) == FETCH_DEST_DOCUMENT
        )

        # 1. classification
        _route, policy, cookie_only = classify(scope, self._policies, self._cookie_only_navigation)
        secure = scope.get("scheme") == "https"
        ctx = _Ctx(rpath=rpath, policy=policy, secure=secure)
        out = self._injecting_send(runtime, send, ctx)

        # 2. CSRF / Fetch Metadata (T-QJ1vyQ)
        csrf_error = _check_csrf(scope, headers, policy, navigation=navigation, sfs=sfs)
        if csrf_error is not None:
            await self._deny(runtime, out, csrf_error)
            return

        # 3. auth body cap (T-QJ1vyQ)
        receive, cap_error = await _cap_auth_body(scope, receive, rpath)
        if cap_error is not None:
            await self._deny(runtime, out, cap_error)
            return

        # 4. cookie and session
        cookie_name = runtime.realm.cookie_name(secure=secure)
        value, duplicated = _realm_cookie(headers, cookie_name)
        if duplicated:
            self._warn_duplicate_cookie()  # never log the values (S26)
            value = None
        session = runtime.sessions.lookup(value)
        ctx.clear_cookie = value is not None and session is None and not duplicated

        # 5. session proof
        proof = _check_proof(
            runtime,
            session,
            headers,
            policy=policy,
            cookie_only=cookie_only,
            navigation=navigation,
            sfs=sfs,
        )
        session = proof.session
        state[SCOPE_PROOF_OK_KEY] = proof.proof_ok

        # 6. revalidation (tri-state)
        if session is not None:
            outcome = runtime.provider.revalidate(session.user_id, session.credential_epoch)
            if outcome is Revalidation.REVOKED:
                runtime.sessions.destroy(session)
                session = None
                ctx.clear_cookie = True
            elif outcome is Revalidation.UNAVAILABLE:
                # The store is unreadable, not the credentials revoked: keep the session.
                await self._deny(runtime, out, StoreUnavailableError())
                return
        state[SCOPE_SESSION_KEY] = session

        # 7. policy decision
        session_state = session.state if session is not None else None
        if not policy_allows(policy, session_state):
            if (
                method in NAVIGATION_METHODS
                and not is_api_path(rpath)
                and HTML_MEDIA_TYPE in headers.get("accept", "")
            ):
                await self._redirect(runtime, out)
                return
            await self._deny(runtime, out, AuthError(denial_code(session_state)))
            return

        if session is not None and session.state is SessionState.FULL:
            # 8. principal: only when attested (the proof matched, or the flagged cookie-only page)
            if proof.attested:
                state[SCOPE_PRINCIPAL_KEY] = runtime.sessions.principal_for(session)
            # 9. sliding: only with the proof, or a browser-attested navigation to a flagged
            # route. No GET poll, no unproven request, no other navigation ever slides.
            if proof.proof_ok and (method in MUTATING_METHODS or rpath == AUTH_KEEPALIVE_PATH):
                runtime.sessions.touch(session)
            elif cookie_only and proof.browser_nav:
                runtime.sessions.touch(session)

        # 10. dispatch with header injection
        await self.app(scope, receive, out)

    # --- helpers ---

    def _warn_duplicate_cookie(self) -> None:
        if not self._warned_duplicate_cookie:
            self._warned_duplicate_cookie = True
            logger.warning(
                "request carried the realm session cookie more than once; "
                "treating it as no session (cookie-tossing defence)"
            )

    async def _deny(self, runtime: AuthRuntime, send: Send, err: AuthError) -> None:
        """Send the error envelope for ``err`` (the same bytes the routes produce)."""
        body = error_bytes(err)
        headers = error_headers(
            err, runtime.realm, www_authenticate=err.status == UNAUTHORIZED_STATUS
        )
        headers.append((CACHE_CONTROL_HEADER.lower().encode(), CACHE_CONTROL_NO_STORE.encode()))
        headers.append((CONTENT_LENGTH_HEADER, str(len(body)).encode()))
        await send({"type": "http.response.start", "status": err.status, "headers": headers})
        await send({"type": "http.response.body", "body": body})

    async def _redirect(self, runtime: AuthRuntime, send: Send) -> None:
        """303 an anonymous HTML navigation to the realm's login page."""
        headers = [
            (LOCATION_HEADER, runtime.realm.login_path.encode("latin-1")),
            (CONTENT_LENGTH_HEADER, b"0"),
        ]
        await send({"type": "http.response.start", "status": REDIRECT_STATUS, "headers": headers})
        await send({"type": "http.response.body", "body": b""})

    def _injecting_send(self, runtime: AuthRuntime, send: Send, ctx: _Ctx) -> Send:
        """Step 10: wrap ``send`` to add the security headers and the cookie clear."""
        cookie_name = runtime.realm.cookie_name(secure=ctx.secure)

        async def wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers[FRAME_OPTIONS_HEADER] = FRAME_OPTIONS_DENY
                if (
                    is_api_path(ctx.rpath)
                    or ctx.policy is not RoutePolicy.PUBLIC
                    or ctx.rpath == HUB_LOGIN_PATH
                ):
                    headers.setdefault(CACHE_CONTROL_HEADER, CACHE_CONTROL_NO_STORE)
                if ctx.clear_cookie and not any(
                    c.startswith(f"{cookie_name}=") for c in headers.getlist(SET_COOKIE)
                ):
                    _, cleared = clear_cookie_header(runtime.realm, secure=ctx.secure)
                    headers.append(SET_COOKIE, cleared.decode("latin-1"))
            await send(message)

        return wrapper


def _realm_cookie(headers: Headers, cookie_name: str) -> tuple[str | None, bool]:
    """The realm cookie from every ``Cookie`` header line (HTTP/2 may split them)."""
    return parse_realm_cookie("; ".join(headers.getlist("cookie")) or None, cookie_name)


__all__ = [
    "AuthMiddleware",
    "ProofCheck",
    "classify",
    "is_api_path",
    "route_path",
]
