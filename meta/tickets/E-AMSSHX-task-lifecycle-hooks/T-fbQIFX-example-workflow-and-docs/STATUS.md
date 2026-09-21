# STATUS

- ID: `T-fbQIFX-example-workflow-and-docs`
- Updated At: 2026-09-21
- State: Done
- Owner: developer/tester (delegated by dev-epic)

## This update
- By: dev-epic
- Role: manager
- Date: 2026-09-21
- Comment: Ticket drafted.

## Risks / Blockers
- None.

## Next actions
1. Delegate together with T-jI3P4p / T-FCC8mT.

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
- Comment: ACs 1-3 implemented (AC-4, the late-gate e2e run, is T-FCC8mT's own scope, not
  this ticket's). `specs/examples/workflow-hooks.json` declares a 2-entry `hooks` registry
  (`check_disk_space`, `grade`) reusing `specs/examples/reposet.json`'s `default-set` and
  `agents.json`'s `architect`/`developer` agents -- no new reposet/agents file needed.
  `design` task uses `pre_hook: {"use": "check_disk_space"}` (real disk-space precondition
  check, not a no-op stub); `implement` task uses `post_hook: {"use": "grade", "on_failure":
  "ignore"}` (reads `AO_HOOK_CONTEXT_PATH`, checks declared outputs exist, writes
  `{"score", "detail"}` to `AO_HOOK_RESULT_PATH`). Both scripts under
  `specs/examples/hooks/` are pure `python3` stdlib, no network access. `grade.py` is
  controllable via `AO_EXAMPLE_GRADE_FORCE_FAIL=1` (documented in its own docstring) so
  T-FCC8mT's e2e run can exercise both `on_failure` policies without a genuinely broken
  upstream task.

## Evidence
- `ao validate --workflow specs/examples/workflow-hooks.json --reposets
  specs/examples/reposet.json --agents specs/examples/agents.json` -> `OK: all specs valid`.
- Same command against throwaway copies with an empty `hooks.check_disk_space.command`
  array, an unknown `hooks.grade.on_failure` value, and an unresolved
  `tasks[0].pre_hook.use` each produced the expected `ERROR: ...` + exit 1.
- Commit: `6e067fc` on `ad/cost-perf-hooks-skills`.
