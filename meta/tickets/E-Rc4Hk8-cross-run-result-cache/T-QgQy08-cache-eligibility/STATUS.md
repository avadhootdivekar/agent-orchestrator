# STATUS

- ID: `T-QgQy08-cache-eligibility`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (Dev B)

## This update
- Rev 3 ticket: adds the `unknown_agent_field` runtime rule (U-E27) and the structural-task
  consistency test (U-E28). Estimate 13 h.

## Evidence
- None yet (not started). Design evidence: HLD Rev 3 and ADR-0019 Rev 3.

## Risks / Blockers
- Depends on T-28J9oR.
- Expected: tripwires fail at the sibling-epic merge until their fields are classified.

## Next actions
1. Start after T-28J9oR.
2. Tables, then the predicate (including the AgentSpec rule), then the tripwires and one test per reason.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (core set); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
