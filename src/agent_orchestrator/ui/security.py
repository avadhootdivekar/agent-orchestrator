"""Origin/Host hardening middleware for the dashboard (E-Tpl3x9 follow-on).

Closes a pre-existing critical gap: before this module, the dashboard had no `Origin`/
`Host` validation at all, so ANY website the user's browser visits could CSRF
`POST /api/runs` (an arbitrary-code-execution-and-spend primitive — see `htmlpreview.py`'s
threat model note), and DNS rebinding could defeat same-origin assumptions entirely for
full filesystem read+write.

Layered checks, each independently sufficient against a different vector:

1. Host allowlist — kills DNS rebinding (a page on an attacker's domain resolving to
   127.0.0.1 cannot present a Host header this server will accept).
2. Origin check on mutating methods — kills cross-origin CSRF from a real browser. Real
   browsers always send `Origin` on cross-origin state-changing requests; its absence
   means a non-browser client (curl, CLI, tests), which is a legitimate caller here since
   this server has no other auth.
3. Required `Content-Type: application/json` on a mutating request that carries a body —
   an HTML `<form>` cannot set that content type, so this blocks form-based CSRF
   specifically. Deliberately scoped to requests that actually have a body (see the
   docstring on `_requires_json_content_type` for why bodyless mutating requests, e.g.
   `POST /api/runs/{id}/resume` and `DELETE /api/runs/{id}` with no payload, are exempt —
   they are real, already-tested dashboard behaviour, and the Origin check above already
   covers the cross-origin case for them regardless of body).
4. Security response headers on every response.
5. CSP on the SPA document responses only (not JSON) — permits the app's own bundle and a
   sandboxed `srcdoc` iframe with inlined `data:` assets, forbids everything else.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from urllib.parse import urlsplit

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# Env var name for the configurable Host allowlist (comma-separated). A literal "*" entry
# disables the Host check entirely (loud warning logged once at startup).
AO_UI_ALLOWED_HOSTS_ENV = "AO_UI_ALLOWED_HOSTS"

# Sentinel that disables the Host check when present in AO_UI_ALLOWED_HOSTS.
DISABLE_HOST_CHECK_SENTINEL = "*"

# Default allowlist: loopback under any of its common spellings, normalized (see
# `_normalize_host`) so "127.0.0.1" and "127.0.0.1:8765", "[::1]" and "[::1]:8765" all
# compare equal to one of these entries regardless of port. NOTE: this covers the
# BRACKETED IPv6 forms only -- an unbracketed literal like a bare "::1" Host/Origin value
# is ambiguous with `host:port` syntax and `urlsplit` (which `_normalize_host` delegates
# to) cannot recover a hostname from it; it normalizes to `None` and is rejected. That is
# a safe, fail-closed outcome (not a security gap), but a real client sending Host: ::1
# unbracketed would be refused rather than accepted -- keep this constant's bracketed
# forms in mind if that ever needs to change.
DEFAULT_ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

# HTTP methods that mutate state and therefore get the Origin/Content-Type checks.
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

REQUIRED_JSON_CONTENT_TYPE = "application/json"

# Security response headers applied to every response, mutating or not.
_STATIC_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}

# CSP for the SPA document itself (NOT the sanitized preview HTML — that carries its own,
# stricter, per-preview CSP meta tag built client-side; this one governs the dashboard's own
# bundle).
#
# EMPIRICALLY VERIFIED (not guessed) with a real browser: served this exact header string
# from a plain `http.server` response, loaded it in real headless Chrome
# (`google-chrome-stable --headless=new`), and screenshotted the result. The page embedded
# a `sandbox=""` iframe (no allow-scripts, no allow-same-origin) with `srcDoc` containing
# `<img src="data:image/png;base64,...">` (a solid-red 2x2 PNG). Pixel-sampling the
# screenshot at the image's location showed the exact red RGB value, i.e. the sandboxed
# srcdoc iframe rendered and its inlined data: image loaded, under this precise CSP.
# NEGATIVE CONTROL: re-ran with `img-src 'self'` (no `data:`) and everything else identical
# -- the same pixel location came back as the page's background color instead, confirming
# the check actually discriminates (not a methodology false-positive) and that `data:` in
# `img-src` is what permits it. `frame-src 'self' data:` covers the sandboxed srcDoc iframe
# itself; `connect-src 'self'` + no wildcard host anywhere is what keeps this "no network
# egress" per the threat model. `tests/ui/test_security.py::TestSecurityHeaders
# ::test_spa_document_response_carries_the_csp` pins the header value as a regression
# check; it cannot itself drive a browser, so it is not a substitute for the manual
# verification above.
SPA_CSP = (
    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
    "font-src 'self' data:; connect-src 'self'; frame-src 'self' data:; "
    "object-src 'none'; base-uri 'none'; form-action 'none'"
)


def _normalize_host(raw: str) -> str | None:
    """Bracket- and port-stripped, lowercased hostname from a raw `Host` header value.

    `Host` header values are bare (`host[:port]`, no scheme) — `urlsplit` needs a `//`
    prefix to treat that as a netloc rather than a path. Delegating to `urlsplit` rather
    than hand-rolling IPv6-bracket/port parsing means `"127.0.0.1:8765"` and `"[::1]:8765"`
    normalize the same way as their bare-port-less forms, for free.

    Rejects (returns ``None`` for) any value containing `@`: a `Host` header never
    legitimately carries userinfo, and `urlsplit` would otherwise silently discard an
    `"attacker@"` prefix and normalize only the trailing host — accepting exactly the
    input a spoofing attempt would send. Fail closed instead of normalizing past it.
    """
    if "@" in raw:
        return None
    try:
        return urlsplit(f"//{raw.strip()}").hostname
    except ValueError:
        return None


def _normalize_origin(raw: str) -> str | None:
    """Bracket- and port-stripped, lowercased hostname from an `Origin` header value.

    Unlike `Host`, an `Origin` value already carries its own scheme (`"http://host:port"`,
    or the opaque literal `"null"`) — feeding it through the same `//`-prefix trick as
    `_normalize_host` would corrupt it (`"//http://host"` parses as host `"http"`). No
    prefix is needed here; `urlsplit` parses a proper scheme+netloc directly.

    Rejects (returns ``None`` for) any value containing `@`, for the same reason as
    `_normalize_host`: real browsers never construct an `Origin` with userinfo, and
    `urlsplit("http://evil.com@localhost").hostname` silently resolves to `"localhost"` —
    normalizing straight past the spoofed prefix rather than rejecting it.
    """
    if "@" in raw:
        return None
    try:
        return urlsplit(raw.strip()).hostname
    except ValueError:
        return None


def resolve_allowed_hosts(
    bound_host: str | None = None,
    env: Mapping[str, str] | None = None,
) -> frozenset[str] | None:
    """Compute the effective Host allowlist.

    Returns ``None`` to mean "check disabled" (the `AO_UI_ALLOWED_HOSTS=*` escape hatch),
    else a frozenset of normalized (lowercase, bracket/port-stripped) allowed hostnames.

    Args:
        bound_host: The host `ao ui` actually bound to (added to the allowlist so binding
            to a non-loopback address an operator deliberately chose still works).
        env: Environment mapping to read `AO_UI_ALLOWED_HOSTS` from; defaults to
            ``os.environ`` (injectable for tests).
    """
    env = env if env is not None else os.environ
    hosts = set(DEFAULT_ALLOWED_HOSTS)
    if bound_host:
        normalized = _normalize_host(bound_host)
        if normalized:
            hosts.add(normalized)

    raw = env.get(AO_UI_ALLOWED_HOSTS_ENV)
    if raw:
        entries = [p.strip() for p in raw.split(",") if p.strip()]
        if DISABLE_HOST_CHECK_SENTINEL in entries:
            logger.warning(
                "AO_UI_ALLOWED_HOSTS=* -- Host header validation is DISABLED. Any DNS "
                "name that resolves to this machine can now reach the dashboard "
                "(filesystem read + run-launch/spend). Only do this on a trusted network."
            )
            return None
        for entry in entries:
            normalized = _normalize_host(entry)
            if normalized:
                hosts.add(normalized)

    return frozenset(hosts)


def _is_host_allowed(host_header: str | None, allowed: frozenset[str] | None) -> bool:
    if allowed is None:
        return True
    if not host_header:
        return False
    normalized = _normalize_host(host_header)
    return normalized is not None and normalized in allowed


def _is_origin_allowed(
    origin_header: str, request: Request, allowed: frozenset[str] | None
) -> bool:
    """Whether *origin_header* is genuinely the SAME origin as *request* itself.

    Two checks, both required:
    1. The origin's host normalizes (via `_normalize_origin` — same `@`/bracket/port
       handling as the `Host` header check) and is in the Host allowlist (kills
       DNS-rebinding-style hosts).
    2. The origin's SCHEME and PORT match this request's own scheme/port exactly — host
       alone is not enough, or `https://localhost:X` and a different port on `localhost`
       would incorrectly compare equal to this server's own origin. Compared against the
       request actually received (already Host-allowlist-validated by the time this runs)
       rather than a second, separately-maintained "allowed origins" config, since that
       would just be the same information kept in two places, one of which could drift.
    """
    if allowed is None:
        return True
    host = _normalize_origin(origin_header)
    if host is None or host not in allowed:
        return False
    parsed = urlsplit(origin_header.strip())
    if (parsed.scheme or "").lower() != request.url.scheme.lower():
        return False
    return parsed.port == request.url.port


def _requires_json_content_type(request: Request) -> bool:
    """Whether this request must present `Content-Type: application/json`.

    Deliberately scoped to requests that carry a body (`Content-Length` present and
    nonzero, or chunked transfer-encoding). A completely bodyless mutating request (no
    Content-Length at all) cannot smuggle an attacker-controlled JSON payload via the wrong
    declared content type — there IS no payload — and existing, legitimate dashboard calls
    rely on exactly this shape (`POST /runs/{id}/resume` and `DELETE /runs/{id}` with no
    body). The Origin check independently covers the cross-origin case for those too, since
    a real browser always sends Origin on a cross-origin mutating request regardless of
    whether it has a body.
    """
    if request.method not in MUTATING_METHODS:
        return False
    transfer_encoding = request.headers.get("transfer-encoding", "").lower()
    if "chunked" in transfer_encoding:
        return True
    content_length = request.headers.get("content-length")
    return bool(content_length) and content_length != "0"


class SecurityMiddleware(BaseHTTPMiddleware):
    """Host allowlist + Origin/Content-Type CSRF hardening + security headers + SPA CSP."""

    def __init__(self, app: ASGIApp, *, allowed_hosts: frozenset[str] | None) -> None:
        super().__init__(app)
        self._allowed_hosts = allowed_hosts

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if not _is_host_allowed(request.headers.get("host"), self._allowed_hosts):
            return Response(
                content="misdirected request: unrecognized Host header",
                status_code=421,
            )

        if request.method in MUTATING_METHODS:
            origin = request.headers.get("origin")
            if origin and not _is_origin_allowed(origin, request, self._allowed_hosts):
                return Response(content="cross-origin request rejected", status_code=403)
            if _requires_json_content_type(request):
                declared = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                if declared != REQUIRED_JSON_CONTENT_TYPE:
                    return Response(
                        content=f"Content-Type must be {REQUIRED_JSON_CONTENT_TYPE}",
                        status_code=415,
                    )

        response = await call_next(request)
        self._apply_security_headers(response)
        if _is_spa_document(request, response):
            response.headers["Content-Security-Policy"] = SPA_CSP
        return response

    @staticmethod
    def _apply_security_headers(response: Response) -> None:
        for name, value in _STATIC_SECURITY_HEADERS.items():
            response.headers[name] = value


def _is_spa_document(request: Request, response: Response) -> bool:
    """Whether *response* is one of the SPA's own HTML documents (`/` or the SPA fallback),
    as opposed to a JSON API response — the CSP applies only to the former."""
    content_type = response.headers.get("content-type", "")
    if not content_type.startswith("text/html"):
        return False
    return not request.url.path.startswith("/api/")


__all__ = [
    "AO_UI_ALLOWED_HOSTS_ENV",
    "DEFAULT_ALLOWED_HOSTS",
    "DISABLE_HOST_CHECK_SENTINEL",
    "MUTATING_METHODS",
    "SPA_CSP",
    "SecurityMiddleware",
    "resolve_allowed_hosts",
]
