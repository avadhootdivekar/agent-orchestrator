"""Hub-with-auth harness (owner: T-KOv2qD; HLD section 20.2). Test-only.

``make_hub``: the REAL ``build_hub_app(provider, auth=runtime)`` over the real runtime that
``real_routes.build_dash(kind="hub")`` builds (users seeded, deterministic seeds, fake hasher,
fake clock), wrapped in the same ``Dash`` handle the core-route tests use.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from starlette.testclient import TestClient

from agent_orchestrator.service.hub import build_hub_app
from agent_orchestrator.ui.service import DashboardService
from tests.auth.helpers.core import FakeClock
from tests.auth.helpers.real_routes import HUB_PORT, LOOPBACK_PEER, Dash, build_dash

HUB_BASE_URL = f"http://127.0.0.1:{HUB_PORT}"
HUB_SEED = 17

FIXED_PAYLOAD: dict[str, Any] = {
    "workspaces": [
        {
            "root": "/home/user/proj1",
            "port": 9001,
            "pid": 4242,
            "state": "running",
            "restart_count": 0,
            "last_error": None,
            "log_path": "/state/logs/proj1.log",
            "reassignment_reason": None,
            "run_summary": {"total_runs": 3, "runs_by_status": {"succeeded": 3}},
        },
        {
            "root": "/home/user/proj2",
            "port": 9002,
            "pid": None,
            "state": "stopped",
            "restart_count": 2,
            "last_error": "child exited with code 1",
            "log_path": "/state/logs/proj2.log",
            "reassignment_reason": None,
        },
    ],
    "conflicts": [],
    "boot_resume_decisions": [],
    "supervisor_pid": 999,
    "hub_port": HUB_PORT,
    "uptime_seconds": 42.0,
}


def fixed_status_provider() -> dict[str, Any]:
    return FIXED_PAYLOAD


def make_hub(tmp_path: Path, clock: FakeClock, *, totp_service: Any = None) -> Dash:
    """A hub app with auth on. ``totp_service=None`` leaves the second-factor routes out."""
    base = build_dash(
        tmp_path,
        cast(DashboardService, None),  # only the ui kind reads the service
        clock=clock,
        seed=HUB_SEED,
        port=HUB_PORT,
        kind="hub",
        totp_service=totp_service,
        base_url=HUB_BASE_URL,
    )
    app = build_hub_app(fixed_status_provider, auth=base.runtime)
    client = TestClient(app, base_url=HUB_BASE_URL, client=LOOPBACK_PEER, follow_redirects=False)
    return Dash(base.runtime, app, client, clock, base.session_store)
