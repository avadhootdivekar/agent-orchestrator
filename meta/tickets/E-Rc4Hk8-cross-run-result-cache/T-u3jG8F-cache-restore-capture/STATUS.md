# STATUS

- ID: `T-u3jG8F-cache-restore-capture`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (Dev B)

## This update
- Rev 3 ticket: skip markers, simulated permission errors, unsafe-path pass-through; owner Dev B.
  Estimate unchanged (16 h).

## Evidence
- None yet (not started). Design evidence: HLD Rev 3 and ADR-0019 Rev 3.

## Risks / Blockers
- Depends on T-FJH6LI.
- T-gDNjN2 integrates this; keep the signatures frozen.

## Next actions
1. Start after T-FJH6LI, against `InMemoryCacheStore`.
2. Capture, then restore phases 1–2, then the adversarial tests; add a smoke test on `LocalFsCacheStore`.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (core set); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
