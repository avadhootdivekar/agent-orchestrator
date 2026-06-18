"""Budget manager — total + rate-window token enforcement (T-7kp8iv).

DefaultBudgetManager is a pure, injectable meter. It mutates the passed-in
BudgetCounters object, uses an injected clock for all time math (NFR-2),
and NEVER sleeps, persists, or logs. The engine owns those concerns (NFR-6 / ADR-BUD-002).

Invariants:
- Total checked before rate (FR-3 precedence).
- Tumbling window keyed by window_start_epoch in BudgetCounters (ADR-BUD-003).
- reconcile() is idempotent — reconciled_tasks guard prevents double-charge (NFR-3).
- reverse_estimate() undoes a charged estimate before a task is re-run on resume (R2/NFR-3).
- on_provider_429() feeds the same BudgetDecision path as gate() (ADR-BUD-005).
- No magic literals — all constants come from BudgetSpec / models constants (NFR-7).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from .models import DEFAULT_429_BACKOFF_SECONDS, BudgetCounters, BudgetSpec


class BudgetDecision(BaseModel):
    """Gate/429 decision returned by BudgetManager."""

    admit: bool
    blocked_by: Literal["total", "rate"] | None = None
    next_available_epoch: float | None = None  # None for total-cap (no reset within run)


class BudgetManager(ABC):
    """Pluggable token budget manager ABC (NFR-6)."""

    @abstractmethod
    def gate(self, task_id: str, estimate: int, counters: BudgetCounters) -> BudgetDecision:
        """Check whether *estimate* tokens can be admitted. Does NOT charge."""
        ...

    @abstractmethod
    def charge_estimate(self, task_id: str, estimate: int, counters: BudgetCounters) -> None:
        """Record *estimate* as in-flight for *task_id* and add to totals."""
        ...

    @abstractmethod
    def reconcile(self, task_id: str, actual: int, counters: BudgetCounters) -> None:
        """Replace the charged estimate with the actual token count. Idempotent."""
        ...

    @abstractmethod
    def reverse_estimate(self, task_id: str, counters: BudgetCounters) -> None:
        """Undo a charged estimate for *task_id* (used on resume or 429 re-run, NFR-3)."""
        ...

    @abstractmethod
    def on_provider_429(
        self, retry_after_epoch: float | None, counters: BudgetCounters
    ) -> BudgetDecision:
        """Return a BudgetDecision for a provider 429 signal (FR-8, ADR-BUD-005)."""
        ...


class DefaultBudgetManager(BudgetManager):
    """Default total + rate-window budget manager.

    Parameters
    ----------
    spec:
        Effective BudgetSpec (total_tokens, rate, on_exhaustion).
    clock:
        Injected callable returning the current UTC datetime (NFR-2).
    """

    def __init__(self, spec: BudgetSpec, clock: Callable[[], datetime]) -> None:
        self._spec = spec
        self._clock = clock

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _now_epoch(self) -> float:
        return self._clock().timestamp()

    def _window_seconds(self) -> int:
        """Return window duration in seconds from spec.rate."""
        assert self._spec.rate is not None
        return self._spec.rate.seconds()

    def _roll_window_if_needed(self, counters: BudgetCounters) -> None:
        """Initialise or roll the tumbling rate window if now >= start + window_seconds."""
        if self._spec.rate is None:
            return
        now = self._now_epoch()
        w = self._window_seconds()
        if counters.window_start_epoch is None:
            counters.window_start_epoch = now
            counters.window_consumed_tokens = 0
        elif now >= counters.window_start_epoch + w:
            # Window has expired — roll to now
            counters.window_start_epoch = now
            counters.window_consumed_tokens = 0

    # ------------------------------------------------------------------
    # Public ABC implementation
    # ------------------------------------------------------------------

    def gate(self, task_id: str, estimate: int, counters: BudgetCounters) -> BudgetDecision:
        """Admit or block, checking total first then rate (FR-3 precedence).

        Does NOT mutate counters — call charge_estimate() separately on admit.
        """
        self._roll_window_if_needed(counters)

        # Check total cap first (FR-3 / FR-1)
        if self._spec.total_tokens is not None:
            if counters.consumed_tokens + estimate > self._spec.total_tokens:
                return BudgetDecision(
                    admit=False,
                    blocked_by="total",
                    next_available_epoch=None,  # total has no reset within this run
                )

        # Check rate cap (FR-2)
        if self._spec.rate is not None:
            if counters.window_consumed_tokens + estimate > self._spec.rate.tokens:
                assert counters.window_start_epoch is not None
                return BudgetDecision(
                    admit=False,
                    blocked_by="rate",
                    next_available_epoch=counters.window_start_epoch + self._window_seconds(),
                )

        return BudgetDecision(admit=True)

    def charge_estimate(self, task_id: str, estimate: int, counters: BudgetCounters) -> None:
        """Record estimate as in-flight and add to totals (FR-4 — pessimistic pre-charge)."""
        counters.charged_estimate[task_id] = estimate
        counters.consumed_tokens += estimate
        counters.window_consumed_tokens += estimate

    def reconcile(self, task_id: str, actual: int, counters: BudgetCounters) -> None:
        """Replace the charged estimate with actuals. Idempotent (NFR-3 / FR-4).

        If task_id is already in reconciled_tasks, this is a no-op to prevent
        double-charge on repeated calls.
        """
        if task_id in counters.reconciled_tasks:
            return  # idempotent guard (NFR-3)

        est = counters.charged_estimate.pop(task_id, 0)
        delta = actual - est
        counters.consumed_tokens += delta
        counters.window_consumed_tokens += delta
        counters.reconciled_tasks.append(task_id)

    def reverse_estimate(self, task_id: str, counters: BudgetCounters) -> None:
        """Undo a charged estimate (used on 429 re-run or resume double-charge guard, R2/NFR-3).

        If task_id has no charged estimate (already reconciled or never charged), this is a no-op.
        """
        if task_id not in counters.charged_estimate:
            return
        est = counters.charged_estimate.pop(task_id)
        counters.consumed_tokens -= est
        counters.window_consumed_tokens -= est

    def on_provider_429(
        self, retry_after_epoch: float | None, counters: BudgetCounters
    ) -> BudgetDecision:
        """Handle provider 429 signal (FR-8, ADR-BUD-005).

        Returns a BudgetDecision with next_available_epoch set from the retry-after header,
        or now + DEFAULT_429_BACKOFF_SECONDS if no retry-after is provided.
        """
        if retry_after_epoch is not None:
            next_avail = retry_after_epoch
        else:
            next_avail = self._now_epoch() + DEFAULT_429_BACKOFF_SECONDS
        return BudgetDecision(
            admit=False,
            blocked_by="rate",
            next_available_epoch=next_avail,
        )
