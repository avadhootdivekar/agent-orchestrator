# TASK: T-m2h5t7-routing-execution-run-success

## Metadata
- Task ID: `T-m2h5t7-routing-execution-run-success`
- Epic ID: `E-rc7k2v-run-control-routing-breakers`
- Owner: developer
- Created: 2026-07-09
- Last Updated: 2026-07-09
- Status: Done
- Estimate: `~3 days`

## Requirements Mapping
- FR-B1, FR-B3, FR-B4, FR-B5, NFR-4. LLD §5, §0-R1.

## Description
Wire routing into the engine loop: (1) a router-success hook that reads the verdict via `read_routes`, records
`state.route_decisions[router.id]`, marks unselected cones `not_taken`, tags activated tasks' `route`, emits
`branch.route`; (2) a top-of-loop skip so `not_taken` tasks never dispatch; (3) join handling at dispatch
(`all` propagates not_taken, `any` runs on ≥1 live dep) plus the `any` missing-input relaxation (§5.4a);
(4) injected tasks inherit the emitter's `route` (R4); (5) run-success unchanged at the finaliser (not_taken
never sets `failed`). Unknown/empty verdict → `default_route` or validation-style run-fail.

## Acceptance Criteria
1. `Given` a router verdict `{"routes":["bug"]}`, `when` run, `then` only the bug cone executes; other cones are
   `not_taken` with `attempts==0` and no budget charge; run ends `succeeded` (FR-B3, FR-B4).
2. Multi-select `{"routes":["bug","documentation"]}` activates both cones; the untaken cone is `not_taken`.
3. `join:all` convergence task with a not_taken dep becomes `not_taken`; `join:any` runs when ≥1 dep succeeded and
   tolerates inputs whose sole producer was not_taken (no missing-input failure).
4. Empty/unknown verdict with `default_route=null` fails the run (`branch.route` carries `error`); with a
   `default_route` set, that route activates.
5. An injected task on an activated branch inherits `route`; a not_taken emitter never injects (never dispatched).
6. Determinism (NFR-2): identical verdicts → identical activation set + topo order across two runs.
7. Both an engine-API integration test and a CliRunner E2E test exist (memory `engine-api-tests-dont-cover-cli`);
   `uv run ruff/mypy/pytest` clean.

## Risks
- Overriding a shared task already `succeeded` → guard (§5.2): never flip settled tasks to not_taken.
- Missing-input check ordering vs join (§5.4a) — relax only for `any` and only for sole-not_taken producers.
- Interaction with existing `done` set — keep `done` = succeeded/skipped; not_taken handled by the skip check.

## Dependencies
- Upstream: T-c4w6p1 (cones/membership/producer-map), T-k9r3n8 (`read_routes`), T-b7q2m4 (models).
- Downstream: T-t4m8x1 (resume replay), T-n9k3r5 (status/route column).

## Pseudocode / Algorithm
See LLD §5.2 (router-success hook), §5.3 (skip), §5.4/§5.4a (join + input relaxation), §5.5 (injection), §5.6.

## Schemas / Interface Notes
- Interface: engine hooks + `on_router_success`, `apply_join`, `route_fail`.
- Events: `branch.route` (see LLD §10.1).
- Artifacts: reads router verdict JSON (bounded control) only.

## Handoff Boundary
- Upstream: LLD §5; T-c4w6p1/T-k9r3n8/T-b7q2m4.
- Downstream: resume + observability build on `route_decisions`/`not_taken`.

## Artifacts
- Docs/comments: `meta/tickets/E-rc7k2v-run-control-routing-breakers/T-m2h5t7-routing-execution-run-success/`
