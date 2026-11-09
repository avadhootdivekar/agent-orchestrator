"""The sentinel status/control routes are deny-by-default behind AuthMiddleware (T-Sn5Ui1)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI

from tests.auth.helpers.core import make_client
from tests.auth.helpers.stub_runtime import StubRuntime

STATUS = "/api/runs/some-run/sentinel"
CONTROL = "/api/runs/some-run/control"


def test_unauthenticated_requests_are_denied(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime
) -> None:
    client = make_client(build_dashboard(stub_runtime))
    assert client.get(STATUS).status_code == 401
    assert client.get(f"{CONTROL}/abc-20261109t000000-abcd1234").status_code == 401
    # Without the same-origin proof the CSRF gate answers 403 before auth's 401: denied either way.
    assert client.post(CONTROL, json={"kind": "pause_llm"}).status_code in (401, 403)
