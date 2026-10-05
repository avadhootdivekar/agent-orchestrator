"""T-QJ1vyQ: the 16 KiB cap on ``/api/auth/*`` request bodies (HLD 13.3 step 3; AC-5)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from starlette.types import Message

from agent_orchestrator.auth.constants import AUTH_API_PREFIX, AUTH_STATUS_PATH, MAX_AUTH_BODY_BYTES
from agent_orchestrator.auth.errors import ErrorCode
from agent_orchestrator.auth.http.middleware import _cap_auth_body
from tests.auth.helpers.core import run_async
from tests.auth.helpers.edge import ECHO_PATH, EdgeApp, build_edge_app
from tests.auth.helpers.stub_runtime import StubRuntime

JSON_HEADERS = {"Content-Type": "application/json"}


@pytest.fixture()
def edge(stub_runtime: StubRuntime) -> EdgeApp:
    # allowed_hosts=None: only AuthMiddleware sits between the test and the echo route.
    return build_edge_app(stub_runtime, allowed_hosts=None)


def _post(edge: EdgeApp, path: str, content: Any) -> Any:
    _issued, headers = edge.login()
    return edge.client.post(path, content=content, headers={**headers, **JSON_HEADERS})


def test_an_oversized_content_length_is_413_and_the_route_is_not_invoked(edge: EdgeApp) -> None:
    response = _post(edge, ECHO_PATH, b"x" * 20_000)
    assert response.status_code == 413
    assert response.json()["code"] == ErrorCode.BODY_TOO_LARGE.value
    assert edge.echo_calls == []


def test_a_chunked_body_over_the_cap_is_413(edge: EdgeApp) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(MAX_AUTH_BODY_BYTES // 1024):
            yield b"a" * 1024
        yield b"b"  # one byte over

    response = _post(edge, ECHO_PATH, chunks())
    assert response.status_code == 413
    assert edge.echo_calls == []


def test_a_chunked_body_of_exactly_the_cap_passes(edge: EdgeApp) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(MAX_AUTH_BODY_BYTES // 1024):
            yield b"a" * 1024

    response = _post(edge, ECHO_PATH, chunks())
    assert response.status_code == 200
    assert response.content == b"a" * MAX_AUTH_BODY_BYTES


def test_a_body_of_exactly_the_cap_passes(edge: EdgeApp) -> None:
    response = _post(edge, ECHO_PATH, b"z" * MAX_AUTH_BODY_BYTES)
    assert response.status_code == 200
    assert len(edge.echo_calls) == 1 and len(edge.echo_calls[0]) == MAX_AUTH_BODY_BYTES


def test_one_byte_over_the_cap_is_413_by_content_length(edge: EdgeApp) -> None:
    assert _post(edge, ECHO_PATH, b"z" * (MAX_AUTH_BODY_BYTES + 1)).status_code == 413


def test_a_small_body_is_echoed_byte_identical(edge: EdgeApp) -> None:
    payload = bytes(range(256)) * 4  # 1 KiB of every byte value
    response = _post(edge, ECHO_PATH, payload)
    assert response.status_code == 200
    assert response.content == payload
    assert edge.echo_calls == [payload]


def test_an_empty_body_passes(edge: EdgeApp) -> None:
    response = _post(edge, ECHO_PATH, b"")
    assert response.status_code == 200 and response.content == b""


def test_other_paths_are_not_capped(edge: EdgeApp) -> None:
    response = _post(edge, "/api/runs", b"x" * 20_000)
    assert response.status_code == 204  # the AUTHENTICATED probe ran: no cap applied


def test_the_cap_does_not_apply_to_a_get(edge: EdgeApp) -> None:
    _issued, headers = edge.login()
    response = edge.client.request("GET", AUTH_STATUS_PATH, headers=headers, content=b"x" * 20_000)
    assert response.status_code == 200


def test_the_prefix_is_the_auth_api_prefix_with_a_separator() -> None:
    assert AUTH_API_PREFIX == "/api/auth"


# --- the hook itself, with a synthetic multi-message receive ----------------------------------


def _scope(method: str = "POST", path: str = ECHO_PATH, content_length: str | None = None) -> Any:
    headers = [] if content_length is None else [(b"content-length", content_length.encode())]
    return {"type": "http", "method": method, "path": path, "headers": headers}


def _receive_of(messages: list[Message]) -> Any:
    queue = list(messages)

    async def receive() -> Message:
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    return receive


def _body(body: bytes, more: bool) -> Message:
    return {"type": "http.request", "body": body, "more_body": more}


def test_multi_message_bodies_are_joined_and_replayed_as_one_message() -> None:
    receive = _receive_of([_body(b"ab", True), _body(b"cd", True), _body(b"ef", False)])
    replay, error = run_async(_cap_auth_body(_scope(), receive, ECHO_PATH))
    assert error is None
    first = run_async(replay())
    assert first == {"type": "http.request", "body": b"abcdef", "more_body": False}
    assert run_async(replay())["type"] == "http.disconnect"  # then the real receive


def test_a_stream_crossing_the_cap_in_the_last_chunk_is_413() -> None:
    receive = _receive_of(
        [_body(b"x" * MAX_AUTH_BODY_BYTES, True), _body(b"y", False), _body(b"never", False)]
    )
    _replay, error = run_async(_cap_auth_body(_scope(), receive, ECHO_PATH))
    assert error is not None and error.code is ErrorCode.BODY_TOO_LARGE and error.status == 413


def test_an_invalid_content_length_falls_back_to_counting_the_stream() -> None:
    receive = _receive_of([_body(b"hello", False)])
    replay, error = run_async(_cap_auth_body(_scope(content_length="oops"), receive, ECHO_PATH))
    assert error is None
    assert run_async(replay())["body"] == b"hello"


@pytest.mark.parametrize("raw", [b"\xb2", b"\xb9\xb3"], ids=["superscript-2", "superscript-1-3"])
def test_a_unicode_digit_content_length_is_not_a_crash(raw: bytes) -> None:
    """T-2wE08U: ``"²".isdigit()`` is True but ``int("²")`` raises; it must count the stream."""
    scope = _scope()
    scope["headers"] = [(b"content-length", raw)]  # latin-1 bytes decode to a non-ASCII digit
    receive = _receive_of([_body(b"hello", False)])
    replay, error = run_async(_cap_auth_body(scope, receive, ECHO_PATH))
    assert error is None
    assert run_async(replay())["body"] == b"hello"


def test_a_disconnect_while_buffering_is_passed_on() -> None:
    receive = _receive_of([_body(b"ab", True), {"type": "http.disconnect"}])
    replay, error = run_async(_cap_auth_body(_scope(), receive, ECHO_PATH))
    assert error is None
    assert run_async(replay())["type"] == "http.disconnect"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", ECHO_PATH),
        ("POST", "/api/runs"),
        ("POST", "/api/authx/login"),
        ("POST", AUTH_API_PREFIX),  # the bare prefix is not "/api/auth/..."
    ],
)
def test_the_hook_returns_the_original_receive_when_it_does_not_apply(
    method: str, path: str
) -> None:
    receive = _receive_of([])
    returned, error = run_async(_cap_auth_body(_scope(method, path), receive, path))
    assert error is None and returned is receive
