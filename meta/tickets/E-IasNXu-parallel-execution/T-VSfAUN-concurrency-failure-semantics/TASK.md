# TASK: T-VSfAUN-concurrency-failure-semantics

## Metadata
- Task ID: `T-VSfAUN-concurrency-failure-semantics`
- Epic ID: `E-IasNXu-parallel-execution`
- Owner: developer agent
- Created: 2026-07-15
- Last Updated: 2026-07-15
- Status: Done
- Estimate: 2.5 days

## Requirements Mapping
- FR-6, FR-7, FR-8, NFR-4

## Description
Make every interleaved subsystem correct when up to `N` tasks are in flight. In the serial engine
these are `cursor -= 1; continue` (requeue) or `break` (halt) inside the run loop; under waves they
become signals from `_settle_completed_task` / `_prepare_and_maybe_dispatch` plus a drain policy. This
task implements and proves those semantics (the scheduler mechanism itself is `T-j8YLGd`).

Policy (ADR-0007 D7, design doc §7): **drain, don't kill.** On halt/cancel/requeue we stop admitting
new tasks and let in-flight workers finish; the main thread post-processes each drained result (so
artifacts + `RunState` stay consistent and resumable). Worker `claude` subprocesses are never
hard-killed mid-flight.

### Sub-deliverables
1. **Budget gate/charge before dispatch; reconcile on completion (FR-6).**
   - Gate + charge run in `_prepare_and_maybe_dispatch` on the main thread **before** `pool.submit`,
     so at most `N` charged estimates are ever outstanding. Preserve the resume double-charge guard
     (reverse stale estimate, `engine.py:368`).
   - Reconcile runs in `_settle_completed_task` (existing block `engine.py:729-750`), replacing the
     estimate with actuals. Confirm final `consumed_tokens` is order-independent.
   - **Budget "wait" (window roll) must not deadlock:** if the gate blocks and `in_flight != {}`,
     return `BLOCKED` (stop filling; the loop drains a completion — which reconciles and may free the
     window — then re-gates next wave). If the gate blocks and `in_flight == {}`, sleep to
     `next_available_epoch` exactly as today (`engine.py:471-501`). Unsatisfiable / `on_exhaustion ==
     "stop"` → `record_trip` + HALT (drain in-flight first). Identical events to today.
2. **Quota / 429 / self-heal requeue under concurrency (FR-7).** Each keeps its existing
   reverse-estimate + `_sleeper` wait + `ts.status = "pending"`, then `_settle_completed_task` returns
   `REQUEUE`. The task re-enters the ready set on a later wave. In-flight siblings **drain** (they are
   independent and already charged) — they are not cancelled. The per-episode quota timer
   (`_quota_exhausted_since`) stays main-thread state; a success anywhere resets it. Quota max-wait
   exceeded → `record_trip` + HALT.
3. **Failure / breaker halt / cancel (FR-8).** `_settle_completed_task` returns `HALT` for: a settled
   `failed`/`timed_out`/`cancelled` task, a tripped-and-non-extended breaker, budget stop/unsatisfiable,
   or a router-fail. `run()` on HALT (and on `cancel_fn`) calls `_drain_remaining(in_flight)` which
   waits for each running worker, settles its result (persisting usage/actuals), then finalizes
   `state.status` per today's rules (`failed` / `cancelled`). The run stays resumable (`ao resume`).
4. **`_drain_remaining` (NFR-4).** Waits on all in-flight futures via `as_completed`, settles each
   (ignoring further HALT — already halting), and relies on `pool.shutdown(wait=True)` as the backstop
   so no thread leaks even if settling raises.

## Acceptance Criteria
1. **Budget cap holds under concurrency.** Given `max_parallel=4`, a total/rate budget admitting only
   3 of 4 ready tasks, When `run()` executes, Then exactly 3 dispatch concurrently, the 4th is gated
   (BLOCKED) until a completion reconciles/frees budget, and there is **no double-charge** (assert
   `consumed_tokens` equals the sum of the 3 actuals + whatever the 4th consumes, never estimates
   left stranded). Final `consumed_tokens` is identical across two forced completion orders.
2. **No budget deadlock.** Given `on_exhaustion == "wait"`, a rate window that rolls, and tasks in
   flight, When the gate blocks, Then the engine drains an in-flight completion and makes progress
   (no hang); with `in_flight == {}` it sleeps via `self._sleeper` (fixed fake clock/sleeper) and
   re-gates. Test with a deterministic clock + sleeper (learning: fixed clocks).
3. **Quota requeue with siblings.** Given task `q` returns `claude_quota_exhausted` while sibling `s`
   runs (`max_parallel=2`), When settled, Then `s` drains and settles normally, `q` is reset to
   `pending`, the engine waits (`_sleeper`), and `q` re-dispatches on a later wave and succeeds; the
   estimate for `q` was reversed (no leak). Same shape for provider-429 (`provider_rate_limited`) and
   for self-heal (`self_heal` enabled, `q` fails transiently once then heals).
