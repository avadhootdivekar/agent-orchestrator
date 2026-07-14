# TASK: T-t4m8x1-resume-replay

## Metadata
- Task ID: `T-t4m8x1-resume-replay`
- Epic ID: `E-rc7k2v-run-control-routing-breakers`
- Owner: developer
- Created: 2026-07-09
- Last Updated: 2026-07-09
- Status: Done
- Estimate: `~2 days`

## Requirements Mapping
- FR-CB5, NFR-2, NFR-5. LLD §9, §0-R1.

## Description
Make routing + breaker facts survive `ao resume`. In `prepare_resume`: (1) never reset a `not_taken` task to
pending; (2) deterministically re-derive not_taken from persisted `state.route_decisions` via `compute_cones`
(do NOT re-read verdict files, NFR-2); the router stays `succeeded` (never re-runs). Confirm `tripped_breakers`
persists and that resume re-evaluates breakers fresh — a cleared transient condition (e.g. removed `stop_file`)
does not re-trip, while a still-true structural condition (`injected_task_count` over cap) does.

## Acceptance Criteria
1. `Given` a run stopped mid-way after a route decision, `when` resumed, `then` the router is not re-run, the
   recorded route replays, and previously-not_taken tasks stay `not_taken` (not reset to pending).
2. A task that was still `pending` at stop but belongs to an unselected cone is re-derived to `not_taken` on
   resume (idempotent, from `route_decisions`).
3. `stop_file` breaker: removing the file before resume → not re-tripped; leaving it → re-trips.
4. `injected_task_count` breaker still over cap after resume → re-trips immediately (documented); failure-count
   breakers start clean because `prepare_resume` reset failed→pending.
5. Old `state.json` without `route_decisions`/`tripped_breakers` resumes with defaults (NFR-5).
6. Both engine-API and CliRunner resume tests; `uv run ruff/mypy/pytest` clean.

## Risks
- Re-deriving not_taken must not clobber a task that legitimately `succeeded` before deactivation (guard as in §5.2).
- `compute_cones` on resume must run after injected tasks are re-attached to `workflow.tasks` (existing
  prepare_resume merge order).
- Determinism of re-derivation (NFR-2) — no verdict re-read.

## Dependencies
- Upstream: T-m2h5t7 (routing + `route_decisions`), T-x8v4d3 (breaker records), T-c4w6p1 (cones).
- Downstream: T-d8w4v2 (docs/e2e).

## Pseudocode / Algorithm
See LLD §9 (prepare_resume changes + breaker re-evaluation semantics).

## Schemas / Interface Notes
- Interface: `prepare_resume` extension in `runstate.py`.
- Artifacts: reads persisted `state.json` only; no verdict re-read.
- Triggers/events: N/A.

## Handoff Boundary
- Upstream: LLD §9; T-m2h5t7/T-x8v4d3.
- Downstream: docs/tests.

## Artifacts
- Docs/comments: `ad/tickets/E-rc7k2v-run-control-routing-breakers/T-t4m8x1-resume-replay/`
