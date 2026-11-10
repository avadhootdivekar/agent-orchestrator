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

import pytest
import uvicorn

from agent_orchestrator.service import webhook as wh
from agent_orchestrator.service.webhook import WebhookRequestQueue, build_webhook_app

DEADLINE = 0.6


@contextmanager
def _listener(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[int]:
    monkeypatch.setattr(wh, "WEBHOOK_HEADER_TIMEOUT_SECONDS", DEADLINE)
    app = build_webhook_app(lambda s, i: None, WebhookRequestQueue(tmp_path / "state"))
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
                got += s.recv(4096)
            assert b"200 OK" in got  # headers completed in time: deadline disarmed
            time.sleep(DEADLINE + 0.4)  # well past the original deadline: not killed mid-flight...
            # ...but the connection is idle between requests, so the re-armed deadline closes it.
            assert _closed_by_server(s, 2)
