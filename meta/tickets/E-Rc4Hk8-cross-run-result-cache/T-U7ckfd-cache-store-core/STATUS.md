# STATUS

- ID: `T-U7ckfd-cache-store-core`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (Dev C)

## This update
- Rev 2 ticket: per-operation checks, versioned `entries/v1`, canonical bytes, `check()`, `has_blob`,
  `is_expired`, placeholder `maybe_enforce_limits`. Estimate unchanged (16 h).

## Evidence
- None yet (not started). Design evidence: HLD Rev 2 and ADR-0019 Rev 2.

## Risks / Blockers
- Depends on T-FJH6LI.
- Residual TOCTOU risk documented (HLD §7.7).

## Next actions
1. Start after T-FJH6LI commit 3 (types, fakes, contract suite).
2. Write the factory and checks, then the layout, entries and blobs; rerun the contract suite against the real store.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation. State stays `Draft` (Sprint 1, Wave 2); this file, `TASK.md`, `HANDOFF.md` (when present)
  and the epic `STATUS.md` rollup agree.
