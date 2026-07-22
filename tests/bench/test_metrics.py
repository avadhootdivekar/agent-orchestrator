"""Unit tests for bench/metrics.py: build_task_metric + aggregate (T-Grd7Vx AC5)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from agent_orchestrator.bench.graders import GradeResult
from agent_orchestrator.bench.metrics import Aggregate, TaskMetric, aggregate, build_task_metric
from agent_orchestrator.bench.spec import BenchTask, GraderConfig


@dataclass
class _FakeSubjectResult:
    """Minimal stand-in for `SubjectResult` -- satisfies `SubjectResultLike` structurally."""

    status: str
    wall_clock_seconds: float
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    attempts: int | None = None
    turns: int | None = None
    capture_dir: str = "/tmp/unused/capture"


def _task(task_id: str = "bugfix-off-by-one") -> BenchTask:
    return BenchTask(
        id=task_id,
        category="bugfix",
        instruction="tasks/x/instruction.md",
        fixture="tasks/x/fixture",
        grader=GraderConfig(type="pytest"),
        timeout_seconds=60,
        tags=[],
    )


def _metric(
    task_id: str = "t1",
    *,
    solved: bool = True,
    score: float = 1.0,
    cost_usd: float | None = 0.10,
    wall_clock_seconds: float = 5.0,
) -> TaskMetric:
    return TaskMetric(
        subject_id="claude-haiku",
        task_id=task_id,
        domain="software",
        category="bugfix",
        solved=solved,
        score=score,
        wall_clock_seconds=wall_clock_seconds,
        cost_usd=cost_usd,
        input_tokens=100,
        output_tokens=50,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
        attempts=1,
        turns=3,
        subject_status="succeeded",
        grader_type="pytest",
        workspace="/ws/capture",
        config_fingerprint="deadbeef",
        started_at="2026-07-22T00:00:00Z",
        ended_at="2026-07-22T00:00:05Z",
    )


# ---------------------------------------------------------------------------
# build_task_metric
# ---------------------------------------------------------------------------


def test_build_task_metric_merges_subject_and_grade_result() -> None:
    task = _task("bugfix-off-by-one")
    sr = _FakeSubjectResult(
        status="succeeded",
        wall_clock_seconds=12.5,
        cost_usd=0.03,
        input_tokens=500,
        output_tokens=200,
        cache_creation_input_tokens=10,
        cache_read_input_tokens=20,
        attempts=1,
        turns=4,
        capture_dir="/ws/claude-haiku/bugfix-off-by-one/capture",
    )
    gr = GradeResult(
        solved=True, score=1.0, detail={"passed": 4, "failed": 0, "total": 4}, raw_tail=""
    )

    metric = build_task_metric(
        subject_id="claude-haiku",
        task=task,
        domain="software",
        sr=sr,
        gr=gr,
        fingerprint="sha256:abc123",
        started_at="2026-07-22T00:00:00Z",
        ended_at="2026-07-22T00:00:12Z",
    )

    assert metric.subject_id == "claude-haiku"
    assert metric.task_id == "bugfix-off-by-one"
    assert metric.domain == "software"
    assert metric.category == "bugfix"
    assert metric.solved is True
    assert metric.score == 1.0
    assert metric.wall_clock_seconds == 12.5
    assert metric.cost_usd == 0.03
    assert metric.input_tokens == 500
    assert metric.output_tokens == 200
    assert metric.cache_creation_input_tokens == 10
    assert metric.cache_read_input_tokens == 20
    assert metric.attempts == 1
    assert metric.turns == 4
    assert metric.subject_status == "succeeded"
    assert metric.grader_type == "pytest"
    assert metric.workspace == "/ws/claude-haiku/bugfix-off-by-one/capture"
    assert metric.config_fingerprint == "sha256:abc123"
    assert metric.started_at == "2026-07-22T00:00:00Z"
    assert metric.ended_at == "2026-07-22T00:00:12Z"


def test_build_task_metric_propagates_none_cost() -> None:
    task = _task("errored-task")
    sr = _FakeSubjectResult(status="error", wall_clock_seconds=0.5, cost_usd=None)
    gr = GradeResult(solved=False, score=0.0, detail={}, raw_tail="subject error")

    metric = build_task_metric(
        subject_id="ao-workflow",
        task=task,
        domain="software",
        sr=sr,
        gr=gr,
        fingerprint="sha256:def456",
        started_at="2026-07-22T00:00:00Z",
        ended_at="2026-07-22T00:00:01Z",
    )
    assert metric.cost_usd is None
    assert metric.subject_status == "error"
    assert metric.solved is False


# ---------------------------------------------------------------------------
# aggregate (AC5)
# ---------------------------------------------------------------------------


def test_aggregate_empty_list_no_crash() -> None:
    agg = aggregate([])
    assert agg.total == 0
    assert agg.solved == 0
    assert agg.solve_rate == 0.0
    assert agg.mean_score == 0.0
    assert agg.total_cost_usd == 0.0
    assert agg.cost_available is True
    assert agg.cost_per_solved is None


def test_aggregate_basic_solve_rate_and_mean_score() -> None:
    metrics = [
        _metric("t1", solved=True, score=1.0, cost_usd=0.10),
        _metric("t2", solved=False, score=0.5, cost_usd=0.20),
        _metric("t3", solved=True, score=0.8, cost_usd=0.05),
    ]
    agg = aggregate(metrics)
    assert agg.solved == 2
    assert agg.total == 3
    assert agg.solve_rate == pytest.approx(2 / 3)
    assert agg.mean_score == pytest.approx((1.0 + 0.5 + 0.8) / 3)
    assert agg.total_cost_usd == pytest.approx(0.35)
    assert agg.cost_available is True
    assert agg.cost_per_solved == pytest.approx(0.35 / 2)


def test_aggregate_excludes_none_cost_and_flags_unavailable() -> None:
    metrics = [
        _metric("t1", solved=True, cost_usd=0.10),
        _metric("t2", solved=True, cost_usd=None),  # actuals unavailable
        _metric("t3", solved=True, cost_usd=0.20),
    ]
    agg = aggregate(metrics)
    assert agg.cost_available is False
    assert agg.total_cost_usd == pytest.approx(0.30)  # excludes the None entry
    assert agg.cost_per_solved == pytest.approx(0.30 / 3)


def test_aggregate_zero_solved_cost_per_solved_is_none_no_zerodivision() -> None:
    metrics = [
        _metric("t1", solved=False, score=0.0, cost_usd=0.10),
        _metric("t2", solved=False, score=0.2, cost_usd=0.15),
    ]
    agg = aggregate(metrics)
    assert agg.solved == 0
    assert agg.cost_per_solved is None
    assert agg.total_cost_usd == pytest.approx(0.25)  # sum still computed


def test_aggregate_totals_tokens_and_wall_clock() -> None:
    metrics = [
        _metric("t1", wall_clock_seconds=5.0),
        _metric("t2", wall_clock_seconds=7.5),
    ]
    agg = aggregate(metrics)
    assert agg.total_wall_clock_seconds == pytest.approx(12.5)
    assert agg.total_input_tokens == 200  # 100 + 100
    assert agg.total_output_tokens == 100  # 50 + 50


def test_aggregate_returns_aggregate_instance() -> None:
    assert isinstance(aggregate([_metric()]), Aggregate)
