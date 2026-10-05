# STATUS

- ID: `T-uoYW6b-cache-key-builder`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (Dev A)

## This update
- Rev 3 ticket: version memo by binary identity; AgentSpec field sets from `constants`; explicit
  U-K1…U-K12. GV-1 unchanged (`6646469e…`). Estimate unchanged (20 h).

## Evidence
- None yet (not started). Design evidence: HLD Rev 3 and ADR-0019 Rev 3.

## Risks / Blockers
- Depends on T-FJH6LI, T-OeRYSO and T-8tr1H4.
- Risk: GV-1 drift from upstream prompt changes.

## Next actions
1. Start after T-FJH6LI, T-OeRYSO and T-8tr1H4.
2. Reproduce GV-1 exactly, then the sensitivity, insensitivity, normalization and rule suites.
3. Do the A-9 env-allowlist check and record it here.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (core set); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
