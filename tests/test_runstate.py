"""Tests for RunStateStore: new run, save/load, skip logic, and resume."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.models import (
    TaskSpec,
    WorkflowSpec,
)
from agent_orchestrator.runstate import RunStateStore


def _fixed_clock(dt: datetime):
    def _clock():
        return dt

    return _clock


def _workflow(tasks=None) -> WorkflowSpec:
    if tasks is None:
        tasks = [
            TaskSpec(id="a", agent="ag", instruction="instr.md", outputs=["output/a.txt"]),
            TaskSpec(id="b", agent="ag", instruction="instr.md", outputs=["output/b.txt"]),
        ]
    return WorkflowSpec(version="1.0", id="test-wf", repo_set="rs", tasks=tasks)


class TestNewRun:
    def test_all_tasks_start_as_pending(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        clock = _fixed_clock(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC))
        rs = RunStateStore(str(tmp_path), store, clock=clock)
        wf = _workflow()

        state = rs.new_run(wf)
        assert set(state.tasks.keys()) == {"a", "b"}
        for ts in state.tasks.values():
            assert ts.status == "pending"
            assert ts.attempts == 0

    def test_run_id_uses_workflow_id_and_timestamp(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        clock = _fixed_clock(datetime(2026, 1, 15, 12, 30, 0, tzinfo=UTC))
        rs = RunStateStore(str(tmp_path), store, clock=clock)

        state = rs.new_run(_workflow())
        assert state.run_id.startswith("test-wf-20260115T123000Z")


class TestSaveLoad:
    def test_round_trip(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        rs = RunStateStore(str(tmp_path), store)
        wf = _workflow()

        original = rs.new_run(wf)
        original.tasks["a"].status = "succeeded"
        original.tasks["a"].attempts = 2
        rs.save(original)

        loaded = rs.load(original.run_id)
        assert loaded.run_id == original.run_id
        assert loaded.tasks["a"].status == "succeeded"
        assert loaded.tasks["a"].attempts == 2
        assert loaded.tasks["b"].status == "pending"

    def test_save_is_atomic(self, tmp_path) -> None:
        """Save should leave no .tmp file behind."""
        store = LocalFsArtifactStore(str(tmp_path))
        rs = RunStateStore(str(tmp_path), store)
        state = rs.new_run(_workflow())
        rs.save(state)

        run_dir = tmp_path / ".orchestrator" / "runs" / state.run_id
        assert (run_dir / "state.json").exists()
        assert not (run_dir / "state.tmp").exists()

    def test_load_missing_raises(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        rs = RunStateStore(str(tmp_path), store)
        with pytest.raises(FileNotFoundError):
            rs.load("nonexistent-run-id")


class TestShouldSkip:
    def test_skip_when_succeeded_and_outputs_present(self, tmp_path) -> None:
        out = tmp_path / "output" / "a.txt"
        out.parent.mkdir(parents=True)
        out.write_text("done")

        store = LocalFsArtifactStore(str(tmp_path))
        rs = RunStateStore(str(tmp_path), store)
        wf = _workflow()

        state = rs.new_run(wf)
        state.tasks["a"].status = "succeeded"
        rs.save(state)

        task_a = wf.task("a")
        assert rs.should_skip(task_a, state) is True

    def test_no_skip_when_succeeded_but_outputs_missing(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        rs = RunStateStore(str(tmp_path), store)
        wf = _workflow()

        state = rs.new_run(wf)
        state.tasks["a"].status = "succeeded"
        rs.save(state)

        task_a = wf.task("a")
        assert rs.should_skip(task_a, state) is False

    def test_skip_if_outputs_exist_on_fresh_run(self, tmp_path) -> None:
        """skip_if_outputs_exist=True + all outputs present => skip even if never ran."""
        out = tmp_path / "output" / "a.txt"
        out.parent.mkdir(parents=True)
        out.write_text("pre-existing")

        store = LocalFsArtifactStore(str(tmp_path))
        rs = RunStateStore(str(tmp_path), store)
        wf = _workflow()
        state = rs.new_run(wf)  # tasks are all "pending"

        task_a = wf.task("a")
        assert rs.should_skip(task_a, state) is True

    def test_no_skip_when_pending_and_no_outputs(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        rs = RunStateStore(str(tmp_path), store)
        wf = _workflow()
        state = rs.new_run(wf)

        task_a = wf.task("a")
        assert rs.should_skip(task_a, state) is False


class TestPrepareResume:
    def test_succeeded_tasks_with_outputs_kept(self, tmp_path) -> None:
        out_a = tmp_path / "output" / "a.txt"
        out_a.parent.mkdir(parents=True)
        out_a.write_text("done-a")

        store = LocalFsArtifactStore(str(tmp_path))
        rs = RunStateStore(str(tmp_path), store)
        wf = _workflow()

        state = rs.new_run(wf)
        state.tasks["a"].status = "succeeded"
        state.tasks["b"].status = "failed"
        rs.save(state)

        resumed = rs.prepare_resume(state, wf)
        assert resumed.tasks["a"].status == "succeeded"
        assert resumed.tasks["b"].status == "pending"
        assert resumed.status == "running"

    def test_failed_tasks_reset_to_pending(self, tmp_path) -> None:
        store = LocalFsArtifactStore(str(tmp_path))
        rs = RunStateStore(str(tmp_path), store)
        wf = _workflow()

        state = rs.new_run(wf)
        state.tasks["a"].status = "running"  # interrupted mid-run
        state.tasks["b"].status = "timed_out"
        rs.save(state)

        resumed = rs.prepare_resume(state, wf)
        assert resumed.tasks["a"].status == "pending"
        assert resumed.tasks["b"].status == "pending"
        assert resumed.status == "running"
