# TASK: T-x8v4d3-breaker-framework-registry

## Metadata
- Task ID: `T-x8v4d3-breaker-framework-registry`
- Epic ID: `E-rc7k2v-run-control-routing-breakers`
- Owner: developer
- Created: 2026-07-09
- Last Updated: 2026-07-09
- Status: Done
- Estimate: `~2.5 days`

## Requirements Mapping
- FR-CB1, FR-CB2, FR-CB4. LLD §6.

## Description
Add the pluggable breaker framework: a `Breaker` ABC + `BreakerContext` (paths/ids/counters only — NFR-1), a
`BREAKER_REGISTRY` (condition name → instance), and the engine's evaluation loop at task boundaries
(trip → record → act). A trip appends to `state.tripped_breakers` (latched once per id), emits `breaker.trip`,
and the first tripped breaker in evaluation order owns the action. Implement action execution for `fail`/`stop`/
`pause` at `run` scope per ADR-RC-004 (all land on resumable `status="failed"`; the real action is recorded in
`tripped_breakers[].action`). Ship the framework with **no conditions registered yet** (or a trivial test-only
one); the six MVP conditions land in T-q5n7k2.

## Acceptance Criteria
1. `evaluate_breakers` runs at the task boundary (after outcome handling + save), before the next dispatch.
2. A tripped breaker appends exactly one `TrippedBreaker` (latched by id — re-evaluation does not duplicate) and
   emits one `breaker.trip` event with `{breaker_id, condition, action, detail, at}`.
3. Two breakers tripping at one boundary both record; the first in evaluation order (declared spec order, then
   built-ins) executes its action (deterministic).
4. `fail`/`stop`/`pause` all terminate the loop with `status="failed"` (resumable); exit code 1; `action` value is
   preserved distinctly in the record.
5. Breaker evaluation is pure w.r.t. the injected clock (no `time.time()` in breaker code).
6. `uv run ruff/mypy/pytest` clean; unit tests use a stub breaker to prove trip/record/act/latch.

## Risks
- Evaluation-order non-determinism → fix order (declared, then built-in) and test it.
- Double-action if two breakers trip → only the first acts; assert.
- Keep the breaker ABC free of engine imports (DI style like `Executor`/`BudgetManager`).

## Dependencies
- Upstream: T-b7q2m4 (`CircuitBreakerSpec`, `TrippedBreaker`).
- Downstream: T-q5n7k2 (conditions), T-r3j9b6 (built-ins reuse `trip`/record path), T-t4m8x1, T-n9k3r5.

## Pseudocode / Algorithm
See LLD §6.2 (registry/ABC), §6.3 (eval loop), §6.4 (actions).

## Schemas / Interface Notes
- Interface: `Breaker` ABC, `BreakerContext`, `BREAKER_REGISTRY`, `evaluate_breakers`, `map_action`.
- Events: `breaker.trip` (LLD §10.1).
- Artifacts: breaker reads bounded control (verdict/stop_file) only.

## Handoff Boundary
- Upstream: LLD §6; T-b7q2m4.
- Downstream: conditions + re-frame register into the same loop; do not implement the six conditions here.

## Artifacts
- Docs/comments: `ad/tickets/E-rc7k2v-run-control-routing-breakers/T-x8v4d3-breaker-framework-registry/`
