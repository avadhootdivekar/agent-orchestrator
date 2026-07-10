# TASK: T-b7q2m4-branch-breaker-schema-models

## Metadata
- Task ID: `T-b7q2m4-branch-breaker-schema-models`
- Epic ID: `E-rc7k2v-run-control-routing-breakers`
- Owner: developer
- Created: 2026-07-09
- Last Updated: 2026-07-09
- Status: Done
- Estimate: `~2.5 days`

## Requirements Mapping
- FR-B2, FR-B5, FR-CB1, NFR-5. LLD §2 (data model), §0-R1.

## Description
Land the spec surface + models for routing and breakers. Add `branches` and `circuit_breakers` to
`workflow.schema.json` plus per-task `join`; add pydantic `RouteSpec`/`RouterSpec`/`CircuitBreakerSpec`/
`TrippedBreaker`; widen `TaskStatus` with `not_taken`; add defaulted fields to `TaskRunState`
(`route`, `not_taken_reason`) and `RunState` (`route_decisions`, `tripped_breakers`); add named breaker-id and
`MAX_CONTROL_FILE_BYTES` constants. No engine behaviour yet — schema + models + round-trip only. Foundational;
blocks every other task.

## Acceptance Criteria
1. `specs/workflow.schema.json` accepts a workflow with `branches`, `circuit_breakers`, and task `join`;
   `additionalProperties:false` still rejects unknown keys; conditional `required` per breaker condition (§2.1)
   enforced (e.g. `verdict` requires `task_id`+`verdict_path`).
2. `WorkflowSpec` parses the new blocks; `TaskSpec.join` defaults `"all"`; all new `RunState`/`TaskRunState`
   fields have defaults.
3. An **old** `state.json` written before this change deserialises with defaults (NFR-5) — regression test loads a
   fixture lacking `route_decisions`/`tripped_breakers`.
4. `TaskStatus` includes `not_taken`; a `TaskRunState(status="not_taken")` round-trips through
   `model_dump_json`/`model_validate_json`.
5. `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`, `uv run pytest -q` all pass.

## Risks
- Schema drift vs models (memory `agentspec-schema-must-stay-in-sync`, `agents-json-required-fields`): keep schema
  `$defs` and pydantic fields in one commit.
- Widening `TaskStatus` must not break `write_status` counts dict (handled in T-n9k3r5, but seed key here if easy).

## Dependencies
- Upstream: none (first task).
- Downstream: blocks T-k9r3n8, T-c4w6p1, T-x8v4d3, and transitively all others.

## Pseudocode / Algorithm
See LLD §2.1 (schema diff) and §2.2 (models). No control flow — declarative additions + defaults.

## Schemas / Interface Notes
- Spec: new `$defs` `route`, `router`, `circuitBreaker`; `branches`/`circuit_breakers` top-level; task `join`.
- Models: `RouteSpec`, `RouterSpec`, `CircuitBreakerSpec`, `TrippedBreaker`; field additions with defaults.
- Constants: `BUILTIN_BUDGET_EXHAUSTED`, `BUILTIN_BUDGET_UNSATISFIABLE`, `BUILTIN_QUOTA_MAX_WAIT`,
  `MAX_CONTROL_FILE_BYTES`.
- Triggers/events: N/A.
- Artifacts: none (types only).

## Handoff Boundary
- Upstream: LLD §2.
- Downstream: every consumer imports these types; do not add engine logic here.

## Artifacts
- Docs/comments: `ad/tickets/E-rc7k2v-run-control-routing-breakers/T-b7q2m4-branch-breaker-schema-models/`
