"""Budget manager — total + rate-window token enforcement (T-7kp8iv).

DefaultBudgetManager is a pure, injectable meter. It mutates the passed-in
BudgetCounters object, uses an injected clock for all time math (NFR-2),
and NEVER sleeps, persists, or logs. The engine owns those concerns (NFR-6 / ADR-BUD-002).

Invariants:
- Total checked before rate (FR-3 precedence).
- Tumbling window keyed by window_start_epoch in BudgetCounters (ADR-BUD-003).
- reconcile() is idempotent per DISPATCH CYCLE — ``reconciled_cycles`` (keyed
  ``"<task_id>#<cycle>"`` via `cycle_key`) guards double-charge (NFR-3, R-1b/E-Wk9Tz3
  T-Ac6Vd9). A T2/T3 conflict-ladder redispatch (or self-heal/quota/429 requeue) is a
  NEW cycle of the SAME task, so it must be independently gateable, chargeable and
  reconcilable rather than silently latched out by the first cycle's reconcile() --
  `DefaultBudgetManager.reconcile()` used to latch one-shot per bare `task_id`
  (`reconciled_tasks`), which made every later redispatch's reconcile a no-op and hid
  a conflicting task's real spend from `total_tokens`/rate-window gating (the exact
  "spend is bounded for free" gap D9/ADR-0013 assumed away). `reconciled_tasks` is
  left in place, populated once per task_id on its first reconcile (deduped), purely
  for backward compatibility with any reader of an older `state.json` shape -- it is
  never consulted for the idempotency guard itself.
- reverse_estimate() undoes a charged estimate before a task is re-run on resume (R2/NFR-3),
  now scoped to the specific dispatch cycle that charged it.
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

# R-1b (E-Wk9Tz3 T-Ac6Vd9): ledger key separator between a task id and its dispatch
# cycle. Task ids are schema-constrained (`^[a-z0-9][a-z0-9-_]*$`, see the HLD's ref
# sanitization note) and never contain "#", so this is unambiguous and reversible.
_CYCLE_KEY_SEP = "#"


def cycle_key(task_id: str, cycle: int) -> str:
    """Ledger key for one dispatch cycle of a task: ``"<task_id>#<cycle>"``.

    Every independently-charged/reconciled dispatch (the original attempt, a T2/T3
    conflict-ladder redispatch, a self-heal retry, a quota/429 requeue) gets its own
    key so a later cycle's reconcile is never silently latched out by an earlier
    cycle's (R-1b) -- see the module docstring. Exported so `engine.py` can look up
    a *specific* cycle's stale `charged_estimate` entry directly (the resume
    double-charge guard) without a matching `BudgetManager` ABC method for a plain
    membership check.
    """
    return f"{task_id}{_CYCLE_KEY_SEP}{cycle}"


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
    def charge_estimate(
        self, task_id: str, estimate: int, counters: BudgetCounters, cycle: int = 1
    ) -> None:
        """Record *estimate* as in-flight for *task_id*'s dispatch *cycle* and add to totals.

        *cycle* defaults to ``1`` so an existing caller/third-party implementation
        keeps working unchanged (R-1b, E-Wk9Tz3 T-Ac6Vd9) -- pass
        ``TaskRunState.dispatch_cycle`` to make a redispatch independently chargeable.
        """
        ...

    @abstractmethod
    def reconcile(
        self, task_id: str, actual: int, counters: BudgetCounters, cycle: int = 1
    ) -> None:
        """Replace *cycle*'s charged estimate with the actual token count. Idempotent per cycle."""
        ...

    @abstractmethod
    def reverse_estimate(self, task_id: str, counters: BudgetCounters, cycle: int = 1) -> None:
        """Undo *cycle*'s charged estimate for *task_id* (used on resume or 429 re-run, NFR-3)."""
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

    def charge_estimate(
        self, task_id: str, estimate: int, counters: BudgetCounters, cycle: int = 1
    ) -> None:
        """Record estimate as in-flight and add to totals (FR-4 — pessimistic pre-charge).

        Keyed by (*task_id*, *cycle*) (R-1b) so a redispatch of the same task never
        overwrites an earlier, still-outstanding cycle's own charged_estimate entry.
        """
        counters.charged_estimate[cycle_key(task_id, cycle)] = estimate
        counters.consumed_tokens += estimate
        counters.window_consumed_tokens += estimate

    def reconcile(
        self, task_id: str, actual: int, counters: BudgetCounters, cycle: int = 1
    ) -> None:
        """Replace *cycle*'s charged estimate with actuals. Idempotent per cycle (NFR-3/FR-4/R-1b).

        If this (task_id, cycle) is already in reconciled_cycles, this is a no-op to
        prevent double-charge on repeated calls -- but, unlike the pre-R-1b guard, a
        LATER cycle of the same task_id (a T2/T3 conflict-ladder redispatch, or a
        self-heal/quota/429 requeue) is NOT blocked by an earlier cycle's own
        reconcile, which is exactly the bug this ticket fixes: D9/ADR-0013's "conflict
        spend is bounded for free" claim requires every redispatch's real spend to
        reach `consumed_tokens`/the rate window, not just the first one.
        """
        key = cycle_key(task_id, cycle)
        if key in counters.reconciled_cycles:
            return  # idempotent guard, scoped to this cycle (NFR-3 / R-1b)

        est = counters.charged_estimate.pop(key, 0)
        delta = actual - est
        counters.consumed_tokens += delta
        counters.window_consumed_tokens += delta
        counters.reconciled_cycles.append(key)
        # Backward-compat dual-write: `reconciled_tasks` (pre-R-1b field) is left
        # exactly as populated as it always was for a single-cycle task (appended once,
        # deduped) so an older `state.json` reader / a task that never requeues sees
        # byte-identical `reconciled_tasks` contents. It is never read by this class's
        # own idempotency check (that's `reconciled_cycles`' job now).
        if task_id not in counters.reconciled_tasks:
            counters.reconciled_tasks.append(task_id)

    def reverse_estimate(self, task_id: str, counters: BudgetCounters, cycle: int = 1) -> None:
        """Undo *cycle*'s charged estimate (429 re-run / resume double-charge guard, R2/NFR-3/R-1b).

        If (task_id, cycle) has no charged estimate (already reconciled or never
        charged), this is a no-op.
        """
        key = cycle_key(task_id, cycle)
        if key not in counters.charged_estimate:
            return
        est = counters.charged_estimate.pop(key)
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
