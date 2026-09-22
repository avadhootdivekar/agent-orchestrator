# STATUS

- ID: `T-DgheoA-spec-schema-validation`
- Updated At: 2026-09-21
- State: Done
- Owner: developer (delegated by dev-epic)

## This update
- By: dev-epic
- Role: manager
- Date: 2026-09-21
- Comment: Ticket drafted.

## Risks / Blockers
- None.

## Next actions
1. Delegate alongside T-AHvmYR/T-lzQEyy.

## Rev 2 sync (post early-gate review)
- By: dev-epic
- Role: manager
- Date: 2026-09-21
- Comment: Acceptance criteria updated to match HLD Rev 2 (hooks moved to a `WorkflowSpec.hooks`
  named registry referenced by `HookRef`, T2 resolver-dispatch suppression added, `hooks.py`
  module extraction, error-detail folding for self-heal). See
  `meta/tickets/E-AMSSHX-task-lifecycle-hooks/STATUS.md` for the full review outcome.

## Implementation complete
- By: developer
- Role: developer
- Date: 2026-09-21
- Comment: All 7 ACs implemented. `specs/workflow.schema.json` gained `$defs/hook`,
  `$defs/hookRef`, a `hooks` registry property on the workflow root, and `pre_hook`/
  `post_hook` refs on the task def (bounds match `HookSpec`/`HookRef` exactly).
  `spec.py::cross_validate` gained a new per-task rule inside the existing per-task loop,
  right next to the "unknown agent id" check: unresolved `pre_hook.use`/`post_hook.use`
  raises `SpecValidationError` naming the task id and hook name.

## Evidence
- `python -m json.tool specs/workflow.schema.json` confirms valid JSON.
- `jsonschema` smoke test: an empty `hooks.*.command` array and an unknown `on_failure`
  value are both rejected.
- Direct `cross_validate()` call: an unresolved `pre_hook.use` raises
  `SpecValidationError("Task 't1': unknown hook 'nope' referenced by pre_hook")`.
- `ao validate` against throwaway copies of `specs/examples/workflow-hooks.json` confirms
  all three rejection cases end-to-end via the real CLI (see T-fbQIFX's STATUS.md).
- `ruff check`/`ruff format --check`/`mypy` clean on `spec.py`.
- `tests/test_isolation_spec_validation.py`, `test_config.py`, `test_dynamic_injection.py`,
  `test_loop_construct.py`, `test_task_settings_override.py`, `test_validate_run_control.py`:
  163 passed, zero regressions.
- Commit: `77b2fa2` on `ad/cost-perf-hooks-skills`.
