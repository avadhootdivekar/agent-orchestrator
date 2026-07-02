# STATUS

- ID: `T-5igs6g-docs-refresh-budget`
- Updated At: 2026-06-18
- State: Done
- Owner: manager

## This update
- Updated `docs-md/token-budgeting-hld.md` to reconcile with shipped behavior:
  - Section 9 OPEN_QUESTIONs resolved: usage field names confirmed, cache_read counted fully, max-wait unbounded+cancel_fn-interruptible, tumbling window shipped.
  - New section 3.7 "Deviations from original design" added documenting 4 minor differences from pseudocode.
  - Inline OPEN_QUESTION markers in 3.2 and 3.4 replaced with prose / RESOLVED status.
- Added cross-link to `docs-md/hld-agent-orchestrator.md` pointing to `token-budgeting-hld.md`.
- ADR-BUD-001 through ADR-BUD-005 verified accurate against shipped code — no corrections needed.
- `tests/fixtures/claude_usage.json` confirmed to match documented shape.

By: manager · Role: manager · Date: 2026-06-18 · Comment: Docs reconciled. All OPEN_QUESTIONs resolved. Epic ready to close.

## Evidence
- `docs-md/token-budgeting-hld.md` — updated with resolved OQs + deviations
- `docs-md/hld-agent-orchestrator.md` — cross-link added
- `tests/fixtures/claude_usage.json` — confirmed real CLI shape
- `pytest -q`: 313 passed (docs-only changes, no regressions)

## Risks / Blockers
- None.

## Next actions
- Task complete. Epic E-j4gno6 can be closed.
