# STATUS

- ID: `T-eyn5UG-cache-reporting`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (Dev B)

## This update
- Rev 2 ticket: explicit hit exclusion in usage, shadow counters, `ended_at` binding, `settle_reason: cached`,
  key-set equality test. Estimate unchanged (16 h).

## Evidence
- None yet (not started). Design evidence: HLD Rev 2 and ADR-0019 Rev 2.

## Risks / Blockers
- Depends on T-28J9oR (Sprint 1).

## Next actions
1. Start at the beginning of Sprint 2.
2. Write `report.py` and its tests, then the `write_status`, `usage` and `outcomes` hooks, then the stale-filtering test across every helper.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation. State stays `Draft` (Sprint 2); this file, `TASK.md`, `HANDOFF.md` (when present)
  and the epic `STATUS.md` rollup agree.
