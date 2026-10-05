# STATUS

- ID: `T-U7ckfd-cache-store-core`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (Dev C)

## This update
- Rev 3 ticket: `LocalFsCacheStore(CacheStore)` only; checks before every operation, including
  delete and touch; `CacheUnsafePathError`; U-ST15. Estimate 17 h.

## Evidence
- None yet (not started). Design evidence: HLD Rev 3 and ADR-0019 Rev 3.

## Risks / Blockers
- Depends on T-FJH6LI.
- Residual TOCTOU risk documented (HLD §7.7).

## Next actions
1. Start after T-FJH6LI.
2. Factory and checks, then layout, entries and blobs; rerun the contract suite against the real store.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 re-plan after the
  early-gate review and the manager's scope decisions (HLD §23.5). State stays `Draft`
  (core set); this file, `TASK.md`, `HANDOFF.md` (when present) and the epic `STATUS.md` rollup
  agree.
