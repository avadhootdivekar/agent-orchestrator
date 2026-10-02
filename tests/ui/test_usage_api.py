"""`GET /api/usage` and the shared-module proof with `ao rate` (E-Us9Kd4 T-Ui5Ij6, backend)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

from agent_orchestrator.cli import app as cli_app  # noqa: E402
from agent_orchestrator.project_config import ProjectConfig  # noqa: E402
from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402
from agent_orchestrator.ui.service import (  # noqa: E402
    DashboardBusyError,
    DashboardService,
    DashboardValidationError,
)
from agent_orchestrator.usage import MAX_REPORT_RUN_IDS  # noqa: E402

from .conftest import StubSupervisor, make_run_state, write_run  # noqa: E402

RUN = "demo-20260724T100000Z"
RUN2 = "demo-20260724T110000Z"
URL = f"{API_PREFIX}/usage"


@pytest.fixture()
def svc(workspace: Path, stub_supervisor: StubSupervisor) -> DashboardService:
    for rid in (RUN, RUN2):
        st = make_run_state(rid)
        for ts in st.tasks.values():
            ts.dispatch_cycle = 1  # usage only groups dispatched tasks
            ts.agent = "dev"
        write_run(workspace, st)
    return DashboardService(
        str(workspace),
        supervisor=stub_supervisor,  # type: ignore[arg-type]
        project_config=ProjectConfig(),
    )


@pytest.fixture()
def client(svc: DashboardService) -> Iterator[TestClient]:
    with TestClient(create_app(svc)) as c:
        yield c


class TestUsageApi:
    def test_all_runs_and_shape(self, client: TestClient) -> None:
        body = client.get(URL).json()
        assert {"groups", "skipped", "survival_available", "survival_runs_capped"} <= set(body)
        assert body["groups"]
        assert "mean_cost_usd" in body["groups"][0]

    def test_run_id_filter(self, client: TestClient) -> None:
        both = client.get(URL).json()["groups"][0]["tasks"]
        one = client.get(URL, params={"run_id": RUN}).json()["groups"][0]["tasks"]
        assert (one, both) == (2, 4)

    def test_unknown_run_is_reported_skipped(self, client: TestClient) -> None:
        body = client.get(URL, params=[("run_id", RUN), ("run_id", "ghost")]).json()
        assert any("ghost" in s for s in body["skipped"])

    @pytest.mark.parametrize("bad", ["..", "x" * 200, "a b"])
    def test_bad_run_id_400(self, client: TestClient, bad: str) -> None:
        assert client.get(URL, params={"run_id": bad}).status_code == 400

    def test_too_many_run_ids_400(self, client: TestClient) -> None:
        ids = [("run_id", f"r{i}") for i in range(MAX_REPORT_RUN_IDS + 1)]
        assert client.get(URL, params=ids).status_code == 400

    @pytest.mark.parametrize("ref", ["--upload-pack=x", "a b", "a\x01b"])
    def test_bad_ref_400(self, client: TestClient, ref: str) -> None:
        assert client.get(URL, params={"survival": "true", "ref": ref}).status_code == 400

    def test_ref_without_survival_400(self, client: TestClient) -> None:
        assert client.get(URL, params={"ref": "main"}).status_code == 400

    def test_survival_degrades_without_git_state(self, client: TestClient) -> None:
        r = client.get(URL, params={"survival": "true"})
        assert r.status_code == 200
        assert r.json()["survival_runs_capped"] is False


class TestUsageService:
    def test_validation_errors(self, svc: DashboardService) -> None:
        with pytest.raises(DashboardValidationError):
            svc.usage_report(["..", RUN])

    def test_survival_slots_are_bounded(self, svc: DashboardService) -> None:
        slots = [svc._survival_slot(True) for _ in range(3)]
        slots[0].__enter__()
        slots[1].__enter__()
        try:
            with pytest.raises(DashboardBusyError):
                slots[2].__enter__()
        finally:
            slots[0].__exit__()
            slots[1].__exit__()
        with svc._survival_slot(True):
            pass  # released slots are reusable


def test_post_feedback_is_seen_by_ao_rate_and_usage(
    client: TestClient, workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = client.post(
        f"{API_PREFIX}/runs/{RUN}/feedback",
        json={"scope": "run", "rating": "bad", "reasons": ["wrong"]},
    )
    assert r.status_code == 201
    shown = CliRunner().invoke(
        cli_app, ["rate", RUN, "--show", "--json", "--workspace", str(workspace)]
    )
    assert shown.exit_code == 0, shown.output
    doc = json.loads(shown.output)
    assert [e["source"] for e in doc["entries"]] == ["dashboard"]
    assert doc["effective"][0]["rating"] == "bad"
    # and a CLI-recorded rating is visible through the dashboard
    rated = CliRunner().invoke(
        cli_app, ["rate", RUN, "good", "--task", "build", "--workspace", str(workspace)]
    )
    assert rated.exit_code == 0, rated.output
    tasks = client.get(f"{API_PREFIX}/runs/{RUN}/feedback").json()["tasks"]
    assert tasks["build"]["rating"] == "good" and tasks["build"]["source"] == "cli"
    assert tasks["test"]["rating"] == "bad"
    # usage report reflects the same feedback
    groups = client.get(URL, params={"run_id": RUN}).json()["groups"]
    # run-level "bad" applies to "test" (no task rating); "build" has its own "good"
    assert sum(g["fb_bad"] for g in groups) == 1
    assert sum(g["fb_good"] for g in groups) == 1
