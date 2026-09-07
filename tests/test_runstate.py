"""Tests for RunStateStore: new run, save/load, skip logic, and resume."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

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

    def test_cumulative_usage_and_dispatch_cycle_survive_resume_for_a_requeued_task(
        self, tmp_path
    ) -> None:
        """E-Wk9Tz3 T-Ac6Vd9 (review Major-1). A task mid-conflict-ladder (cycle 1
        settled and reconciled real actuals into cumulative_*, then a T2 requeue put
        it into cycle 2 "running" before the process crashed) must not lose cycle 1's
        already-accumulated actuals on resume -- only `dispatch_cycle` was carried
        forward by this branch before this fix; `cumulative_*` silently reset to 0/0.0,
        even though `RunState.budget_counters` (the durable, enforcement-path ledger)
        was never affected. This is the regression test the review asked for."""
        store = LocalFsArtifactStore(str(tmp_path))
        rs = RunStateStore(str(tmp_path), store)
        wf = _workflow()

        state = rs.new_run(wf)
        state.tasks["a"].status = "running"
        state.tasks["a"].dispatch_cycle = 2
        state.tasks["a"].cumulative_input_tokens = 100
        state.tasks["a"].cumulative_output_tokens = 50
        state.tasks["a"].cumulative_cache_creation_input_tokens = 5
        state.tasks["a"].cumulative_cache_read_input_tokens = 3
        state.tasks["a"].cumulative_cost_usd = 1.25
        rs.save(state)

        resumed = rs.prepare_resume(state, wf)

        ts = resumed.tasks["a"]
        assert ts.status == "pending"
        assert ts.dispatch_cycle == 2  # unchanged, pre-existing guarantee
        assert ts.cumulative_input_tokens == 100
        assert ts.cumulative_output_tokens == 50
        assert ts.cumulative_cache_creation_input_tokens == 5
        assert ts.cumulative_cache_read_input_tokens == 3
        assert ts.cumulative_cost_usd == pytest.approx(1.25)

        # And still increasing after the NEXT cycle settles (mirrors what
        # _accumulate_actuals does in engine.py -- this test stays at the runstate
        # layer, so it drives the same `+=` shape directly rather than a live run).
        ts.cumulative_input_tokens += 40
        ts.cumulative_cost_usd += 0.5
        assert ts.cumulative_input_tokens == 140
        assert ts.cumulative_cost_usd == pytest.approx(1.75)

    def test_old_state_json_without_cumulative_fields_still_loads(self, tmp_path) -> None:
        """Backward compat: cumulative_* are pre-existing `TaskRunState` fields with
        defaults (0 / 0.0) -- not new ones -- so an older `state.json` written before
        they existed still loads (pydantic default-fills the missing keys) and
        `prepare_resume` carries those (zero) defaults forward without error."""
        store = LocalFsArtifactStore(str(tmp_path))
        rs = RunStateStore(str(tmp_path), store)
        wf = _workflow()

        state = rs.new_run(wf)
        state.tasks["a"].status = "running"
        rs.save(state)

        state_path = Path(tmp_path) / ".orchestrator" / "runs" / state.run_id / "state.json"
        raw = json.loads(state_path.read_text())
        for field in (
            "cumulative_input_tokens",
            "cumulative_output_tokens",
            "cumulative_cache_creation_input_tokens",
            "cumulative_cache_read_input_tokens",
            "cumulative_cost_usd",
        ):
            del raw["tasks"]["a"][field]
        state_path.write_text(json.dumps(raw))

        loaded = rs.load(state.run_id)
        resumed = rs.prepare_resume(loaded, wf)
        assert resumed.tasks["a"].status == "pending"
        assert resumed.tasks["a"].cumulative_input_tokens == 0
        assert resumed.tasks["a"].cumulative_cost_usd == 0.0
