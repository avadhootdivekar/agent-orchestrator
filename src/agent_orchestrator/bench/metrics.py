"""Metrics normalization (design doc `docs-md/benchmarking-framework-hld.md` §4.4, §6).

Merges a subject run result (`SubjectResult`-shaped) + a `GradeResult` into a persisted
`TaskMetric`, and computes per-subject `Aggregate`s over a list of `TaskMetric`s.

`SubjectResult` is defined in `bench/subjects.py`, a concurrently-written sibling task
(T-Sbj9Ka) -- this module MUST NOT import it (see repo-wide instruction for this task).
Instead `build_task_metric` takes its subject-result argument typed against a *local*
structural `SubjectResultLike` Protocol built directly from design §6's field list; any
object with those attributes (including the real `SubjectResult` once T-Sbj9Ka lands)
satisfies it without an import edge.

`GradeResult` IS imported from this task's own `graders.py` -- no cross-task or
concurrency concern there.

Determinism note (CLAUDE.md): `started_at`/`ended_at` are accepted as caller-supplied
strings rather than computed here via `datetime.now()` -- the future runner (T-Run5Tz)
owns the injected clock; this module only merges/aggregates already-computed values.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field

from .graders import GradeResult
from .spec import BenchTask


@runtime_checkable
class SubjectResultLike(Protocol):
    """Structural subset of `SubjectResult` (design §6, owned by T-Sbj9Ka's
    subjects.py) that `build_task_metric` reads. See module docstring for why this is
    a local Protocol instead of an import.
    """

    status: str
    wall_clock_seconds: float
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    cache_creation_input_tokens: int | None
    cache_read_input_tokens: int | None
    attempts: int | None
    turns: int | None
    capture_dir: str


class TaskMetric(BaseModel):
    """One (subject × task) result row (design §6). `workspace` is a POINTER to the
    capture dir, never its content (design §4.6: transcripts are not committed).
    """

    subject_id: str
    task_id: str
    domain: str
    category: str
    solved: bool
    score: float = Field(ge=0.0, le=1.0)
    wall_clock_seconds: float
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    attempts: int | None = None
    turns: int | None = None
    subject_status: str
    grader_type: str
    workspace: str
    config_fingerprint: str
    started_at: str
    ended_at: str


class Aggregate(BaseModel):
    """Per-subject rollup over a suite's `TaskMetric`s (design §4.4, TASK.md: "totals:
    cost/tokens/wall-clock"). `cost_available` is False when at least one metric's
    `cost_usd` was None (actuals unavailable) -- `total_cost_usd`/`cost_per_solved` are
    still computed from the metrics that DO have a cost, but the flag tells a
    downstream report writer (T-Rpt3Wq) the total is a partial one.
    """

    solved: int
    total: int
    solve_rate: float = Field(ge=0.0, le=1.0)
    mean_score: float = Field(ge=0.0, le=1.0)
    total_cost_usd: float
    cost_available: bool
    cost_per_solved: float | None
    total_wall_clock_seconds: float
    total_input_tokens: int
    total_output_tokens: int
    total_cache_creation_input_tokens: int
    total_cache_read_input_tokens: int


def build_task_metric(
    subject_id: str,
    task: BenchTask,
    domain: str,
    sr: SubjectResultLike,
    gr: GradeResult,
    fingerprint: str,
    started_at: str,
    ended_at: str,
) -> TaskMetric:
    """Merge a subject run result + a grade verdict into one persisted `TaskMetric`
    (design §4.4 pseudocode).

    `domain` is passed explicitly rather than read off `task.domain`: `BenchTask`
    (`bench/spec.py`, owned by T-Sc4Hm2) has no `domain` field -- design §6 marks it
    "domain:str(inherited)", i.e. inherited from the *suite*, not the task. The caller
    (the future runner, which holds the `BenchSuite`) passes `suite.domain` here.
    """
    return TaskMetric(
        subject_id=subject_id,
        task_id=task.id,
        domain=domain,
        category=task.category,
        solved=gr.solved,
        score=gr.score,
        wall_clock_seconds=sr.wall_clock_seconds,
        cost_usd=sr.cost_usd,
        input_tokens=sr.input_tokens,
        output_tokens=sr.output_tokens,
        cache_creation_input_tokens=sr.cache_creation_input_tokens,
        cache_read_input_tokens=sr.cache_read_input_tokens,
        attempts=sr.attempts,
        turns=sr.turns,
        subject_status=sr.status,
        grader_type=task.grader.type,
        workspace=sr.capture_dir,
        config_fingerprint=fingerprint,
        started_at=started_at,
        ended_at=ended_at,
    )


def aggregate(metrics: list[TaskMetric]) -> Aggregate:
    """Compute a per-subject `Aggregate` over `metrics` (design §4.4 pseudocode).

    Edge cases (design §4.4): `cost_usd is None` on any metric excludes it from the
    cost sum and flips `cost_available` to False; `solved == 0` -> `cost_per_solved is
    None` (no ZeroDivisionError); an empty `metrics` list -> all rate/mean fields are
    0.0 and `cost_per_solved` is None, rather than raising.
    """
    total = len(metrics)
    solved = sum(1 for m in metrics if m.solved)
    solve_rate = (solved / total) if total > 0 else 0.0
    mean_score = (sum(m.score for m in metrics) / total) if total > 0 else 0.0

    cost_values = [m.cost_usd for m in metrics]
    cost_available = all(c is not None for c in cost_values)
    total_cost_usd = sum(c for c in cost_values if c is not None)
    cost_per_solved = (total_cost_usd / solved) if solved > 0 else None

    return Aggregate(
        solved=solved,
        total=total,
        solve_rate=solve_rate,
        mean_score=mean_score,
        total_cost_usd=total_cost_usd,
        cost_available=cost_available,
        cost_per_solved=cost_per_solved,
        total_wall_clock_seconds=sum(m.wall_clock_seconds for m in metrics),
        total_input_tokens=sum(m.input_tokens or 0 for m in metrics),
        total_output_tokens=sum(m.output_tokens or 0 for m in metrics),
        total_cache_creation_input_tokens=sum(m.cache_creation_input_tokens or 0 for m in metrics),
        total_cache_read_input_tokens=sum(m.cache_read_input_tokens or 0 for m in metrics),
    )
