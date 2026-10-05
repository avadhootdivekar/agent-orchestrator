"""Byte-exact response builders shared by the middleware and the routes (HLD 11.18, 2.2, 2.3; L4).

Pure: no framework import, so a route module and ``AuthMiddleware`` produce **identical bytes**
for the same cookie or error (reviewer finding R-9, ``tests/auth/test_responses.py``). The
cookie attributes and the error envelope are frozen by the HTTP contract (HLD 2.2/2.3); the
single-owner spelling lives here and nowhere else.
"""

from __future__ import annotations

import json
from typing import Literal, Protocol

from ..constants import WWW_AUTHENTICATE_SCHEME
from ..errors import DEFAULT_DETAIL, AuthError, ErrorCode

SET_COOKIE_HEADER = b"set-cookie"
CONTENT_TYPE_HEADER = b"content-type"
WWW_AUTHENTICATE_HEADER = b"www-authenticate"
JSON_CONTENT_TYPE = b"application/json"

# Cookie attributes (HLD 2.3). ``Secure`` is appended last over https only.
_COOKIE_ATTRIBUTES = "Path=/; HttpOnly; SameSite=Strict"
_COOKIE_SECURE_ATTRIBUTE = "Secure"
_COOKIE_EXPIRED_ATTRIBUTES = "Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT"
_COOKIE_PAIR_SEPARATOR = ";"


class RealmLike(Protocol):
    """The parts of ``runtime.Realm`` (HLD 11.16) that HTTP responses need."""

    @property
    def kind(self) -> Literal["ui", "hub"]: ...

    @property
    def id(self) -> str: ...

    @property
    def login_path(self) -> str: ...

    def cookie_name(self, *, secure: bool) -> str: ...


def json_bytes(payload: object) -> bytes:
    """Compact UTF-8 JSON, byte-for-byte what Starlette's ``JSONResponse`` renders."""
    return json.dumps(
        payload, ensure_ascii=False, allow_nan=False, indent=None, separators=(",", ":")
    ).encode("utf-8")


def _cookie_header(name: str, value: str, extra: str, *, secure: bool) -> tuple[bytes, bytes]:
    parts = [f"{name}={value}", _COOKIE_ATTRIBUTES]
    if extra:
        parts.append(extra)
    if secure:
        parts.append(_COOKIE_SECURE_ATTRIBUTE)
    return SET_COOKIE_HEADER, "; ".join(parts).encode("latin-1")


def session_cookie_header(realm: RealmLike, token: str, *, secure: bool) -> tuple[bytes, bytes]:
    """The ``Set-Cookie`` that installs ``token`` (HttpOnly, SameSite=Strict, host-only)."""
    return _cookie_header(realm.cookie_name(secure=secure), token, "", secure=secure)


def clear_cookie_header(realm: RealmLike, *, secure: bool) -> tuple[bytes, bytes]:
    """The ``Set-Cookie`` that deletes the realm cookie."""
    return _cookie_header(
        realm.cookie_name(secure=secure), "", _COOKIE_EXPIRED_ATTRIBUTES, secure=secure
    )


def parse_realm_cookie(cookie_header: str | None, name: str) -> tuple[str | None, bool]:
    """``(value, duplicated)`` for the cookie called ``name`` in a ``Cookie`` header value.

    Two or more occurrences of the name -> ``duplicated`` (the caller treats that as *no
    session*: a cookie-tossing defence, S26) and ``value`` is the first one. Other cookie names
    are ignored. An absent or empty-valued cookie yields ``None``.
    """
    if not cookie_header:
        return None, False
    values: list[str] = []
    for pair in cookie_header.split(_COOKIE_PAIR_SEPARATOR):
        key, sep, value = pair.strip().partition("=")
        if sep and key == name:
            values.append(value.strip())
    if not values:
        return None, False
    return (values[0] or None), len(values) > 1


def error_body(code: ErrorCode, detail: str | None = None, **extra: object) -> bytes:
    """The error envelope ``{"detail", "code", ...extra}`` as bytes (HLD 2.2)."""
    envelope: dict[str, object] = {
        "detail": detail if detail is not None else DEFAULT_DETAIL[code],
        "code": code.value,
    }
    envelope.update(extra)
    return json_bytes(envelope)


def error_bytes(err: AuthError) -> bytes:
    """``error_body`` of an :class:`AuthError` (its code, detail and extra keys)."""
    return error_body(err.code, err.detail, **err.extra)


def error_headers(
    err: AuthError, realm: RealmLike, *, www_authenticate: bool
) -> list[tuple[bytes, bytes]]:
    """Response headers for ``err``: content type, the error's own (``Retry-After``), challenge."""
    headers = [(CONTENT_TYPE_HEADER, JSON_CONTENT_TYPE)]
    headers.extend(
        (k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in err.headers.items()
    )
    if www_authenticate:
        challenge = f'{WWW_AUTHENTICATE_SCHEME} realm="{realm.id}"'
        headers.append((WWW_AUTHENTICATE_HEADER, challenge.encode("latin-1")))
    return headers
