# TASK: T-n9k3r5-observability-ao-status

## Metadata
- Task ID: `T-n9k3r5-observability-ao-status`
- Epic ID: `E-rc7k2v-run-control-routing-breakers`
- Owner: developer
- Created: 2026-07-09
- Last Updated: 2026-07-09
- Status: Done
- Estimate: `~1.5 days`

## Requirements Mapping
- FR-CB4, HLD §7. LLD §10.

## Description
Surface routing + breaker facts in run state and `ao status`. In `write_status`: seed the `counts` dict with
`not_taken`, add per-task `route`/`not_taken_reason`, and add top-level `route_decisions` + `tripped_breakers`
summaries to `status.json`. In `ao status` (`_print_status_snapshot`/`_print_state`): add a `Route` column,
render `not_taken` rows, and print a trailer line listing tripped breakers (`id (condition, action)`). Confirm
the `branch.route`/`breaker.trip` events reach the per-run structured log via the existing handler.

## Acceptance Criteria
1. `status.json` counts include a `not_taken` key (seeded to 0), each task entry carries `route` +
   `not_taken_reason`, and top-level `route_decisions`/`tripped_breakers` summaries are present.
2. `ao status <run>` prints a `Route` column and, when any breaker tripped, a trailer:
   `Tripped breakers: <id> (<condition>, action=<action>)`.
3. A multi-endpoint run's `ao status` visibly distinguishes `succeeded`/`not_taken`/`skipped` rows.
4. `branch.route` and `breaker.trip` events appear in `run.log` for a routed + breaker-tripped run.
5. CliRunner E2E asserts the status output for a routed run and a tripped-breaker run
   (memory `engine-api-tests-dont-cover-cli`); `uv run ruff/mypy/pytest` clean.

## Risks
- `write_status` currently hard-codes 7 status keys — add `not_taken` to the seed dict (KeyError-safe already via
  `.get`, but seed for a stable snapshot).
- Keep table columns aligned; `route` may be None (render blank).

## Dependencies
- Upstream: T-m2h5t7 (`route`/`route_decisions`), T-x8v4d3 + T-r3j9b6 (`tripped_breakers`).
- Downstream: T-d8w4v2 (docs screenshots/examples).

## Pseudocode / Algorithm
See LLD §10.2 (write_status + ao status rendering).

## Schemas / Interface Notes
- Interface: `write_status`, `_print_status_snapshot`, `_print_state`.
- Events: consumes `branch.route`/`breaker.trip` (emitted by earlier tasks).
- Artifacts: `status.json` gains route/breaker fields.

## Handoff Boundary
- Upstream: LLD §10; T-m2h5t7/T-x8v4d3/T-r3j9b6.
- Downstream: docs refresh.

## Artifacts
- Docs/comments: `ad/tickets/E-rc7k2v-run-control-routing-breakers/T-n9k3r5-observability-ao-status/`
