# STATUS

- ID: `T-6tRKml-cache-cli-commands`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (Dev C)

## This update
- Rev 2 ticket: adds `rm` (key/prefix and `--run/--task`), control-character stripping, foreign-version
  reporting; read-only commands start at the Sprint 2 tail. Estimate unchanged (20 h).

## Evidence
- None yet (not started). Design evidence: HLD Rev 2 and ADR-0019 Rev 2.

## Risks / Blockers
- Depends on T-HjxNQ0.
- This task is on the resource-constrained critical path (HLD §22.1); start it as soon as T-HjxNQ0 lands.

## Next actions
1. When T-HjxNQ0 lands: `ls`, `stats`, `show`.
2. Sprint 3: `rm`, `prune`, `clear`, `verify`, the schema fixtures and the full E-7 suite.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation. State stays `Draft` (Sprint 2 tail → Sprint 3); this file, `TASK.md`, `HANDOFF.md` (when present)
  and the epic `STATUS.md` rollup agree.
