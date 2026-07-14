# TASK: T-7kp8iv-budget-manager-meter

## Metadata
- Task ID: `T-7kp8iv-budget-manager-meter`
- Epic ID: `E-j4gno6-token-budget-rate-limit`
- Owner: TODO
- Created: 2026-06-18
- Last Updated: 2026-06-18
- Status: Draft
- Estimate: < 3 days

## Requirements Mapping
- Requirement IDs: FR-1, FR-2, FR-3, FR-4, FR-6, FR-8, FR-9, NFR-2, NFR-3, NFR-6, NFR-7

## Description
The core meter. New module `src/agent_orchestrator/budget.py`:
- `BudgetDecision` model (`admit`, `blocked_by`, `next_available_epoch`).
- `BudgetManager(ABC)` with `gate`, `charge_estimate`, `reconcile`, `on_provider_429`.
- `DefaultBudgetManager(BudgetManager)` constructed with `(spec: BudgetSpec, clock: Callable[[], datetime])`.

`DefaultBudgetManager` operates **purely** on a passed-in `BudgetCounters` (it mutates
the counters object; the engine owns persistence). It uses the injected `clock` for all
time math (NFR-2). It does NOT sleep and does NOT touch RunState directly — pure,
unit-testable in isolation (NFR-6).

Enforcement logic:
- **Total** (FR-1): block if `counters.consumed_tokens + estimate > spec.total_tokens`. Total has no "next available" within the run → `next_available_epoch = None`.
- **Rate** (FR-2): tumbling window keyed by `counters.window_start_epoch`. On each gate, roll the window if `now >= window_start + window_seconds` (reset `window_start_epoch=now`, `window_consumed_tokens=0`). Block if `window_consumed_tokens + estimate > spec.rate.tokens`; `next_available_epoch = window_start_epoch + window_seconds`.
- **Precedence** (FR-3): check total first, then rate; both reasons computed for logging, `blocked_by` is the first that blocks.
- `charge_estimate`: record `charged_estimate[task_id]=estimate`, add to `consumed_tokens` and `window_consumed_tokens`.
- `reconcile`: replace the charged estimate with the actual — `delta = actual - charged_estimate[task_id]`; apply `delta` to `consumed_tokens` and `window_consumed_tokens`; move task to `reconciled_tasks`; drop from `charged_estimate`. (No-op / idempotent if already reconciled — FR-4 / NFR-3.)
- `reverse_estimate(task_id)` helper: subtract a stale un-reconciled estimate (used by engine on resume to avoid double-charge — R2).
- `on_provider_429`: return a `BudgetDecision(admit=False, blocked_by="rate", next_available_epoch=retry_after_epoch or now+DEFAULT_429_BACKOFF_SECONDS)` (FR-8).

## Acceptance Criteria
1. Given `total_tokens=1000`, `consumed=900`, estimate=200, When `gate`, Then `admit=False, blocked_by="total", next_available_epoch=None`.
2. Given `total_tokens=1000`, `consumed=900`, estimate=50, When `gate`, Then `admit=True`.
3. Given rate `{tokens:500, window_seconds:60}`, `window_start=T`, `window_consumed=400`, estimate=200, clock=`T+10s`, When `gate`, Then `admit=False, blocked_by="rate", next_available_epoch=T+60`.
4. Given the same rate config and clock=`T+61s` (past window end), When `gate`, Then the window rolls (`window_start=T+61`, `window_consumed=0`) and the task is admitted; counters reflect the new window.
5. Given a `charge_estimate(task,520)` then `reconcile(task, 300)`, Then `consumed_tokens` net change is `+300` (not `+820`) and `task ∈ reconciled_tasks`, `task ∉ charged_estimate`.
6. Given `reconcile` is called **twice** for the same task, Then the second call is a no-op (idempotent — no double subtract/add).
7. Given `on_provider_429(retry_after_epoch=R)`, Then it returns `admit=False, next_available_epoch=R`; given `retry_after_epoch=None`, Then `next_available_epoch = now + DEFAULT_429_BACKOFF_SECONDS`.
8. Given `total_tokens=None` and `rate=None`, Then `gate` always returns `admit=True` (no-limit passthrough — feature opt-in).
9. All time math uses the injected `clock`; a test with a fixed clock asserts exact `next_available_epoch` values (NFR-2). Constants (`DEFAULT_429_BACKOFF_SECONDS`, window mappings) are named (NFR-7). `ruff`/`mypy`/`pytest` green.

## Risks
- Reconcile double-application corrupts cumulative totals — guard with `reconciled_tasks` membership (AC-6).
- Window roll-over off-by-one at exactly `now == window_start + window_seconds` — define boundary inclusively (`>=` rolls); unit-test the exact boundary.

## Dependencies
- Upstream: T-oh5gl5 (`BudgetSpec`, `BudgetCounters`, `BudgetDecision`-related constants).
- Downstream: T-algywf (engine drives gate/charge/reconcile/429).

## Pseudocode / Algorithm
```text
FUNCTION gate(task_id, estimate, counters) -> BudgetDecision:
  _roll_window_if_needed(counters)           # uses clock
  # total first (precedence FR-3)
  IF spec.total_tokens is not None AND counters.consumed_tokens + estimate > spec.total_tokens:
    RETURN BudgetDecision(admit=False, blocked_by="total", next_available_epoch=None)
  IF spec.rate is not None AND counters.window_consumed_tokens + estimate > spec.rate.tokens:
    RETURN BudgetDecision(admit=False, blocked_by="rate",
                          next_available_epoch=counters.window_start_epoch + window_seconds)
  RETURN BudgetDecision(admit=True)

FUNCTION _roll_window_if_needed(counters):
  now = clock().timestamp()
  IF spec.rate is None: RETURN
  IF counters.window_start_epoch is None:
    counters.window_start_epoch = now; counters.window_consumed_tokens = 0
  ELIF now >= counters.window_start_epoch + window_seconds:
    counters.window_start_epoch = now; counters.window_consumed_tokens = 0

FUNCTION reconcile(task_id, actual, counters):
  IF task_id IN counters.reconciled_tasks: RETURN          # idempotent (NFR-3)
  est = counters.charged_estimate.pop(task_id, 0)
  delta = actual - est
  counters.consumed_tokens += delta
  counters.window_consumed_tokens += delta
  counters.reconciled_tasks.append(task_id)
```

## Schemas / Interface Notes
- Interface / API: `BudgetManager` ABC + `DefaultBudgetManager(spec, clock)`; methods `gate`/`charge_estimate`/`reconcile`/`reverse_estimate`/`on_provider_429`.
- Spec / data schema: mutates `BudgetCounters`; reads `BudgetSpec`.
- Triggers / events: N/A (engine emits the budget.* events around these calls).
- Artifacts: none (pure in-memory; engine persists counters via RunState).

## Handoff Boundary
- Upstream: T-oh5gl5 models.
- Downstream: engine (T-algywf) owns the call order, persistence, sleeping, and event logging.
