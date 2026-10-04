# STATUS

- ID: `T-HjxNQ0-cache-store-maintenance`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (Dev C)

## This update
- Rev 3 ticket: adds the `CacheAdmin` base; byte-bounded inline enforcement; read-only `verify`;
  non-vacuous race test. Estimate 15 h.

## Evidence
- None yet (not started). Design evidence: HLD Rev 3 and ADR-0019 Rev 3.

## Risks / Blockers
- Depends on T-U7ckfd.
- High-scrutiny (destructive) code: reviewed at gate G1a.

## Next actions
1. Start after T-U7ckfd.
2. `iter_entries` and the mark phase, then `prune`, `maybe_enforce_limits`, `clear`, `verify`, then the race test.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (core set); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
