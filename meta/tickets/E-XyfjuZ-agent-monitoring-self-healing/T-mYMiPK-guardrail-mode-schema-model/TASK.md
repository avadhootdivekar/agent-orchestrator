# TASK: T-mYMiPK-guardrail-mode-schema-model

## Metadata
- Task ID: `T-mYMiPK-guardrail-mode-schema-model`
- Epic ID: `E-XyfjuZ-agent-monitoring-self-healing`
- Owner: dev-epic agent (self-implemented)
- Created: 2026-07-14
- Last Updated: 2026-07-14
- Status: Draft
- Estimate: 0.5 day

## Requirements Mapping
- Requirement IDs: FR-1, FR-7

## Description
Add the guardrail `mode` field to `CircuitBreakerSpec` (`src/agent_orchestrator/models.py:190`)
and `specs/workflow.schema.json`'s `circuitBreaker` `$def`. Default `"hard"` so every existing
workflow (which never declares `mode`) is byte-identical. Add a test proving the schema enum and
the Pydantic `Literal` never drift apart (mirrors the `BREAKER_REGISTRY`-derived allowlist fix in
commit 8debeb3 — derive from one source of truth, never hand-copy).

## Acceptance Criteria
1. `CircuitBreakerSpec.mode: Literal["hard", "recommend"] = "hard"`.
2. `specs/workflow.schema.json`'s `circuitBreaker.properties.mode` is `{"enum": ["hard",
   "recommend"], "default": "hard"}`; `additionalProperties: false` still holds (already present).
3. A workflow declaring `mode: "bogus"` fails `ao validate` / `load_workflow` (schema rejects
   before Pydantic ever sees it).
4. A test asserts the schema's `mode` enum values exactly match
   `get_args(CircuitBreakerSpec.model_fields["mode"].annotation)` (or equivalent introspection) —
   fails loudly on future drift instead of silently accepting/rejecting the wrong set.
5. Existing breaker tests (`test_breakers.py`, `test_mvp_breaker_conditions.py`,
   `test_breaker_extension.py`, `test_routing_breaker_models.py`,
   `test_run_active_seconds_breaker.py`) stay green unmodified (constructing a
   `CircuitBreakerSpec` without `mode=` must still work — the new field is optional/defaulted).

## Risks
- Low risk: purely additive field with a default; no behavior change unless `mode` is read
  elsewhere (which only happens starting in `T-TdildW`).

## Dependencies
- None (first task in the sequence; everything else depends on this field existing).

## Pseudocode / Algorithm
```text
CircuitBreakerSpec adds: mode: Literal["hard", "recommend"] = "hard"
schema circuitBreaker.properties adds: "mode": {"enum": [...], "default": "hard"}
```

## Schemas / Interface Notes
- Interface / API: `CircuitBreakerSpec.mode` (Pydantic field).
- Spec / data schema (JSON/YAML): `specs/workflow.schema.json` `$defs.circuitBreaker.properties.mode`.
- Triggers / events (cron/event): N/A.
- Artifacts (inputs/outputs by path): N/A (schema/model only, no runtime artifact).

## Handoff Boundary
- Upstream: none.
- Downstream: `T-TdildW-breaker-consult-engine` reads `spec.mode` to decide hard-vs-recommend at
  the consult boundary; `T-Wx8vUq-tests-e2e-monitoring` covers acceptance criteria 3-5 fully.

## Artifacts
- Docs/comments: `meta/tickets/E-XyfjuZ-agent-monitoring-self-healing/T-mYMiPK-guardrail-mode-schema-model/`
- Large outputs: none.
