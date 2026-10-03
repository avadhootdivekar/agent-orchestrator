# TASK: T-mee0lL-component-props-refactor

## Metadata
- Task ID: `T-mee0lL-component-props-refactor`
- Epic ID: `E-iafh2F-live-status-and-tabbed-workspace`
- Owner: dev-epic
- Created: 2026-10-02
- Last Updated: 2026-10-02
- Status: Done
- Estimate: < 3 days

## Requirements Mapping
- Requirement IDs: FR-8,FR-11

## Description
RunDetail/FileBrowser/RunGraph/TaskDetailPanel take ids/paths as props. Minimal edits; polling `active` plumbed.

## Acceptance Criteria
1. Behaviour per HLD `docs-md/live-activity-and-tabs-hld.md` (Phase 2).
2. Tests added and passing; gates (pytest, ruff, mypy, npm typecheck/test/build) green.

## Handoff Boundary
- Upstream: previous task in epic list. Downstream: next task.
