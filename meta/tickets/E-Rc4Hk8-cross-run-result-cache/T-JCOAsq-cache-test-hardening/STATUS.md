# STATUS

- ID: `T-JCOAsq-cache-test-hardening`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `tester`

## This update
- Rev 2 ticket: golden determinism, I-1 targeting the engine's private methods and `ResultCache`, E-1
  negative control, new I-19…I-26, hostile-corpus harness, no conftest edit. Estimate unchanged (24 h).

## Evidence
- None yet (not started). Design evidence: HLD Rev 2 and ADR-0019 Rev 2.

## Risks / Blockers
- No blockers for Part 1.
- Part 3 depends on T-o95l1M, T-6tRKml and T-ZTxN1x.

## Next actions
1. Part 1 now (Sprint 1): capture the base golden at `bb6d8a0`; land I-2 (active) and I-1 (importorskip).
2. Part 2 as the modules land (Sprint 2).
3. Part 3 in Sprint 3: e2e, coverage, full suite.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation. State stays `Draft` (Sprint 1–3); this file, `TASK.md`, `HANDOFF.md` (when present)
  and the epic `STATUS.md` rollup agree.
