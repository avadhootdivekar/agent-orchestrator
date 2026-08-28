"""Edge-path tests for the dashboard layers (E-Ui7Kq2).

Covers the defensive branches that the happy-path suites do not reach: log tailing,
signal fallbacks, restart-recovery in the supervisor, and the service's degraded modes.
These paths exist precisely because the dashboard runs against a live, messy workspace, so
they need coverage as much as the main flows do.
"""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest

from agent_orchestrator.project_config import ProjectConfig
from agent_orchestrator.ui.files import FileBrowser, Root
from agent_orchestrator.ui.processes import (
    LaunchRecord,
    ProcessSupervisor,
    _pid_alive,
    _utc_now_iso,
)
from agent_orchestrator.ui.runs import RunRepository, asdict_safe
from agent_orchestrator.ui.service import DashboardService

from .conftest import StubSupervisor, make_run_state, write_run

NOOP_COMMAND = [sys.executable, "-c", "pass"]
FAST_DISCOVERY = 1.0


class TestLogTailing:
    def test_returns_empty_for_a_launch_with_no_log(self, workspace: Path) -> None:
        supervisor = ProcessSupervisor(str(workspace), ao_command=NOOP_COMMAND)
        assert supervisor.read_log("no-such-launch") == ""

    def test_returns_the_tail_of_a_large_log(self, workspace: Path) -> None:
        # A long-running agent produces a huge log; the UI must get the END of it (the
        # recent, relevant part) and never load the whole thing into memory.
        supervisor = ProcessSupervisor(str(workspace), ao_command=NOOP_COMMAND)
        supervisor.logs_dir.mkdir(parents=True, exist_ok=True)
        (supervisor.logs_dir / "big.log").write_text("A" * 500 + "TAIL", encoding="utf-8")

        tail = supervisor.read_log("big", max_bytes=10)
        assert tail.endswith("TAIL")
        assert len(tail) == 10

    def test_returns_the_whole_log_when_it_fits(self, workspace: Path) -> None:
        supervisor = ProcessSupervisor(str(workspace), ao_command=NOOP_COMMAND)
        supervisor.logs_dir.mkdir(parents=True, exist_ok=True)
        (supervisor.logs_dir / "small.log").write_text("short output", encoding="utf-8")
        assert supervisor.read_log("small") == "short output"

    def test_undecodable_bytes_are_replaced_not_fatal(self, workspace: Path) -> None:
        supervisor = ProcessSupervisor(str(workspace), ao_command=NOOP_COMMAND)
        supervisor.logs_dir.mkdir(parents=True, exist_ok=True)
        (supervisor.logs_dir / "bad.log").write_bytes(b"ok \xff\xfe more")
        assert "ok" in supervisor.read_log("bad")


class TestPidProbe:
    def test_nonpositive_pids_are_never_alive(self) -> None:
        assert _pid_alive(0) is False
        assert _pid_alive(-1) is False

    def test_current_process_is_alive(self) -> None:
        assert _pid_alive(os.getpid()) is True

    def test_signalling_a_dead_group_is_a_silent_noop(self) -> None:
        # Cancel is idempotent by intent: signalling an already-gone process must not raise.
        ProcessSupervisor._signal_group(999_999_999, signal.SIGTERM)


class TestRestartRecovery:
    def test_a_fresh_supervisor_sees_prior_launches(self, workspace: Path) -> None:
        first = ProcessSupervisor(
            str(workspace), ao_command=NOOP_COMMAND, run_id_discovery_timeout=FAST_DISCOVERY
        )
        first.launch_resume("run-x")

        # Simulates the dashboard process restarting: no in-memory state carries over.
        second = ProcessSupervisor(str(workspace), ao_command=NOOP_COMMAND)
        assert second.record_for_run("run-x") is not None

    def test_reconcile_marks_a_stale_record_finished(self, workspace: Path) -> None:
        supervisor = ProcessSupervisor(str(workspace), ao_command=NOOP_COMMAND)
        supervisor.launches_dir.mkdir(parents=True, exist_ok=True)

        # A record whose PID cannot exist — the dashboard died and the run is long gone.
        stale = LaunchRecord(
            launch_id="stale",
            kind="run",
            pid=999_999_999,
            argv=["ao", "run"],
            started_at=_utc_now_iso(),
            log_path=str(workspace / "stale.log"),
            run_id="ghost-run",
        )
        supervisor._save(stale)

        records = supervisor.reconcile()
        assert records[0].finished_at is not None
        assert supervisor.is_running("ghost-run") is False

    def test_reconcile_attaches_a_late_run_id(self, workspace: Path) -> None:
        # A run whose directory appeared after the launch-time discovery window closed
        # must still be attributed on a later read.
        supervisor = ProcessSupervisor(str(workspace), ao_command=NOOP_COMMAND)
        supervisor.launches_dir.mkdir(parents=True, exist_ok=True)
        supervisor._save(
            LaunchRecord(
                launch_id="pending",
                kind="run",
                pid=os.getpid(),  # alive, so reconcile still looks for its run
                argv=["ao", "run"],
                started_at=_utc_now_iso(),
                log_path=str(workspace / "pending.log"),
                run_id=None,
                known_run_ids=[],
            )
        )
        write_run(workspace, make_run_state(run_id="late-run"))

        assert supervisor.reconcile()[0].run_id == "late-run"


