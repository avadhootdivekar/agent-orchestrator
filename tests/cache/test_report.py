"""T-eyn5UG AC-1..AC-6 (U-RP1..U-RP8): `cache/report.py`, the read-side helpers."""

from __future__ import annotations

import ast
from pathlib import Path

import jsonschema
import pytest

from agent_orchestrator.cache import report
from agent_orchestrator.models import (
    RESULT_CACHE_INELIGIBLE,
    RESULT_CACHE_MISS,
    RunState,
)
from tests.cache._report_states import (
    BRIEF_KEYS,
    RESULT_CACHE_RUN_SCHEMA,
    RESULT_CACHE_TASK_SCHEMA,
    RESULT_CACHE_USAGE_SCHEMA,
    TASK_VIEW_KEYS,
    hit_rec,
    mixed_state,
    rec,
    run_state,
    settled,
    would_hit_rec,
)

REPORT_SOURCE = Path(report.__file__)

# The HLD 8.8.2 example: two hits, one stored miss, one ineligible task.
EXAMPLE_LINE = (
    "Result cache: hits=2 (saved ~$1.2345 est., ~54000 tokens, ~312s) "
    "would_hits=0 misses=1 stored=1 ineligible=1"
)


def _example_state() -> RunState:
    return run_state(
        {tid: settled() for tid in ("h1", "h2", "m", "i")},
        {
            "h1": hit_rec(),  # 0.4123 USD, 15400 tokens, 95.2 s
            "h2": hit_rec(
                saved_cost_usd=0.8222,
                saved_input_tokens=33000,
                saved_output_tokens=5600,
                saved_seconds=217.2,
            ),
            "m": rec(RESULT_CACHE_MISS, stored=True),
            "i": rec(RESULT_CACHE_INELIGIBLE),
        },
    )


class TestCurrency:
    """U-RP1: the currency truth table, including the `ended_at` binding (U-RP8 for the rest)."""

    def test_current_records_filters_by_cycle_and_ended_at(self) -> None:
        state = mixed_state()
        assert sorted(report.current_records(state)) == ["hit", "miss"]

    def test_current_hit_follows_the_same_truth_table(self) -> None:
        state = mixed_state()
        assert report.current_hit(state, "hit") is True
        assert report.current_hit(state, "stale_cycle") is False
        assert report.current_hit(state, "stale_ended") is False
        assert report.current_hit(state, "miss") is False  # current, but not a hit
        assert report.current_hit(state, "no_such_task") is False

    def test_hit_on_a_task_that_is_no_longer_succeeded_is_stale(self) -> None:
        state = run_state({"t": settled(status="failed")}, {"t": hit_rec()})
        assert report.current_records(state) == {}
        assert report.current_hit(state, "t") is False

    def test_non_hit_records_need_only_the_cycle(self) -> None:
        state = run_state({"t": settled(status="failed")}, {"t": rec(RESULT_CACHE_MISS)})
        assert list(report.current_records(state)) == ["t"]

    def test_record_for_an_unknown_task_is_not_current(self) -> None:
        state = run_state({}, {"ghost": rec(RESULT_CACHE_MISS)})
        assert report.current_records(state) == {}


