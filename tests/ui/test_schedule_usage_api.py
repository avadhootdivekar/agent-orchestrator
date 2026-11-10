"""``GET /api/schedules/{id}/usage`` and ``/api/schedules/governance-events`` (T-8knqVe).

Fixed clock; the scheduler state dir is a per-test temp dir.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator.service.fire_store import ScheduleEventLog  # noqa: E402
from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402
from agent_orchestrator.ui.service import DashboardService  # noqa: E402

from .conftest import StubSupervisor  # noqa: E402

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
URL = f"{API_PREFIX}/schedules"


def _binding(sid: str) -> dict[str, Any]:
    return {
        "id": sid,
        "kind": "cron",
        "schedule": "0 * * * *",
        "workflow": "w.json",
        "limits": {
            "quotas": [{"period": "day", "max_runs": 2}],
            "cost": {"periods": [{"period": "day", "max_usd": 5.0}]},
        },
    }


@pytest.fixture()
def state_dir(tmp_path: Path) -> Path:
    return tmp_path / "state"


@pytest.fixture()
def svc(workspace: Path, stub_supervisor: StubSupervisor, state_dir: Path) -> DashboardService:
    path = workspace / ".ao" / "schedules.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"version": "1.0", "schedules": [_binding("a"), _binding("b")]}))
    return DashboardService(
        str(workspace),
        supervisor=stub_supervisor,  # type: ignore[arg-type]
        clock=lambda: NOW,
        schedule_state_dir=state_dir,
    )


@pytest.fixture()
def client(svc: DashboardService) -> TestClient:
    return TestClient(create_app(svc))


@pytest.fixture()
def emit(svc: DashboardService, state_dir: Path) -> Callable[..., None]:
    def _emit(event: str, sid: str, at: datetime = NOW, **fields: Any) -> None:
        ScheduleEventLog(state_dir, clock=lambda: at).emit(
            event, workspace=svc.workspace_root, schedule_id=sid, **fields
        )

    return _emit


def test_usage_empty_window_shows_limits(client: TestClient) -> None:
    r = client.get(f"{URL}/a/usage")
    assert r.status_code == 200
    body = r.json()
    assert body["schema"] == "ao.schedule.usage/v1" and body["schedule_id"] == "a"
    (w,) = body["windows"]
    assert (w["period"], w["runs_used"], w["runs_max"], w["max_usd"]) == ("day", 0, 2, 5.0)
    assert w["resets_at"] == "2026-11-12T00:00:00+00:00"
    assert w["skipped"] == 0 and w["quota_exhausted"] == 0


def test_usage_counts_quota_exhausted_skips_in_window_only(
    client: TestClient, emit: Callable[..., None]
) -> None:
    emit("schedule.governance.skipped", "a", reason="quota_exhausted")
    emit("schedule.governance.skipped", "a", reason="cost_cap_reached")
    emit("schedule.governance.skipped", "a", NOW - timedelta(days=2), reason="quota_exhausted")
    emit("schedule.governance.skipped", "b", reason="quota_exhausted")
    (w,) = client.get(f"{URL}/a/usage").json()["windows"]
    assert (w["skipped"], w["quota_exhausted"]) == (2, 1)


def test_usage_at_selects_window(client: TestClient) -> None:
    (w,) = client.get(f"{URL}/a/usage", params={"at": "2026-11-05T10:00:00+00:00"}).json()[
        "windows"
    ]
    assert w["window_key"] == "day:2026-11-05"


def test_usage_errors(client: TestClient) -> None:
    assert client.get(f"{URL}/nope/usage").status_code == 404
    assert client.get(f"{URL}/a/usage", params={"at": "garbage"}).status_code == 400


def test_governance_events_newest_first_filtered_and_clamped(
    client: TestClient, emit: Callable[..., None]
) -> None:
    emit("schedule.governance.skipped", "a", NOW - timedelta(hours=2), reason="quota_exhausted")
    emit("schedule.governance.resumed", "a", NOW - timedelta(hours=1))
    emit("schedule.fired", "a", NOW)  # not governance
    emit("schedule.governance.skipped", "b", NOW, reason="cost_cap_reached", prompt_text="SECRET")
    events = client.get(f"{URL}/governance-events").json()["events"]
    assert [e["event"] for e in events] == [
        "schedule.governance.skipped",
        "schedule.governance.resumed",
        "schedule.governance.skipped",
    ]
    assert "SECRET" not in client.get(f"{URL}/governance-events").text
    only_a = client.get(f"{URL}/governance-events", params={"id": "a"}).json()["events"]
    assert {e["schedule_id"] for e in only_a} == {"a"} and len(only_a) == 2
    assert len(client.get(f"{URL}/governance-events", params={"limit": 1}).json()["events"]) == 1
    assert client.get(f"{URL}/governance-events", params={"limit": 0}).status_code == 422
    assert client.get(f"{URL}/governance-events", params={"limit": 501}).status_code == 422
    assert client.get(f"{URL}/governance-events", params={"id": "nope"}).status_code == 404
