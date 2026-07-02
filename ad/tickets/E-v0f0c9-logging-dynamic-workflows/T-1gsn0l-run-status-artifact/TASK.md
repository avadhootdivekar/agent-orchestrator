# TASK: T-1gsn0l-run-status-artifact

## Metadata
- Task ID: `T-1gsn0l-run-status-artifact`
- Epic ID: `E-v0f0c9-logging-dynamic-workflows`
- Owner: developer
- Created: 2026-06-18
- Last Updated: 2026-06-18
- Status: Done
- Estimate: `< 2 days`

## Requirements Mapping
- Requirement IDs: FR-1, FR-6, NFR-3

## Description
Produce a `status.json` snapshot at `.orchestrator/runs/<run_id>/status.json`,
refreshed on every task transition, so a user/tool can read the latest run
status at any time. Derive it purely from `RunState` and write it inside
`RunStateStore.save()` (same atomic write-then-rename) so it can never diverge
from `state.json`. Update the existing `ao status <run_id>` command to read
`status.json` first (fallback to `state.json`).

## Acceptance Criteria (all met)
1. `status.json` exists after every `save(state)`.
2. Contains `run_id`, `workflow_id`, `status`, `updated_at`, `counts`, `current_task`, `tasks[]`.
3. Atomic write-then-rename; no partial reads.
4. `ao status` reads `status.json`, falls back to `state.json` without error.
5. `status.json` byte-consistent with `state.json` after every save.
6. ruff / mypy / pytest pass.

## Artifacts
- Code: `src/agent_orchestrator/runstate.py`, `src/agent_orchestrator/cli.py`.
- Tests: `tests/test_status_artifact.py` (TestWriteStatus, TestAoStatusCommand classes).
