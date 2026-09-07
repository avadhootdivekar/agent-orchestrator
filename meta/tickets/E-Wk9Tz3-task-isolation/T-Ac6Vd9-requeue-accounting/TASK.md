# TASK: T-Ac6Vd9-requeue-accounting

## Metadata
- Task ID: `T-Ac6Vd9-requeue-accounting`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: developer-agent
- Created: 2026-09-07
- Last Updated: 2026-09-07
- Status: Done
- Estimate: 2 days

## Requirements Mapping
- Requirement IDs: FR-7 (the ladder's cost bound), NFR-3 · Design: HLD §13, §10.3, §11 M5
- Created by the 2026-09-07 design gate to carry **R-1** (Blocking) and **R-21** (Major) out of
  `T-En8Hd4`, which would otherwise have exceeded the epic's 3-day task cap.

## Description
Make the ladder's "conflict spend is bounded for free" claim actually true. ADR-0013 D9 says T2/T3 are
extra attempts of the same task, so the existing budget and cost machinery bounds them with zero new
plumbing. The design gate found that **two** things break that claim, and both are instances of bug
classes this codebase has already found and fixed once elsewhere:

1. **R-1a — actuals discarded across a requeue.** `_run_with_retries`'s `cum_*` accumulators reset to
   zero on **every call**, and a T2/T3 requeue is a brand-new call. Unless settle accumulates the
   finished cycle's actuals into `ts.cumulative_*` *before* returning `"requeue"`, that cycle's cost
   and tokens vanish. The self-heal requeue path already needed exactly this fix, and the code carries
   a comment marking it a reviewer-caught Critical (`engine.py:1063-1071`: *"the same bug class
   E-9h3m7k fixed for retries WITHIN one `_run_with_retries` call; self-heal's cross-call redispatch
   needed the identical treatment"*).
2. **R-1b — the token ledger is one-shot per task id.** `DefaultBudgetManager.reconcile()`
   (`budget.py:146-159`) latches per `task_id` in `reconciled_tasks`, and it runs at the **first**
   settle of a completed dispatch — before the conflict outcome is known. Every later reconcile for
   the same task is silently a no-op. `reverse_estimate()` (`budget.py:161-171`) pops
   `charged_estimate[task_id]` but does not un-latch `reconciled_tasks`, and by then is usually a
   no-op itself. Net: a conflicting task's real spend on its repair attempts is invisible to
   `total_tokens` and rate-window gating — exactly in the pathological-repeat-conflict scenario that
   D9 and risk R2 claim is safety-bounded.

Plus **R-21**: `output_dir` is derived from the task id alone (`engine.py:1969-1971`) and the internal
attempt loop restarts at 1 on every call, so with the default `RetryPolicy(max_attempts=1)` a requeued
dispatch writes to the same `attempt-1/` directory as the original and **clobbers its transcript** —
undoing E-9h3m7k's "every attempt gets its own capture" guarantee.

Files you own:
- `src/agent_orchestrator/budget.py` (edit — cycle-keyed ledger)
- `src/agent_orchestrator/engine.py` (edit — **narrow**: settle-time accumulation before requeue, the
  cycle-keyed `output_dir`, and passing the cycle into the budget calls. Read the **merged** file from
  `T-En8Hd4` first; do not refactor around its hook points.)
- `tests/test_requeue_accounting.py`, `tests/test_budget_cycles.py` (new)

Do NOT touch: `models.py` (the two fields — `TaskRunState.dispatch_cycle`,
`BudgetCounters.reconciled_cycles` — ship with `T-Sc7Rm2` and are **frozen**; use them as-is), any
`isolation/` module, `cli.py`, `runstate.py`, `spec.py`.

## Acceptance Criteria
1. **R-1a — accumulate before requeue.** In `_settle_completed_task`, both the `conflict_resolver` and
   the `conflict_rerun` branches accumulate `outcome.result`'s actuals into `ts.cumulative_input_tokens`
   / `cumulative_output_tokens` / `cumulative_cache_*` / `cumulative_cost_usd` **before** returning
   `SettleResult("requeue")`. Mirror the existing self-heal block (`engine.py:1063-1071`) rather than
   inventing a second shape — extract the shared helper if that makes it one implementation instead of
   three.
2. Test: a task that goes clean-attempt -> conflict -> T2 resolver -> integrated has a
   `cumulative_cost_usd` equal to the **sum** of both cycles, and the same for all four token counters.
   A second test does the same for a T3 rerun.
3. **R-1b — the ledger is keyed by cycle.** `charged_estimate` and reconcile-latching are keyed by
   `"<task_id>#<dispatch_cycle>"` using the frozen `BudgetCounters.reconciled_cycles` field. Each
   redispatch is independently **gateable, chargeable and reconcilable**. `reconciled_tasks` stays in
   place, untouched, for backward compatibility with an older `state.json`.
4. Test: a redispatched task is gated and charged again, and its actuals reconcile again — asserted
   against `BudgetCounters` (`consumed_tokens`, `charged_estimate`, `reconciled_cycles`), **not** only
   against `TaskRunState.cumulative_*`. A cost test that passes on cumulative fields while the ledger
   is wrong is exactly how this defect survives review.
5. Test: rate-window gating sees the T2 attempt's tokens — a budget whose rolling window is just large
   enough for one attempt **blocks** the second, proving the spend is visible to the gate rather than
   merely recorded after the fact.
6. **Idempotency preserved.** The existing resume double-charge guard (reverse stale estimate,
   `engine.py:368`) still works: an interrupted-then-resumed run does not double-charge. Test with a
   run interrupted mid-cycle-2.
7. **R-21 — capture directories are cycle-keyed.** `output_dir` becomes
   `<run_dir>/<task_id>/cycle-<dispatch_cycle>/`, with `attempt-<n>/` beneath it as today. State in one
   place (a module constant plus a docstring) whether cycle 1 keeps the legacy flat `attempt-<n>` layout
   or always nests — and make the dashboard/`ui/runs.py` reader tolerate both, since old runs on disk
   use the flat form.
8. Test: a task that runs, conflicts, and is redispatched has **two** capture directories with distinct
   transcripts; neither overwrites the other. Assert on file content, not just existence.
9. **Investigate the same latent gap in self-heal (R-21).** The reviewer suspected — but did not
   verify — that self-heal's own cross-call retry already clobbers transcripts today by the identical
   mechanism. Determine this by test. If confirmed, **do not fix it here**: record it in `STATUS.md` as
   a pre-existing defect with a reproduction, for a separate ticket. If not confirmed, say why.
10. `uv run pytest -q` fully green with recorded before/after counts; `ruff` clean; `uv run mypy src`
    zero new errors; the NFR-2 gate (pre-existing engine suite unedited) still passes.

## Risks
- `budget.py` is load-bearing for every run, not just isolated ones. Keying by cycle must be a
  **superset** of today's behaviour: at `dispatch_cycle == 1` with no requeue, every counter must be
  byte-identical to today. Make that an explicit test, not an assumption.
- Changing `output_dir`'s shape affects the dashboard and anything reading capture paths. AC-7's
  "tolerate both" requirement is what stops this from breaking existing runs on disk.
- This is the third place this codebase has had to fix cross-call accounting (retries within a call →
  E-9h3m7k; self-heal cross-call → the reviewer-flagged Critical; now the ladder). Prefer extracting
  one shared helper over adding a third near-copy — CLAUDE.md's no-duplicate-logic rule applies
  directly.

## Dependencies
- Upstream: `T-Sc7Rm2` (both fields, frozen), `T-En8Hd4` (the merged settle/dispatch structure and its
  published hook points).
- Downstream: `T-Lr6Ka3` consumes this — its D9 cost test asserts against `BudgetCounters`, so it will
  fail loudly if this ticket has not landed.

## Pseudocode / Algorithm
```text
HLD §13 (both defects, with the engine.py/budget.py line references), §11 M5 (the settle branches and
the cycle-keyed output_dir), §10.3 (why dispatch_cycle and reconciled_cycles are separate fields).

_settle_completed_task, conflict branches:
    accumulate_actuals(ts, outcome.result)          # BEFORE the requeue return, both branches
    ...
    RETURN SettleResult("requeue")
# reverse_estimate is deliberately NOT called: by this point reconcile() has already run once for
# this task_id, so it would be a no-op and would not un-latch reconcile either. The cycle key is
# the fix, not a reversal.

budget.py:
    key(task_id, cycle) -> f"{task_id}#{cycle}"
    charge_estimate(task_id, cycle, est) ; reconcile(task_id, cycle, actual) ; gate(...)
    reconciled_cycles: list[str]        # replaces the reconciled_tasks latch for new writes
```

## Schemas / Interface Notes
- Interface / API: `BudgetManager` ABC gains a `cycle: int` parameter on `charge_estimate` /
  `reconcile` / `reverse_estimate` (default `1`, so an existing caller and any third-party
  implementation keeps working). Publish the final signatures in `STATUS.md`.
- Spec / data schema: none new — both fields ship with `T-Sc7Rm2`.
- Triggers / events: existing `budget.charge` / `budget.reconcile` lines gain a `cycle` field.
- Artifacts: `<run_dir>/<task_id>/cycle-<n>/attempt-<m>/` capture directories.

## Handoff Boundary
- Upstream: merged `engine.py` from `T-En8Hd4`; frozen fields from `T-Sc7Rm2`.
- Downstream: `T-Lr6Ka3` (ladder cost tests), `T-Cx4Jf1` (any capture-path display), `T-Ee3Mn8`
  (§17.5 rows R-1a, R-1b, R-21).

## Artifacts
- Docs/comments: `meta/tickets/E-Wk9Tz3-task-isolation/T-Ac6Vd9-requeue-accounting/`
- Large outputs: none

---
- By: architect · Role: architect · Date: 2026-09-07 · Comment: Created by the Phase-2 review pass to
  carry R-1 (Blocking) and R-21 (Major) out of `T-En8Hd4`, which would otherwise have exceeded the
  epic's 3-day cap. Deliberately scoped to the accounting/capture layer so it can be reviewed against
  `budget.py`'s existing semantics without the isolation machinery in the way. AC-9 asks for an
  investigation, not a fix, of the suspected pre-existing self-heal transcript clobber — fixing someone
  else's latent defect inside this epic would blur the evidence for both.
