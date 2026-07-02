# TASK: T-oh5gl5-budget-config-schema

## Metadata
- Task ID: `T-oh5gl5-budget-config-schema`
- Epic ID: `E-j4gno6-token-budget-rate-limit`
- Owner: TODO
- Created: 2026-06-18
- Last Updated: 2026-06-18
- Status: Draft
- Estimate: < 2 days

## Requirements Mapping
- Requirement IDs: FR-1, FR-2, FR-7, FR-9, NFR-5, NFR-7

## Description
Land the shared config + counter models for the epic and wire them into the spec
schema and `RunState`. This is the foundation task: estimator, budget manager,
executor, and engine all depend on these types. No behavior yet — just models,
schema, validation, and persistence plumbing.

Add to `src/agent_orchestrator/models.py`:
- `RateLimit`, `EstimatorConfig`, `BudgetSpec`, `BudgetCounters` (see Interface Contracts in EPIC.md).
- Add an optional `budget: BudgetSpec | None = None` field to `WorkflowSpec` (sibling of `defaults`).
- Add `budget_counters: BudgetCounters = BudgetCounters()` to `RunState` (persisted; default empty so old states load).

Add to `specs/workflow.schema.json` a `budget` object under top-level properties
(schema is `additionalProperties: false`, so it MUST be declared) + a `$defs/budget`,
`$defs/rateLimit`, `$defs/estimatorConfig`.

Add cross-validation in `src/agent_orchestrator/spec.py` (`cross_validate`) /
`validate.py`: exactly one of `rate.window` / `rate.window_seconds` is set; `total_tokens`
and `rate.tokens` are positive when present; `pessimism_buffer >= 1.0`.

Add named constants module-level (NFR-7): `DEFAULT_CHARS_PER_TOKEN=4`,
`DEFAULT_PESSIMISM_BUFFER=1.3`, `DEFAULT_OUTPUT_ALLOWANCE_TOKENS=1000`,
`WINDOW_SECONDS = {"minute":60, "ten_minutes":600, "hour":3600}`.

## Acceptance Criteria
1. Given a workflow spec with a valid `budget` block (total + rate + on_exhaustion + estimator), When loaded via `load_workflow`, Then `wf.budget` is a populated `BudgetSpec` and the spec validates against `workflow.schema.json`.
2. Given a `budget.rate` that sets **both** `window` and `window_seconds` (or neither), When cross-validated, Then a `SpecValidationError` naming the conflicting fields is raised.
3. Given `pessimism_buffer < 1.0` or a non-positive `total_tokens`/`rate.tokens`, When cross-validated, Then a `SpecValidationError` is raised.
4. Given an existing `RunState` JSON written before this task (no `budget_counters`), When loaded, Then it parses with a default empty `BudgetCounters` (backward compatible — no migration needed).
5. Given a workflow with **no** `budget` block, When loaded, Then `wf.budget is None` and all existing tests still pass (feature is opt-in, zero behavior change).
6. The constants `1.3`, `4`, `1000`, and window mappings appear **only** as named constants (grep shows no bare magic literals in budget logic). `ruff`, `mypy`, `pytest` green; schema round-trips (load → re-serialize → re-validate).

## Risks
- Schema `additionalProperties:false` means forgetting any field makes valid specs fail validation — add a round-trip test for every field.
- `RunState` backward compatibility: default factory must not break loading old states.

## Dependencies
- Upstream: none (foundation task).
- Downstream: T-n7hmwj, T-7kp8iv, T-1m9744 (all consume these models).

## Pseudocode / Algorithm
```text
# spec.cross_validate additions
IF wf.budget is not None:
  b = wf.budget
  IF b.total_tokens is not None AND b.total_tokens <= 0: RAISE SpecValidationError("budget.total_tokens must be > 0")
  IF b.rate is not None:
    has_window = b.rate.window is not None
    has_secs   = b.rate.window_seconds is not None
    IF has_window == has_secs: RAISE SpecValidationError("set exactly one of rate.window / rate.window_seconds")
    IF b.rate.tokens <= 0: RAISE SpecValidationError("budget.rate.tokens must be > 0")
  IF b.estimator.pessimism_buffer < 1.0: RAISE SpecValidationError("pessimism_buffer must be >= 1.0")
```

## Schemas / Interface Notes
- Interface / API: models in `models.py` per EPIC Interface Contracts (`RateLimit`, `EstimatorConfig`, `BudgetSpec`, `BudgetCounters`); `WorkflowSpec.budget`; `RunState.budget_counters`.
- Spec / data schema (JSON): new `budget` block —
```jsonc
"budget": {
  "type": "object", "additionalProperties": false,
  "properties": {
    "total_tokens": { "type": "integer", "minimum": 1 },
    "rate": { "type": "object", "additionalProperties": false,
      "required": ["tokens"],
      "properties": {
        "tokens": { "type": "integer", "minimum": 1 },
        "window": { "enum": ["minute","ten_minutes","hour"] },
        "window_seconds": { "type": "integer", "minimum": 1 }
      } },
    "on_exhaustion": { "enum": ["stop","wait"], "default": "stop" },
    "estimator": { "type": "object", "additionalProperties": false,
      "properties": {
        "chars_per_token": { "type": "integer", "minimum": 1, "default": 4 },
        "pessimism_buffer": { "type": "number", "minimum": 1.0, "default": 1.3 },
        "output_allowance_tokens": { "type": "integer", "minimum": 0, "default": 1000 }
      } }
  }
}
```
- Triggers / events: N/A.
- Artifacts: none (models + schema only).

## Handoff Boundary
- Upstream: epic Interface Contracts (frozen model shapes).
- Downstream: hands `BudgetSpec`/`EstimatorConfig`/`BudgetCounters`/`TaskResult` field plan to estimator, manager, executor tasks.
