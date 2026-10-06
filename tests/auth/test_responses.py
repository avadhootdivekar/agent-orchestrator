"""T-G7qByZ: byte pins of ``auth/http/responses.py`` (AC-41 part; HLD 2.2, 2.3, 20.3 #12)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_orchestrator.auth.errors import (
    DEFAULT_DETAIL,
    STATUS_BY_CODE,
    AuthError,
    BusyError,
    ErrorCode,
    StoreUnavailableError,
    TooManyAttemptsError,
)
from agent_orchestrator.auth.http.responses import (
    clear_cookie_header,
    error_body,
    error_bytes,
    error_headers,
    json_bytes,
    parse_realm_cookie,
    session_cookie_header,
)
from tests.auth.helpers.stub_runtime import StubRealm

REALM = StubRealm(kind="ui", port=8765, workspace_root=Path("/ws"))
HUB = StubRealm(kind="hub", port=8770, workspace_root=None)
TOKEN = "A" * 43
EXPIRED = "Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT"


def test_set_cookie_bytes_over_http() -> None:
    assert session_cookie_header(REALM, TOKEN, secure=False) == (
        b"set-cookie",
        b"ao_sid_8765=" + TOKEN.encode() + b"; Path=/; HttpOnly; SameSite=Strict",
    )


def test_set_cookie_bytes_over_https_use_the_host_prefix_and_secure() -> None:
    assert session_cookie_header(REALM, TOKEN, secure=True) == (
        b"set-cookie",
        b"__Host-ao_sid_8765=" + TOKEN.encode() + b"; Path=/; HttpOnly; SameSite=Strict; Secure",
    )


def test_clear_cookie_bytes_over_http_and_https() -> None:
    assert clear_cookie_header(HUB, secure=False) == (
        b"set-cookie",
        f"ao_sid_8770=; Path=/; HttpOnly; SameSite=Strict; {EXPIRED}".encode(),
    )
    assert clear_cookie_header(HUB, secure=True) == (
        b"set-cookie",
        f"__Host-ao_sid_8770=; Path=/; HttpOnly; SameSite=Strict; {EXPIRED}; Secure".encode(),
    )


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (f"ao_sid_8765={TOKEN}", (TOKEN, False)),
        (f"other=1; ao_sid_8765={TOKEN}; theme=dark", (TOKEN, False)),
        (None, (None, False)),
        ("", (None, False)),
        ("other=1; ao_sid_8766=zzz", (None, False)),  # another realm's cookie is ignored
        (f"ao_sid_8765={TOKEN}; ao_sid_8765=B{TOKEN[1:]}", (TOKEN, True)),
        (f"ao_sid_8765={TOKEN};ao_sid_8765={TOKEN}", (TOKEN, True)),
        ("ao_sid_8765=", (None, False)),
        ("ao_sid_8765x=1", (None, False)),  # a longer name is not this cookie
    ],
)
def test_parse_realm_cookie(header: str | None, expected: tuple[str | None, bool]) -> None:
    assert parse_realm_cookie(header, "ao_sid_8765") == expected


@pytest.mark.parametrize("code", list(ErrorCode))
def test_error_body_bytes_for_every_code(code: ErrorCode) -> None:
    detail = DEFAULT_DETAIL[code]
    assert (
        error_body(code)
        == json.dumps(
            {"detail": detail, "code": code.value}, separators=(",", ":"), ensure_ascii=False
        ).encode()
    )
    assert list(json.loads(error_body(code))) == ["detail", "code"]  # key order is the contract


def test_error_body_extra_keys_and_custom_detail() -> None:
    body = error_body(ErrorCode.INVALID_CODE, "No.", reason="invalid", attempts_remaining=2)
    assert (
        body == b'{"detail":"No.","code":"invalid_code","reason":"invalid","attempts_remaining":2}'
    )


def test_error_bytes_uses_the_error_detail_and_extra() -> None:
    err = TooManyAttemptsError(30)
    assert error_bytes(err) == error_body(err.code, err.detail, **err.extra)
    assert json.loads(error_bytes(err))["retry_after_seconds"] == 30


def test_json_bytes_matches_starlette_json_response() -> None:
    from starlette.responses import JSONResponse

    payload = {"a": None, "b": [1, "é"], "c": {"d": False}}
    assert json_bytes(payload) == JSONResponse(payload).body


def test_error_headers_include_retry_after_for_429_and_503() -> None:
    def names(err: AuthError, *, www: bool = False) -> dict[bytes, bytes]:
        return dict(error_headers(err, REALM, www_authenticate=www))

    assert names(TooManyAttemptsError(30))[b"retry-after"] == b"30"
    assert names(BusyError())[b"retry-after"] == b"1"
    assert names(StoreUnavailableError())[b"retry-after"] == b"5"
    assert names(AuthError(ErrorCode.INVALID_REQUEST)) == {b"content-type": b"application/json"}


def test_error_headers_www_authenticate_names_the_realm() -> None:
    headers = dict(
        error_headers(AuthError(ErrorCode.NOT_AUTHENTICATED), REALM, www_authenticate=True)
    )
    assert headers[b"www-authenticate"] == f'AO-Session realm="{REALM.id}"'.encode()
    hub = dict(error_headers(AuthError(ErrorCode.NOT_AUTHENTICATED), HUB, www_authenticate=True))
    assert hub[b"www-authenticate"] == b'AO-Session realm="hub"'


def test_every_error_code_has_a_status() -> None:
    # The envelope builders are total over the vocabulary.
    assert set(ErrorCode) == set(STATUS_BY_CODE)
