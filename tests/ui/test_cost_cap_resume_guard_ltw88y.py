"""T-lTW88y H-2: the generic dashboard resume/start must not replace a governed run's cap."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator.project_config import ProjectConfig  # noqa: E402
from agent_orchestrator.run_options import MAX_COST_CAP_USD, validate_cost_cap_usd  # noqa: E402
from agent_orchestrator.service.fire_store import (  # noqa: E402
    FireGovernance,
    FireIntentRecord,
    FireIntentStore,
)  # noqa: E402
from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402
from agent_orchestrator.ui.service import DashboardService  # noqa: E402

from .conftest import StubSupervisor, make_run_state, write_run  # noqa: E402


@pytest.fixture()
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "_sched_state"
    d.mkdir()
    return d


@pytest.fixture()
def client(
    workspace: Path, stub_supervisor: StubSupervisor, state_dir: Path
) -> Iterator[TestClient]:
    service = DashboardService(
        str(workspace),
        supervisor=stub_supervisor,  # type: ignore[arg-type]
        project_config=ProjectConfig(),
        schedule_state_dir=state_dir,
    )
    with TestClient(create_app(service)) as c:
        yield c


def _govern(state_dir: Path, workspace: Path, run_id: str) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    FireIntentStore(state_dir).write(
        FireIntentRecord(
            fire_key="s1@2026-01-01T00:00:00+00:00",
            workspace_root=str(workspace.resolve()),
            schedule_id="s1",
            scheduled_for=now,
            created_at=now,
            status="launched",
            run_id=run_id,
            governance=FireGovernance(cap_state="paused"),
        )
    )


def test_validate_cost_cap_has_upper_bound() -> None:
    assert validate_cost_cap_usd(MAX_COST_CAP_USD) == MAX_COST_CAP_USD
    with pytest.raises(ValueError):
        validate_cost_cap_usd(MAX_COST_CAP_USD * 10)


def test_governed_run_resume_rejects_cost_cap(
    client: TestClient, workspace: Path, state_dir: Path
) -> None:
    write_run(workspace, make_run_state(run_id="gov", status="failed"))
    _govern(state_dir, workspace, "gov")
    r = client.post(f"{API_PREFIX}/runs/gov/resume", json={"options": {"cost_cap_usd": 5}})
    assert r.status_code == 409
    assert "governed" in r.json()["detail"]


def test_governed_run_resume_without_cap_still_works(
    client: TestClient, workspace: Path, state_dir: Path
) -> None:
    write_run(workspace, make_run_state(run_id="gov", status="failed"))
    _govern(state_dir, workspace, "gov")
    assert client.post(f"{API_PREFIX}/runs/gov/resume", json={"options": {}}).status_code == 200


def test_ungoverned_run_resume_accepts_bounded_cap(client: TestClient, workspace: Path) -> None:
    write_run(workspace, make_run_state(run_id="plain", status="failed"))
    r = client.post(f"{API_PREFIX}/runs/plain/resume", json={"options": {"cost_cap_usd": 5}})
    assert r.status_code == 200


@pytest.mark.parametrize("bad", [0, -1, 1e9, "abc"])
def test_resume_rejects_out_of_range_cap_with_400(
    client: TestClient, workspace: Path, bad: object
) -> None:
    write_run(workspace, make_run_state(run_id="plain", status="failed"))
    r = client.post(f"{API_PREFIX}/runs/plain/resume", json={"options": {"cost_cap_usd": bad}})
    assert r.status_code == 400
