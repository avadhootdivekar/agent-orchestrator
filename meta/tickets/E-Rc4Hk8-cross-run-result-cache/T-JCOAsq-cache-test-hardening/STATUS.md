# STATUS

- ID: `T-JCOAsq-cache-test-hardening`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `tester`

## This update
- Rev 3 ticket: parts re-split (Part 1 needs only base code); poisoned-import I-1; I-2 at
  `max_parallel=3`; ADV-4b; CI coverage step in scope; hard coverage gate. Estimate unchanged (24 h).

## Evidence
- None yet (not started). Design evidence: HLD Rev 3 and ADR-0019 Rev 3.

## Risks / Blockers
- No blockers for Part 1.
- Golden recapture may be needed after the sibling merges (parent; HLD §24.2).

## Next actions
1. Part 1 now: capture the base golden at `bb6d8a0` (serial and `max_parallel=3`); land I-2 and I-1.
2. Part 2 after T-XpF1pF, T-u3jG8F and T-HjxNQ0.
3. Part 3 after T-o95l1M, T-6tRKml, T-ZTxN1x and T-bLpoze: e2e, CI step, full suite.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (Part 1 any time before T-XpF1pF; Parts 2–3 in the hardening phase); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
