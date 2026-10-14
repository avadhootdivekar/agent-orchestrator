"""The operator-notes routes are deny-by-default behind AuthMiddleware (no new open write path)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI

from tests.auth.helpers.core import make_client
from tests.auth.helpers.stub_runtime import StubRuntime

NOTES = "/api/runs/some-run/notes"


def test_unauthenticated_get_and_post_are_401(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime
) -> None:
    client = make_client(build_dashboard(stub_runtime))
    assert client.get(NOTES).status_code == 401
    # A POST without the same-origin proof is refused by the CSRF gate (403) before it reaches the
    # auth check (401); either way it is denied and nothing is written.
    assert client.post(NOTES, json={"text": "hello"}).status_code in (401, 403)
