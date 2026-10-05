"""T-eyn5UG AC-9 (U-US1..U-US4) and the usage half of AC-6: `usage.aggregate_usage` sites A and B,
the cross-run `result_cache` object and the payload."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from agent_orchestrator.artifacts import LocalFsArtifactStore
from agent_orchestrator.models import RESULT_CACHE_MISS, RunState
from agent_orchestrator.usage import UsageReport, aggregate_usage, usage_report_payload
from tests.cache._report_states import (
    RESULT_CACHE_USAGE_SCHEMA,
    hit_rec,
    mixed_state,
    rec,
    run_state,
    settled,
    would_hit_rec,
)

VERDICT_PATH = "t1/review-verdict.json"


def _agg(tmp_path: Path, *states: RunState) -> UsageReport:
    return aggregate_usage(list(states), LocalFsArtifactStore(str(tmp_path)))


def _group(report: UsageReport, agent: str = "writer"):
    return next(g for g in report.groups if g.agent == agent)


class TestSiteAGroupMetrics:
    """U-US1: a current hit is not a dispatched task; its real carried spend still counts."""

    def test_first_pass_hit_is_not_counted_and_adds_no_spend(self, tmp_path: Path) -> None:
        state = run_state(
            {"dispatched": settled(cumulative_cost_usd=0.2, attempts=1), "hit": settled()},
            {"hit": hit_rec()},
        )
        g = _group(_agg(tmp_path, state))
        assert (g.tasks, g.succeeded, g.failed, g.retried) == (1, 1, 0, 0)
        assert g.cost_usd == 0.2

    def test_a_group_of_only_first_pass_hits_does_not_appear(self, tmp_path: Path) -> None:
        state = run_state({"hit": settled()}, {"hit": hit_rec()})
        assert _agg(tmp_path, state).groups == []

    def test_hit_after_spend_adds_its_carried_spend_but_is_not_a_dispatched_task(
        self, tmp_path: Path
    ) -> None:
        carried = settled(
            cumulative_cost_usd=0.5,
            cumulative_input_tokens=300,
            cumulative_output_tokens=70,
            attempts=2,
            cycle=2,
        )
        state = run_state(
            {"first": settled(cumulative_cost_usd=0.1), "after": carried},
            {"after": hit_rec(dispatch_cycle=2)},
        )
        g = _group(_agg(tmp_path, state))
        assert (g.tasks, g.succeeded, g.failed, g.retried) == (1, 1, 0, 0)
        assert g.cost_usd == 0.1 + 0.5
        assert (g.input_tokens, g.output_tokens) == (300, 70)

    def test_hit_after_spend_alone_creates_a_group_with_zero_tasks(self, tmp_path: Path) -> None:
        carried = settled(cumulative_cost_usd=0.5, cycle=2, attempts=3)
        state = run_state({"after": carried}, {"after": hit_rec(dispatch_cycle=2)})
        g = _group(_agg(tmp_path, state))
        assert g.tasks == 0 and g.cost_usd == 0.5
        assert g.mean_cost_usd is None and g.retry_rate is None  # no division by zero

    def test_stale_hits_are_counted_like_any_dispatched_task(self, tmp_path: Path) -> None:
        # U-RP8 (usage half): cycle mismatch and ended_at mismatch are NOT current hits.
        g = _group(_agg(tmp_path, mixed_state()))
        assert g.tasks == 3  # stale_cycle, stale_ended and miss; only `hit` is excluded

    def test_without_records_the_numbers_are_the_pre_epic_numbers(self, tmp_path: Path) -> None:
        plain = run_state({"a": settled(cumulative_cost_usd=0.3), "b": settled(attempts=2)})
        report = _agg(tmp_path, plain)
        g = _group(report)
        assert (g.tasks, g.succeeded, g.retried, g.cost_usd) == (2, 2, 1, 0.3)
        assert report.result_cache is None


class TestSiteBProducerAttribution:
    """U-US2: a producer that is a current hit gets no review attribution."""

    @staticmethod
    def _state(tmp_path: Path, records: dict) -> RunState:
        (tmp_path / "t1").mkdir()
        (tmp_path / VERDICT_PATH).write_text(
            json.dumps({"verdict": "FAIL", "findings": {"major": 2}, "must_fix": 1})
        )
        return run_state(
            {
                "prod": settled(cumulative_cost_usd=0.1),
                "other": settled(agent="other-agent"),
                "rev": settled(
                    agent="reviewer",
                    review_verdict_path=VERDICT_PATH,
                    upstream_producers=["prod", "other"],
                ),
            },
            records,
        )

    def test_current_hit_producer_is_skipped(self, tmp_path: Path) -> None:
        report = _agg(tmp_path, self._state(tmp_path, {"prod": hit_rec()}))
        assert (report.reviews_seen, report.verdicts_found) == (1, 1)
        assert _group(report).reviewed == 0  # `prod` (agent "writer") got nothing
        assert _group(report, "other-agent").reviewed == 1  # the other producer still counts

    def test_stale_hit_producer_is_still_attributed(self, tmp_path: Path) -> None:
        stale = hit_rec(ended_at="1999-01-01T00:00:00+00:00")
        report = _agg(tmp_path, self._state(tmp_path, {"prod": stale}))
        assert _group(report).reviewed == 1 and _group(report).major == 2


class TestCrossRunObject:
    """U-US3 / U-US4."""

    def test_sums_usage_counters_over_runs_and_validates_against_13_6(self, tmp_path: Path) -> None:
        a = run_state(
            {"h": settled(), "m": settled()},
            {"h": hit_rec(), "m": rec(RESULT_CACHE_MISS, store_reason="repo_head_moved")},
            run_id="a",
        )
        b = run_state(
            {"w": settled(), "m": settled()},
            {"w": would_hit_rec(), "m": rec(RESULT_CACHE_MISS)},
            run_id="b",
        )
        untouched = run_state({"x": settled()}, run_id="c")
        report = _agg(tmp_path, a, b, untouched)
        rcu = report.result_cache
        assert rcu is not None
        assert (rcu.hits, rcu.would_hits, rcu.misses, rcu.lookups) == (1, 1, 2, 4)
        assert rcu.saved_cost_usd == 0.4123 and rcu.saved_tokens == 15400
        assert rcu.saved_seconds == 95.2 and rcu.avoidable_cost_usd == 0.5
        assert rcu.miss_reasons == {"not_found": 2}
        assert rcu.store_skip_reasons == {"repo_head_moved": 1}
        jsonschema.validate(usage_report_payload(report)["result_cache"], RESULT_CACHE_USAGE_SCHEMA)

    def test_object_is_none_when_no_run_has_current_records(self, tmp_path: Path) -> None:
        stale_only = run_state({"t": settled(cycle=5)}, {"t": rec(RESULT_CACHE_MISS)})
        assert _agg(tmp_path, stale_only).result_cache is None

    def test_payload_omits_the_key_when_absent_and_is_otherwise_unchanged(
        self, tmp_path: Path
    ) -> None:
        plain = run_state({"a": settled(cumulative_cost_usd=0.3)})
        payload = usage_report_payload(_agg(tmp_path, plain))
        assert list(payload) == [  # the pre-epic payload key list, as a literal
            "runs_scanned",
            "verdicts_found",
            "reviews_seen",
            "groups",
            "outcomes",
            "runs_rated",
            "feedback_errors",
            "skipped",
            "survival_available",
            "survival_unavailable_reason",
            "survival_ref",
            "run_signals",
        ]

    def test_payload_carries_the_object_when_present(self, tmp_path: Path) -> None:
        payload = usage_report_payload(_agg(tmp_path, mixed_state()))
        assert payload["result_cache"]["hits"] == 1
        assert payload["result_cache"]["lookups"] == 2
        assert sorted(payload["result_cache"]) == sorted(RESULT_CACHE_USAGE_SCHEMA["properties"])
