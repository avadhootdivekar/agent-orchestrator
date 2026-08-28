"""Integration tests for the dashboard HTTP API (E-Ui7Kq2).

Drives the real FastAPI app through ``TestClient`` — real routing, real request/response
validation, real status codes — over a temp workspace with a stub supervisor. This is the
layer that proves the HTTP contract (status codes, error mapping, route precedence), which
the service-level unit tests cannot.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi", reason="dashboard API needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator.models import TaskRunState  # noqa: E402
from agent_orchestrator.project_config import ProjectConfig  # noqa: E402
from agent_orchestrator.ui.app import API_PREFIX, create_app  # noqa: E402
from agent_orchestrator.ui.service import DashboardService  # noqa: E402

from .conftest import StubSupervisor, make_run_state, write_run, write_workflow  # noqa: E402


@pytest.fixture()
def client(workspace: Path, stub_supervisor: StubSupervisor) -> Iterator[TestClient]:
    service = DashboardService(
        str(workspace),
        supervisor=stub_supervisor,  # type: ignore[arg-type]
        project_config=ProjectConfig(),
    )
    with TestClient(create_app(service)) as test_client:
        yield test_client


class TestHealthAndWorkspace:
    def test_health_reports_ok_with_a_version(self, client: TestClient) -> None:
        response = client.get(f"{API_PREFIX}/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert response.json()["version"]

    def test_workspace_reports_the_served_root(self, client: TestClient, workspace: Path) -> None:
        body = client.get(f"{API_PREFIX}/workspace").json()
        assert body["workspace_root"] == str(workspace.resolve())
        assert body["roots"][0]["name"] == "workspace"

    def test_openapi_schema_is_served(self, client: TestClient) -> None:
        assert client.get(f"{API_PREFIX}/openapi.json").status_code == 200


class TestFilesApi:
    def test_lists_the_root_including_hidden_entries(
        self, client: TestClient, workspace: Path
    ) -> None:
        (workspace / "visible.txt").write_text("v", encoding="utf-8")
        (workspace / ".hidden").write_text("h", encoding="utf-8")

        body = client.get(f"{API_PREFIX}/files").json()
        names = {e["name"] for e in body["entries"]}
        assert names == {"visible.txt", ".hidden"}

    def test_lists_a_subdirectory(self, client: TestClient, workspace: Path) -> None:
        (workspace / "src").mkdir()
        (workspace / "src" / "main.py").write_text("x", encoding="utf-8")

        body = client.get(f"{API_PREFIX}/files", params={"path": "src"}).json()
        assert [e["name"] for e in body["entries"]] == ["main.py"]

    def test_traversal_returns_403_not_500(self, client: TestClient) -> None:
        response = client.get(f"{API_PREFIX}/files", params={"path": "../../etc"})
        assert response.status_code == 403

    def test_missing_directory_returns_404(self, client: TestClient) -> None:
        assert client.get(f"{API_PREFIX}/files", params={"path": "nope"}).status_code == 404

    def test_reads_text_content(self, client: TestClient, workspace: Path) -> None:
        (workspace / "a.py").write_text("print(1)\n", encoding="utf-8")
        body = client.get(f"{API_PREFIX}/files/content", params={"path": "a.py"}).json()
        assert body["text"] == "print(1)\n"
        assert body["is_binary"] is False

    def test_binary_content_is_flagged_and_not_inlined(
        self, client: TestClient, workspace: Path
    ) -> None:
        (workspace / "blob.bin").write_bytes(b"\x00\x01\x02")
        body = client.get(f"{API_PREFIX}/files/content", params={"path": "blob.bin"}).json()
        assert body["is_binary"] is True
        assert body["text"] is None

    def test_content_traversal_returns_403(self, client: TestClient) -> None:
        response = client.get(f"{API_PREFIX}/files/content", params={"path": "/etc/passwd"})
        assert response.status_code == 403

    def test_content_requires_a_path(self, client: TestClient) -> None:
        assert client.get(f"{API_PREFIX}/files/content").status_code == 422


class TestWorkflowsApi:
    def test_lists_discovered_workflows(self, client: TestClient, workspace: Path) -> None:
        write_workflow(workspace / "wf.json", workflow_id="demo", prompt_path="prompt.md")
        body = client.get(f"{API_PREFIX}/workflows").json()
        assert [w["id"] for w in body] == ["demo"]
        assert body[0]["prompt_path"] == "prompt.md"

    def test_empty_workspace_lists_nothing(self, client: TestClient) -> None:
        assert client.get(f"{API_PREFIX}/workflows").json() == []


class TestRunsApi:
    def test_lists_runs(self, client: TestClient, workspace: Path) -> None:
        write_run(workspace, make_run_state())
        body = client.get(f"{API_PREFIX}/runs").json()
        assert [r["run_id"] for r in body] == ["demo-20260724T100000Z"]

    def test_stats_route_is_not_shadowed_by_the_run_id_route(
        self, client: TestClient, workspace: Path
    ) -> None:
        # /runs/stats is a literal that must win over /runs/{run_id}; if ordering
        # regressed this would 404 as a missing run named "stats".
        write_run(workspace, make_run_state())
        response = client.get(f"{API_PREFIX}/runs/stats")
        assert response.status_code == 200
        assert response.json()["total_runs"] == 1

    def test_run_detail(self, client: TestClient, workspace: Path) -> None:
        write_run(workspace, make_run_state())
        body = client.get(f"{API_PREFIX}/runs/demo-20260724T100000Z").json()
        assert body["summary"]["run_id"] == "demo-20260724T100000Z"
        assert len(body["tasks"]) == 2

    def test_unknown_run_returns_404(self, client: TestClient) -> None:
        assert client.get(f"{API_PREFIX}/runs/nope").status_code == 404

    def test_run_log(self, client: TestClient, workspace: Path) -> None:
        write_run(workspace, make_run_state(run_id="foreign"))
        body = client.get(f"{API_PREFIX}/runs/foreign/log").json()
        assert body["text"] == ""

    def test_log_max_bytes_is_validated(self, client: TestClient, workspace: Path) -> None:
        write_run(workspace, make_run_state())
        response = client.get(
            f"{API_PREFIX}/runs/demo-20260724T100000Z/log", params={"max_bytes": 0}
        )
        assert response.status_code == 422


class TestStartRunApi:
    def test_starts_a_run_and_returns_201(
        self, client: TestClient, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        wf = write_workflow(workspace / "wf.json", prompt_path="prompt.md")
        response = client.post(
            f"{API_PREFIX}/runs",
            json={"workflow_path": str(wf), "prompt": "build it", "options": {}},
        )
        assert response.status_code == 201
        assert response.json()["run_id"] == "launched-run"
        assert stub_supervisor.launch_calls[0]["prompt"] == "build it"

    def test_forwards_allow_listed_options(
        self, client: TestClient, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        wf = write_workflow(workspace / "wf.json", prompt_path="prompt.md")
        client.post(
            f"{API_PREFIX}/runs",
            json={
                "workflow_path": str(wf),
                "prompt": "x",
                "options": {"model": "claude-opus-4-8", "max_parallel": 4},
            },
        )
        options = stub_supervisor.launch_calls[0]["options"]
        assert options == {"model": "claude-opus-4-8", "max_parallel": 4}

    def test_prompt_without_prompt_path_returns_400(
        self, client: TestClient, workspace: Path
    ) -> None:
        wf = write_workflow(workspace / "wf.json", prompt_path=None)
        response = client.post(
            f"{API_PREFIX}/runs", json={"workflow_path": str(wf), "prompt": "nowhere to go"}
        )
        assert response.status_code == 400
        assert "prompt_path" in response.json()["detail"]

    def test_missing_workflow_returns_400(self, client: TestClient) -> None:
        response = client.post(f"{API_PREFIX}/runs", json={"workflow_path": "/no/such.json"})
        assert response.status_code == 400

    def test_malformed_body_returns_422(self, client: TestClient) -> None:
        assert client.post(f"{API_PREFIX}/runs", json={"options": "not-a-dict"}).status_code == 422


class TestResumeCancelDeleteApi:
    def test_resume_returns_200(self, client: TestClient, workspace: Path) -> None:
        write_run(workspace, make_run_state(run_id="stalled", status="failed"))
        response = client.post(f"{API_PREFIX}/runs/stalled/resume", json={"options": {}})
        assert response.status_code == 200

    def test_resume_works_without_a_body(self, client: TestClient, workspace: Path) -> None:
        write_run(workspace, make_run_state(run_id="stalled", status="failed"))
        assert client.post(f"{API_PREFIX}/runs/stalled/resume").status_code == 200

    def test_resume_unknown_run_returns_404(self, client: TestClient) -> None:
        assert client.post(f"{API_PREFIX}/runs/nope/resume").status_code == 404

    def test_resume_running_run_returns_409(
        self, client: TestClient, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        write_run(workspace, make_run_state(run_id="live-run", status="running"))
        stub_supervisor.next_run_id = "live-run"
        stub_supervisor.launch_run()
        assert client.post(f"{API_PREFIX}/runs/live-run/resume").status_code == 409

    def test_cancel_returns_200_and_marks_state_cancelled(
        self, client: TestClient, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        write_run(
            workspace,
            make_run_state(
                run_id="live-run", status="running", tasks={"t": TaskRunState(status="running")}
            ),
        )
        stub_supervisor.next_run_id = "live-run"
        stub_supervisor.launch_run()

        assert client.post(f"{API_PREFIX}/runs/live-run/cancel").status_code == 200
        detail = client.get(f"{API_PREFIX}/runs/live-run").json()
        assert detail["summary"]["status"] == "cancelled"

    def test_cancel_a_run_not_launched_here_returns_409(
        self, client: TestClient, workspace: Path
    ) -> None:
        write_run(workspace, make_run_state(run_id="foreign", status="running"))
        assert client.post(f"{API_PREFIX}/runs/foreign/cancel").status_code == 409

    def test_delete_returns_200_and_removes_the_run(
        self, client: TestClient, workspace: Path
    ) -> None:
        write_run(workspace, make_run_state())
        assert client.delete(f"{API_PREFIX}/runs/demo-20260724T100000Z").status_code == 200
        assert client.get(f"{API_PREFIX}/runs").json() == []

    def test_delete_unknown_run_returns_404(self, client: TestClient) -> None:
        assert client.delete(f"{API_PREFIX}/runs/nope").status_code == 404

    def test_delete_live_run_returns_409(
        self, client: TestClient, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        write_run(workspace, make_run_state(run_id="live-run", status="running"))
        stub_supervisor.next_run_id = "live-run"
        stub_supervisor.launch_run()
        assert client.delete(f"{API_PREFIX}/runs/live-run").status_code == 409


class TestGeneralInstructionsApi:
    def test_empty_when_unconfigured(self, client: TestClient) -> None:
        assert client.get(f"{API_PREFIX}/general-instructions").json() == []

    def test_reports_missing_paths(self, workspace: Path, stub_supervisor: StubSupervisor) -> None:
        service = DashboardService(
            str(workspace),
            supervisor=stub_supervisor,  # type: ignore[arg-type]
            project_config=ProjectConfig(general_instructions=["missing.md"]),
        )
        with TestClient(create_app(service)) as local_client:
            body = local_client.get(f"{API_PREFIX}/general-instructions").json()
            assert body[0]["exists"] is False


class TestFrontendServing:
    def test_serves_the_built_spa_at_root(self, client: TestClient) -> None:
        # The wheel ships the built frontend; if it is missing the route returns a 503 that
        # says how to build it, which is still a defined contract rather than a crash.
        response = client.get("/")
        assert response.status_code in (200, 503)
        if response.status_code == 200:
            assert "text/html" in response.headers["content-type"]

    def test_unknown_api_route_is_404_not_the_spa(self, client: TestClient) -> None:
        # The SPA fallback must never swallow an unknown /api path — that would turn a
        # typo'd endpoint into a confusing HTML response instead of a 404.
        assert client.get(f"{API_PREFIX}/does-not-exist").status_code == 404
