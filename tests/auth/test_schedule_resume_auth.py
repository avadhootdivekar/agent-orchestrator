"""The schedule governance routes are deny-by-default behind AuthMiddleware (T-8knqVe AC4)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI

from tests.auth.helpers.core import make_client
from tests.auth.helpers.stub_runtime import StubRuntime

BASE = "/api/schedules/nightly"


def test_unauthenticated_requests_are_denied(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime
) -> None:
    client = make_client(build_dashboard(stub_runtime))
    assert client.get(f"{BASE}/paused-runs").status_code == 401
    # Without the same-origin proof the CSRF gate answers 403 before auth's 401: denied either way.
    r = client.post(f"{BASE}/runs/r1/resume-capped", json={"add_usd": 1})
    assert r.status_code in (401, 403)
