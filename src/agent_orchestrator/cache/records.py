"""`ResultCacheRecord` builders (HLD 8.6.3, ADR-0019 D14 / D35).

Builders only: they construct a record from a `LookupRequest` (+ a cache entry) and NEVER touch
`RunState` / `TaskRunState`; the engine is the only writer of `state.result_cache` (D12). Every
free-text value is clipped to the record model's bounds, so a hostile-but-valid cache entry can
never make record construction fail. `hit` and `saved_tokens` are derived by the record model.
"""

from __future__ import annotations

from agent_orchestrator.cache.constants import MAX_REASON_CHARS
from agent_orchestrator.cache.types import CacheEntry, LookupRequest, clip_text
from agent_orchestrator.models import (
    RESULT_CACHE_HIT,
    RESULT_CACHE_INELIGIBLE,
    RESULT_CACHE_MISS,
    RESULT_CACHE_WOULD_HIT,
    ResultCacheRecord,
)


def _short(text: str | None) -> str | None:
    """Clip a reason-like value to the record's `reason` bound."""
    return None if text is None else text[:MAX_REASON_CHARS]


def _entry_record(
    outcome: str,
    entry: CacheEntry,
    key: str,
    req: LookupRequest,
    mode: str,
    source: str,
    store_reason: str | None,
) -> ResultCacheRecord:
    usage = entry.usage
    return ResultCacheRecord(
        outcome=outcome,
        mode=mode,
        mode_source=source,
        key=key,
        dispatch_cycle=req.dispatch_cycle,
        at=req.now.isoformat(),
        ended_at=None,  # the engine fills it for a hit (D14)
        store_reason=_short(store_reason),
        saved_cost_usd=usage.cost_usd,
        saved_input_tokens=usage.input_tokens,
        saved_output_tokens=usage.output_tokens,
        saved_seconds=usage.duration_seconds,
        source_run_id=clip_text(entry.source.run_id),
    )


def make_hit_record(
    entry: CacheEntry, key: str, req: LookupRequest, mode: str, source: str
) -> ResultCacheRecord:
    """A restored hit; `saved_*` is copied from the entry's usage (D16)."""
    return _entry_record(RESULT_CACHE_HIT, entry, key, req, mode, source, None)


def make_would_hit_record(
    entry: CacheEntry,
    key: str,
    req: LookupRequest,
    mode: str,
    source: str,
    *,
    store_reason: str | None = None,
) -> ResultCacheRecord:
    """Shadow mode: a valid entry exists, nothing was restored."""
    return _entry_record(RESULT_CACHE_WOULD_HIT, entry, key, req, mode, source, store_reason)


def make_miss_record(
    req: LookupRequest,
    reason: str,
    key: str | None,
    mode: str,
    source: str,
    detail: str | None = None,
    *,
    store_reason: str | None = None,
) -> ResultCacheRecord:
    """An eligible lookup that was not served (`key` is None when no key could be built)."""
    return ResultCacheRecord(
        outcome=RESULT_CACHE_MISS,
        mode=mode,
        mode_source=source,
        reason=_short(reason),
        reason_detail=clip_text(detail),
        key=key,
        dispatch_cycle=req.dispatch_cycle,
        at=req.now.isoformat(),
        store_reason=_short(store_reason),
    )


def make_ineligible_record(
    req: LookupRequest, reason: str, detail: str | None, mode: str, source: str
) -> ResultCacheRecord:
    """The task opted in but is not cacheable; there is never a key."""
    return ResultCacheRecord(
        outcome=RESULT_CACHE_INELIGIBLE,
        mode=mode,
        mode_source=source,
        reason=_short(reason),
        reason_detail=clip_text(detail),
        key=None,
        dispatch_cycle=req.dispatch_cycle,
        at=req.now.isoformat(),
    )
