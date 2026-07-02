# STATUS

- ID: `E-j4gno6-token-budget-rate-limit`
- Updated At: 2026-06-18
- State: Done
- Owner: manager

## This update
All 8 tasks implemented, tested, and docs-reconciled. Epic complete.

### Task rollup
| Task | State | Key evidence |
|------|-------|--------------|
| T-oh5gl5-budget-config-schema | Done | models.py + schema + spec.py |
| T-n7hmwj-token-estimator | Done | estimator.py + artifacts.py size() |
| T-7kp8iv-budget-manager-meter | Done | budget.py DefaultBudgetManager |
| T-1m9744-executor-actuals-429 | Done | claude_cli.py parse_usage_and_429 + fixture |
| T-algywf-engine-budget-integration | Done | engine.py gate/reconcile/stop/wait/resume |
| T-xefapr-cli-budget-flags | Done | cli.py _build_effective_budget + flags |
| T-67kiia-tests-budget | Done | 313 tests, estimator/budget 100%, engine 94% |
| T-5igs6g-docs-refresh-budget | Done | HLD reconciled, OPEN_QUESTIONs resolved |

By: manager · Role: manager · Date: 2026-06-18 · Comment: All 8 tasks Done. Quality gates: pytest -q 313 passed; ruff check clean; ruff format --check clean; mypy src clean. No commits made — changes in working tree on branch ad/orch-goals-1 for review.

## Evidence
- `pytest -q`: **313 passed, 0 failed**
- `ruff check .`: All checks passed
- `ruff format --check .`: 40 files already formatted
- `mypy src`: Success: no issues found in 20 source files
- Coverage: `estimator.py` 100%, `budget.py` 100%, `engine.py` 94%
- Schema round-trip: `specs/examples/workflow-budget.json` loads + validates
- End-to-end demo: FakeExecutor + fixed clock proves (a) stop-at-total, (b) wait-then-continue on window roll, (c) resume no-double-charge
- NFR-1 audit: `estimator.py` uses only `store.size()` / `os.stat` — no artifact content reads
- OPEN_QUESTIONs: all resolved (claude CLI usage shape confirmed, cache_read counted fully, max-wait unbounded+cancel_fn, tumbling window shipped)

## Resolved OPEN_QUESTIONs
- **Usage JSON shape**: Confirmed `total_cost_usd` at top level; `usage.{input_tokens,output_tokens,cache_creation_input_tokens,cache_read_input_tokens}` exact match. Fixture frozen at `tests/fixtures/claude_usage.json`.
- **cache_read_input_tokens discount**: Count fully (conservative; revisit with real data in follow-on).
- **Max wait cap**: Unbounded but `cancel_fn()`-interruptible. `max_wait_seconds` deferred to follow-on.
- **Window model**: Tumbling (shipped). Sliding window is Scope Out.

## Deviations from design (minor)
1. `_is_unsatisfiable` guard added to engine to prevent infinite wait when estimate exceeds full window budget.
2. Provider 429 re-run via `cursor -= 1; continue` (not a separate re-gate inner loop).
3. `_build_effective_budget` pre-validates `rate_window` enum in CLI before Pydantic.
4. `BudgetCounters` in RunState uses `Field(default_factory=BudgetCounters)` for backward-compat deserialization.

## Risks / Blockers
- None. All tasks complete.

## Next actions
- Epic closed. Follow-on items (if desired): USD cost budgeting, sliding windows, max_wait_seconds, recursive directory sizing in estimator.
