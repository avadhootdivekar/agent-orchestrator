"""``origin_matches_host``: the pure same-origin test the auth middleware uses (HLD 11.18, S4).

It compares a request's ``Origin`` header against the request's own scheme and ``Host`` header
(not an allowlist). ``ui/security.py`` keeps its own ``_is_origin_allowed``; this module is
deliberately **not** wired into it, because the edge semantics differ and switching would change
auth-off behaviour (NFR-1). ``tests/auth/test_origin.py`` documents the differences.
"""

from __future__ import annotations

from http.client import HTTP_PORT, HTTPS_PORT
from urllib.parse import urlsplit

_SCHEME_DEFAULT_PORTS = {"http": HTTP_PORT, "https": HTTPS_PORT}
_OPAQUE_ORIGIN = "null"
_USERINFO_MARK = "@"
_HOST_FORBIDDEN_MARKS = ("/", "?", "#")
_ALLOWED_ORIGIN_PATHS = frozenset({"", "/"})


def _host_and_port(netloc_text: str, default_port: int) -> tuple[str, int] | None:
    """``(lower-cased hostname without brackets, port or the default)``; ``None`` if unusable."""
    try:
        parts = urlsplit(netloc_text)
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if not hostname:
        return None
    return hostname.lower(), default_port if port is None else port


def origin_matches_host(origin: str, *, scheme: str, host_header: str) -> bool:
    """Whether ``origin`` is exactly this request's own origin (scheme + host + port).

    Rejects userinfo (``@``), the opaque ``null`` origin, a scheme other than http/https or other
    than the request's, a non-empty path (other than ``/``), and any query or fragment. Host
    comparison is case-insensitive with IPv6 brackets stripped; default ports (80 for http, 443
    for https) are normalised on both sides using the request scheme.
    """
    if not origin or not host_header:
        return False
    if _USERINFO_MARK in origin or _USERINFO_MARK in host_header:
        return False
    raw = origin.strip()
    if raw.lower() == _OPAQUE_ORIGIN:
        return False
    request_scheme = scheme.lower()
    default_port = _SCHEME_DEFAULT_PORTS.get(request_scheme)
    if default_port is None:
        return False
    try:
        origin_parts = urlsplit(raw)
    except ValueError:
        return False
    if origin_parts.scheme.lower() != request_scheme:
        return False
    if origin_parts.path not in _ALLOWED_ORIGIN_PATHS or "?" in raw or "#" in raw:
        return False
    origin_target = _host_and_port(f"//{origin_parts.netloc}", default_port)
    # A Host header is ``host[:port]`` only: a path, query or fragment makes it garbage.
    if origin_target is None or any(mark in host_header for mark in _HOST_FORBIDDEN_MARKS):
        return False
    host_target = _host_and_port(f"//{host_header.strip()}", default_port)
    return host_target is not None and origin_target == host_target
