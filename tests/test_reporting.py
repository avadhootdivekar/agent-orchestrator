"""Tests for `reporting.py` (E-1cecSx B2.1 top-N slowest tasks, B4 cache effectiveness)."""

from __future__ import annotations

from agent_orchestrator.models import RunState, RunUsageTotals, TaskRunState
from agent_orchestrator.reporting import (
    TaskDuration,
    cache_effectiveness,
    run_cache_effectiveness,
    top_n_slowest_tasks,
)

_BASE_KWARGS = {
    "run_id": "r1",
    "workflow_id": "wf",
    "repo_set": "rs",
    "started_at": "2026-01-01T00:00:00+00:00",
    "updated_at": "2026-01-01T00:00:00+00:00",
}


class TestTopNSlowestTasks:
    def test_empty_run_returns_empty_list(self) -> None:
        state = RunState(**_BASE_KWARGS)
        assert top_n_slowest_tasks(state, 5) == []

    def test_n_le_zero_returns_empty_list(self) -> None:
        state = RunState(
            **_BASE_KWARGS,
            tasks={
                "a": TaskRunState(
                    started_at="2026-01-01T00:00:00+00:00", ended_at="2026-01-01T00:00:10+00:00"
                )
            },
        )
        assert top_n_slowest_tasks(state, 0) == []
        assert top_n_slowest_tasks(state, -1) == []

    def test_excludes_tasks_missing_either_timestamp(self) -> None:
        state = RunState(
            **_BASE_KWARGS,
            tasks={
                "pending": TaskRunState(status="pending"),
                "running": TaskRunState(status="running", started_at="2026-01-01T00:00:00+00:00"),
                "done": TaskRunState(
                    status="succeeded",
                    started_at="2026-01-01T00:00:00+00:00",
                    ended_at="2026-01-01T00:00:05+00:00",
                ),
            },
        )
        result = top_n_slowest_tasks(state, 10)
        assert [d.task_id for d in result] == ["done"]

    def test_sorted_descending_by_duration(self) -> None:
        state = RunState(
            **_BASE_KWARGS,
            tasks={
                "short": TaskRunState(
                    started_at="2026-01-01T00:00:00+00:00", ended_at="2026-01-01T00:00:02+00:00"
                ),
                "long": TaskRunState(
                    started_at="2026-01-01T00:00:00+00:00", ended_at="2026-01-01T00:01:00+00:00"
                ),
                "medium": TaskRunState(
                    started_at="2026-01-01T00:00:00+00:00", ended_at="2026-01-01T00:00:20+00:00"
                ),
            },
        )
        result = top_n_slowest_tasks(state, 10)
        assert [d.task_id for d in result] == ["long", "medium", "short"]
        assert result[0] == TaskDuration(task_id="long", seconds=60.0)

    def test_n_caps_the_result(self) -> None:
        state = RunState(
            **_BASE_KWARGS,
            tasks={
                str(i): TaskRunState(
                    started_at="2026-01-01T00:00:00+00:00",
                    ended_at=f"2026-01-01T00:00:{i:02d}+00:00",
                )
                for i in range(1, 6)
            },
        )
        result = top_n_slowest_tasks(state, 2)
        assert [d.task_id for d in result] == ["5", "4"]

    def test_ties_break_by_task_id_ascending(self) -> None:
        state = RunState(
            **_BASE_KWARGS,
            tasks={
                "b": TaskRunState(
                    started_at="2026-01-01T00:00:00+00:00", ended_at="2026-01-01T00:00:10+00:00"
                ),
                "a": TaskRunState(
                    started_at="2026-01-01T00:00:00+00:00", ended_at="2026-01-01T00:00:10+00:00"
                ),
            },
        )
        result = top_n_slowest_tasks(state, 10)
        assert [d.task_id for d in result] == ["a", "b"]


class TestCacheEffectiveness:
    def test_zero_denominator_returns_none_hit_rate(self) -> None:
        eff = cache_effectiveness(TaskRunState())
        assert eff.hit_rate is None
        assert eff.cache_read_tokens == 0
        assert eff.cache_creation_tokens == 0
        assert eff.uncached_input_tokens == 0

    def test_hit_rate_computed_correctly(self) -> None:
        ts = TaskRunState(
            cumulative_cache_read_input_tokens=90,
            cumulative_cache_creation_input_tokens=5,
            cumulative_input_tokens=5,
        )
        eff = cache_effectiveness(ts)
        assert eff.hit_rate == 0.9

    def test_genuine_zero_hit_rate_is_distinct_from_none(self) -> None:
        ts = TaskRunState(cumulative_input_tokens=100)
        eff = cache_effectiveness(ts)
        assert eff.hit_rate == 0.0

    def test_run_cache_effectiveness_from_totals(self) -> None:
        totals = RunUsageTotals(
            input_tokens=10, cache_creation_input_tokens=20, cache_read_input_tokens=70
        )
        eff = run_cache_effectiveness(totals)
        assert eff.hit_rate == 0.7
