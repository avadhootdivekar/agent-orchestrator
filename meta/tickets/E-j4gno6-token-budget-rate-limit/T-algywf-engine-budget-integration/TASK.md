# TASK: T-algywf-engine-budget-integration

## Metadata
- Task ID: `T-algywf-engine-budget-integration`
- Epic ID: `E-j4gno6-token-budget-rate-limit`
- Owner: TODO
- Created: 2026-06-18
- Last Updated: 2026-06-18
- Status: Draft
- Estimate: < 3 days

## Requirements Mapping
- Requirement IDs: FR-1..FR-9, FR-11, NFR-2, NFR-3, NFR-6

## Description
Wire the meter into `Orchestrator.run()`. Inject an optional `budget_manager: BudgetManager | None`
and `estimator: TokenEstimator | None` into `Orchestrator.__init__` (alongside the
existing `sleeper`/`cancel_fn`). Add an injectable `clock: Callable[[], datetime]`
(reuse the `RunStateStore` clock convention) for window math and 429 reset comparisons.
When `budget_manager is None`, behavior is **identical to today** (feature opt-in).

Integration points (verified against `engine.py`):
- **GATE** — in the `run()` loop, *just before* calling `_run_with_retries`, after the
  `TaskContext` inputs are known: compute `estimate = estimator.estimate(ctx_like, cfg)`,
  call `budget_manager.gate(tid, estimate, state.budget_counters)`.
  - If `admit=False` → invoke `_handle_exhaustion(decision, ...)` (stop or wait).
  - If admitted → `budget_manager.charge_estimate(tid, estimate, counters)`; `save(state)`; log `budget.charge`.
- **RECONCILE** — *right after* `_run_with_retries` returns a `TaskResult`:
  - If `result.provider_rate_limited` → route to `budget_manager.on_provider_429(...)` → `_handle_exhaustion`. (The task did not complete against quota — treat like a blocked admission and **reverse** the charged estimate before waiting/stopping so it re-runs cleanly.)
  - Else compute `actual = result.input_tokens+output_tokens+cache_* if result.actuals_available else charged_estimate[tid]` and call `budget_manager.reconcile(tid, actual, counters)`; log `budget.reconcile` with estimate-vs-actual delta. Persist counters via `save(state)`.
- **STOP vs WAIT** (`_handle_exhaustion`):
  - `on_exhaustion == "stop"` (default): log `budget.exhausted`; raise/record terminal status. Set `state.status = "failed"` (with a budget reason) OR a dedicated terminal — see ADR-BUD-004; leave the current task **pending** (not charged) so resume re-gates it. Break the loop. RunState stays resumable.
  - `on_exhaustion == "wait"`: log `budget.wait` with `next_available_epoch`; compute `sleep_secs = max(0, next_available_epoch - clock().timestamp())`; call `self._sleeper(sleep_secs)`; check `cancel_fn()`; log `budget.resume`; **retry the gate** for the same task (do not advance the cursor). For a rolled rate window the re-gate now admits; for provider-429 the reset has passed.
- **RESUME double-charge guard** (NFR-3, R2): at run start, for any `tid` in `counters.charged_estimate` that is NOT in `counters.reconciled_tasks` and whose task is being re-run, call `budget_manager.reverse_estimate(tid, counters)` so the stale estimate is removed before the fresh gate. Already-reconciled/succeeded tasks are skipped by existing `should_skip` and never re-charged.

Emit budget events through the per-run logger (`get_run_logger`) with
`extra={"event": "budget.<x>", ...}`: `charge`, `reconcile`, `gate_block`,
`exhausted`, `wait`, `resume`, `provider_429`.

Add `BudgetExhausted` and `RateLimited` to `errors.py`.

