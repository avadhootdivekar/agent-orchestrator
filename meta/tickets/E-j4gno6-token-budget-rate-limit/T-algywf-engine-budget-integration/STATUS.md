# STATUS

- ID: `T-algywf-engine-budget-integration`
- Updated At: 2026-06-18
- State: Done
- Owner: manager

## This update
- Added `BudgetExhausted` and `RateLimited` to `errors.py`.
- `Orchestrator.__init__` extended with `budget_manager`, `estimator`, `clock` optional params.
- Gate block added in `run()` loop: estimate computation, resume double-charge guard (`reverse_estimate`), gate loop with `_is_unsatisfiable` guard, stop/wait handling via injected sleeper + clock.
- Reconcile block added after `_run_with_retries`: provider 429 routing (reverse → `on_provider_429` → stop or sleep+retry via cursor decrement), normal reconcile (actuals or estimate fallback).
- `budget.*` structured events emitted: `charge`, `reconcile`, `gate_block`, `exhausted`, `wait`, `resume`, `provider_429`, `resume_reverse`.
- `_sum_actuals` and `_is_unsatisfiable` private helpers added.
- When `budget_manager is None`: engine behavior is byte-identical to pre-epic.
- Engine integration tests added in `tests/test_engine_budget.py` (19 tests).

By: manager · Role: manager · Date: 2026-06-18 · Comment: Implemented and verified. 288 tests pass at completion. AC-1 no-regression confirmed.

## Evidence
- `src/agent_orchestrator/engine.py` — gate/reconcile/stop/wait/resume integration
- `src/agent_orchestrator/errors.py` — `BudgetExhausted`, `RateLimited`
- `tests/test_engine_budget.py` — 19 integration tests
- `pytest -q`: 288 passed

## Risks / Blockers
- None.

## Next actions
- Task complete.
