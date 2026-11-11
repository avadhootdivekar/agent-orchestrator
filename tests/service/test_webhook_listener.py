"""T-Wh9Kv1 N2: the real uvicorn listener closes stalled sockets (header deadline).

A real ``uvicorn.Server`` on an ephemeral loopback port, configured with the exact kwargs
``ao service run --webhook`` uses (``webhook_uvicorn_kwargs``). The deadline is shortened so the
test is fast; nothing else is faked.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from agent_orchestrator.service import webhook as wh
from agent_orchestrator.service.webhook import (
    WebhookRequestQueue,
    WebhookTarget,
    build_webhook_app,
    compute_signature,
)

from ..schedules._fixtures import minimal_limits
from .test_schedule_engine import binding

DEADLINE = 0.6


@contextmanager
def _listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lookup: Any = lambda s, i: None
) -> Iterator[int]:
    monkeypatch.setattr(wh, "WEBHOOK_HEADER_TIMEOUT_SECONDS", DEADLINE)
    app = build_webhook_app(lookup, WebhookRequestQueue(tmp_path / "state"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", **wh.webhook_uvicorn_kwargs()))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    try:
        yield port
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        sock.close()


def _healthz(port: int) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(b"GET /healthz HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        data = b""
        while chunk := s.recv(4096):
            data += chunk
    return data


def _closed_by_server(s: socket.socket, within: float) -> bool:
    s.settimeout(within)
    try:
        return s.recv(1) == b""
    except (TimeoutError, ConnectionResetError) as exc:
        return isinstance(exc, ConnectionResetError)


def test_idle_and_header_trickle_sockets_are_closed_by_the_header_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _listener(tmp_path, monkeypatch) as port:
        idle = [socket.create_connection(("127.0.0.1", port)) for _ in range(20)]
        trickle = socket.create_connection(("127.0.0.1", port))
        trickle.sendall(b"POST /hooks/x/y HTTP/1.1\r\nHost: local")  # headers never complete
        try:
            assert b"200 OK" in _healthz(port)  # alive while they are held
            time.sleep(DEADLINE + 1.0)
            assert all(_closed_by_server(s, 2) for s in idle)
            assert _closed_by_server(trickle, 2)
            assert b"200 OK" in _healthz(port)
        finally:
            for s in [*idle, trickle]:
                with suppress(OSError):
                    s.close()


def test_limit_minus_one_idle_sockets_do_not_lock_the_listener_out_after_the_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-review probe: ``WEBHOOK_LIMIT_CONCURRENCY - 1`` idle sockets used to give a permanent
    503 on /healthz (and every delivery) until the attacker let go."""
    with _listener(tmp_path, monkeypatch) as port:
        idle = [
            socket.create_connection(("127.0.0.1", port))
            for _ in range(wh.WEBHOOK_LIMIT_CONCURRENCY - 1)
        ]
        try:
            time.sleep(DEADLINE + 1.5)
            assert b"200 OK" in _healthz(port)
        finally:
            for s in idle:
                with suppress(OSError):
                    s.close()


def test_a_prompt_request_is_not_cut_off_and_keep_alive_reuse_is_rearmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with _listener(tmp_path, monkeypatch) as port:
        req = b"GET /healthz HTTP/1.1\r\nHost: localhost\r\n\r\n"
        with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
            s.sendall(req)
            got = b""
            while not got.endswith(b"}"):  # headers + the JSON body (may arrive separately)
                chunk = s.recv(4096)
                assert chunk, f"server closed before the response completed: {got!r}"
                got += chunk
            assert b"200 OK" in got  # headers completed in time: deadline disarmed
            time.sleep(DEADLINE + 0.4)  # well past the original deadline: not killed mid-flight...
            # ...but the connection is idle between requests, so the re-armed deadline closes it.
            assert _closed_by_server(s, 2)


# -- signed POST + slow body over the real socket (T-Se4Bk5 FU-9) ---------------------------------

SECRET = "listener-secret-xyz"
ROOT = "/tmp/ws-listener"
BODY = b'{"ref": "refs/heads/main", "n": 1}'


def _signed_lookup(slug: str, sid: str) -> WebhookTarget | None:
    from agent_orchestrator.schedules.models import WebhookSpec

    if (slug, sid) != ("ws", "hook"):
        return None
    b = binding(
        "hook", kind="webhook", interval_seconds=None,
        webhook=WebhookSpec(secret_env="AO_TEST_LISTENER_SECRET"), limits=minimal_limits(),
    )  # fmt: skip
    return WebhookTarget(ROOT, b)


def _post_head(body: bytes, *, secret: str = SECRET, delivery: str = "d1") -> bytes:
    ts = str(int(time.time()))  # the listener runs on the real clock
    sig = compute_signature(secret.encode(), body, ts)
    return (
        b"POST /hooks/ws/hook HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n"
        b"Content-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\nX-AO-Timestamp: {ts}\r\nX-AO-Signature: {sig}\r\n"
        f"X-AO-Delivery: {delivery}\r\n\r\n".encode()
    )


def _status(s: socket.socket) -> tuple[int, bytes]:
    s.settimeout(10)
    data = b""
    while chunk := s.recv(4096):
        data += chunk
    assert data, "server closed without a response"
    return int(data.split(b" ", 2)[1]), data


def _queued(tmp_path: Path) -> list[Path]:
    d = tmp_path / "state" / "schedules" / "requests"
    return sorted(d.glob("*.json")) if d.is_dir() else []


def test_signed_post_over_a_real_socket_is_accepted_and_a_bad_signature_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AO_TEST_LISTENER_SECRET", SECRET)
    with _listener(tmp_path, monkeypatch, _signed_lookup) as port:
        with socket.create_connection(("127.0.0.1", port)) as s:
            s.sendall(_post_head(BODY) + BODY)
            code, _ = _status(s)
        assert code == 202 and len(_queued(tmp_path)) == 1
        with socket.create_connection(("127.0.0.1", port)) as s:
            s.sendall(_post_head(BODY, secret="wrong", delivery="d2") + BODY)
            code, _ = _status(s)
        assert code == 401 and len(_queued(tmp_path)) == 1  # nothing new persisted


def test_a_body_held_open_past_the_header_deadline_is_not_cut_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Headers arrive at once, the body trickles in slower than the header deadline: the deadline
    is disarmed once headers complete, so the delivery is still accepted."""
    monkeypatch.setenv("AO_TEST_LISTENER_SECRET", SECRET)
    with _listener(tmp_path, monkeypatch, _signed_lookup) as port:
        with socket.create_connection(("127.0.0.1", port)) as s:
            s.sendall(_post_head(BODY))
            s.sendall(BODY[:10])
            time.sleep(DEADLINE + 0.6)  # body held open well past the header deadline
            s.sendall(BODY[10:])
            code, _ = _status(s)
        assert code == 202 and len(_queued(tmp_path)) == 1


def test_a_body_that_never_completes_gets_408_and_persists_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AO_TEST_LISTENER_SECRET", SECRET)
    monkeypatch.setattr(wh, "WEBHOOK_BODY_READ_TIMEOUT_SECONDS", 0.5)
    with _listener(tmp_path, monkeypatch, _signed_lookup) as port:
        with socket.create_connection(("127.0.0.1", port)) as s:
            s.sendall(_post_head(BODY) + BODY[:5])  # promises len(BODY) bytes, sends 5
            code, _ = _status(s)
        assert code == 408 and _queued(tmp_path) == []
