# STATUS

- ID: `T-XpF1pF-cache-engine-integration`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (Dev A)

## This update
- Rev 2 ticket: engine-only scope (CLI wiring moved to T-o95l1M), engine-owned hit settle, kept cycle
  increment, stale budget reversal; I-1, I-2 and U-AST-E are acceptance gates. Estimate 16 h.

## Evidence
- None yet (not started). Design evidence: HLD Rev 2 and ADR-0019 Rev 2.

## Risks / Blockers
- Depends on T-gDNjN2 and T-JCOAsq parts 1–2.
- Risk: merge conflict with E-Ag7Pw3 in `_prepare_and_maybe_dispatch` (HLD §24.2).

## Next actions
1. Start after T-gDNjN2.
2. Confirm the T-JCOAsq I-1/I-2 code and base golden are on the branch.
3. Implement seams (a)–(e), then I-3…I-22; check the line budget.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation. State stays `Draft` (Sprint 2); this file, `TASK.md`, `HANDOFF.md` (when present)
  and the epic `STATUS.md` rollup agree.
