"""Read-side helpers for the result-cache records (E-Rc4Hk8, HLD 8.8.1, ADR-0019 D9 / D14 / D35).

Pure and O(tasks). Depends only on `models` and `constants`: no I/O, no clock, no store, no
engine. Every function filters through `models.is_current_result_cache_record`, so `status.json`,
the summary line, `ao report-usage`, `ao report-outcomes` and the dashboard can never disagree
about which records describe a task's current incarnation.

CALLERS import this module lazily, inside the function and only when `state.result_cache` is
non-empty (D9): a run the cache never touched never loads it.

Floats are rounded (`_USD_DIGITS`, `_SECONDS_DIGITS`) after summing, so `status.json` never shows
binary-float noise such as 1.2345000000000002.
"""

from __future__ import annotations

from collections import Counter
from typing import cast

from agent_orchestrator.models import (
    RESULT_CACHE_HIT,
    RESULT_CACHE_INELIGIBLE,
    RESULT_CACHE_MISS,
    RESULT_CACHE_WOULD_HIT,
    ResultCacheRecord,
    RunState,
    is_current_result_cache_record,
)

_USD_DIGITS = 6
_SECONDS_DIGITS = 3
UNKNOWN_REASON = "unknown"  # a miss / store-skip record that carries no reason string

# The brief's exact fields (D35), in the order the HLD documents them.
BRIEF_FIELDS = ("hit", "key", "saved_cost_usd", "saved_tokens", "saved_seconds")


def current_records(state: RunState) -> dict[str, ResultCacheRecord]:
    """Records for which is_current_result_cache_record(rec, state.tasks.get(tid)) holds."""
    return {
        tid: rec
        for tid, rec in state.result_cache.items()
        if is_current_result_cache_record(rec, state.tasks.get(tid))
    }


def current_hit(state: RunState, tid: str) -> bool:
    """A current record with outcome == hit."""
    rec = state.result_cache.get(tid)
    return (
        rec is not None
        and rec.outcome == RESULT_CACHE_HIT
        and is_current_result_cache_record(rec, state.tasks.get(tid))
    )


def task_view(rec: ResultCacheRecord) -> dict[str, object]:
    """The per-task object (§13.5): exactly hit, key, saved_cost_usd, saved_tokens,
    saved_seconds, plus the additive fields."""
    return {
        "hit": rec.hit,
        "key": rec.key,
        "saved_cost_usd": rec.saved_cost_usd,
        "saved_tokens": rec.saved_tokens,
        "saved_seconds": rec.saved_seconds,
        # Additive (D35).
        "outcome": rec.outcome,
        "mode": rec.mode,
        "mode_source": rec.mode_source,
        "reason": rec.reason,
        "reason_detail": rec.reason_detail,
        "stored": rec.stored,
        "store_reason": rec.store_reason,
        "saved_input_tokens": rec.saved_input_tokens,
        "saved_output_tokens": rec.saved_output_tokens,
        "source_run_id": rec.source_run_id,
    }


def _run_block(recs: dict[str, ResultCacheRecord]) -> dict[str, object] | None:
    if not recs:
        return None
    hits = [r for r in recs.values() if r.outcome == RESULT_CACHE_HIT]
    would = [r for r in recs.values() if r.outcome == RESULT_CACHE_WOULD_HIT]
    misses = sum(r.outcome == RESULT_CACHE_MISS for r in recs.values())
    return {
        "hits": len(hits),
        "saved_cost_usd": round(sum(r.saved_cost_usd for r in hits), _USD_DIGITS),
        "saved_tokens": sum(r.saved_tokens for r in hits),
        "saved_seconds": round(sum(r.saved_seconds for r in hits), _SECONDS_DIGITS),
        "would_hits": len(would),
        "misses": misses,
        "ineligible": sum(r.outcome == RESULT_CACHE_INELIGIBLE for r in recs.values()),
        "stored": sum(r.stored for r in recs.values()),
        "lookups": len(hits) + len(would) + misses,
        "saved_input_tokens": sum(r.saved_input_tokens for r in hits),
        "saved_output_tokens": sum(r.saved_output_tokens for r in hits),
        "avoidable_cost_usd": round(sum(r.saved_cost_usd for r in would), _USD_DIGITS),
    }


def run_block(state: RunState) -> dict[str, object] | None:
    """The run-level object (§13.5); None when there are no current records."""
    return _run_block(current_records(state))


def result_cache_status_fields(
    state: RunState,
) -> tuple[dict[str, dict[str, object]], dict[str, object] | None]:
    """(per-task views by task id, run block); ({}, None) when nothing is current."""
    recs = current_records(state)
    return {tid: task_view(rec) for tid, rec in recs.items()}, _run_block(recs)


def format_summary_line(block: dict[str, object] | None) -> str | None:
    """The §8.8.3 line; None for None."""
    if block is None:
        return None
    return (
        f"Result cache: hits={block['hits']} "
        f"(saved ~${cast(float, block['saved_cost_usd']):.4f} est., "
        f"~{block['saved_tokens']} tokens, ~{cast(float, block['saved_seconds']):.0f}s) "
        f"would_hits={block['would_hits']} misses={block['misses']} "
        f"stored={block['stored']} ineligible={block['ineligible']}"
    )


def usage_counters(state: RunState) -> dict[str, object] | None:
    """One run's contribution to the cross-run `result_cache` usage object (§13.6).

    The additive group-by dicts (`miss_reasons`, `store_skip_reasons`) feed the G0 protocol:
    they say why lookups missed and why successful results were not stored. None when the run
    has no current record. The floats are the per-run rounded sums; callers re-sum them.
    """
    recs = current_records(state)
    if not recs:
        return None
    block = _run_block(recs)
    assert block is not None  # recs is non-empty
    miss_reasons = Counter(
        r.reason or UNKNOWN_REASON for r in recs.values() if r.outcome == RESULT_CACHE_MISS
    )
    skip_reasons = Counter(r.store_reason for r in recs.values() if r.store_reason)
    return {
        "hits": block["hits"],
        "saved_cost_usd": block["saved_cost_usd"],
        "saved_tokens": block["saved_tokens"],
        "saved_seconds": block["saved_seconds"],
        "lookups": block["lookups"],
        "would_hits": block["would_hits"],
        "misses": block["misses"],
        "ineligible": block["ineligible"],
        "avoidable_cost_usd": block["avoidable_cost_usd"],
        "miss_reasons": dict(sorted(miss_reasons.items())),
        "store_skip_reasons": dict(sorted(skip_reasons.items())),
    }