4. **Breaker halt mid-wave drains.** Given a breaker that trips when `s` settles while `t` is still in
   flight (`max_parallel=2`), When `s` trips a hard/non-extended breaker, Then the engine stops
   admitting, `_drain_remaining` settles `t` (its `TaskRunState` + any actuals persisted), and final
   `state.status == "failed"`. A subsequent `ao resume` continues from the drained state without
   re-running `t` if `t` succeeded.
5. **Cancel mid-wave drains + resumable.** Given `cancel_fn` returns True while 2 tasks are in flight,
   When observed at the top of a wave, Then `state.status == "cancelled"`, both in-flight tasks are
   drained (not killed) and persisted, and `ao resume` re-runs only the unfinished work.
6. **No thread leak.** After any HALT/cancel/exception path, `ThreadPoolExecutor.shutdown(wait=True)`
   has run and `threading.active_count()` returns to the pre-run baseline.
7. `ruff`/`mypy src` clean; `uv run pytest` green; **`N=1` remains byte-identical** (re-assert the
   `T-j8YLGd` gate — these changes must not alter serial behavior).

## Risks
- **R3: budget-wait deadlock** if BLOCKED does not drain — covered by AC-2; the rule is "never sleep
  on a rolled window while capacity is held by in-flight siblings; drain first."
- **R4: breaker count nondeterminism at N>1** — `task_failures`/`consecutive_failures` may trip on a
  different task depending on completion order. Documented/accepted (design doc §11); `N=1`
  deterministic. Do NOT try to force determinism by serializing failures — only barriers serialize.
- Requeue must reverse the estimate exactly once (mirror the serial reverse sites at `engine.py:569`,
  `653`); double-reverse would under-count the budget.

## Dependencies
- `T-j8YLGd` (scheduler + signal enum + `_settle_completed_task`/`_prepare_and_maybe_dispatch`/
  `_drain_remaining` stubs). Reuses unchanged: `budget.py` (`gate`/`charge_estimate`/`reconcile`/
  `reverse_estimate`/`on_provider_429`), `breakers.py` (`evaluate_breakers`/`record_trip`),
  `monitoring.py` (self-heal consult).

## Pseudocode / Algorithm
```text
def _prepare_and_maybe_dispatch(tid, ...):  # main thread, before submit
    ... not_taken/done/should_skip/apply_join/missing-inputs (as today) → SKIPPED or HALT ...
    if budget on:
        estimate = estimator.estimate(...)
        reverse_stale_estimate_if_needed(tid)
        decision = budget.gate(tid, estimate, counters)
        if not decision.admit:
            if is_unsatisfiable or on_exhaustion == "stop": record_trip(...); return HALT
            if in_flight_nonempty(): return BLOCKED          # drain then re-gate (no sleep)
            sleep_until(decision.next_available_epoch); return BLOCKED  # re-gate next wave
        budget.charge_estimate(tid, estimate, counters)
    mark_running(tid); return DISPATCH

def _settle_completed_task(tid, result, ...):  # main thread, one at a time
    if result.claude_quota_exhausted: reverse_estimate; wait_or_halt; ts.status="pending"; return REQUEUE|HALT
    if budget on and result.provider_rate_limited: reverse_estimate; wait_or_stop; return REQUEUE|HALT
    elif budget on: reconcile(tid, actuals_or_estimate)
    if self_heal and result.status=="failed":
        verdict = consult(...); if verdict.retry: accumulate_actuals; wait; ts.status="pending"; return REQUEUE
    settle usage/attempts/ended_at; verify outputs; read manifests
    if succeeded: done.add(tid); router hook (barrier); if router fail: return HALT
    evaluate_breakers → consult; if halt: return HALT
    if ts.status not in (succeeded, skipped): return HALT
    if emit_tasks: inject; return RESHAPED
    if loop gate continues: clone+inject; return RESHAPED
    return SETTLED

def _drain_remaining(in_flight, ...):
    for fut in as_completed(list(in_flight)):
        tid = in_flight.pop(fut); result = fut.result()
        _settle_completed_task(tid, result, ...)   # persist; further HALT ignored (already halting)
```

## Schemas / Interface Notes
- Interface / API: fills `_prepare_and_maybe_dispatch` / `_settle_completed_task` / `_drain_remaining`
  bodies with the concurrency-correct semantics; no public signature change.
- Data schema / triggers / artifacts: none new. Budget/breaker/RunState field usage unchanged.

## Handoff Boundary
- Upstream: `T-j8YLGd`.
- Downstream: `T-TNleFt` owns the full interaction integration matrix + e2e; this task ships its own
  focused unit/integration tests for each semantic above.

## Artifacts
- Docs/comments: `meta/tickets/E-IasNXu-parallel-execution/T-VSfAUN-concurrency-failure-semantics/`
- Large outputs: none.
