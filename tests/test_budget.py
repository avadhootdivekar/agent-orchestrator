"""Unit tests for budget.py — DefaultBudgetManager (T-7kp8iv).

All tests use a fixed clock (NFR-2) so assertions on time-derived fields are exact.
Edge cases covered: total block, rate block, window roll, charge+reconcile net,
reconcile idempotency, reverse_estimate, 429 paths, no-limit passthrough.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_orchestrator.budget import DefaultBudgetManager
from agent_orchestrator.models import (
    DEFAULT_429_BACKOFF_SECONDS,
    BudgetCounters,
    BudgetSpec,
    RateLimit,
)

# ---------------------------------------------------------------------------
# Fixed-clock helpers
# ---------------------------------------------------------------------------

# Arbitrary epoch chosen to keep arithmetic readable: 2026-01-01 00:00:00 UTC
_BASE_EPOCH: float = datetime(2026, 1, 1, tzinfo=UTC).timestamp()


def _clock_at(offset_seconds: float = 0.0):
    """Return a clock callable that always returns _BASE_EPOCH + offset_seconds."""
    target = datetime.fromtimestamp(_BASE_EPOCH + offset_seconds, tz=UTC)
    return lambda: target


def _make_rate_spec(tokens: int = 500, window_seconds: int = 60) -> BudgetSpec:
    return BudgetSpec(rate=RateLimit(tokens=tokens, window_seconds=window_seconds))


def _make_total_spec(total: int = 1000) -> BudgetSpec:
    return BudgetSpec(total_tokens=total)


def _make_total_and_rate_spec(
    total: int = 1000, tokens: int = 500, window_seconds: int = 60
) -> BudgetSpec:
    return BudgetSpec(
        total_tokens=total,
        rate=RateLimit(tokens=tokens, window_seconds=window_seconds),
    )


# ---------------------------------------------------------------------------
# AC-1: total cap blocks when consumed + estimate > total
# ---------------------------------------------------------------------------


def test_gate_total_block():
    """AC-1: consumed=900 + estimate=200 > total=1000 → blocked_by=total."""
    spec = _make_total_spec(total=1000)
    mgr = DefaultBudgetManager(spec, _clock_at())
    counters = BudgetCounters(consumed_tokens=900)

    decision = mgr.gate("t1", 200, counters)

    assert decision.admit is False
    assert decision.blocked_by == "total"
    assert decision.next_available_epoch is None  # total has no reset epoch


# ---------------------------------------------------------------------------
# AC-2: total cap admits when consumed + estimate <= total
# ---------------------------------------------------------------------------


def test_gate_total_admit():
    """AC-2: consumed=900 + estimate=50 <= total=1000 → admitted."""
    spec = _make_total_spec(total=1000)
    mgr = DefaultBudgetManager(spec, _clock_at())
    counters = BudgetCounters(consumed_tokens=900)

    decision = mgr.gate("t1", 50, counters)

    assert decision.admit is True
    assert decision.blocked_by is None


# ---------------------------------------------------------------------------
# AC-3: rate cap blocks within the current window
# ---------------------------------------------------------------------------


def test_gate_rate_block_within_window():
    """AC-3: window_consumed=400 + estimate=200 > rate=500 → blocked_by=rate, next=T+60."""
    spec = _make_rate_spec(tokens=500, window_seconds=60)
    mgr = DefaultBudgetManager(spec, _clock_at(offset_seconds=10))  # now = T+10
    counters = BudgetCounters(
        window_start_epoch=_BASE_EPOCH,
        window_consumed_tokens=400,
    )

    decision = mgr.gate("t1", 200, counters)

    assert decision.admit is False
    assert decision.blocked_by == "rate"
    # next_available = window_start (T) + window_seconds (60) = T+60
    assert decision.next_available_epoch == pytest.approx(_BASE_EPOCH + 60)


# ---------------------------------------------------------------------------
# AC-4: rate window rolls when clock is past window end
# ---------------------------------------------------------------------------


def test_gate_rate_window_rolls_and_admits():
    """AC-4: clock=T+61 → window rolls to T+61; estimate=200 admitted (fresh window)."""
    spec = _make_rate_spec(tokens=500, window_seconds=60)
    mgr = DefaultBudgetManager(spec, _clock_at(offset_seconds=61))  # now = T+61
    counters = BudgetCounters(
        window_start_epoch=_BASE_EPOCH,  # old window started at T
        window_consumed_tokens=400,
    )

    decision = mgr.gate("t1", 200, counters)

    assert decision.admit is True
    # Window must have rolled: new start = T+61, consumed reset to 0
    assert counters.window_start_epoch == pytest.approx(_BASE_EPOCH + 61)
    assert counters.window_consumed_tokens == 0


def test_gate_rate_window_rolls_at_exact_boundary():
    """Edge case: now == window_start + window_seconds ('>=' boundary, not just '>')."""
    spec = _make_rate_spec(tokens=500, window_seconds=60)
    mgr = DefaultBudgetManager(spec, _clock_at(offset_seconds=60))  # now = T+60 exactly
    counters = BudgetCounters(
        window_start_epoch=_BASE_EPOCH,
        window_consumed_tokens=400,
    )

    decision = mgr.gate("t1", 200, counters)

    assert decision.admit is True
    assert counters.window_start_epoch == pytest.approx(_BASE_EPOCH + 60)
    assert counters.window_consumed_tokens == 0


# ---------------------------------------------------------------------------
# AC-5: charge then reconcile — net consumed = actual, not estimate+actual
# ---------------------------------------------------------------------------


def test_charge_then_reconcile_net_is_actual():
    """AC-5: charge(520) then reconcile(300) → consumed_tokens net change = +300."""
    spec = _make_rate_spec()
    mgr = DefaultBudgetManager(spec, _clock_at())
    counters = BudgetCounters()

    mgr.charge_estimate("t1", 520, counters)
    assert counters.consumed_tokens == 520
    assert counters.charged_estimate == {"t1": 520}

    mgr.reconcile("t1", 300, counters)

    # Net change from zero = +300 (not +820)
    assert counters.consumed_tokens == 300
    assert "t1" in counters.reconciled_tasks
    assert "t1" not in counters.charged_estimate


def test_reconcile_window_consumed_adjusted():
    """reconcile() also adjusts window_consumed_tokens by the same delta."""
    spec = _make_rate_spec(tokens=1000, window_seconds=60)
    mgr = DefaultBudgetManager(spec, _clock_at())
    counters = BudgetCounters()

    mgr.charge_estimate("t1", 520, counters)
    mgr.reconcile("t1", 300, counters)

    assert counters.window_consumed_tokens == 300


# ---------------------------------------------------------------------------
# AC-6: reconcile is idempotent — second call is a no-op
# ---------------------------------------------------------------------------


def test_reconcile_idempotent():
    """AC-6: calling reconcile twice for the same task makes no further change."""
    spec = _make_rate_spec()
    mgr = DefaultBudgetManager(spec, _clock_at())
    counters = BudgetCounters()

    mgr.charge_estimate("t1", 520, counters)
    mgr.reconcile("t1", 300, counters)

    # Snapshot state after first reconcile
    consumed_after_first = counters.consumed_tokens
    window_after_first = counters.window_consumed_tokens
    reconciled_len = len(counters.reconciled_tasks)

    # Second reconcile must be a no-op
    mgr.reconcile("t1", 300, counters)

    assert counters.consumed_tokens == consumed_after_first
    assert counters.window_consumed_tokens == window_after_first
    assert len(counters.reconciled_tasks) == reconciled_len


# ---------------------------------------------------------------------------
# AC-7: on_provider_429 — with and without retry_after_epoch
# ---------------------------------------------------------------------------


def test_on_provider_429_with_retry_after():
    """AC-7a: explicit retry_after_epoch is returned directly."""
    spec = _make_rate_spec()
    mgr = DefaultBudgetManager(spec, _clock_at())
    counters = BudgetCounters()
    retry_epoch = _BASE_EPOCH + 120.0

    decision = mgr.on_provider_429(retry_after_epoch=retry_epoch, counters=counters)

    assert decision.admit is False
    assert decision.blocked_by == "rate"
    assert decision.next_available_epoch == pytest.approx(retry_epoch)


def test_on_provider_429_without_retry_after():
    """AC-7b: None retry_after → next_available = now + DEFAULT_429_BACKOFF_SECONDS."""
    spec = _make_rate_spec()
    mgr = DefaultBudgetManager(spec, _clock_at())
    counters = BudgetCounters()

    decision = mgr.on_provider_429(retry_after_epoch=None, counters=counters)

    assert decision.admit is False
    assert decision.blocked_by == "rate"
    assert decision.next_available_epoch == pytest.approx(_BASE_EPOCH + DEFAULT_429_BACKOFF_SECONDS)


# ---------------------------------------------------------------------------
# AC-8: no-limit passthrough (total=None, rate=None)
# ---------------------------------------------------------------------------


def test_gate_no_limits_always_admits():
    """AC-8: BudgetSpec with no total and no rate → gate always returns admit=True."""
    spec = BudgetSpec()  # total_tokens=None, rate=None
    mgr = DefaultBudgetManager(spec, _clock_at())
    counters = BudgetCounters(consumed_tokens=999_999)

    decision = mgr.gate("t1", 1_000_000, counters)

    assert decision.admit is True
    assert decision.blocked_by is None


# ---------------------------------------------------------------------------
# AC-9 / NFR-2: injected clock used exclusively — exact epoch assertions
# ---------------------------------------------------------------------------


def test_fixed_clock_used_for_next_available_epoch():
    """NFR-2: next_available_epoch derived purely from injected clock (no datetime.now)."""
    spec = _make_rate_spec(tokens=100, window_seconds=300)
    # Clock pinned to T+50 (inside window)
    mgr = DefaultBudgetManager(spec, _clock_at(offset_seconds=50))
    counters = BudgetCounters(
        window_start_epoch=_BASE_EPOCH,
        window_consumed_tokens=90,
    )

    decision = mgr.gate("t1", 20, counters)  # 90+20 > 100 → blocked

    assert decision.admit is False
    assert decision.next_available_epoch == pytest.approx(_BASE_EPOCH + 300)


def test_fixed_clock_used_for_429_backoff():
    """NFR-2: 429 backoff epoch is exactly clock().timestamp() + DEFAULT_429_BACKOFF_SECONDS."""
    spec = _make_rate_spec()
    clock_offset = 500.0
    mgr = DefaultBudgetManager(spec, _clock_at(offset_seconds=clock_offset))
    counters = BudgetCounters()

    decision = mgr.on_provider_429(retry_after_epoch=None, counters=counters)

    assert decision.next_available_epoch == pytest.approx(
        _BASE_EPOCH + clock_offset + DEFAULT_429_BACKOFF_SECONDS
    )


# ---------------------------------------------------------------------------
# reverse_estimate: undoes an in-flight charge
# ---------------------------------------------------------------------------


def test_reverse_estimate_removes_charged():
    """reverse_estimate() subtracts the charged estimate and removes it from charged_estimate."""
    spec = _make_rate_spec()
    mgr = DefaultBudgetManager(spec, _clock_at())
    counters = BudgetCounters()

    mgr.charge_estimate("t1", 400, counters)
    assert counters.consumed_tokens == 400

    mgr.reverse_estimate("t1", counters)

    assert counters.consumed_tokens == 0
    assert counters.window_consumed_tokens == 0
    assert "t1" not in counters.charged_estimate


def test_reverse_estimate_noop_when_not_charged():
    """reverse_estimate() is a no-op for a task that was never charged or already reconciled."""
    spec = _make_rate_spec()
    mgr = DefaultBudgetManager(spec, _clock_at())
    counters = BudgetCounters(consumed_tokens=100, window_consumed_tokens=100)

    # No prior charge for "t1"
    mgr.reverse_estimate("t1", counters)

    assert counters.consumed_tokens == 100
    assert counters.window_consumed_tokens == 100


# ---------------------------------------------------------------------------
# Precedence (FR-3): total checked before rate
# ---------------------------------------------------------------------------


def test_total_takes_precedence_over_rate():
    """FR-3: when both total AND rate would block, blocked_by=total (first check wins)."""
    spec = _make_total_and_rate_spec(total=1000, tokens=500, window_seconds=60)
    mgr = DefaultBudgetManager(spec, _clock_at(offset_seconds=10))
    # Both limits would block
    counters = BudgetCounters(
        consumed_tokens=900,  # 900+200 > 1000 (total block)
        window_start_epoch=_BASE_EPOCH,
        window_consumed_tokens=400,  # 400+200 > 500 (rate block too)
    )

    decision = mgr.gate("t1", 200, counters)

    assert decision.admit is False
    assert decision.blocked_by == "total"
    assert decision.next_available_epoch is None


# ---------------------------------------------------------------------------
# Window initialisation when window_start_epoch is None
# ---------------------------------------------------------------------------


def test_window_initialised_on_first_gate():
    """_roll_window_if_needed initialises window_start_epoch when it is None."""
    spec = _make_rate_spec(tokens=500, window_seconds=60)
    mgr = DefaultBudgetManager(spec, _clock_at(offset_seconds=5))
    counters = BudgetCounters()  # window_start_epoch=None

    decision = mgr.gate("t1", 100, counters)

    assert decision.admit is True
    assert counters.window_start_epoch == pytest.approx(_BASE_EPOCH + 5)
    assert counters.window_consumed_tokens == 0


# ---------------------------------------------------------------------------
# charge_estimate does not call gate — both must be called explicitly
# ---------------------------------------------------------------------------


def test_charge_estimate_adds_to_both_totals():
    """charge_estimate() adds estimate to consumed_tokens AND window_consumed_tokens."""
    spec = _make_rate_spec()
    mgr = DefaultBudgetManager(spec, _clock_at())
    counters = BudgetCounters()

    mgr.charge_estimate("t1", 300, counters)

    assert counters.consumed_tokens == 300
    assert counters.window_consumed_tokens == 300
    assert counters.charged_estimate["t1"] == 300
