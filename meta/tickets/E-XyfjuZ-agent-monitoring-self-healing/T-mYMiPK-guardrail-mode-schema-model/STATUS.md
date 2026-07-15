# STATUS

- ID: `T-mYMiPK-guardrail-mode-schema-model`
- Updated At: 2026-07-15
- State: Done
- Owner: dev-epic agent

## This update
- By: dev-epic agent
- Role: developer
- Date: 2026-07-15
- Comment: Implemented. `CircuitBreakerSpec.mode: Literal["hard","recommend"] = "hard"` added
  (`src/agent_orchestrator/models.py`); `specs/workflow.schema.json`'s `circuitBreaker.properties`
  gained the matching `mode` enum/default. 7 new tests added to
  `tests/test_routing_breaker_models.py::TestGuardrailMode` covering: default value, valid/invalid
  values (Pydantic + schema), omitted-mode-still-valid (byte-identical), and a schema-vs-Literal
  drift guard (introspects `CircuitBreakerSpec.model_fields["mode"].annotation` via
  `typing.get_args` and asserts it exactly equals the schema's enum set).

## Evidence
- `uv run pytest tests/test_routing_breaker_models.py -q` → 40 passed (33 pre-existing + 7 new).
- `uv run pytest -q` (full suite) → 693 passed, 3 skipped (baseline 686 passed/3 skipped + 7 new,
  zero regressions).
- `uv run ruff check src/agent_orchestrator/models.py tests/test_routing_breaker_models.py` →
  All checks passed. `ruff format --check` → 2 files already formatted.
- `uv run mypy src` → 4 errors, all pre-existing in `_version.py` (unchanged from baseline).

## Risks / Blockers
- None.

## Next actions
1. Done — no further action for this task. `T-TdildW`/`T-yjtdAq` will read `spec.mode` at the
   consult boundary.
