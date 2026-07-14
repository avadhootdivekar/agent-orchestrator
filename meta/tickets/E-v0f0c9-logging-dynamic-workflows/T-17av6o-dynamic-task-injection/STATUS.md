# STATUS

- ID: `T-17av6o-dynamic-task-injection`
- Updated At: 2026-06-18
- State: Done
- Owner: developer

## This update (2026-06-18 — Done)
- `TaskSpec.emit_tasks` + `task_manifest_path` added to `models.py` and `workflow.schema.json`.
- `RunState.injected_tasks` + `loop_iterations` added; `TaskRunState.origin` uses real values.
- `artifacts.read_task_manifest` implemented (ValueError on bad/missing manifest).
- `errors.InjectionError` added.
- `spec.cross_validate` enforces `emit_tasks ⇔ task_manifest_path` pairing.
- `Orchestrator.run()` refactored from `for tid in order` to re-entrant `while cursor` + `done: set` (ADR-005). All existing behavior preserved.
- `_inject`, `_recompute_order` helpers implemented.
- `runstate.prepare_resume` merges `injected_tasks` before returning (FR-8).
- `TaskContext` gains `task_manifest_path`, `gate_output_path` fields.

## Evidence
By: developer · Role: developer · Date: 2026-06-18
207 passed, 0 failed; 89% total coverage (up from 87% baseline). All 156 pre-existing tests still pass.
`ruff check`, `ruff format --check` clean; `mypy` clean on new modules (3 pre-existing errors in `tests/test_project_config.py:68` unchanged).

## Risks / Blockers
None — all acceptance criteria met.
