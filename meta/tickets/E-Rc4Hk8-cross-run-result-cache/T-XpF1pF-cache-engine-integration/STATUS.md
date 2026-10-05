# STATUS

- ID: `T-XpF1pF-cache-engine-integration`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (Dev A)

## This update
- Rev 3 ticket: depends on T-JCOAsq Part 1 only; `_reverse_stale_charge` extraction with the
  `budget.resume_reverse` event; honest line budget; I-27. Estimate unchanged (16 h).

## Evidence
- None yet (not started). Design evidence: HLD Rev 3 and ADR-0019 Rev 3.

## Risks / Blockers
- Depends on T-gDNjN2 and T-JCOAsq Part 1.
- Risk: merge conflict with E-Ag7Pw3 in `_prepare_and_maybe_dispatch` (HLD §24.2).

## Next actions
1. Start after T-gDNjN2, with T-JCOAsq Part 1 on the branch.
2. Seams (a)–(e) including the extraction; then I-3…I-27; check the budget with `ruff format`.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (engine set); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
