"""The five ``/api/schedules`` routes are AUTHENTICATED by omission from the policy table
(T-Ap1Xs3 AC14a): 401 without a session, working with one."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI

from agent_orchestrator.auth.model import SessionState
from tests.auth.helpers.core import make_client, same_origin_headers
from tests.auth.helpers.stub_runtime import StubRuntime

ROUTES = [
    ("GET", "/api/schedules"),
    ("GET", "/api/schedules/a/history"),
    ("POST", "/api/schedules/a/enable"),
    ("POST", "/api/schedules/a/disable"),
    ("POST", "/api/schedules/a/run-now"),
]


@pytest.fixture(autouse=True)
def _state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AO_SERVICE_STATE_DIR", str(tmp_path / "state"))


@pytest.mark.parametrize(("method", "path"), ROUTES)
def test_unauthenticated_is_401(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime, method: str, path: str
) -> None:
    client = make_client(build_dashboard(stub_runtime))
    response = client.request(method, path, headers=same_origin_headers(client))
    assert response.status_code == 401


def test_with_a_session_all_five_routes_work(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime, tmp_path: Path
) -> None:
    app = build_dashboard(stub_runtime)
    workspace = Path(app.state.service.workspace_root)
    (workspace / ".ao").mkdir()
    (workspace / ".ao" / "schedules.yaml").write_text(
        "version: '1.0'\n"
        "schedules:\n"
        "  - id: a\n    kind: cron\n    schedule: '0 * * * *'\n    workflow: w.json\n"
        "    limits: {quotas: [{period: day, max_runs: 5}]}\n"
    )
    client = make_client(app)
    issued = stub_runtime.issue(SessionState.FULL)
    client.cookies.set(stub_runtime.cookie_name, issued.token)
    headers = same_origin_headers(client, issued.proof)
    expected = {"/api/schedules": 200, "/api/schedules/a/history": 200}
    for method, path in ROUTES:
        want = expected.get(path, 202 if path.endswith("run-now") else 200)
        assert client.request(method, path, headers=headers).status_code == want, path
