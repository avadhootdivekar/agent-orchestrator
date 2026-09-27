# TASK: T-AsQ77e-run-graph-endpoint

## Metadata
- Task ID: `T-AsQ77e-run-graph-endpoint`
- Epic ID: `E-k3AMEr-run-graph-canvas`
- Owner: `developer` (Dev A)
- Created: `2026-09-27`
- Last Updated: `2026-09-27`
- Status: `Done`
- Estimate: `6 focus hours (< 1 day)`

## Requirements Mapping
- Requirement IDs: FR-4, FR-7, D-3, D-4, NFR-1, NFR-6 · HLD §8.4, §14.2 · ADR-0017 D4

## Description
Wire the pure builder to HTTP:
- `RunRepository.load_graph(run_id)` does the I/O. It loads `state.json` and loads the snapshot for
  the latest `spec_sessions` sha via `runstate.load_workflow_snapshot_at` (the shared bounded parser).
- `DashboardService.run_graph(run_id)` wraps it.
- The route is `GET /api/runs/{run_id}/graph`.

Additive changes to the existing detail payload:
- `RunDetail.graph_version = compute_graph_version(state)`, the same function as the builder, so
  the two values are equal by construction.
- `TaskStat.dispatch_cycle` and `TaskStat.not_taken_reason`.

There is **no launch-record fallback**. It was removed after the security review, and this task
must not call `_workflow_for_run`.

§14.2 was **frozen at design time**. This task *verifies conformance* against the frontend's
checked-in fixture. It does not redefine the contract.

## Acceptance Criteria
1. `GET /api/runs/{id}/graph` for a snapshot-backed run returns 200, with `source="snapshot"` and
   `schema_version=1`. The node, edge, and spawn sets equal `build_run_graph(state, snapshot)` for
   the same inputs.
2. **404s.** An unknown run id gives 404 `{"detail": "run not found: <id>"}`. The traversal ids
   `..%2F..%2Fetc` and `../x` give 404 (existing `run_dir` guard). An unreadable `state.json` gives 404.
3. **Degraded = 200 with warnings, never 5xx.** Each of the following returns 200 with
   `source="unavailable"` and a non-empty `warnings`:
   - a pre-epic run (no `spec_sessions`)
   - a run whose snapshot file was deleted
   - a snapshot over the size cap (monkeypatched cap)
   - a snapshot with a mismatched sha
   - invalid snapshot JSON

   A `ui.graph.degraded` warning is logged for every case except the pre-epic one.
4. **Version equality** (reviewer MUST-FIX). For each run kind in AC-1 and AC-3,
   `GET /api/runs/{id}` `graph_version` == `GET /api/runs/{id}/graph` `graph_version`.
5. **Detail additive only.** Every pre-existing `RunDetail`/`TaskStat` key keeps its value for an
   existing fixture (a key-by-key comparison against the pre-change payload). The new keys
   `graph_version`, `tasks[].dispatch_cycle`, and `tasks[].not_taken_reason` are present.
6. **No leakage.** For a fixture whose workflow has hooks (`command` argv), task instructions, and
   integration commands, the `/graph` JSON contains none of those strings (the test searches the
   serialized response for each sentinel string).
7. **Contract test.** `tests/ui/test_graph_contract.py` loads `ui/src/test/fixtures/run-graph.json`
   (from T-adVpTj) and asserts that its top-level keys, node keys, edge keys, and enum values equal
   the `RunGraph` dataclass field set and the documented enums. A mismatch fails CI in both directions.
8. The CI UI coverage gate (`--cov=agent_orchestrator.ui --cov-fail-under=80`) still passes. Record
   the coverage number in STATUS.

## Risks
- Low. Keep route registration consistent with the existing `/runs/{run_id}/log` style.

## Dependencies
- `T-M4qboy` (builder), `T-l7t6TT` (shared snapshot loader), and `T-adVpTj` (fixture file, for AC-7).

## Pseudocode / Algorithm
HLD §8.4 (verbatim, authoritative).

## Schemas / Interface Notes
- Interface / API: `GET /api/runs/{run_id}/graph` gives 200 `RunGraph` or 404 (HLD §14.2). `RunDetail` additive fields.
- Spec / data schema: HLD §14.2.
- Triggers / events: log `ui.graph.degraded`.
- Artifacts: reads the run dir only.

## Handoff Boundary
- Upstream: `T-M4qboy`.
- Downstream: the frontend tasks consume the live endpoint. `T-F1caAt` e2e.

## Artifacts
- Docs/comments: `meta/tickets/E-k3AMEr-run-graph-canvas/T-AsQ77e-run-graph-endpoint/`
- Large outputs: N/A
