# STATUS

- ID: `T-b7q2m4-branch-breaker-schema-models`
- Updated At: 2026-07-09
- State: Done
- Owner: developer

## This update
Implemented per LLD §2.1/§2.2: schema `branches`/`circuit_breakers`/task `join` + `$defs`
`route`/`router`/`circuitBreaker`; pydantic `RouteSpec`/`RouterSpec`/`CircuitBreakerSpec`/
`TrippedBreaker`; `TaskStatus` widened with `not_taken`; defaulted `TaskRunState.route`/
`not_taken_reason` and `RunState.route_decisions`/`tripped_breakers`; named breaker-id +
`MAX_CONTROL_FILE_BYTES` constants. Types/schema only — no engine/control-flow behaviour.

By: architect · Role: architect · Date: 2026-07-09 · Comment: Wave-1 foundation. Schema `$defs` + pydantic must land in one commit (memory `agentspec-schema-must-stay-in-sync`); all new persisted fields defaulted (NFR-5).

By: developer · Role: developer · Date: 2026-07-09 · Comment: All 5 acceptance criteria met.
(1) `specs/workflow.schema.json`: added `branches`/`circuit_breakers` top-level + task `join` +
new `$defs` `route`/`router`/`circuitBreaker` with the three conditional-`required` `allOf`
rules (task_failures/consecutive_failures/run_wall_clock_seconds/injected_task_count →
threshold; verdict → task_id+verdict_path; stop_file → path); `additionalProperties:false`
still rejects unknown keys at every level (verified with `jsonschema.validate` directly, incl.
negative cases). (2) `models.py`: `RouteSpec`, `RouterSpec`, `CircuitBreakerSpec`,
`TrippedBreaker`, `BreakerCondition` Literal, `BUILTIN_BUDGET_EXHAUSTED`/
`BUILTIN_BUDGET_UNSATISFIABLE`/`BUILTIN_QUOTA_MAX_WAIT`/`MAX_CONTROL_FILE_BYTES` constants;
`TaskSpec.join: Literal["all","any"]="all"`; `WorkflowSpec.branches`/`circuit_breakers` default
`[]`; confirmed `RunState`/`TaskRunState` live in `models.py` (not `runstate.py`) so no move was
needed. (3) NFR-5 regression: new fixture `tests/fixtures/state_pre_routing_breakers.json` (a
pre-change `state.json` lacking `route_decisions`/`tripped_breakers`/`route`/`not_taken_reason`)
loads via `RunState.model_validate_json` with all new fields defaulting cleanly, and round-trips
through dump/reload unchanged. (4) `TaskStatus` widened with `"not_taken"`;
`TaskRunState(status="not_taken")` round-trips through `model_dump_json`/`model_validate_json`.
(5) Also seeded `"not_taken": 0` in `runstate.py::write_status`'s counts dict (ticket's own "if
easy" note) so the snapshot key is always present, not just when a not_taken task exists — pure
bookkeeping, no control-flow change. New test module `tests/test_routing_breaker_models.py` (26
tests) covers all 5 ACs incl. negative schema cases.
Verification (scoped to this ticket's files): `uv run ruff check src/agent_orchestrator/models.py
src/agent_orchestrator/runstate.py tests/test_routing_breaker_models.py` → All checks passed.
`uv run ruff format --check` (same files) → 3 files already formatted. `uv run mypy
src/agent_orchestrator/models.py src/agent_orchestrator/runstate.py` → Success, no issues.
`uv run pytest -q` (full suite) → 431 passed, 3 skipped, no regressions.
Whole-repo `ruff check .`/`ruff format --check .`/`mypy .` surface pre-existing issues in files
this ticket does not touch (`cli.py`, `engine.py`, `artifacts.py` import order, `test_e2e_cli.py`,
`test_engine.py`, `test_engine_budget.py`, `test_executor.py`, `test_project_config.py`) — confirmed
via `git status` that none of these are part of this diff; out of scope per the "no engine changes"
constraint and left untouched (one, `test_dynamic_injection.py`, is concurrently owned by
in-flight ticket T-h5b2q7 in this shared worktree).

## Evidence
- Design: `docs-md/lld-run-control-routing-breakers.md` §2, §0-R1.
- Code: `specs/workflow.schema.json`, `src/agent_orchestrator/models.py`,
  `src/agent_orchestrator/runstate.py` (one-line `not_taken` counts-seed only).
- Tests: `tests/test_routing_breaker_models.py`, `tests/fixtures/state_pre_routing_breakers.json`.

## Risks / Blockers
- None. Schema and models land together in this change (memory
  `agentspec-schema-must-stay-in-sync` honoured).

## Next actions
1. Unblocked: T-k9r3n8, T-c4w6p1, T-x8v4d3 can start.
