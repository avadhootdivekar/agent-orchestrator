# TASK: T-LYf6DJ-activity-endpoint

## Metadata
- Task ID: `T-LYf6DJ-activity-endpoint`
- Epic ID: `E-iafh2F-live-status-and-tabbed-workspace`
- Owner: dev-epic
- Created: 2026-10-02
- Last Updated: 2026-10-02
- Status: Done
- Estimate: < 3 days

## Requirements Mapping
- Requirement IDs: FR-1,FR-3

## Description
Activity endpoint + additive payload fields. RunRepository.load_activity, DashboardService.run_activity, GET /api/runs/{id}/activity, TaskStat model/effort/agent, RunSummary.running_tasks.

## Acceptance Criteria
1. Behaviour per HLD `docs-md/live-activity-and-tabs-hld.md` (Phase 1).
2. Tests added and passing; gates (pytest, ruff, mypy, npm typecheck/test/build) green.

## Handoff Boundary
- Upstream: previous task in epic list. Downstream: next task.
