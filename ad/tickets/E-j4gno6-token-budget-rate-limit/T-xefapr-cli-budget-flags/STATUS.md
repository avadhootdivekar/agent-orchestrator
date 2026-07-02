# STATUS

- ID: `T-xefapr-cli-budget-flags`
- Updated At: 2026-06-18
- State: Done
- Owner: manager

## This update
- Added `_build_effective_budget()` helper to `cli.py`: merges CLI flags over spec per-field (CLI > spec > unset). Pre-validates `rate_window` enum and `on_exhaustion` value before Pydantic.
- `ao run` extended with `--budget-total`, `--rate-tokens`, `--rate-window`, `--on-exhaustion`, `--pessimism-buffer` flags.
- `ao resume` extended with the same budget flags.
- Both commands construct `DefaultBudgetManager` + `HeuristicTokenEstimator` and pass them (with a shared `clock`) to `Orchestrator`. When no budget: `budget_manager=None` (no-op path).
- AC-2 confirmed: no spec + no CLI flags → `budget_manager=None` → zero behavior change.
- AC-1 confirmed: CLI `--budget-total` overrides spec `total_tokens` per-field without wiping spec `rate` block.
- Tests added to `tests/test_cli.py` (16 new tests).

By: manager · Role: manager · Date: 2026-06-18 · Comment: Implemented and verified. 304 tests pass at completion.

## Evidence
- `src/agent_orchestrator/cli.py` — `_build_effective_budget`, updated `run`/`resume`
- `tests/test_cli.py` — 16 new tests
- `pytest -q`: 304 passed

## Risks / Blockers
- None.

## Next actions
- Task complete.
