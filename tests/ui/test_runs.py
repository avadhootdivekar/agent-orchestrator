"""Unit tests for run discovery, stats, and deletion (E-Ui7Kq2 FR-R3..FR-R5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.models import TaskRunState
from agent_orchestrator.ui.runs import RunNotFoundError, RunRepository

from .conftest import make_run_state, write_run, write_run_raw


@pytest.fixture()
def repo(workspace: Path) -> RunRepository:
    return RunRepository(str(workspace))


class TestDiscovery:
    def test_empty_workspace_has_no_runs(self, repo: RunRepository) -> None:
        assert repo.list_run_ids() == []
        assert repo.list_summaries() == []

    def test_finds_a_persisted_run(self, workspace: Path, repo: RunRepository) -> None:
        write_run(workspace, make_run_state())
        assert repo.list_run_ids() == ["demo-20260724T100000Z"]

    def test_lists_runs_newest_first(self, workspace: Path, repo: RunRepository) -> None:
        for run_id in ("run-a", "run-b", "run-c"):
            write_run(workspace, make_run_state(run_id=run_id))
            # Force distinct mtimes so the ordering assertion is about sorting, not
            # filesystem timestamp granularity.
            path = workspace / ".orchestrator" / "runs" / run_id
            import os
            import time

            os.utime(path, (time.time() + hash(run_id) % 100, time.time() + hash(run_id) % 100))

        ids = repo.list_run_ids()
        mtimes = [(workspace / ".orchestrator" / "runs" / rid).stat().st_mtime for rid in ids]
        assert mtimes == sorted(mtimes, reverse=True)

    def test_corrupt_state_is_skipped_not_fatal(self, workspace: Path, repo: RunRepository) -> None:
        write_run(workspace, make_run_state(run_id="good-run"))
        bad = workspace / ".orchestrator" / "runs" / "bad-run"
        bad.mkdir(parents=True)
        (bad / "state.json").write_text("{not json", encoding="utf-8")

        summaries = repo.list_summaries()
        assert [s.run_id for s in summaries] == ["good-run"], (
            "one unreadable run directory must not take down the whole listing"
        )

    def test_run_dir_without_state_is_skipped(self, workspace: Path, repo: RunRepository) -> None:
        (workspace / ".orchestrator" / "runs" / "stub").mkdir(parents=True)
        assert repo.list_summaries() == []

    def test_exists_reflects_presence(self, workspace: Path, repo: RunRepository) -> None:
        write_run(workspace, make_run_state(run_id="here"))
        assert repo.exists("here") is True
        assert repo.exists("not-here") is False


class TestSummary:
    def test_counts_tasks_by_status(self, workspace: Path, repo: RunRepository) -> None:
        write_run(
            workspace,
            make_run_state(
                tasks={
                    "a": TaskRunState(status="succeeded"),
                    "b": TaskRunState(status="succeeded"),
                    "c": TaskRunState(status="failed"),
                    "d": TaskRunState(status="pending"),
                }
            ),
        )
        summary = repo.summarize("demo-20260724T100000Z")
        assert summary.task_count == 4
        assert summary.task_counts == {"succeeded": 2, "failed": 1, "pending": 1}

    def test_sums_cost_and_tokens_across_tasks(self, workspace: Path, repo: RunRepository) -> None:
        write_run(workspace, make_run_state())
        summary = repo.summarize("demo-20260724T100000Z")
        assert summary.cost_usd == pytest.approx(0.75)
        assert summary.input_tokens == 1500
        assert summary.output_tokens == 375

    def test_wall_time_spans_start_to_last_update(
        self, workspace: Path, repo: RunRepository
    ) -> None:
        write_run(workspace, make_run_state())  # 10:00:00 -> 10:05:00
        assert repo.summarize("demo-20260724T100000Z").wall_seconds == pytest.approx(300)

    def test_actual_time_sums_only_settled_task_windows(
        self, workspace: Path, repo: RunRepository
    ) -> None:
        # build 10:00->10:02 (120s) + test 10:02->10:03 (60s) = 180s, which is LESS than
        # the 300s wall clock — that gap is exactly what "actual time" is meant to exclude.
        write_run(workspace, make_run_state())
        summary = repo.summarize("demo-20260724T100000Z")
        assert summary.active_seconds == pytest.approx(180)
        assert summary.active_seconds < summary.wall_seconds

    def test_unfinished_tasks_contribute_no_active_time(
        self, workspace: Path, repo: RunRepository
    ) -> None:
        write_run(
            workspace,
            make_run_state(
                status="running",
                tasks={
                    "running-task": TaskRunState(
                        status="running", started_at="2026-07-24T10:00:00+00:00"
                    )
                },
            ),
        )
        assert repo.summarize("demo-20260724T100000Z").active_seconds == 0.0

    @pytest.mark.parametrize(
        ("status", "terminal"),
        [("succeeded", True), ("failed", True), ("cancelled", True), ("running", False)],
    )
    def test_terminal_flag(
        self, workspace: Path, repo: RunRepository, status: str, terminal: bool
    ) -> None:
        write_run(workspace, make_run_state(status=status))
        assert repo.summarize("demo-20260724T100000Z").is_terminal is terminal

    def test_malformed_timestamps_degrade_to_zero_not_a_crash(
        self, workspace: Path, repo: RunRepository
    ) -> None:
        write_run_raw(workspace, make_run_state(started_at="garbage", updated_at="also-garbage"))
        assert repo.summarize("demo-20260724T100000Z").wall_seconds == 0.0


class TestDetail:
    def test_includes_a_row_per_task_with_durations(
        self, workspace: Path, repo: RunRepository
    ) -> None:
        write_run(workspace, make_run_state())
        detail = repo.detail("demo-20260724T100000Z")

        assert {t.id for t in detail.tasks} == {"build", "test"}
        build = next(t for t in detail.tasks if t.id == "build")
        assert build.duration_seconds == pytest.approx(120)
        assert build.attempts == 1

    def test_task_without_end_time_has_no_duration(
        self, workspace: Path, repo: RunRepository
    ) -> None:
        write_run(
            workspace,
            make_run_state(
                tasks={"t": TaskRunState(status="running", started_at="2026-07-24T10:00:00+00:00")}
            ),
        )
        assert repo.detail("demo-20260724T100000Z").tasks[0].duration_seconds is None

    def test_missing_run_raises(self, repo: RunRepository) -> None:
        with pytest.raises(RunNotFoundError):
            repo.detail("no-such-run")


class TestAggregate:
    def test_totals_across_runs(self, workspace: Path, repo: RunRepository) -> None:
        write_run(workspace, make_run_state(run_id="run-1", status="succeeded"))
        write_run(workspace, make_run_state(run_id="run-2", status="failed"))

        stats = repo.aggregate()
        assert stats.total_runs == 2
        assert stats.runs_by_status == {"succeeded": 1, "failed": 1}
        assert stats.total_tasks == 4
        assert stats.tasks_by_status == {"succeeded": 4}
        assert stats.total_cost_usd == pytest.approx(1.5)
        assert stats.total_active_seconds == pytest.approx(360)

    def test_empty_workspace_totals_are_zero(self, repo: RunRepository) -> None:
        stats = repo.aggregate()
        assert stats.total_runs == 0
        assert stats.total_cost_usd == 0.0
        assert stats.runs_by_status == {}


class TestDelete:
    def test_removes_the_run_directory(self, workspace: Path, repo: RunRepository) -> None:
        run_dir = write_run(workspace, make_run_state())
        assert run_dir.exists()

        repo.delete("demo-20260724T100000Z")
        assert not run_dir.exists()
        assert repo.list_run_ids() == []

    def test_deleting_a_missing_run_raises(self, repo: RunRepository) -> None:
        with pytest.raises(RunNotFoundError):
            repo.delete("no-such-run")

    @pytest.mark.parametrize("evil_id", ["../../etc", "..", "../runs"])
    def test_run_id_cannot_escape_the_runs_directory(
        self, workspace: Path, repo: RunRepository, evil_id: str
    ) -> None:
        # run_id arrives from an HTTP path segment; a traversal id must never resolve to a
        # deletable directory outside .orchestrator/runs/.
        sentinel = workspace / "important.txt"
        sentinel.write_text("do not delete", encoding="utf-8")

        with pytest.raises(RunNotFoundError):
            repo.delete(evil_id)
        assert sentinel.exists()


class TestStatusSnapshot:
    def test_reads_the_engine_written_snapshot(self, workspace: Path, repo: RunRepository) -> None:
        write_run(workspace, make_run_state())
        snap = repo.status_snapshot("demo-20260724T100000Z")
        assert snap is not None
        assert snap["run_id"] == "demo-20260724T100000Z"
        assert "usage_totals" in snap

    def test_corrupt_snapshot_returns_none_so_callers_fall_back(
        self, workspace: Path, repo: RunRepository
    ) -> None:
        write_run(workspace, make_run_state())
        path = workspace / ".orchestrator" / "runs" / "demo-20260724T100000Z" / "status.json"
        path.write_text("{ broken", encoding="utf-8")
        assert repo.status_snapshot("demo-20260724T100000Z") is None

    def test_absent_snapshot_returns_none(self, repo: RunRepository, workspace: Path) -> None:
        (workspace / ".orchestrator" / "runs" / "bare").mkdir(parents=True)
        assert repo.status_snapshot("bare") is None
