# STATUS

- ID: `T-OeRYSO-executor-argv-builder`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (Dev C)

## This update
- Ticket created (Rev 2): a pure `build_claude_argv` extraction plus behaviour-identity tests. Estimate 6 h.

## Evidence
- None yet (not started). Design evidence: HLD Rev 2 and ADR-0019 Rev 2.

## Risks / Blockers
- No blockers.
- Risk: argv reordering; U-A1 compares against the real `Popen` argv.

## Next actions
1. Extract the function and make `execute()` call it.
2. Write the U-A1..A3 and U-K8a tests; run the full executor suite, ruff and mypy.

## Comments
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation. State stays `Draft` (Sprint 1, Wave 1); this file, `TASK.md`, `HANDOFF.md` (when present)
  and the epic `STATUS.md` rollup agree.
