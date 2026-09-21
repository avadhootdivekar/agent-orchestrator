"""Timing/profiling and cache-effectiveness reporting (E-1cecSx B2/B4,
`docs-md/cost-caching-optimization-hld.md` §2, §4).

Two families of pure function, both read-only over already-persisted data:

- **Run-level timing** (B2.1, this module): `top_n_slowest_tasks` reuses the exact per-task
  `ended_at - started_at` derivation `models.py::compute_run_active_seconds` already performs
  (summed, run-wide) -- here, unsummed and ranked. No new capture needed.
- **Cache effectiveness** (B4, this module): `cache_effectiveness` derives a hit-rate figure
  from the token-accounting fields E-9h3m7k already captures
  (`TaskRunState.cumulative_cache_read_input_tokens`/`.cumulative_cache_creation_input_tokens`).

**Within-task activity-type breakdown (B2.2)** -- parsing `transcript.jsonl`'s per-event
timestamps and bucketing by tool name -- is added to this module by the delegated
`T-J1b0FN` task; not present as of this file's initial creation (`outcomes.py`/this module
were split from the design doc's `T-lue4Rz` prep work to reduce merge risk with the parallel
`developer` agents assigned `T-J1b0FN`/`T-h1KdlK`).

Both function families surface via the on-demand `ao report timing`/dashboard expandable
detail view only -- never a new default column on the main task table (locked-in constraint,
design doc §2.3/§4).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from .models import RunState, RunUsageTotals, TaskRunState


class TaskDuration(BaseModel):
    """One task's wall-clock duration for one dispatch cycle's settle (design doc §2.1)."""

    task_id: str
    seconds: float


def top_n_slowest_tasks(state: RunState, n: int) -> list[TaskDuration]:
    """The *n* slowest SETTLED tasks in *state*, by `ended_at - started_at`, descending.

    Same per-task duration derivation `models.py::compute_run_active_seconds` already uses
    (summed, run-wide) -- unsummed here, one row per task, ranked. A task contributes only
    once BOTH timestamps are present (pending/running/not_taken tasks are excluded, same
    guard as `compute_run_active_seconds`). Ties break by task id (ascending) for
    deterministic output. `n <= 0` returns an empty list rather than raising.
    """
    if n <= 0:
        return []
    durations: list[TaskDuration] = []
    for task_id, ts in state.tasks.items():
        if ts.started_at is None or ts.ended_at is None:
            continue
        started = datetime.fromisoformat(ts.started_at)
        ended = datetime.fromisoformat(ts.ended_at)
        durations.append(TaskDuration(task_id=task_id, seconds=(ended - started).total_seconds()))
    durations.sort(key=lambda d: (-d.seconds, d.task_id))
    return durations[:n]


class CacheEffectiveness(BaseModel):
    """Prompt-cache hit-rate figure for one task (or a run-wide total)."""

    cache_read_tokens: int
    cache_creation_tokens: int
    uncached_input_tokens: int
    # None when there is no input at all to compute a rate over (the zero-denominator case) --
    # distinct from a genuine 0.0 hit rate (input existed, none of it was cached), so a
    # dashboard/report can render "n/a" rather than a misleading "0%".
    hit_rate: float | None = None


def _effectiveness(
    cache_read: int, cache_creation: int, uncached_input: int
) -> CacheEffectiveness:
    denominator = cache_read + cache_creation + uncached_input
    hit_rate = (cache_read / denominator) if denominator > 0 else None
    return CacheEffectiveness(
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        uncached_input_tokens=uncached_input,
        hit_rate=hit_rate,
    )


def cache_effectiveness(ts: TaskRunState) -> CacheEffectiveness:
    """Cache hit-rate for one task, from its already-persisted cumulative usage (E-9h3m7k)."""
    return _effectiveness(
        cache_read=ts.cumulative_cache_read_input_tokens,
        cache_creation=ts.cumulative_cache_creation_input_tokens,
        uncached_input=ts.cumulative_input_tokens,
    )


def run_cache_effectiveness(totals: RunUsageTotals) -> CacheEffectiveness:
    """Run-wide cache hit-rate, from `models.py::compute_run_usage_totals`'s output."""
    return _effectiveness(
        cache_read=totals.cache_read_input_tokens,
        cache_creation=totals.cache_creation_input_tokens,
        uncached_input=totals.input_tokens,
    )
