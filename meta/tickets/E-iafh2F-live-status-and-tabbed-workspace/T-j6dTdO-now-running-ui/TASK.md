# TASK: T-j6dTdO-now-running-ui

## Metadata
- Task ID: `T-j6dTdO-now-running-ui`
- Epic ID: `E-iafh2F-live-status-and-tabbed-workspace`
- Owner: dev-epic
- Created: 2026-10-02
- Last Updated: 2026-10-02
- Status: Done
- Estimate: < 3 days

## Requirements Mapping
- Requirement IDs: FR-4,FR-5,FR-6,NFR-3

## Description
Now-running box, list compact variant, Model/Turns columns, usePolling. Fixed 3-row non-collapsible scroll box; polling pauses when document.hidden.

## Acceptance Criteria
1. Behaviour per HLD `docs-md/live-activity-and-tabs-hld.md` (Phase 1).
2. Tests added and passing; gates (pytest, ruff, mypy, npm typecheck/test/build) green.

## Handoff Boundary
- Upstream: previous task in epic list. Downstream: next task.
