# STATUS

- ID: `T-u3jG8F-cache-restore-capture`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (Dev C)

## This update
- Rev 2 ticket: sensitive re-check, realpath re-validation, `O_CLOEXEC`, non-storable misses; moved to
  Sprint 2. Estimate unchanged (16 h).

## Evidence
- None yet (not started). Design evidence: HLD Rev 2 and ADR-0019 Rev 2.

## Risks / Blockers
- Depends on T-FJH6LI.
- T-gDNjN2 integrates this around day 4 of Sprint 2; keep the signatures frozen.

## Next actions
1. Start at the beginning of Sprint 2, against `InMemoryCacheStore`.
2. Write capture, then restore phases 1–2, then the adversarial tests; add a smoke test on `LocalFsCacheStore`.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation. State stays `Draft` (Sprint 2); this file, `TASK.md`, `HANDOFF.md` (when present)
  and the epic `STATUS.md` rollup agree.
