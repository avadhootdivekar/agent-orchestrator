"""Dashboard surface for per-task git isolation (E-Wk9Tz3 `T-Cx4Jf1` AC-9 / S-5).

Two things are pinned here, at both layers the dashboard has:

1. `RunRepository.detail` carries the per-task "Integration" column data
   (`integration_status` / `tier_reached` / `conflicted_count`) and the run-header block
   (branch, head(s), `tier_counts`).
2. Both degrade to blank — `None`/`0`/absent — for a run that never used isolation, so
   every pre-epic run renders exactly as it did before the column existed (NFR-2).

The `/api/runs/{id}` assertions go through the real FastAPI app: the React table consumes
the JSON payload, not the dataclass, so the JSON is the contract worth pinning.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_orchestrator.models import (
    RunIntegrationState,
    RunState,
    TaskIntegrationState,
    TaskRunState,
)
from agent_orchestrator.ui.runs import RunRepository

from .conftest import make_run_state, write_run

fastapi = pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator.project_config import ProjectConfig  # noqa: E402
from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402
from agent_orchestrator.ui.service import DashboardService  # noqa: E402

from .conftest import StubSupervisor  # noqa: E402

RUN_ID = "iso-20260907T100000Z"
BRANCH = f"ao/{RUN_ID}/integration"
HEAD = "0123456789abcdef0123456789abcdef01234567"


def _isolated_run_state() -> RunState:
    """A run that used isolation: one clean land, one rerun-then-landed, one T4 failure."""
    state = make_run_state(
        run_id=RUN_ID,
        tasks={
            "clean": TaskRunState(status="succeeded"),
            "reran": TaskRunState(status="succeeded"),
            "broke": TaskRunState(status="failed"),
        },
    )
    state.integration = RunIntegrationState(
        active=True,
        branch=BRANCH,
        heads={"core": HEAD},
        # S-5: `rerun` is the whole point — a task that conflicted and was rerun lands at
        # tier `auto` on the fresh base, so without the histogram the run looks free.
        tier_counts={"auto": 3, "mechanical": 1, "rerun": 1},
    )
    state.task_integration = {
        "clean": TaskIntegrationState(
            status="integrated", isolation="worktree", tier_reached="auto"
        ),
        "reran": TaskIntegrationState(
            status="integrated",
            isolation="worktree",
            tier_reached="rerun",
            conflicted_paths=["src/shared.rs"],
        ),
        "broke": TaskIntegrationState(
            status="failed",
            isolation="worktree",
            tier_reached="mechanical",
            conflicted_paths=["src/a.rs", "src/b.rs"],
            last_error="conflict_unresolved",
        ),
    }
    return state


@pytest.fixture()
def repo(workspace: Path) -> RunRepository:
    return RunRepository(str(workspace))


@pytest.fixture()
def client(workspace: Path, stub_supervisor: StubSupervisor) -> Iterator[TestClient]:
    service = DashboardService(
        str(workspace),
        supervisor=stub_supervisor,  # type: ignore[arg-type]
        project_config=ProjectConfig(),
    )
    with TestClient(create_app(service)) as test_client:
        yield test_client


class TestIntegrationColumn:
    def test_task_rows_carry_status_tier_and_conflict_count(
        self, workspace: Path, repo: RunRepository
    ) -> None:
        write_run(workspace, _isolated_run_state())
        rows = {task.id: task for task in repo.detail(RUN_ID).tasks}

        assert rows["clean"].integration_status == "integrated"
        assert rows["clean"].tier_reached == "auto"
        assert rows["clean"].conflicted_count == 0

        # S-5: the successful rerun must still show the tier it actually cost.
        assert rows["reran"].tier_reached == "rerun"
        assert rows["reran"].conflicted_count == 1

        assert rows["broke"].integration_status == "failed"
        assert rows["broke"].tier_reached == "mechanical"
        assert rows["broke"].conflicted_count == 2

    def test_run_header_carries_branch_heads_and_tier_counts(
        self, workspace: Path, repo: RunRepository
    ) -> None:
        write_run(workspace, _isolated_run_state())
        integration = repo.detail(RUN_ID).integration

        assert integration is not None
        assert integration.active is True
        assert integration.branch == BRANCH
        assert integration.heads == {"core": HEAD}
        assert integration.tier_counts == {"auto": 3, "mechanical": 1, "rerun": 1}
        assert integration.degraded_reason is None

    def test_api_payload_exposes_the_same_fields(self, workspace: Path, client: TestClient) -> None:
        write_run(workspace, _isolated_run_state())
        payload = client.get(f"{API_PREFIX}/runs/{RUN_ID}").json()

        assert payload["integration"]["branch"] == BRANCH
        assert payload["integration"]["heads"] == {"core": HEAD}
        assert payload["integration"]["tier_counts"]["rerun"] == 1

        rows = {row["id"]: row for row in payload["tasks"]}
        assert rows["reran"]["integration_status"] == "integrated"
        assert rows["reran"]["tier_reached"] == "rerun"
        assert rows["reran"]["conflicted_count"] == 1


class TestDegradesBlankWithoutIsolation:
    def test_run_without_isolation_has_no_integration_block(
        self, workspace: Path, repo: RunRepository
    ) -> None:
        """NFR-2: a pre-epic run carries no `task_integration` entries and an inactive
        `RunIntegrationState`, so every new field must read as "nothing to show" rather
        than as a status the run never had.
        """
        write_run(workspace, make_run_state(tasks={"only": TaskRunState(status="succeeded")}))
        detail = repo.detail("demo-20260724T100000Z")

        assert detail.integration is None
        (row,) = detail.tasks
        assert row.integration_status is None
        assert row.tier_reached is None
        assert row.conflicted_count == 0

    def test_api_payload_degrades_to_nulls(self, workspace: Path, client: TestClient) -> None:
        write_run(workspace, make_run_state(tasks={"only": TaskRunState(status="succeeded")}))
        payload = client.get(f"{API_PREFIX}/runs/demo-20260724T100000Z").json()

        assert payload["integration"] is None
        (row,) = payload["tasks"]
        assert row["integration_status"] is None
        assert row["tier_reached"] is None
        assert row["conflicted_count"] == 0

    def test_degraded_isolation_still_reports_its_reason(
        self, workspace: Path, repo: RunRepository
    ) -> None:
        """A run whose isolation degraded mid-flight (FR-12) keeps `active=True` and a
        `degraded_reason`; the header must surface the reason rather than showing a branch
        with no explanation for why later tasks were not isolated.
        """
        state = _isolated_run_state()
        state.integration.degraded_reason = "git_too_old"
        write_run(workspace, state)

        integration = repo.detail(RUN_ID).integration
        assert integration is not None
        assert integration.degraded_reason == "git_too_old"
