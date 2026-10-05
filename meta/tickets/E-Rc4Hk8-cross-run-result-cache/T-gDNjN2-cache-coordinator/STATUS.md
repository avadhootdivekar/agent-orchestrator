# STATUS

- ID: `T-gDNjN2-cache-coordinator`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (Dev A)

## This update
- Rev 3 ticket: no `refresh`; lazy guard 3; unsafe paths never evicted; factory warnings; new
  tests U-CO17…U-CO20. Estimate unchanged (20 h).

## Evidence
- None yet (not started). Design evidence: HLD Rev 3 and ADR-0019 Rev 3.

## Risks / Blockers
- Depends on the core-set tasks.

## Next actions
1. Start after the core set and gate G1a.
2. Builders, then `_lookup` (unsafe-path branch, lazy `_storable`), then `_store_success`, then the boundary.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (engine set, first); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