class TestServiceDegradedModes:
    def test_launches_list_is_empty_before_anything_runs(self, service: DashboardService) -> None:
        assert service.list_launches() == []

    def test_lists_launches_after_a_run(
        self, service: DashboardService, stub_supervisor: StubSupervisor
    ) -> None:
        stub_supervisor.launch_run()
        assert len(service.list_launches()) == 1

    def test_a_broken_project_config_is_tolerated(self, workspace: Path) -> None:
        # A malformed .ao/config.yaml must not make the dashboard unstartable — the file
        # browser and run views still work without it.
        (workspace / ".git").mkdir()
        (workspace / ".ao").mkdir()
        (workspace / ".ao" / "config.yaml").write_text(": : not valid yaml : :", encoding="utf-8")

        svc = DashboardService(str(workspace))
        assert svc.workspace_info().workflow is None
        assert svc.list_runs() == []

    def test_discovers_a_config_from_the_workspace_root(self, workspace: Path) -> None:
        (workspace / ".git").mkdir()
        (workspace / ".ao").mkdir()
        (workspace / ".ao" / "config.yaml").write_text("model: claude-opus-4-8\n", encoding="utf-8")
        info = DashboardService(str(workspace)).workspace_info()
        assert info.config_path is not None
        assert info.config_path.endswith("config.yaml")

    def test_custom_roots_are_reported(self, workspace: Path, tmp_path: Path) -> None:
        extra = tmp_path / "extra"
        extra.mkdir()
        svc = DashboardService(
            str(workspace),
            browser=FileBrowser(
                roots=[
                    Root(name="workspace", path=str(workspace)),
                    Root(name="extra", path=str(extra), role="repo"),
                ]
            ),
        )
        assert [r["name"] for r in svc.workspace_info().roots] == ["workspace", "extra"]

    def test_search_roots_are_configurable(self, workspace: Path, tmp_path: Path) -> None:
        from .conftest import write_workflow

        elsewhere = tmp_path / "elsewhere"
        write_workflow(elsewhere / "wf.json", workflow_id="elsewhere-wf")

        svc = DashboardService(
            str(workspace),
            project_config=ProjectConfig(),
            search_roots=[str(elsewhere)],
        )
        assert [w.id for w in svc.list_workflows()] == ["elsewhere-wf"]

    def test_a_missing_search_root_is_skipped(self, workspace: Path, tmp_path: Path) -> None:
        svc = DashboardService(
            str(workspace),
            project_config=ProjectConfig(),
            search_roots=[str(tmp_path / "does-not-exist")],
        )
        assert svc.list_workflows() == []


class TestRunRepositoryEdges:
    def test_run_dir_for_a_plain_id_resolves_under_the_runs_dir(self, workspace: Path) -> None:
        repo = RunRepository(str(workspace))
        assert repo.run_dir("some-run").parent == repo.runs_dir

    def test_load_state_of_a_missing_run_raises(self, workspace: Path) -> None:
        from agent_orchestrator.ui.runs import RunNotFoundError

        with pytest.raises(RunNotFoundError):
            RunRepository(str(workspace)).load_state("nope")

    def test_asdict_safe_handles_pydantic_models(self) -> None:
        from agent_orchestrator.models import TrippedBreaker

        result = asdict_safe(
            TrippedBreaker(id="b", condition="task_failures", action="fail", at="2026-01-01")
        )
        assert result["id"] == "b"

    def test_asdict_safe_handles_dataclasses(self) -> None:
        assert asdict_safe(Root(name="r", path="/tmp"))["name"] == "r"

    def test_asdict_safe_falls_back_to_vars(self) -> None:
        class Plain:
            def __init__(self) -> None:
                self.value = 1

        assert asdict_safe(Plain()) == {"value": 1}
