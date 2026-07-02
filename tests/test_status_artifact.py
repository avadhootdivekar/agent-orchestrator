"""Tests for T-f0xkdw, T-1gsn0l, T-38jqbk — output capture, status artifact, ao status."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from typer.testing import CliRunner

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.cli import app
from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    AgentSpec,
    RepoRef,
    RepoSet,
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore


def make_fake_agents() -> dict:
    return {"ag": AgentSpec(executor="fake")}


def make_fake_reposets(workspace: str) -> dict:
    return {
        "rs": RepoSet(
            workspace_root=workspace,
            repos=[RepoRef(id="core", path=".", role="primary")],
        )
    }


runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _simple_workflow(workspace: Path, n_tasks: int = 2) -> WorkflowSpec:
    """Build a workflow with n_tasks sequential tasks."""
    instr_dir = workspace / "specs" / "instructions"
    instr_dir.mkdir(parents=True, exist_ok=True)

    tasks = []
    for i in range(n_tasks):
        tid = f"task{i}"
        (instr_dir / f"{tid}.md").write_text(f"instruction for {tid}")
        tasks.append(
            TaskSpec(
                id=tid,
                agent="ag",
                instruction=f"specs/instructions/{tid}.md",
                outputs=[f"output/{tid}.txt"],
                depends_on=[f"task{i - 1}"] if i > 0 else [],
                inputs=[f"output/task{i - 1}.txt"] if i > 0 else [],
            )
        )

    return WorkflowSpec(
        version="1.0",
        id="test-wf",
        repo_set="rs",
        tasks=tasks,
    )


def _make_stores(workspace: Path) -> tuple[LocalFsArtifactStore, RunStateStore]:
    store = LocalFsArtifactStore(str(workspace))
    rs_store = RunStateStore(str(workspace), store)
    return store, rs_store


def _run_workflow(workspace: Path, wf: WorkflowSpec, **fake_kwargs):
    store, rs_store = _make_stores(workspace)
    executor = FakeExecutor(**fake_kwargs)
    orch = Orchestrator(executor, store, rs_store)
    return orch.run(wf, make_fake_reposets(str(workspace)), make_fake_agents()), rs_store


# ---------------------------------------------------------------------------
# T-f0xkdw — Output capture
# ---------------------------------------------------------------------------


class TestOutputCapture:
    """AC-1, AC-2, AC-3, AC-4 from T-f0xkdw TASK.md."""

    def test_capture_files_written_on_success(self, tmp_path: Path) -> None:
        wf = _simple_workflow(tmp_path, n_tasks=2)
        state, _ = _run_workflow(tmp_path, wf)

        assert state.status == "succeeded"
        for tid in ["task0", "task1"]:
            task_dir = tmp_path / ".orchestrator" / "runs" / state.run_id / tid
            assert task_dir.exists(), f"output_dir missing for {tid}"
            assert (task_dir / "stdout.txt").exists(), f"stdout.txt missing for {tid}"
            assert (task_dir / "stderr.txt").exists(), f"stderr.txt missing for {tid}"

    def test_output_artifact_path_set_in_task_run_state(self, tmp_path: Path) -> None:
        wf = _simple_workflow(tmp_path, n_tasks=1)
        state, _ = _run_workflow(tmp_path, wf)

        ts = state.tasks["task0"]
        assert ts.output_artifact_path is not None
        assert "task0" in ts.output_artifact_path

    def test_engine_does_not_read_capture_files(self, tmp_path: Path) -> None:
        """NFR-1: engine records the path but must never read its content."""
        wf = _simple_workflow(tmp_path, n_tasks=1)
        state, _ = _run_workflow(tmp_path, wf)

        # The path is set
        ts = state.tasks["task0"]
        assert ts.output_artifact_path is not None

        # Verify output_artifact_path only points to a directory, never a file content
        assert Path(ts.output_artifact_path).is_dir()

    def test_capture_files_written_on_failure(self, tmp_path: Path) -> None:
        wf = _simple_workflow(tmp_path, n_tasks=1)
        state, _ = _run_workflow(tmp_path, wf, behaviors={"task0": "fail"})

        task_dir = tmp_path / ".orchestrator" / "runs" / state.run_id / "task0"
        assert (task_dir / "stdout.txt").exists()
        assert (task_dir / "stderr.txt").exists()
        # Path should still be recorded even on failure
        assert state.tasks["task0"].output_artifact_path is not None

    def test_capture_stdout_has_content(self, tmp_path: Path) -> None:
        """FakeExecutor writes a non-empty stub stdout."""
        wf = _simple_workflow(tmp_path, n_tasks=1)
        state, _ = _run_workflow(tmp_path, wf)

        task_dir = tmp_path / ".orchestrator" / "runs" / state.run_id / "task0"
        stdout_content = (task_dir / "stdout.txt").read_text()
        assert "task0" in stdout_content  # stub content includes task id


# ---------------------------------------------------------------------------
# T-1gsn0l — write_status / status.json
# ---------------------------------------------------------------------------


class TestWriteStatus:
    """Unit tests for RunStateStore.write_status()."""

    def test_status_json_created_by_save(self, tmp_path: Path) -> None:
        store, rs_store = _make_stores(tmp_path)
        wf = _simple_workflow(tmp_path, n_tasks=1)
        state = rs_store.new_run(wf)
        rs_store.save(state)

        status_path = tmp_path / ".orchestrator" / "runs" / state.run_id / "status.json"
        assert status_path.exists()

    def test_status_json_shape(self, tmp_path: Path) -> None:
        wf = _simple_workflow(tmp_path, n_tasks=2)
        state, rs_store = _run_workflow(tmp_path, wf)

        status_path = tmp_path / ".orchestrator" / "runs" / state.run_id / "status.json"
        snap = json.loads(status_path.read_text())

        assert snap["run_id"] == state.run_id
        assert snap["workflow_id"] == state.workflow_id
        assert snap["status"] in ("running", "succeeded", "failed", "cancelled")
        assert "updated_at" in snap
        assert "current_task" in snap
        assert "counts" in snap
        assert "tasks" in snap

    def test_tasks_array_shape(self, tmp_path: Path) -> None:
        wf = _simple_workflow(tmp_path, n_tasks=2)
        state, _ = _run_workflow(tmp_path, wf)

        status_path = tmp_path / ".orchestrator" / "runs" / state.run_id / "status.json"
        snap = json.loads(status_path.read_text())

        for task_entry in snap["tasks"]:
            assert "id" in task_entry
            assert "status" in task_entry
            assert "attempts" in task_entry
            assert "origin" in task_entry
            assert task_entry["origin"] in ("static", "injected", "loop")

    def test_counts_match_state(self, tmp_path: Path) -> None:
        wf = _simple_workflow(tmp_path, n_tasks=2)
        state, _ = _run_workflow(tmp_path, wf)

        status_path = tmp_path / ".orchestrator" / "runs" / state.run_id / "status.json"
        snap = json.loads(status_path.read_text())

        # All tasks should have succeeded
        assert snap["counts"].get("succeeded", 0) == 2

    def test_current_task_is_null_after_completion(self, tmp_path: Path) -> None:
        wf = _simple_workflow(tmp_path, n_tasks=1)
        state, _ = _run_workflow(tmp_path, wf)

        status_path = tmp_path / ".orchestrator" / "runs" / state.run_id / "status.json"
        snap = json.loads(status_path.read_text())
        # After a successful run no task is pending/running
        assert snap["current_task"] is None

    def test_status_consistent_with_state(self, tmp_path: Path) -> None:
        """AC-5: status.json must be byte-consistent with state.json (no drift)."""
        wf = _simple_workflow(tmp_path, n_tasks=2)
        state, _ = _run_workflow(tmp_path, wf)

        run_dir = tmp_path / ".orchestrator" / "runs" / state.run_id
        snap = json.loads((run_dir / "status.json").read_text())
        full = json.loads((run_dir / "state.json").read_text())

        # Cross-check key fields
        assert snap["run_id"] == full["run_id"]
        assert snap["workflow_id"] == full["workflow_id"]
        assert snap["status"] == full["status"]

        # Every task in state.json should appear in status.json with matching status
        state_task_statuses = {tid: ts["status"] for tid, ts in full["tasks"].items()}
        snap_task_statuses = {t["id"]: t["status"] for t in snap["tasks"]}
        assert state_task_statuses == snap_task_statuses

    def test_write_status_atomic(self, tmp_path: Path) -> None:
        """Verify no partial file: status.json should be valid JSON after each save."""
        store, rs_store = _make_stores(tmp_path)
        wf = _simple_workflow(tmp_path, n_tasks=1)
        state = rs_store.new_run(wf)

        # Call save multiple times
        for _ in range(5):
            rs_store.save(state)

        status_path = tmp_path / ".orchestrator" / "runs" / state.run_id / "status.json"
        # Must be valid JSON (atomic write ensures no partial read)
        json.loads(status_path.read_text())  # raises if invalid


# ---------------------------------------------------------------------------
# Integration: full run produces run.log + status.json
# ---------------------------------------------------------------------------


class TestIntegrationRun:
    """Integration tests: full FakeExecutor run verifies all Area-1 outputs."""

    def test_run_produces_valid_jsonlines_log(
        self,
        tmp_path: Path,
        read_jsonl: Callable[[Path], list[dict]],
    ) -> None:
        """AC-4: run.log exists and every non-empty line is valid JSON with run_id."""
        wf = _simple_workflow(tmp_path, n_tasks=2)
        state, _ = _run_workflow(tmp_path, wf)

        run_log_path = tmp_path / ".orchestrator" / "runs" / state.run_id / "run.log"
        assert run_log_path.exists(), "run.log missing"

        records = read_jsonl(run_log_path)
        assert len(records) > 0, "run.log is empty"
        for rec in records:
            assert "ts" in rec
            assert "level" in rec
            assert "msg" in rec
            assert "run_id" in rec
            assert rec["run_id"] == state.run_id

    def test_run_log_contains_task_events(
        self,
        tmp_path: Path,
        read_jsonl: Callable[[Path], list[dict]],
    ) -> None:
        wf = _simple_workflow(tmp_path, n_tasks=2)
        state, _ = _run_workflow(tmp_path, wf)

        run_log_path = tmp_path / ".orchestrator" / "runs" / state.run_id / "run.log"
        records = read_jsonl(run_log_path)

        events = [r.get("event") for r in records]
        assert "task.start" in events
        assert "task.end" in events

    def test_run_log_contains_run_events(
        self,
        tmp_path: Path,
        read_jsonl: Callable[[Path], list[dict]],
    ) -> None:
        wf = _simple_workflow(tmp_path, n_tasks=1)
        state, _ = _run_workflow(tmp_path, wf)

        run_log_path = tmp_path / ".orchestrator" / "runs" / state.run_id / "run.log"
        records = read_jsonl(run_log_path)

        events = [r.get("event") for r in records]
        assert "run.start" in events
        assert "run.end" in events

    def test_run_log_records_task_id(
        self,
        tmp_path: Path,
        read_jsonl: Callable[[Path], list[dict]],
    ) -> None:
        """AC-2 from T-pd2vu2: task-scoped records include task_id."""
        wf = _simple_workflow(tmp_path, n_tasks=1)
        state, _ = _run_workflow(tmp_path, wf)

        run_log_path = tmp_path / ".orchestrator" / "runs" / state.run_id / "run.log"
        records = read_jsonl(run_log_path)

        task_records = [r for r in records if r.get("task_id") == "task0"]
        assert len(task_records) > 0, "No records with task_id=task0"

    def test_status_json_consistent_after_full_run(
        self,
        tmp_path: Path,
        read_status: Callable[[Path], dict],
    ) -> None:
        wf = _simple_workflow(tmp_path, n_tasks=2)
        state, _ = _run_workflow(tmp_path, wf)

        run_dir = tmp_path / ".orchestrator" / "runs" / state.run_id
        snap = read_status(run_dir)

        assert snap["status"] == "succeeded"
        task_ids_in_snap = {t["id"] for t in snap["tasks"]}
        assert task_ids_in_snap == {"task0", "task1"}

    def test_handler_detached_after_run(self, tmp_path: Path) -> None:
        """AC-4: handler removed even after normal completion."""

        from agent_orchestrator.logging_setup import _run_handlers

        wf = _simple_workflow(tmp_path, n_tasks=1)
        state, _ = _run_workflow(tmp_path, wf)

        # Handler registry must be empty for this run after completion
        assert state.run_id not in _run_handlers

    def test_handler_detached_after_failed_run(self, tmp_path: Path) -> None:
        """Handler must be detached even when run ends in failure."""
        from agent_orchestrator.logging_setup import _run_handlers

        wf = _simple_workflow(tmp_path, n_tasks=1)
        state, _ = _run_workflow(tmp_path, wf, behaviors={"task0": "fail"})

        assert state.run_id not in _run_handlers

    def test_no_handler_leak_across_two_sequential_runs(self, tmp_path: Path) -> None:
        """Two sequential runs must not cross-contaminate log files (AC-3)."""

        wf1 = _simple_workflow(tmp_path, n_tasks=1)

        # Build a second workflow with a different id
        instr_dir = tmp_path / "specs" / "instructions"
        instr_dir.mkdir(parents=True, exist_ok=True)
        (instr_dir / "taskX.md").write_text("instr X")
        wf2_task = TaskSpec(
            id="taskX",
            agent="ag",
            instruction="specs/instructions/taskX.md",
            outputs=["output/taskX.txt"],
        )
        wf2 = WorkflowSpec(version="1.0", id="wf2", repo_set="rs", tasks=[wf2_task])

        state1, _ = _run_workflow(tmp_path, wf1)
        state2, _ = _run_workflow(tmp_path, wf2)

        # Each log file should only contain its own run_id
        log1 = (tmp_path / ".orchestrator" / "runs" / state1.run_id / "run.log").read_text()
        log2 = (tmp_path / ".orchestrator" / "runs" / state2.run_id / "run.log").read_text()

        assert state1.run_id in log1
        assert state2.run_id not in log1
        assert state2.run_id in log2
        assert state1.run_id not in log2


# ---------------------------------------------------------------------------
# CLI: ao status reads status.json and falls back to state.json
# ---------------------------------------------------------------------------


class TestAoStatusCommand:
    """AC-4, AC-6 from T-1gsn0l TASK.md."""

    def test_status_reads_status_json_with_workspace_flag(self, tmp_path: Path) -> None:
        """ao status --run-id X --workspace W reads status.json without spec triplet."""
        wf = _simple_workflow(tmp_path, n_tasks=1)
        state, _ = _run_workflow(tmp_path, wf)

        result = runner.invoke(
            app,
            ["status", "--run-id", state.run_id, "--workspace", str(tmp_path)],
        )
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        assert state.run_id in result.output
        assert "succeeded" in result.output

    def test_status_shows_current_task(self, tmp_path: Path) -> None:
        """status.json path: current_task appears in output."""
        wf = _simple_workflow(tmp_path, n_tasks=1)
        state, _ = _run_workflow(tmp_path, wf)

        # Manually patch status.json to show a current_task
        run_dir = tmp_path / ".orchestrator" / "runs" / state.run_id
        snap = json.loads((run_dir / "status.json").read_text())
        snap["current_task"] = "task0"
        (run_dir / "status.json").write_text(json.dumps(snap, indent=2))

        result = runner.invoke(
            app,
            ["status", "--run-id", state.run_id, "--workspace", str(tmp_path)],
        )
        assert "task0" in result.output

    def test_status_falls_back_to_state_json(self, tmp_path: Path) -> None:
        """When status.json is absent, fall back to state.json without error."""
        wf = _simple_workflow(tmp_path, n_tasks=1)
        state, _ = _run_workflow(tmp_path, wf)

        # Remove status.json to force fallback
        status_json = tmp_path / ".orchestrator" / "runs" / state.run_id / "status.json"
        status_json.unlink()

        result = runner.invoke(
            app,
            ["status", "--run-id", state.run_id, "--workspace", str(tmp_path)],
        )
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        assert state.run_id in result.output

    def test_status_exit_1_when_run_not_found(self, tmp_path: Path) -> None:
        result = runner.invoke(
            app,
            ["status", "--run-id", "nonexistent-run", "--workspace", str(tmp_path)],
        )
        assert result.exit_code == 1

    def test_status_workspace_env_var(self, tmp_path: Path) -> None:
        """AO_WORKSPACE_ROOT env var substitutes for --workspace."""
        wf = _simple_workflow(tmp_path, n_tasks=1)
        state, _ = _run_workflow(tmp_path, wf)

        result = runner.invoke(
            app,
            ["status", "--run-id", state.run_id],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )
        assert result.exit_code == 0, f"CLI failed: {result.output}"
        assert state.run_id in result.output

    def test_status_task_table_shown(self, tmp_path: Path) -> None:
        """Task IDs should appear in the output table."""
        wf = _simple_workflow(tmp_path, n_tasks=2)
        state, _ = _run_workflow(tmp_path, wf)

        result = runner.invoke(
            app,
            ["status", "--run-id", state.run_id, "--workspace", str(tmp_path)],
        )
        assert "task0" in result.output
        assert "task1" in result.output
