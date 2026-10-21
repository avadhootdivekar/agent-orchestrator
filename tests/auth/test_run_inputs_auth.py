"""The awaiting-input routes are deny-by-default behind AuthMiddleware (no new open write path)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI

from tests.auth.helpers.core import make_client
from tests.auth.helpers.stub_runtime import StubRuntime

LIST = "/api/runs/some-run/inputs"
ANSWER = "/api/runs/some-run/inputs/q-t-1"


def test_unauthenticated_get_and_post_are_denied(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime
) -> None:
    client = make_client(build_dashboard(stub_runtime))
    assert client.get(LIST).status_code == 401
    # POST without the same-origin proof is stopped by the CSRF gate (403) before auth (401).
    assert client.post(ANSWER, json={"values": {}}).status_code in (401, 403)
