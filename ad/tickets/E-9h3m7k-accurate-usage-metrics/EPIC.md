# EPIC: E-9h3m7k-accurate-usage-metrics

## Metadata
- Epic ID: `E-9h3m7k-accurate-usage-metrics`
- Title: Accurate cumulative usage metrics + actual-cost circuit breakers
- Owner: Claude (direct implementation, per user selection)
- Created: 2026-07-10
- Last Updated: 2026-07-10
- Status: Done

## Summary
- Goal: We already capture reliable per-run token/cost usage from the Claude CLI's
  terminal `result` event, but it is not aggregated correctly. Fix three confirmed gaps:
  1. **Per-task**: a retry loop discards token/cost usage from failed attempts —
     only the last attempt's numbers survive (`engine.py:_execute_with_retry`).
  2. **Per-run**: nothing sums task usage into a run-level total; `cost_usd` isn't
     even extracted from the CLI's `total_cost_usd` field at all.
  3. **Circuit breakers**: no breaker condition trips on actual USD cost, only on
     token *estimates* (`budget.py`) pre-charged before a task runs.
- Scope In:
  - `TaskResult.cost_usd` (from `total_cost_usd`, confirmed field name per
    `docs-md/token-budgeting-hld.md` / `tests/fixtures/claude_usage.json`).
  - Retry loop sums token/cost across all attempts of a task (not just the last).
  - Attempt-suffixed output dirs (`attempt-N/`) so a failed attempt's
    `transcript.jsonl` survives instead of being overwritten by the next retry.
  - `RunUsageTotals` — pure, derived (not separately persisted/mutated) sum over
    `RunState.tasks[*].cumulative_*`, used by CLI display and the new run-level breaker.
  - Two new breaker conditions: `task_cost_usd` (any single task's cumulative cost
    exceeds threshold) and `run_cost_usd` (run-wide cumulative cost exceeds threshold).
  - CLI displays cost/token columns per task + a totals line.
- Scope Out:
  - `projected_cost_exceeds` (pre-flight estimate breaker) — separate, already-reserved
    condition name tied to `budget.py`/`estimator.py`; not touched here.
  - Token-count breakers (`task_tokens`/`run_tokens`) — not requested; token *totals*
    are caps via existing `BudgetSpec.total_tokens`, only cost_usd breakers are new.
  - `ao-runner-finplan` config changes and the laptop `ao` install — tracked as
    separate, non-epic follow-up items in this same session (per `ad/prompts/prompt.md`).

## Requirements
- FR-1: `TaskResult.cost_usd` populated from the CLI's `total_cost_usd`.
- FR-2: A task's final `TaskResult` token/cost fields are the SUM across all retry
  attempts, not just the last attempt's.
- FR-3: `RunUsageTotals` derivable from `RunState` at any time (resume-safe — no
  separately mutated counter to desync).
- FR-4: `task_cost_usd` / `run_cost_usd` breaker conditions, configurable threshold
  (float, since USD amounts are fractional), same pluggable-registry pattern as the
  existing six MVP conditions.
- FR-5: CLI (`ao run` / `ao status`) shows per-task and total cost/tokens.
- NFR-1: No change to single-attempt (no-retry, no-failure) task semantics — sums
  degenerate to today's values when `attempts == 1`.

## Task List
(Direct-implementation mode — tracked via session TaskCreate/TaskUpdate, not
per-task ticket docs. See STATUS.md for the running log.)

## Risks and Dependencies
- Widening `CircuitBreakerSpec.threshold` from `int` to `float` is a schema-visible
  change (`specs/workflow.schema.json`); verified no test asserts `isinstance(..., int)`
  before landing.
- Attempt-suffixed output dirs change `TaskResult.output_artifact_path` for retried
  tasks (now points at `attempt-N/` instead of the bare task dir); verified no test/doc
  hardcodes the old unsuffixed path for a retried task before landing.

## Links
- Design doc: `docs-md/token-budgeting-hld.md` (addendum), `docs-md/multi-endpoint-circuit-breaker-hld.md` (breaker catalog)
- Related prior epics: `E-j4gno6-token-budget-rate-limit`, `E-rc7k2v-run-control-routing-breakers`
- Output artifacts (if any): N/A
