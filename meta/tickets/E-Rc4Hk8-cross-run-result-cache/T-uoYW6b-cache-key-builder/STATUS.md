# STATUS

- ID: `T-uoYW6b-cache-key-builder`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (Dev A)

## This update
- Rev 2 ticket: fingerprint + keys + `summary_from_doc`; GV-1 is now Rev 2 (`6646469e…`); hashing and repo
  state moved to T-8tr1H4. Estimate 20 h.

## Evidence
- None yet (not started). Design evidence: HLD Rev 2 and ADR-0019 Rev 2.

## Risks / Blockers
- Depends on T-FJH6LI, T-OeRYSO and T-8tr1H4 (all Sprint 1).
- Risk: GV-1 drift from upstream prompt changes (see `TASK.md`).

## Next actions
1. Start against the T-FJH6LI contracts; integrate T-OeRYSO and T-8tr1H4 when they land.
2. Reproduce GV-1 exactly, then write the sensitivity, insensitivity and normalization suites.
3. Do the A-9 env-allowlist check and record it here.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation. State stays `Draft` (Sprint 1, Wave 2); this file, `TASK.md`, `HANDOFF.md` (when present)
  and the epic `STATUS.md` rollup agree.
