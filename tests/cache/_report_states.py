"""Shared builders and the HLD 13.5 / 13.6 schemas for the T-eyn5UG read-side tests.

The four-task state returned by `mixed_state` is the one fixture every helper test reuses:
a CURRENT hit, a STALE hit (cycle mismatch), a STALE hit (`ended_at` mismatch) and a CURRENT miss.
"""

from __future__ import annotations

from typing import Any

from agent_orchestrator.models import (
    RESULT_CACHE_HIT,
    RESULT_CACHE_INELIGIBLE,
    RESULT_CACHE_MISS,
    RESULT_CACHE_WOULD_HIT,
    ResultCacheRecord,
    RunState,
    TaskRunState,
)

KEY = "6646469e94a695fe1a994d35f54ca74e007262911255552b03c54ce2e5d0319f"
OTHER_KEY = "b" * 64
STAMP = "2026-10-05T10:00:00+00:00"
ENDED = "2026-10-05T10:00:05+00:00"

# HLD 13.5 and 13.6, verbatim (the `$id` / comment keys are annotations only).
RESULT_CACHE_TASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "hit",
        "key",
        "saved_cost_usd",
        "saved_tokens",
        "saved_seconds",
        "outcome",
        "mode",
        "stored",
    ],
    "properties": {
        "hit": {"type": "boolean"},
        "key": {"type": ["string", "null"], "pattern": "^[0-9a-f]{64}$"},
        "saved_cost_usd": {"type": "number"},
        "saved_tokens": {"type": "integer"},
        "saved_seconds": {"type": "number"},
        "outcome": {"type": "string"},
        "mode": {"type": "string"},
        "mode_source": {"type": "string"},
        "reason": {"type": ["string", "null"]},
        "reason_detail": {"type": ["string", "null"]},
        "stored": {"type": "boolean"},
        "store_reason": {"type": ["string", "null"]},
        "saved_input_tokens": {"type": "integer"},
        "saved_output_tokens": {"type": "integer"},
        "source_run_id": {"type": ["string", "null"]},
    },
}
RESULT_CACHE_RUN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "hits",
        "saved_cost_usd",
        "saved_tokens",
        "saved_seconds",
        "would_hits",
        "misses",
        "ineligible",
        "stored",
        "lookups",
    ],
    "properties": {
        "hits": {"type": "integer"},
        "saved_cost_usd": {"type": "number"},
        "saved_tokens": {"type": "integer"},
        "saved_seconds": {"type": "number"},
        "would_hits": {"type": "integer"},
        "misses": {"type": "integer"},
        "ineligible": {"type": "integer"},
        "stored": {"type": "integer"},
        "lookups": {"type": "integer"},
        "saved_input_tokens": {"type": "integer"},
        "saved_output_tokens": {"type": "integer"},
        "avoidable_cost_usd": {"type": "number"},
    },
}
RESULT_CACHE_USAGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "hits",
        "saved_cost_usd",
        "saved_tokens",
        "saved_seconds",
        "lookups",
        "would_hits",
        "misses",
        "ineligible",
        "avoidable_cost_usd",
    ],
    "properties": {
        "hits": {"type": "integer"},
        "saved_cost_usd": {"type": "number"},
        "saved_tokens": {"type": "integer"},
        "saved_seconds": {"type": "number"},
        "lookups": {"type": "integer"},
        "would_hits": {"type": "integer"},
        "misses": {"type": "integer"},
        "ineligible": {"type": "integer"},
        "avoidable_cost_usd": {"type": "number"},
        "miss_reasons": {"type": "object", "additionalProperties": {"type": "integer"}},
        "store_skip_reasons": {"type": "object", "additionalProperties": {"type": "integer"}},
    },
}
TASK_VIEW_KEYS = {
    "hit",
    "key",
    "saved_cost_usd",
    "saved_tokens",
    "saved_seconds",
    "outcome",
    "mode",
    "mode_source",
    "reason",
    "reason_detail",
    "stored",
    "store_reason",
    "saved_input_tokens",
    "saved_output_tokens",
    "source_run_id",
}
BRIEF_KEYS = ["hit", "key", "saved_cost_usd", "saved_tokens", "saved_seconds"]


def rec(outcome: str = RESULT_CACHE_MISS, **kw: Any) -> ResultCacheRecord:
    base: dict[str, Any] = {
        "outcome": outcome,
        "mode": "on",
        "mode_source": "cli",
        "dispatch_cycle": 1,
        "at": STAMP,
        "key": KEY,
    }
    if outcome == RESULT_CACHE_MISS:
        base["reason"] = "not_found"
    if outcome == RESULT_CACHE_INELIGIBLE:
        base.update(key=None, reason="unknown_task_field", reason_detail="approval")
    if outcome == RESULT_CACHE_HIT:
        base["ended_at"] = ENDED
    base.update(kw)
    return ResultCacheRecord(**base)


def hit_rec(**kw: Any) -> ResultCacheRecord:
    base: dict[str, Any] = {
        "saved_cost_usd": 0.4123,
        "saved_input_tokens": 12000,
        "saved_output_tokens": 3400,
        "saved_seconds": 95.2,
        "source_run_id": "doc-pipeline-20261005T101450Z",
        "stored": False,
    }
    base.update(kw)
    return rec(RESULT_CACHE_HIT, **base)


def would_hit_rec(**kw: Any) -> ResultCacheRecord:
    base: dict[str, Any] = {
        "mode": "shadow",
        "saved_cost_usd": 0.5,
        "saved_input_tokens": 100,
        "saved_output_tokens": 50,
        "saved_seconds": 10.0,
    }
    base.update(kw)
    return rec(RESULT_CACHE_WOULD_HIT, **base)


def settled(cycle: int = 1, ended: str | None = ENDED, **kw: Any) -> TaskRunState:
    """A succeeded task on its `cycle`-th dispatch."""
    base: dict[str, Any] = {
        "status": "succeeded",
        "dispatch_cycle": cycle,
        "ended_at": ended,
        "agent": "writer",
        "model": "sonnet",
        "effort": "medium",
    }
    base.update(kw)
    return TaskRunState(**base)


def run_state(
    tasks: dict[str, TaskRunState],
    records: dict[str, ResultCacheRecord] | None = None,
    run_id: str = "r1",
) -> RunState:
    return RunState(
        run_id=run_id,
        workflow_id="wf",
        repo_set="rs",
        started_at=STAMP,
        updated_at=STAMP,
        tasks=tasks,
        result_cache=records or {},
    )


def mixed_state(run_id: str = "r1") -> RunState:
    """current hit / stale hit (cycle) / stale hit (ended_at) / current miss."""
    return run_state(
        {
            "hit": settled(),
            "stale_cycle": settled(cycle=2),
            "stale_ended": settled(ended="2026-10-05T11:00:00+00:00"),
            "miss": settled(),
        },
        {
            "hit": hit_rec(),
            "stale_cycle": hit_rec(saved_cost_usd=9.0),  # recorded for cycle 1, task is on 2
            "stale_ended": hit_rec(saved_cost_usd=7.0),  # ended_at no longer matches
            "miss": rec(RESULT_CACHE_MISS, stored=True),
        },
        run_id,
    )