class TestTaskView:
    """U-RP2."""

    def test_exactly_the_documented_fields_in_brief_order(self) -> None:
        view = report.task_view(hit_rec())
        assert set(view) == TASK_VIEW_KEYS
        assert list(view)[:5] == BRIEF_KEYS == list(report.BRIEF_FIELDS)

    def test_hit_example_values(self) -> None:
        view = report.task_view(hit_rec())
        assert view["hit"] is True
        assert view["key"] == "6646469e94a695fe1a994d35f54ca74e007262911255552b03c54ce2e5d0319f"
        assert view["saved_cost_usd"] == 0.4123
        assert view["saved_tokens"] == 15400
        assert view["saved_input_tokens"] == 12000
        assert view["saved_output_tokens"] == 3400
        assert view["saved_seconds"] == 95.2
        assert view["source_run_id"] == "doc-pipeline-20261005T101450Z"

    @pytest.mark.parametrize(
        "record",
        [
            hit_rec(),
            would_hit_rec(stored=True),
            rec(RESULT_CACHE_MISS, store_reason="repo_worktree_probe_failed"),
            rec(RESULT_CACHE_INELIGIBLE),
        ],
        ids=["hit", "would_hit", "miss_not_storable", "ineligible"],
    )
    def test_validates_against_the_13_5_schema(self, record: object) -> None:
        jsonschema.validate(report.task_view(record), RESULT_CACHE_TASK_SCHEMA)  # type: ignore[arg-type]

    def test_ineligible_has_a_null_key_and_a_reason(self) -> None:
        view = report.task_view(rec(RESULT_CACHE_INELIGIBLE))
        assert view["hit"] is False and view["key"] is None
        assert (view["reason"], view["reason_detail"]) == ("unknown_task_field", "approval")

    def test_hit_is_derived_not_trusted(self) -> None:
        # A hostile state.json cannot make a miss claim a hit (the record model re-derives it).
        record = rec(RESULT_CACHE_MISS, hit=True, saved_input_tokens=3, saved_output_tokens=4)
        view = report.task_view(record)
        assert view["hit"] is False and view["saved_tokens"] == 7


class TestRunBlock:
    """U-RP3 / U-RP4."""

    def test_example_block_values_and_schema(self) -> None:
        block = report.run_block(_example_state())
        assert block == {
            "hits": 2,
            "saved_cost_usd": 1.2345,
            "saved_tokens": 54000,
            "saved_seconds": 312.4,
            "would_hits": 0,
            "misses": 1,
            "ineligible": 1,
            "stored": 1,
            "lookups": 3,
            "saved_input_tokens": 45000,
            "saved_output_tokens": 9000,
            "avoidable_cost_usd": 0.0,
        }
        jsonschema.validate(block, RESULT_CACHE_RUN_SCHEMA)

    def test_saved_sums_current_hits_only_and_avoidable_current_would_hits_only(self) -> None:
        state = run_state(
            {tid: settled() for tid in ("h", "w1", "w2", "stale")},
            {
                "h": hit_rec(),
                "w1": would_hit_rec(),
                "w2": would_hit_rec(saved_cost_usd=0.25),
                "stale": hit_rec(saved_cost_usd=100.0, ended_at="1999-01-01T00:00:00+00:00"),
            },
        )
        block = report.run_block(state)
        assert block is not None
        assert block["hits"] == 1
        assert block["saved_cost_usd"] == 0.4123  # would-hits and the stale hit excluded
        assert block["saved_tokens"] == 15400
        assert block["would_hits"] == 2
        assert block["avoidable_cost_usd"] == 0.75
        assert block["lookups"] == 3

    def test_lookups_is_hits_plus_would_hits_plus_misses(self) -> None:
        state = run_state(
            {tid: settled() for tid in "abcd"},
            {
                "a": hit_rec(),
                "b": would_hit_rec(),
                "c": rec(RESULT_CACHE_MISS),
                "d": rec(RESULT_CACHE_INELIGIBLE),  # never a lookup
            },
        )
        block = report.run_block(state)
        assert block is not None
        assert block["lookups"] == block["hits"] + block["would_hits"] + block["misses"] == 3  # type: ignore[operator]
        assert block["ineligible"] == 1

    def test_none_when_nothing_is_current(self) -> None:
        assert report.run_block(run_state({"t": settled()})) is None
        stale = run_state({"t": settled(cycle=3)}, {"t": rec(RESULT_CACHE_MISS)})
        assert report.run_block(stale) is None
        assert report.result_cache_status_fields(stale) == ({}, None)

    def test_float_noise_is_rounded_away(self) -> None:
        state = run_state(
            {"a": settled(), "b": settled()},
            {"a": hit_rec(saved_cost_usd=0.1), "b": hit_rec(saved_cost_usd=0.2)},
        )
        block = report.run_block(state)
        assert block is not None and block["saved_cost_usd"] == 0.3

    def test_status_fields_pair_task_views_with_the_run_block(self) -> None:
        tasks, block = report.result_cache_status_fields(mixed_state())
        assert sorted(tasks) == ["hit", "miss"]
        assert tasks["hit"] == report.task_view(hit_rec())
        assert block is not None and block["hits"] == 1 and block["misses"] == 1


