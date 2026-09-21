"""Tests for `reporting.py` (E-1cecSx B2.1 top-N slowest tasks, B2.2 within-task activity
breakdown, B4 cache effectiveness)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_orchestrator.models import RunState, RunUsageTotals, TaskRunState
from agent_orchestrator.reporting import (
    ActivityBreakdown,
    ActivityCategorySeconds,
    TaskDuration,
    _categorize_tool,
    cache_effectiveness,
    run_cache_effectiveness,
    task_activity_breakdown,
    top_n_slowest_tasks,
)

_FIXTURE_TRANSCRIPT = Path(__file__).parent / "fixtures" / "transcript_activity_breakdown.jsonl"

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


def _write_transcript(path: Path, events: list[dict]) -> str:
    """Write *events* as a JSONL transcript at *path* and return its str path."""
    with open(path, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")
    return str(path)


def _assistant_event(
    timestamp: str, tool_uses: list[dict] | None = None, text: str | None = None
) -> dict:
    """Build one synthetic `assistant` event -- either carrying `tool_use` block(s) (pass
    `tool_uses`, each `{"name": ..., "input": {...}}`) or a plain text-only turn (`text`)."""
    if tool_uses:
        content = [
            {
                "type": "tool_use",
                "id": f"toolu_{i}",
                "name": tu["name"],
                "input": tu.get("input", {}),
            }
            for i, tu in enumerate(tool_uses)
        ]
    else:
        content = [{"type": "text", "text": text or "..."}]
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": content},
        "timestamp": timestamp,
    }


def _user_event(timestamp: str) -> dict:
    """Build one synthetic `user` tool_result event (no `tool_use` block, by construction)."""
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_0", "content": "ok"}],
        },
        "timestamp": timestamp,
    }


class TestCategorizeTool:
    """White-box coverage of the build/test keyword heuristic (design doc §2.2's only
    heuristic-based MVP item, flagged for hardening at early-gate review)."""

    @pytest.mark.parametrize(
        "command",
        [
            "pytest -q",
            "npm run build",
            "make all",
            "go build ./...",
            "go test ./...",
            "cargo build --release",
            "cargo test",
            "mvn test",
            "ruff check .",
            "mypy src",
        ],
    )
    def test_bash_build_or_test_keywords_match(self, command: str) -> None:
        assert _categorize_tool("Bash", {"command": command}) == "build-or-test"

    def test_bash_keyword_match_is_case_insensitive(self) -> None:
        assert _categorize_tool("Bash", {"command": "PYTEST -q"}) == "build-or-test"

    def test_bash_without_keyword_is_shell_other(self) -> None:
        assert _categorize_tool("Bash", {"command": "ls -la"}) == "shell-other"

    def test_bash_missing_command_is_shell_other(self) -> None:
        assert _categorize_tool("Bash", {}) == "shell-other"

    @pytest.mark.parametrize("name", ["Edit", "Write", "MultiEdit", "NotebookEdit"])
    def test_file_edit_tools(self, name: str) -> None:
        assert _categorize_tool(name, {}) == "file-edit"

    @pytest.mark.parametrize("name", ["Read", "Grep", "Glob"])
    def test_search_or_read_tools(self, name: str) -> None:
        assert _categorize_tool(name, {}) == "search-or-read"

    def test_unrecognized_tool_is_other_tool(self) -> None:
        assert _categorize_tool("WebFetch", {}) == "other-tool"


class TestTaskActivityBreakdown:
    def test_missing_file_raises_oserror(self, tmp_path: Path) -> None:
        with pytest.raises(OSError):
            task_activity_breakdown(str(tmp_path / "does-not-exist.jsonl"))

    def test_empty_transcript_returns_all_zero_breakdown(self, tmp_path: Path) -> None:
        path = _write_transcript(tmp_path / "empty.jsonl", [])
        breakdown = task_activity_breakdown(path)
        assert breakdown == ActivityBreakdown(total_seconds=0.0, by_category=[])

    def test_single_event_transcript_has_no_next_boundary(self, tmp_path: Path) -> None:
        path = _write_transcript(
            tmp_path / "single.jsonl",
            [_assistant_event("2026-01-01T00:00:00.000Z", text="only turn")],
        )
        breakdown = task_activity_breakdown(path)
        assert breakdown == ActivityBreakdown(total_seconds=0.0, by_category=[])

    def test_events_without_timestamp_are_excluded_from_the_walk(self, tmp_path: Path) -> None:
        events = [
            {"type": "system", "subtype": "init"},  # no "timestamp" -- must be skipped
            _assistant_event("2026-01-01T00:00:00.000Z", tool_uses=[{"name": "Read", "input": {}}]),
            _user_event("2026-01-01T00:00:04.000Z"),
        ]
        path = _write_transcript(tmp_path / "no_ts.jsonl", events)
        breakdown = task_activity_breakdown(path)
        assert breakdown.total_seconds == 4.0
        assert breakdown.by_category == [
            ActivityCategorySeconds(category="search-or-read", seconds=4.0)
        ]

    def test_mixed_tool_turn_attributes_whole_interval_by_priority(self, tmp_path: Path) -> None:
        # One turn with both an Edit (file-edit) and a Bash (shell-other) tool_use block --
        # design doc §2.2: attributed to the whole set, not split. `_CATEGORY_PRIORITY` picks
        # `file-edit` first among a mixed set.
        events = [
            _assistant_event(
                "2026-01-01T00:00:00.000Z",
                tool_uses=[
                    {"name": "Edit", "input": {}},
                    {"name": "Bash", "input": {"command": "ls"}},
                ],
            ),
            _user_event("2026-01-01T00:00:09.000Z"),
        ]
        path = _write_transcript(tmp_path / "mixed.jsonl", events)
        breakdown = task_activity_breakdown(path)
        assert breakdown.total_seconds == 9.0
        assert breakdown.by_category == [ActivityCategorySeconds(category="file-edit", seconds=9.0)]

    def test_fixture_transcript_covers_every_category_exactly(self) -> None:
        """Against the COMMITTED, redacted fixture (architect finding B-8) -- exercises every
        row of design doc §2.2's table: file-edit, build-or-test (Bash w/ `pytest`), shell-other
        (Bash w/ `ls`), search-or-read (`Read`), and thinking-or-text (both a pure-thinking
        turn and `user`/`system`/`result` events carrying no `tool_use` block)."""
        breakdown = task_activity_breakdown(str(_FIXTURE_TRANSCRIPT))
        by_category = {row.category: row.seconds for row in breakdown.by_category}
        assert by_category == {
            "thinking-or-text": 11.0,
            "search-or-read": 3.0,
            "build-or-test": 10.0,
            "shell-other": 4.0,
            "file-edit": 6.0,
        }
        assert breakdown.total_seconds == 34.0
        # Total start (t0) to end (t10) of the fixture's own timeline is exactly 34s --
        # every second accounted for, none double-counted or dropped.
        assert breakdown.total_seconds == sum(by_category.values())

    def test_by_category_sorted_descending_seconds_then_category_ascending(self) -> None:
        breakdown = task_activity_breakdown(str(_FIXTURE_TRANSCRIPT))
        seconds = [row.seconds for row in breakdown.by_category]
        assert seconds == sorted(seconds, reverse=True)
        # Descending by seconds: thinking-or-text (11.0), build-or-test (10.0), file-edit
        # (6.0), shell-other (4.0), search-or-read (3.0).
        assert [row.category for row in breakdown.by_category] == [
            "thinking-or-text",
            "build-or-test",
            "file-edit",
            "shell-other",
            "search-or-read",
        ]
