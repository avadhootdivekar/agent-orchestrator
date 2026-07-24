# TASK: T-Db2Hs5-dashboard-backend

## Metadata
- Task ID: `T-Db2Hs5-dashboard-backend`
- Epic ID: `E-Ui7Kq2-dashboard-and-general-instructions`
- Owner: `Avadhoot Divekar`
- Created: `2026-07-24`
- Last Updated: `2026-07-24`
- Status: `Done`
- Estimate: `< 3 days`

## Requirements Mapping
- Requirement IDs: `FR-B1..B4, FR-R1..R5, NFR-2, NFR-3`

## Description
The framework-free core of the dashboard: file browsing, run discovery/stats/deletion, subprocess supervision, and the `DashboardService` facade that composes them.

## Acceptance Criteria
1. `FileBrowser` lists every entry including hidden and binary; refuses traversal AND symlink escapes; bounds reads.
2. `RunRepository` derives per-run and aggregate stats from `state.json` with NO new persisted state; wall time and actual (execution) time are distinct measures.
3. `ProcessSupervisor` launches `ao run`/`ao resume` in their own process group, persists launch records so cancel survives a dashboard restart, reaps children, passes prompts by file, and allow-lists forwarded options.
4. `DashboardService` imports no web framework and takes its collaborators by injection.
5. Cancel kills the process group AND marks the persisted run state `cancelled` (status.json regenerated).
6. A live run cannot be deleted.

## Risks
- See the epic's Risks section; nothing task-specific outstanding.

## Dependencies
- T-Pr7Wt3-run-prompt

## Schemas / Interface Notes
- `ui/files.py` — `FileBrowser`, `Root`, `DirEntry`, `FileContent`
- `ui/runs.py` — `RunRepository`, `RunSummary`, `RunDetail`, `AggregateStats`
- `ui/processes.py` — `ProcessSupervisor`, `LaunchRecord`, `ALLOWED_OPTIONS`
- `ui/service.py` — `DashboardService`
- `models.compute_run_active_seconds` promoted from `breakers.py` (shared definition)

## Handoff Boundary
- Upstream: `T-Pr7Wt3-run-prompt`
- Downstream: `T-Ap9Kf1-dashboard-api`

## Evidence
- `tests/ui/test_files.py`, `test_runs.py`, `test_processes.py`, `test_service.py`, `test_edge_cases.py`.

## Artifacts
- Docs/comments: `meta/tickets/E-Ui7Kq2-dashboard-and-general-instructions/T-Db2Hs5-dashboard-backend/`
- Design doc: [`docs-md/dashboard-and-general-instructions-hld.md`](../../../../docs-md/dashboard-and-general-instructions-hld.md)
