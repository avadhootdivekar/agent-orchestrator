# STATUS

- ID: `T-AHvmYR-hook-schema-models`
- Updated At: 2026-09-21
- State: Draft
- Owner: developer (delegated by dev-epic)

## This update
- By: dev-epic
- Role: manager
- Date: 2026-09-21
- Comment: Ticket drafted with explicit acceptance criteria mirroring HLD §3-4. Will be updated
  again once delegated developer work lands and the full suite is re-run.

## Evidence
- (pending implementation)

## Risks / Blockers
- None.

## Next actions
1. Delegate to `developer` alongside T-lzQEyy (same PR-shaped change, models first).
2. Confirm full test suite + ruff + mypy clean after landing.

## Rev 2 sync (post early-gate review)
- By: dev-epic
- Role: manager
- Date: 2026-09-21
- Comment: Acceptance criteria updated to match HLD Rev 2 (hooks moved to a `WorkflowSpec.hooks`
  named registry referenced by `HookRef`, T2 resolver-dispatch suppression added, `hooks.py`
  module extraction, error-detail folding for self-heal). See
  `meta/tickets/E-AMSSHX-task-lifecycle-hooks/STATUS.md` for the full review outcome.
