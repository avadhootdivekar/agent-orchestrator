"""Unit tests for DashboardService (E-Ui7Kq2).

Exercises the dashboard's whole API surface as plain Python — no HTTP, no subprocesses —
which is exactly what the framework-free service layer exists to make possible.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.models import TaskRunState
from agent_orchestrator.project_config import ProjectConfig
from agent_orchestrator.ui.files import PathNotAllowedError, PathNotFoundError
from agent_orchestrator.ui.service import DashboardError, DashboardService

from .conftest import StubSupervisor, make_run_state, write_run, write_workflow


class TestWorkspaceInfo:
    def test_reports_the_served_root(self, service: DashboardService, workspace: Path) -> None:
        assert service.workspace_info().workspace_root == str(workspace.resolve())

    def test_surfaces_configured_paths(
        self, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        config = ProjectConfig(workflow="/ws/wf.json", reposets="/ws/rs.json")
        svc = DashboardService(
            str(workspace),
            supervisor=stub_supervisor,  # type: ignore[arg-type]
            project_config=config,
            config_path="/ws/.ao/config.yaml",
        )
        info = svc.workspace_info()
        assert info.workflow == "/ws/wf.json"
        assert info.config_path == "/ws/.ao/config.yaml"


class TestGeneralInstructions:
    def test_empty_when_nothing_is_configured(self, service: DashboardService) -> None:
        assert service.general_instructions() == []

    def test_reports_existence_so_a_typo_is_visible(
        self, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        # The engine silently skips an unresolvable general instruction, so the dashboard is
        # the only place an operator finds out their house rules never reached any task.
        real = workspace / "rules.md"
        real.write_text("be careful", encoding="utf-8")

        svc = DashboardService(
            str(workspace),
            supervisor=stub_supervisor,  # type: ignore[arg-type]
            project_config=ProjectConfig(
                general_instructions=[str(real), str(workspace / "typo.md")]
            ),
        )
        results = svc.general_instructions()
        assert [r["exists"] for r in results] == [True, False]

    def test_relative_paths_resolve_against_the_workspace(
        self, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        (workspace / "rules.md").write_text("x", encoding="utf-8")
        svc = DashboardService(
            str(workspace),
            supervisor=stub_supervisor,  # type: ignore[arg-type]
            project_config=ProjectConfig(general_instructions=["rules.md"]),
        )
        assert svc.general_instructions()[0]["exists"] is True


class TestFileBrowsing:
    def test_lists_the_workspace_root(self, service: DashboardService, workspace: Path) -> None:
        (workspace / "a.txt").write_text("a", encoding="utf-8")
        listing = service.list_dir()
        assert [e["name"] for e in listing["entries"]] == ["a.txt"]
        assert listing["path"] == ""

    def test_reads_a_file(self, service: DashboardService, workspace: Path) -> None:
        (workspace / "a.txt").write_text("hello", encoding="utf-8")
        assert service.read_file(path="a.txt")["text"] == "hello"

    def test_traversal_is_refused(self, service: DashboardService) -> None:
        with pytest.raises(PathNotAllowedError):
            service.list_dir(path="../..")

    def test_missing_path_raises_not_found(self, service: DashboardService) -> None:
        with pytest.raises(PathNotFoundError):
            service.read_file(path="nope.txt")


class TestWorkflowDiscovery:
    def test_finds_specs_under_the_workspace(
        self, service: DashboardService, workspace: Path
    ) -> None:
        write_workflow(workspace / "wf.json", workflow_id="demo", prompt_path="prompt.md")
        found = service.list_workflows()
        assert [w.id for w in found] == ["demo"]
        assert found[0].prompt_path == "prompt.md"
        assert found[0].task_count == 1

    def test_ignores_json_that_is_not_a_workflow(
        self, service: DashboardService, workspace: Path
    ) -> None:
        (workspace / "package.json").write_text('{"name": "x"}', encoding="utf-8")
        (workspace / "notes.md").write_text("# hi", encoding="utf-8")
        assert service.list_workflows() == []

    def test_ignores_malformed_json(self, service: DashboardService, workspace: Path) -> None:
        (workspace / "broken.json").write_text("{not json", encoding="utf-8")
        assert service.list_workflows() == []

    def test_skips_noisy_directories(self, service: DashboardService, workspace: Path) -> None:
        # A spec inside .git/ or node_modules/ is never the user's workflow, and walking
        # those trees makes discovery feel broken.
        for skip_dir in (".git", "node_modules", ".venv"):
            write_workflow(workspace / skip_dir / "wf.json", workflow_id="hidden")
        assert service.list_workflows() == []

    def test_finds_specs_in_subdirectories(
        self, service: DashboardService, workspace: Path
    ) -> None:
        write_workflow(workspace / "specs" / "nested" / "wf.json", workflow_id="nested")
        assert [w.id for w in service.list_workflows()] == ["nested"]

    def test_includes_the_configured_workflow_from_outside_the_search_roots(
        self, workspace: Path, tmp_path: Path, stub_supervisor: StubSupervisor
    ) -> None:
        external = write_workflow(tmp_path / "external" / "wf.json", workflow_id="external")
        svc = DashboardService(
            str(workspace),
            supervisor=stub_supervisor,  # type: ignore[arg-type]
            project_config=ProjectConfig(workflow=str(external)),
        )
        assert [w.id for w in svc.list_workflows()] == ["external"]


class TestRunListing:
    def test_lists_persisted_runs_with_stats(
        self, service: DashboardService, workspace: Path
    ) -> None:
        write_run(workspace, make_run_state())
        rows = service.list_runs()
        assert len(rows) == 1
        assert rows[0]["run_id"] == "demo-20260724T100000Z"
        assert rows[0]["cost_usd"] == pytest.approx(0.75)
        assert rows[0]["is_live"] is False

    def test_marks_a_dashboard_launched_run_as_live(
        self, service: DashboardService, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        write_run(workspace, make_run_state(run_id="live-run", status="running"))
        stub_supervisor.next_run_id = "live-run"
        stub_supervisor.launch_run()

        assert service.list_runs()[0]["is_live"] is True

    def test_detail_includes_tasks(self, service: DashboardService, workspace: Path) -> None:
        write_run(workspace, make_run_state())
        detail = service.run_detail("demo-20260724T100000Z")
        assert {t["id"] for t in detail["tasks"]} == {"build", "test"}

    def test_detail_of_a_missing_run_raises(self, service: DashboardService) -> None:
        with pytest.raises(DashboardError, match="run not found"):
            service.run_detail("nope")

    def test_stats_aggregate_across_runs(self, service: DashboardService, workspace: Path) -> None:
        write_run(workspace, make_run_state(run_id="r1"))
        write_run(workspace, make_run_state(run_id="r2"))
        stats = service.run_stats()
        assert stats["total_runs"] == 2
        assert stats["total_cost_usd"] == pytest.approx(1.5)


class TestStartRun:
    def test_launches_the_selected_workflow(
        self, service: DashboardService, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        wf = write_workflow(workspace / "wf.json", prompt_path="prompt.md")
        service.start_run(workflow_path=str(wf), prompt="do the thing")

        assert len(stub_supervisor.launch_calls) == 1
        call = stub_supervisor.launch_calls[0]
        assert call["workflow_path"] == str(wf)
        assert call["prompt"] == "do the thing"

    def test_rejects_a_prompt_for_a_workflow_with_no_prompt_path(
        self, service: DashboardService, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        # Silently dropping the prompt would let an operator pay for a run that ignored
        # everything they typed.
        wf = write_workflow(workspace / "wf.json", prompt_path=None)
        with pytest.raises(DashboardError, match="declares no `prompt_path`"):
            service.start_run(workflow_path=str(wf), prompt="this has nowhere to go")
        assert stub_supervisor.launch_calls == []

    def test_allows_no_prompt_for_a_workflow_with_no_prompt_path(
        self, service: DashboardService, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        wf = write_workflow(workspace / "wf.json", prompt_path=None)
        service.start_run(workflow_path=str(wf))
        assert len(stub_supervisor.launch_calls) == 1

    def test_blank_prompt_is_not_treated_as_a_prompt(
        self, service: DashboardService, workspace: Path
    ) -> None:
        wf = write_workflow(workspace / "wf.json", prompt_path=None)
        service.start_run(workflow_path=str(wf), prompt="   \n  ")  # must not raise

    def test_rejects_a_missing_spec_file(self, service: DashboardService) -> None:
        with pytest.raises(DashboardError, match="not found"):
            service.start_run(workflow_path="/no/such/wf.json")

    def test_rejects_when_no_workflow_is_given_or_configured(
        self, service: DashboardService
    ) -> None:
        with pytest.raises(DashboardError, match="no workflow specified"):
            service.start_run()

    def test_falls_back_to_the_configured_workflow(
        self, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        wf = write_workflow(workspace / "configured.json")
        svc = DashboardService(
            str(workspace),
            supervisor=stub_supervisor,  # type: ignore[arg-type]
            project_config=ProjectConfig(workflow=str(wf)),
        )
        svc.start_run()
        assert stub_supervisor.launch_calls[0]["workflow_path"] == str(wf)


class TestResumeRun:
    def test_resumes_an_existing_run(
        self, service: DashboardService, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        write_run(workspace, make_run_state(run_id="stalled", status="failed"))
        service.resume_run("stalled")
        assert stub_supervisor.launch_calls[0]["run_id"] == "stalled"

    def test_rejects_an_unknown_run(self, service: DashboardService) -> None:
        with pytest.raises(DashboardError, match="run not found"):
            service.resume_run("nope")

    def test_rejects_resuming_a_run_that_is_already_going(
        self, service: DashboardService, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        write_run(workspace, make_run_state(run_id="live-run", status="running"))
        stub_supervisor.next_run_id = "live-run"
        stub_supervisor.launch_run()

        with pytest.raises(DashboardError, match="already running"):
            service.resume_run("live-run")

    def test_reuses_the_workflow_path_from_the_original_launch(
        self, service: DashboardService, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        write_run(workspace, make_run_state(run_id="prior", status="failed"))
        stub_supervisor.next_run_id = "prior"
        stub_supervisor.launch_run(workflow_path="/ws/original.json")
        stub_supervisor.live_runs.discard("prior")

        service.resume_run("prior")
        assert stub_supervisor.launch_calls[-1]["workflow_path"] == "/ws/original.json"


class TestCancelRun:
    def test_cancels_and_marks_the_state_cancelled(
        self, service: DashboardService, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        # The engine cannot record its own cancellation once signalled, so if the service
        # skipped this the run would sit at "running" forever.
        write_run(
            workspace,
            make_run_state(
                run_id="live-run",
                status="running",
                tasks={"t": TaskRunState(status="running")},
            ),
        )
        stub_supervisor.next_run_id = "live-run"
        stub_supervisor.launch_run()

        service.cancel_run("live-run")

        assert stub_supervisor.cancelled == ["live-run"]
        detail = service.run_detail("live-run")
        assert detail["summary"]["status"] == "cancelled"
        assert detail["tasks"][0]["status"] == "cancelled", (
            "a task killed with the process must not stay 'running'"
        )

    def test_cancel_regenerates_the_status_snapshot(
        self, service: DashboardService, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        # state.json and status.json must never diverge (ADR-002).
        import json

        write_run(workspace, make_run_state(run_id="live-run", status="running"))
        stub_supervisor.next_run_id = "live-run"
        stub_supervisor.launch_run()
        service.cancel_run("live-run")

        snapshot = json.loads(
            (workspace / ".orchestrator" / "runs" / "live-run" / "status.json").read_text()
        )
        assert snapshot["status"] == "cancelled"

    def test_cancelling_a_run_the_dashboard_did_not_launch_raises(
        self, service: DashboardService, workspace: Path
    ) -> None:
        write_run(workspace, make_run_state(run_id="foreign", status="running"))
        with pytest.raises(DashboardError):
            service.cancel_run("foreign")


class TestDeleteRun:
    def test_deletes_a_finished_run(self, service: DashboardService, workspace: Path) -> None:
        write_run(workspace, make_run_state())
        service.delete_run("demo-20260724T100000Z")
        assert service.list_runs() == []

    def test_refuses_to_delete_a_live_run(
        self, service: DashboardService, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        # Deleting the directory the engine is writing to would corrupt the run and crash
        # the child.
        write_run(workspace, make_run_state(run_id="live-run", status="running"))
        stub_supervisor.next_run_id = "live-run"
        stub_supervisor.launch_run()

        with pytest.raises(DashboardError, match="cancel it before deleting"):
            service.delete_run("live-run")
        assert (workspace / ".orchestrator" / "runs" / "live-run").exists()

    def test_deleting_a_missing_run_raises(self, service: DashboardService) -> None:
        with pytest.raises(DashboardError, match="run not found"):
            service.delete_run("nope")


class TestRunLog:
    def test_returns_the_log_for_a_dashboard_launched_run(
        self, service: DashboardService, workspace: Path, stub_supervisor: StubSupervisor
    ) -> None:
        write_run(workspace, make_run_state(run_id="mine", status="running"))
        stub_supervisor.next_run_id = "mine"
        stub_supervisor.launch_run()
        assert "log for" in service.run_log("mine")["text"]

    def test_returns_empty_for_a_run_started_elsewhere(
        self, service: DashboardService, workspace: Path
    ) -> None:
        write_run(workspace, make_run_state(run_id="foreign"))
        result = service.run_log("foreign")
        assert result["text"] == ""
        assert result["launch_id"] is None
