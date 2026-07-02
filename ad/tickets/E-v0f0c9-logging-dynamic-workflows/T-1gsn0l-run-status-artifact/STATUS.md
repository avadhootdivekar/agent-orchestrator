# STATUS

- ID: `T-1gsn0l-run-status-artifact`
- Updated At: 2026-06-18
- State: Done
- Owner: developer

## This update
- `RunStateStore.write_status(state)` added; called inside `save()` (ADR-002).
- `status.json` atomic-written via write-then-rename next to `state.json`.
- `ao status` updated: accepts `--workspace <root>` (or `AO_WORKSPACE_ROOT`); prefers `status.json`, falls back to `state.json`; shows `current_task` in output.
- `status.json` shape matches EPIC contract: `run_id`, `workflow_id`, `status`, `updated_at`, `current_task`, `counts`, `tasks[{id, status, attempts, origin, output_artifact_path}]`.

## Evidence
- 156 tests passed; `runstate.py` 100% coverage.
- `ao status --workspace <path>` works without spec triplet.
- By: developer · Role: developer · Date: 2026-06-18 · Comment: All 156 tests pass; runstate.py 100%; ruff+mypy clean.

## Risks / Blockers
- None.

## Next actions
- Done.
