# STATUS

- ID: `T-6tRKml-cache-cli-commands`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (Dev C)

## This update
- Rev 3 ticket: `rm --run/--task` and `verify --repair` deferred; G0 fields in `stats --json`.
  Estimate 17 h.

## Evidence
- None yet (not started). Design evidence: HLD Rev 3 and ADR-0019 Rev 3.

## Risks / Blockers
- Depends on T-HjxNQ0.

## Next actions
1. Start after T-HjxNQ0.
2. `ls`, `stats`, `show`, then `rm`, `prune`, `clear`, `verify`; the schema fixtures and the full E-7 suite.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (surfaces); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: G1b carry-over: orphaned `.ao-result-cache-*.tmp[.bak]` restore leftovers are only ignored by hashing (sec S-1), not deleted; a prune-side sweep (with a liveness / age rule) belongs here (see also G1a SEC-19). See `T-fXWbqg-cache-review-gates/STATUS.md` (G1b remediation).
