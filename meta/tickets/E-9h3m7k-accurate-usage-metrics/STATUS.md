# STATUS

- ID: `E-9h3m7k-accurate-usage-metrics`
- Updated At: 2026-07-10
- State: Done
- Owner: Claude (direct implementation)

## This update
- By: Claude
- Role: developer
- Date: 2026-07-10
- Comment: Epic complete. All FR-1..FR-5 landed:
  - `TaskResult.cost_usd` extracted from the Claude CLI's `total_cost_usd`
    (`executors/claude_cli.py: parse_usage_and_429`).
  - `engine._run_with_retries` sums input/output/cache tokens + cost_usd across every
    retry attempt (previously overwritten by the last attempt only) and returns the
    cumulative total as the task's `TaskResult`; this also fixes an adjacent budget
    under-reconciliation bug for retried tasks (`_sum_actuals` now sums the true total).
  - Attempt-suffixed capture dirs (`.../<task_id>/attempt-<N>/`) so a failed attempt's
    `transcript.jsonl`/`result.json` survive instead of being clobbered by the next retry.
  - `TaskRunState.cumulative_*` fields + `models.compute_run_usage_totals(state)` (pure,
    derived — not a separately mutated counter, so it's resume-safe by construction).
  - `ao run`/`ao status` display per-task cost/tokens + a run-total line; `status.json`
    carries a `usage_totals` block.
  - Two new circuit-breaker conditions: `task_cost_usd`, `run_cost_usd` (actual-dollar
    thresholds), registered in `BREAKER_REGISTRY` + `spec._MVP_BREAKER_CONDITIONS` +
    `workflow.schema.json` (condition enum + conditional-required + widened
    `threshold: int -> float` for fractional USD amounts).
  - Docs updated: `docs-md/logging-dynamic-workflows-hld.md` §11 (addendum, capture
    layout diagram), `docs-md/token-budgeting-hld.md` §10 (addendum, full rationale),
    `docs-md/multi-endpoint-circuit-breaker-hld.md` (catalog table entries).

## Evidence
- `pytest -q`: 629 passed, 3 skipped (baseline before this epic: 605 passed, 3 skipped —
  24 new tests added, zero regressions).
- `ruff check .`: clean on all touched files (2 pre-existing unrelated warnings in
  `tests/test_e2e_cli.py`, confirmed present on the base branch before this epic via
  `git stash` diff — not introduced here).
- `ruff format --check .`: clean after formatting the 3 newly-edited test files.
- `mypy src`: `Success: no issues found in 21 source files`.
- Manual end-to-end smoke test (engine + CLI, real retry scenario): task `a` fails
  attempt 1 ($0.01/150 tokens) then succeeds attempt 2 ($0.02/280 tokens) → CLI
  correctly displays cumulative $0.03/430 tokens for the task and $0.05 run total
  (paired with task `b`'s $0.02) — confirms the fix end-to-end, not just at the unit level.
- New test files/cases: `tests/test_engine.py` (2 new tests: cumulative-usage summing,
  distinct-per-attempt output dirs), `tests/test_executor.py` (4 new: cost_usd
  extraction + fixture regression pin + end-to-end execute() populates cost_usd),
  `tests/test_mvp_breaker_conditions.py` (2 new test classes: TaskCostUsdBreaker,
  RunCostUsdBreaker), `tests/test_routing_breaker_models.py` (3 new: schema accepts
  fractional threshold, rejects zero threshold, requires threshold for new conditions),
  `tests/test_validate_run_control.py` (3 new: conditions accepted, projected_cost_exceeds
  stays unimplemented).

## Risks / Blockers
- None open. Known, explicitly-scoped-out follow-up: usage is NOT accumulated across a
  quota-exhaustion-triggered whole-task re-run (the outer `engine.run()` loop's
  `cursor -= 1; continue` path is separate from the retry loop fixed here). Judged
  low-impact since quota exhaustion is typically detected before real work happens.

## Next actions
1. None — epic done. Follow-up items (downstream-runner config, laptop `ao` install) tracked
   separately per `meta/prompts/prompt.md`, not part of this epic's scope.