class TestSummaryLine:
    """U-RP5."""

    def test_exact_text_for_the_8_8_2_example(self) -> None:
        assert report.format_summary_line(report.run_block(_example_state())) == EXAMPLE_LINE

    def test_none_gives_none(self) -> None:
        assert report.format_summary_line(None) is None

    def test_shadow_only_run_still_prints_a_line(self) -> None:
        state = run_state({"w": settled()}, {"w": would_hit_rec(stored=True)})
        line = report.format_summary_line(report.run_block(state))
        assert line == (
            "Result cache: hits=0 (saved ~$0.0000 est., ~0 tokens, ~0s) "
            "would_hits=1 misses=0 stored=1 ineligible=0"
        )


class TestUsageCounters:
    """U-RP6."""

    def test_none_when_nothing_is_current(self) -> None:
        assert report.usage_counters(run_state({"t": settled()})) is None

    def test_per_run_contribution(self) -> None:
        state = run_state(
            {tid: settled() for tid in ("h", "w", "m1", "m2", "i")},
            {
                "h": hit_rec(),
                "w": would_hit_rec(store_reason="repo_head_moved"),
                "m1": rec(RESULT_CACHE_MISS, store_reason="repo_head_moved"),
                "m2": rec(RESULT_CACHE_MISS, reason="expired"),
                "i": rec(RESULT_CACHE_INELIGIBLE),
            },
        )
        counters = report.usage_counters(state)
        assert counters == {
            "hits": 1,
            "saved_cost_usd": 0.4123,
            "saved_tokens": 15400,
            "saved_seconds": 95.2,
            "lookups": 4,
            "would_hits": 1,
            "misses": 2,
            "ineligible": 1,
            "avoidable_cost_usd": 0.5,
            "miss_reasons": {"expired": 1, "not_found": 1},
            "store_skip_reasons": {"repo_head_moved": 2},
        }
        jsonschema.validate(counters, RESULT_CACHE_USAGE_SCHEMA)


class TestStaleFilteringThroughEveryHelper:
    """U-RP8 (helper half; status/usage/outcomes halves live in their own modules)."""

    def test_the_four_task_state_gives_one_answer_everywhere(self) -> None:
        state = mixed_state()
        current = report.current_records(state)
        tasks, block = report.result_cache_status_fields(state)
        counters = report.usage_counters(state)
        assert sorted(current) == sorted(tasks) == ["hit", "miss"]
        assert block is not None and counters is not None
        assert block["hits"] == counters["hits"] == 1
        assert block["saved_cost_usd"] == counters["saved_cost_usd"] == 0.4123
        assert block == report.run_block(state)
        line = report.format_summary_line(block)
        assert line is not None and "hits=1 " in line and "misses=1 " in line


class TestImports:
    """U-RP7: `report.py` imports only `models` and `constants`."""

    def test_only_models_and_constants_are_imported(self) -> None:
        tree = ast.parse(REPORT_SOURCE.read_text(encoding="utf-8"))
        allowed = {
            "__future__",
            "collections",
            "typing",
            "agent_orchestrator.models",
            "agent_orchestrator.cache.constants",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.level == 0 and node.module in allowed, ast.dump(node)
            elif isinstance(node, ast.Import):
                assert {a.name for a in node.names} <= allowed, ast.dump(node)
