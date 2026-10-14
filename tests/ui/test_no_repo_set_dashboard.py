"""Dashboard/service layer for no-repo-set mode (ADR-0022, HLD §8 unit U4: S20, S21, S28).

A workflow without a ``repo_set`` must launch from ``DashboardService`` with no reposets file
configured, forward ``--workspace`` to the spawned ``ao run``/``ao resume`` (and so survive
boot-resume), and show up in the run list. Repo-set behaviour is pinned unchanged alongside.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agent_orchestrator.project_config import ProjectConfig
from agent_orchestrator.service.boot_resume import scan_resumable_runs
from agent_orchestrator.ui.processes import LaunchRecord, ProcessSupervisor
from agent_orchestrator.ui.service import DashboardError, DashboardService

from .conftest import StubSupervisor, make_run_state, write_run, write_workflow

NOOP = [sys.executable, "-c", "pass"]


@pytest.fixture(autouse=True)
def _no_ambient_ao_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AO_REPOSETS", raising=False)
    monkeypatch.delenv("AO_AGENTS", raising=False)


def _workflow(path: Path, repo_set: str | None) -> Path:
    write_workflow(path)
    data = json.loads(path.read_text())
    if repo_set is None:
        del data["repo_set"]
    else:
        data["repo_set"] = repo_set
    path.write_text(json.dumps(data))
    return path


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


class TestServiceLaunch:
    def test_no_repo_workflow_launches_without_reposets_and_forwards_workspace(
        self, workspace: Path
    ) -> None:
        stub = StubSupervisor(workspace)
        svc = DashboardService(str(workspace), supervisor=stub)  # type: ignore[arg-type]
        wf = _workflow(workspace / "wf.json", None)
        svc.start_run(workflow_path=str(wf))
        assert len(stub.launch_calls) == 1
        call = stub.launch_calls[0]
        assert call["workspace"] == str(svc.workspace_root)
        assert call["reposets"] is None

    def test_no_repo_workflow_validated_before_spawn_with_agents(self, workspace: Path) -> None:
        agents = workspace / "agents.json"
        agents.write_text(
            json.dumps({"version": "1.0", "agents": {"other": {"command": "x"}}}), encoding="utf-8"
        )
        stub = StubSupervisor(workspace)
        svc = DashboardService(
            str(workspace),
            supervisor=stub,
            project_config=ProjectConfig(agents=str(agents)),  # type: ignore[arg-type]
        )
        wf = _workflow(workspace / "wf.json", None)
        with pytest.raises(DashboardError):
            svc.start_run(workflow_path=str(wf))
        assert stub.launch_calls == []

    def test_repo_set_workflow_call_shape_unchanged(self, workspace: Path) -> None:
        stub = StubSupervisor(workspace)
        svc = DashboardService(str(workspace), supervisor=stub)  # type: ignore[arg-type]
        wf = _workflow(workspace / "wf.json", "demo-repos")
        svc.start_run(workflow_path=str(wf))
        assert "workspace" not in stub.launch_calls[0]

    def test_resume_forwards_workspace_for_no_repo_run(self, workspace: Path) -> None:
        stub = StubSupervisor(workspace)
        svc = DashboardService(str(workspace), supervisor=stub, project_config=None)  # type: ignore[arg-type]
        wf = _workflow(workspace / "wf.json", None)
        write_run(workspace, make_run_state(status="failed"))
        stub_run = "demo-20260724T100000Z"
        svc._workflow_for_run = lambda run_id: str(wf)  # type: ignore[method-assign]
        svc.resume_run(stub_run)
        assert stub.launch_calls[-1]["workspace"] == str(svc.workspace_root)

    def test_resume_forwards_workspace_when_workflow_path_unknown(self, workspace: Path) -> None:
        stub = StubSupervisor(workspace)
        svc = DashboardService(str(workspace), supervisor=stub, project_config=None)  # type: ignore[arg-type]
        write_run(workspace, make_run_state(status="failed"))
        assert svc._workflow_for_run("demo-20260724T100000Z") is None
        svc.resume_run("demo-20260724T100000Z")
        assert stub.launch_calls[-1]["workspace"] == str(svc.workspace_root)

    def test_resume_repo_set_run_still_omits_workspace(self, workspace: Path) -> None:
        stub = StubSupervisor(workspace)
        svc = DashboardService(str(workspace), supervisor=stub, project_config=None)  # type: ignore[arg-type]
        wf = _workflow(workspace / "wf.json", "demo-repos")
        write_run(workspace, make_run_state(status="failed"))
        svc._workflow_for_run = lambda run_id: str(wf)  # type: ignore[method-assign]
        svc.resume_run("demo-20260724T100000Z")
        assert "workspace" not in stub.launch_calls[-1]

    def test_start_run_with_unknown_workflow_does_not_forward_workspace(
        self, workspace: Path
    ) -> None:
        svc = DashboardService(
            str(workspace), supervisor=StubSupervisor(workspace), project_config=None
        )  # type: ignore[arg-type]
        assert svc._workspace_kwargs(None) == {}


class TestProcessArgv:
    def test_workspace_flag_only_when_given(self, workspace: Path) -> None:
        sup = ProcessSupervisor(str(workspace), ao_command=NOOP, run_id_discovery_timeout=0.2)
        plain = sup.launch_run(workflow_path="/w.json")
        assert "--workspace" not in plain.argv
        with_ws = sup.launch_run(workflow_path="/w.json", workspace=str(workspace))
        assert with_ws.argv[with_ws.argv.index("--workspace") + 1] == str(workspace)
        resumed = sup.launch_resume("r1", workflow_path="/w.json", workspace=str(workspace))
        assert resumed.argv[resumed.argv.index("--workspace") + 1] == str(workspace)


class TestBootResume:
    def _persist(self, workspace: Path, argv: list[str]) -> None:
        record = LaunchRecord(
            launch_id="launch-20261006T000000000000Z",
            kind="run",
            pid=_dead_pid(),
            argv=argv,
            started_at="2026-10-06T00:00:00+00:00",
            log_path=str(workspace / "l.log"),
            run_id="demo-20260724T100000Z",
            workflow_path="/w.json",
        )
        ProcessSupervisor(str(workspace))._save(record)

    def test_no_repo_run_is_a_candidate_with_workspace_and_no_reposets(
        self, workspace: Path
    ) -> None:
        write_run(workspace, make_run_state(status="running"))
        self._persist(
            workspace, [*NOOP, "run", "--workflow", "/w.json", "--workspace", str(workspace)]
        )
        (cand,) = scan_resumable_runs(str(workspace))
        assert cand.reposets is None
        assert cand.workspace == str(workspace)

    def test_repo_set_run_has_no_workspace(self, workspace: Path) -> None:
        write_run(workspace, make_run_state(status="running"))
        self._persist(workspace, [*NOOP, "run", "--workflow", "/w.json", "--reposets", "/r.json"])
        (cand,) = scan_resumable_runs(str(workspace))
        assert cand.workspace is None and cand.reposets == "/r.json"


class TestRunList:
    def test_run_without_repo_set_lists(self, workspace: Path) -> None:
        state = make_run_state(status="succeeded")
        state.repo_set = None
        write_run(workspace, state)
        svc = DashboardService(str(workspace), supervisor=StubSupervisor(workspace))  # type: ignore[arg-type]
        rows = svc.list_runs()
        assert [r["run_id"] for r in rows] == [state.run_id]
        assert rows[0].get("repo_set") in (None, "—")
