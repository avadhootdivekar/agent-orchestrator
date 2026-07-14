# TASK: T-q5n7k2-mvp-breaker-conditions

## Metadata
- Task ID: `T-q5n7k2-mvp-breaker-conditions`
- Epic ID: `E-rc7k2v-run-control-routing-breakers`
- Owner: developer
- Created: 2026-07-09
- Last Updated: 2026-07-09
- Status: Done
- Estimate: `~2.5 days`

## Requirements Mapping
- FR-CB1, FR-CB3, NFR-4, HLD §6 MVP set. LLD §7. (Delivers E2 `injected_task_count` to epic E-gd8m4x.)

## Description
Implement and register the six MVP breaker conditions: `task_failures`, `consecutive_failures`,
`run_wall_clock_seconds` (injected clock, from `state.started_at`), `verdict` (via `read_bool_field`),
`injected_task_count`, `stop_file` (existence-only). Each is a `Breaker` returning a `TripResult` with a `detail`
payload exactly at threshold. A non-MVP condition reaching the registry raises "condition not implemented in this
release" (surfaced at validate time by T-w6p2c8).

## Acceptance Criteria
1. Under a **fixed clock**, each condition trips exactly at threshold and NOT before (parametrised unit tests):
   `task_failures>=N`, `consecutive_failures` trailing run `>=N`, `run_wall_clock_seconds` elapsed `>=T`,
   `verdict` when the named task settled succeeded and its `{field:true}`, `injected_task_count>=cap`,
   `stop_file` when the path exists.
2. `verdict` reads via the shared `read_bool_field` (no second reader) and only evaluates once the named
   `task_id` is settled `succeeded`.
3. `injected_task_count` counts `len(state.injected_tasks)` incl. nested-emitted tasks (NFR-4 / E2).
4. `consecutive_failures` uses completion order and resets its streak on a success; on resume it reconstructs
   order from `ended_at` (LLD §7 note).
5. `stop_file` uses `store.exists` (never content) and a workspace-guarded path.
6. `uv run ruff/mypy/pytest` clean; each condition has a boundary test (at, below, above threshold).

## Risks
- `run_wall_clock_seconds` boundary granularity (task boundary, not real-time) — documented; test asserts trip at
  the next boundary after the deadline.
- Timezone/clock: always via injected clock (fixed in tests) — memory-consistent with budget tests.
- `consecutive_failures` order reconstruction on resume must be deterministic.

## Dependencies
- Upstream: T-x8v4d3 (framework/registry), T-k9r3n8 (`read_bool_field`), T-b7q2m4 (specs).
- Downstream: T-r3j9b6 (built-ins parallel these), T-n9k3r5 (status trailer), E-gd8m4x consumes `injected_task_count`.

## Pseudocode / Algorithm
See LLD §7 (trip predicates table + notes).

## Schemas / Interface Notes
- Interface: six `Breaker` subclasses registered by condition name.
- Events: `breaker.trip` via the framework.
- Artifacts: `verdict` reads bounded control; `stop_file` existence-only.

## Handoff Boundary
- Upstream: LLD §7; T-x8v4d3/T-k9r3n8.
- Downstream: none new; conditions are consumed by the eval loop.

## Artifacts
- Docs/comments: `meta/tickets/E-rc7k2v-run-control-routing-breakers/T-q5n7k2-mvp-breaker-conditions/`
