# TASK: T-qifi3r-activity-reader

## Metadata
- Task ID: `T-qifi3r-activity-reader`
- Epic ID: `E-iafh2F-live-status-and-tabbed-workspace`
- Owner: dev-epic
- Created: 2026-10-02
- Last Updated: 2026-10-02
- Status: Done
- Estimate: < 3 days

## Requirements Mapping
- Requirement IDs: FR-1,FR-2,NFR-1,NFR-2

## Description
Pure+IO live-activity reader (ui/activity.py). Bounded incremental transcript tail, turns/tokens/last action/stuck hint, settled turns from result.json, path-safe, cached.

## Acceptance Criteria
1. Behaviour per HLD `docs-md/live-activity-and-tabs-hld.md` (Phase 1).
2. Tests added and passing; gates (pytest, ruff, mypy, npm typecheck/test/build) green.

## Handoff Boundary
- Upstream: previous task in epic list. Downstream: next task.
