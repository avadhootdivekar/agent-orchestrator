# STATUS

- ID: `T-oh5gl5-budget-config-schema`
- Updated At: 2026-06-18
- State: Done
- Owner: manager

## This update
- Implemented all foundation models: `RateLimit`, `EstimatorConfig`, `BudgetSpec`, `BudgetCounters` added to `models.py`.
- Named constants added: `WINDOW_SECONDS`, `DEFAULT_CHARS_PER_TOKEN`, `DEFAULT_PESSIMISM_BUFFER`, `DEFAULT_OUTPUT_ALLOWANCE_TOKENS`, `DEFAULT_429_BACKOFF_SECONDS`.
- `WorkflowSpec.budget` field added (optional, default None — backward-compatible).
- `RunState.budget_counters` field added using `Field(default_factory=BudgetCounters)` for backward-compatible deserialization.
- `TaskResult` token fields added: `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `actuals_available`, `provider_rate_limited`, `provider_retry_after_epoch`.
- `workflow.schema.json` updated with `budget`, `rateLimit`, `estimatorConfig` in `$defs` and `budget` in top-level properties.
- `spec.py` `cross_validate` extended with `budget_cross_validate` (standalone + called from `cross_validate`).

By: manager · Role: manager · Date: 2026-06-18 · Comment: Implemented and verified. 207 tests pass.

## Evidence
- `src/agent_orchestrator/models.py` — new models + constants
- `specs/workflow.schema.json` — budget block added
- `src/agent_orchestrator/spec.py` — `budget_cross_validate` + wiring
- `pytest -q`: 207 passed (at time of task completion)

## Risks / Blockers
- None.

## Next actions
- Task complete. Downstream tasks T-n7hmwj, T-7kp8iv, T-1m9744 consumed these models.
