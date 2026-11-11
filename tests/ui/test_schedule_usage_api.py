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

from agent_orchestrator.service.fire_store import (  # noqa: E402
    FireGovernance,
    FireIntentRecord,
    FireIntentStore,
    ScheduleEventLog,
)
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


def test_usage_reflects_fire_records_through_the_real_ledger(
    client: TestClient, svc: DashboardService, state_dir: Path
) -> None:
    """The panel's numbers come from the same ledger the gate uses: seed fire records (no mocked
    ledger) and check runs / actual / reserved / confidence, window bounds and schedule scoping."""
    store = FireIntentStore(state_dir)

    def put(key: str, at: datetime, sid: str = "a", status: str = "launched", **gov: Any) -> None:
        store.write(
            FireIntentRecord(
                fire_key=key, workspace_root=svc.workspace_root, schedule_id=sid,
                scheduled_for=at, created_at=at, status=status,  # type: ignore[arg-type]
                run_id=f"run-{key}", governance=FireGovernance(**gov) if gov else None,
            )
        )  # fmt: skip

    put("done", NOW - timedelta(hours=3), final=True, cost_observed_usd=1.25,
        cost_confidence="actual", reserved_usd=2.0)  # fmt: skip
    put("running", NOW - timedelta(hours=1), reserved_usd=2.0)  # no run state yet: held
    put("skip", NOW - timedelta(hours=2), status="suppressed", reserved_usd=2.0)  # costs nothing
    put("yesterday", NOW - timedelta(days=1), final=True, cost_observed_usd=9.0)
    put("other", NOW - timedelta(hours=1), sid="b", final=True, cost_observed_usd=9.0)
    (w,) = client.get(f"{URL}/a/usage").json()["windows"]
    assert (w["runs_used"], w["runs_max"]) == (2, 2)
    assert w["actual_usd"] == pytest.approx(1.25) and w["reserved_usd"] == pytest.approx(2.0)
    assert w["confidence"] == "pending"
    (prev,) = client.get(
        f"{URL}/a/usage", params={"at": (NOW - timedelta(days=1)).isoformat()}
    ).json()["windows"]
    assert (prev["runs_used"], prev["actual_usd"]) == (1, pytest.approx(9.0))


def test_usage_at_selects_window(client: TestClient) -> None:
    (w,) = client.get(f"{URL}/a/usage", params={"at": "2026-11-05T10:00:00+00:00"}).json()[
        "windows"
    ]
    assert w["window_key"] == "day:2026-11-05"


def test_usage_errors(client: TestClient) -> None:
    assert client.get(f"{URL}/nope/usage").status_code == 404
    assert client.get(f"{URL}/a/usage", params={"at": "garbage"}).status_code == 400


@pytest.mark.parametrize("at", ["9999-12-31T23:59:59+00:00", "0001-01-01T00:00:00+00:00"])
def test_usage_at_out_of_range_is_400_not_500(client: TestClient, at: str) -> None:
    """T-lTW88y audit: window math overflows near datetime.max; refuse instead of a 500."""
    assert client.get(f"{URL}/a/usage", params={"at": at}).status_code == 400


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


def test_usage_api_never_writes_fire_records(
    client: TestClient, svc: DashboardService, state_dir: Path
) -> None:
    """T-Se4Bk5 R-2: the dashboard is a second process; its ledger must not persist cost marks
    (unlocked read-modify-write racing the engine's fire writes)."""
    store = FireIntentStore(state_dir)
    store.write(
        FireIntentRecord(
            fire_key="k", workspace_root=svc.workspace_root, schedule_id="a",
            scheduled_for=NOW - timedelta(hours=1), created_at=NOW - timedelta(hours=1),
            status="launched", run_id="r",
            governance=FireGovernance(reserved_usd=2.0, run_cost_cap_usd=2.0),
        )
    )  # fmt: skip
    run_dir = Path(svc.workspace_root) / ".orchestrator" / "runs" / "r"
    run_dir.mkdir(parents=True)
    from agent_orchestrator.models import RunState, TaskRunState

    st = RunState(
        run_id="r", workflow_id="w", started_at=NOW.isoformat(), updated_at=NOW.isoformat(),
        status="running",
        tasks={
            "t": TaskRunState(status="succeeded", cumulative_cost_usd=1.5, actuals_available=True)
        },
    )  # type: ignore[arg-type]  # fmt: skip
    (run_dir / "state.json").write_text(st.model_dump_json(), encoding="utf-8")
    path = store.directory(svc.workspace_root) / "k.json"
    before = (path.read_bytes(), path.stat().st_mtime_ns)
    (w,) = client.get(f"{URL}/a/usage").json()["windows"]
    assert w["actual_usd"] == pytest.approx(1.5)  # observed cost is reported...
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before  # ...never persisted


def test_usage_row_keys_match_ui_type_ssot(client: TestClient) -> None:
    """Drift guard for the mocked-fetch UI test (``ui/src/test/schedule-usage.test.tsx``): the
    real endpoint's per-window field names must equal ``ScheduleUsageWindow`` in ``ui/src/types.ts``
    (the UI fixture is typed ``ScheduleUsageDoc``, so tsc ties the mock to the same interface)."""
    import re

    types_ts = Path(__file__).resolve().parents[2] / "ui" / "src" / "types.ts"
    if not types_ts.is_file():
        pytest.skip("ui/src/types.ts not present (installed wheel / sparse checkout)")
    text = types_ts.read_text()
    m = re.search(r"export interface ScheduleUsageWindow \{(.*?)\n\}", text, re.S)
    assert m, "ScheduleUsageWindow interface not found in ui/src/types.ts"
    ts_fields = set(re.findall(r"^\s+(\w+)\??:", m.group(1), re.M))
    doc = client.get(f"{URL}/a/usage").json()
    (w,) = doc["windows"]
    assert set(w) == ts_fields
    m2 = re.search(r"export interface ScheduleUsageDoc \{(.*?)\n\}", text, re.S)
    assert m2
    assert set(doc) == set(re.findall(r"^\s+(\w+)\??:", m2.group(1), re.M))
