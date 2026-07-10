# TASK: T-r3j9b6-reframe-existing-stops

## Metadata
- Task ID: `T-r3j9b6-reframe-existing-stops`
- Epic ID: `E-rc7k2v-run-control-routing-breakers`
- Owner: developer
- Created: 2026-07-09
- Last Updated: 2026-07-09
- Status: Done
- Estimate: `~2 days`

## Requirements Mapping
- HLD §5, FR-CB1/CB4, R3. LLD §8, §0-R3, ADR-RC-003.

## Description
Re-frame the three existing hard-coded terminal stops (budget-exhaustion `stop`, unsatisfiable-estimate,
quota-max-wait) onto the trip→record→act path **additively**: at each terminal site, call
`trip_builtin(...)` to append a `TrippedBreaker` (reserved `builtin.*` id) and emit `breaker.trip`, while keeping
the existing `budget.exhausted` / `quota.max_wait_exceeded` events, `state.status="failed"`, and break EXACTLY
as they are. Transient wait/retry loops (429 wait, quota poll, budget wait) are NOT trips. Zero change to final
status, exit code, or pre-existing events — proven by characterization tests written first.

## Acceptance Criteria
1. **Characterization pins committed first**: `test_budget_exhaustion_stop_parity`,
   `test_unsatisfiable_estimate_parity`, `test_quota_max_wait_parity` (LLD §8.3) capturing current
   status/exit-code/existing-event, at both engine-API and CliRunner levels.
2. After the re-frame, all three pins still pass on the pinned lines (status/exit/existing event unchanged) AND
   each now additionally records the mapped `builtin.*` breaker + a `breaker.trip` event.
3. Transient waits emit no `breaker.trip` and do not append to `tripped_breakers`.
4. Built-in breaker ids are the named constants from T-b7q2m4 (no magic literals).
5. `uv run ruff/mypy/pytest` clean; no unrelated behaviour changes in `engine.py`.

## Risks
- Behaviour drift — the pins are the guardrail; do NOT weaken a pinned assertion to make it pass.
- Ordering: `trip_builtin` runs *before* the existing failed/break lines so the record exists at run end.
- Do not double-fail: `trip_builtin` records; the existing lines still own the terminal transition.

## Dependencies
- Upstream: T-x8v4d3 (record/emit path via `trip_builtin`), T-b7q2m4 (builtin id constants).
- Downstream: T-n9k3r5 (status trailer lists built-in trips), T-d8w4v2 (docs reconcile).

## Pseudocode / Algorithm
See LLD §8.2 (`trip_builtin`) and §8.1 (current sites: engine.py :292-324, :421-437, :531-540).

## Schemas / Interface Notes
- Interface: `trip_builtin(state, id, condition, action, detail, clock, run_log)`.
- Events: adds `breaker.trip`; keeps `budget.exhausted`, `quota.max_wait_exceeded`.
- Artifacts: none new.

## Handoff Boundary
- Upstream: LLD §8; T-x8v4d3.
- Downstream: observability + docs; no schema change.

## Artifacts
- Docs/comments: `ad/tickets/E-rc7k2v-run-control-routing-breakers/T-r3j9b6-reframe-existing-stops/`