## Acceptance Criteria
1. Given `budget_manager=None`, When a workflow runs, Then behavior and RunState are byte-identical to pre-epic (existing integration tests unchanged).
2. Given `total_tokens` small enough that task 2's estimate exceeds the remaining cap, `on_exhaustion="stop"`, When the run executes, Then task 1 succeeds, task 2 is **not executed**, `budget.exhausted` is logged with `blocked_by="total"`, the run ends non-success, and `RunState` shows task 2 still `pending` (resumable, un-charged).
3. Given the same as (2) but `on_exhaustion="wait"` with a rate (not total) block, fixed clock + injected sleeper, When the run executes, Then the engine sleeps exactly `next_available_epoch - now` seconds via the injected sleeper, the window rolls, task 2 is re-gated, admitted, and the run completes successfully. The sleeper is called with the asserted duration (NFR-2).
4. Given a `TaskResult` with `actuals_available=True` (input=120,output=80) after a charged estimate of 520, When reconcile runs, Then `consumed_tokens` reflects `+200` net for that task (not +720), and `budget.reconcile` logs delta `-320`.
5. Given a `TaskResult` with `actuals_available=False`, When reconcile runs, Then the **estimate is kept** as the charge (no change to consumed; task moved to reconciled with `actual==estimate`).
6. Given `result.provider_rate_limited=True` with retry-after R and `on_exhaustion="wait"`, When the run executes, Then the engine reverses the task's charged estimate, sleeps until R, logs `budget.provider_429` then `budget.resume`, and re-runs the task.
7. Given a run stopped on exhaustion (AC-2) is **resumed** with a larger `total_tokens`, When resumed, Then no completed task is re-charged (no double-charge), task 2 runs, and final `consumed_tokens` equals the sum of all tasks' reconciled actuals.
8. Given `cancel_fn()` becomes True during a `wait`, When the sleeper returns, Then the run halts with `cancelled` status (wait is interruptible — R6). `ruff`/`mypy`/`pytest` green.

## Risks
- The gate needs the resolved instruction/input/dynamic-input paths; today those are resolved inside `_run_with_retries`. Mitigation: resolve the paths once before the gate (or have the estimator accept the same raw `task` + store) — keep a single resolution path, no duplication (DRY).
- Stop terminal-status modeling: reusing `"failed"` vs adding a budget terminal — decide in ADR-BUD-004 and keep `status` enum changes minimal/back-compat.
- Re-gate loop on `wait` must terminate: after sleeping to `next_available_epoch`, the window has rolled so the re-gate admits; guard against an infinite wait loop if estimate alone exceeds the whole window quota (detect `estimate > rate.tokens` → unsatisfiable → force stop with a clear error, do not wait forever).

## Dependencies
- Upstream: T-n7hmwj (estimator), T-7kp8iv (BudgetManager), T-1m9744 (TaskResult token fields + 429).
- Downstream: T-xefapr (CLI wiring), T-67kiia (integration tests).

## Pseudocode / Algorithm
```text
# inside run() loop, per task `tid`, after inputs known, before execution:
estimate = estimator.estimate(task_ctx, cfg)
WHILE True:
  decision = budget_manager.gate(tid, estimate, state.budget_counters)
  IF decision.admit: BREAK
  log budget.gate_block (blocked_by, next_available_epoch)
  IF estimate > unsatisfiable_quota(decision): _stop("estimate exceeds window/total"); BREAK_RUN
  IF on_exhaustion == "stop":
    log budget.exhausted; mark run terminal (task stays pending); save(state); BREAK_RUN
  ELSE: # wait
    sleep_secs = max(0, decision.next_available_epoch - clock().timestamp())
    log budget.wait(sleep_secs); self._sleeper(sleep_secs)
    IF cancel_fn(): state.status="cancelled"; BREAK_RUN
    log budget.resume   # loop re-gates
budget_manager.charge_estimate(tid, estimate, counters); save(state); log budget.charge

result = _run_with_retries(...)

IF result.provider_rate_limited:
  budget_manager.reverse_estimate(tid, counters)      # un-charge; task will re-run
  decision = budget_manager.on_provider_429(result.provider_retry_after_epoch, counters)
  log budget.provider_429
  _handle_exhaustion(decision)   # stop or wait+retry same task (cursor not advanced)
ELSE:
  actual = sum_actuals(result) if result.actuals_available else counters.charged_estimate[tid]
  budget_manager.reconcile(tid, actual, counters); log budget.reconcile; save(state)
```

## Schemas / Interface Notes
- Interface / API: `Orchestrator.__init__(..., budget_manager=None, estimator=None, clock=...)`; private `_handle_exhaustion`, `_sum_actuals`. New errors `BudgetExhausted`, `RateLimited`.
- Spec / data schema: reads `wf.budget`; mutates + persists `state.budget_counters`.
- Triggers / events: emits `budget.charge|reconcile|gate_block|exhausted|wait|resume|provider_429`.
- Artifacts: persists counters in `state.json` via `RunStateStore.save`.

## Handoff Boundary
- Upstream: estimator + budget manager + executor token fields.
- Downstream: CLI constructs the manager/estimator and passes them in; tests assert the flows.
