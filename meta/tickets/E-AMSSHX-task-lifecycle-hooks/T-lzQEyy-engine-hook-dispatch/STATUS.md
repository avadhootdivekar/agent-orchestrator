# STATUS

- ID: `T-lzQEyy-engine-hook-dispatch`
- Updated At: 2026-09-21
- State: Draft
- Owner: developer (delegated by dev-epic)

## This update
- By: dev-epic
- Role: manager
- Date: 2026-09-21
- Comment: Ticket drafted with explicit acceptance criteria mirroring HLD §5-6, including exactly
  which of `_run_with_retries`'s 3 early-return points get post-hook wrapping.

## Evidence
- (pending implementation)

## Risks / Blockers
- None yet.

## Next actions
1. Delegate to `developer` together with T-AHvmYR.
2. Verify AC-9 (provable no-op) via T-jI3P4p before closing.

## Rev 2 sync (post early-gate review)
- By: dev-epic
- Role: manager
- Date: 2026-09-21
- Comment: Acceptance criteria updated to match HLD Rev 2 (hooks moved to a `WorkflowSpec.hooks`
  named registry referenced by `HookRef`, T2 resolver-dispatch suppression added, `hooks.py`
  module extraction, error-detail folding for self-heal). See
  `meta/tickets/E-AMSSHX-task-lifecycle-hooks/STATUS.md` for the full review outcome.
