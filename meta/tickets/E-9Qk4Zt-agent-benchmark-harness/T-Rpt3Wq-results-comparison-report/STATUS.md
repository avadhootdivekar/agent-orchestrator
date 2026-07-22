# STATUS

- ID: `T-Rpt3Wq-results-comparison-report`
- Updated At: 2026-07-22
- State: Draft
- Owner: developer agent

## This update
- By: architect · Role: architect · Date: 2026-07-22 · Comment: Task specified from design §4.6. First task of Sprint 2.

## Evidence
- Design: `docs-md/benchmarking-framework-hld.md` §4.6, §6.

## Risks / Blockers
- Divergent task sets across subjects — union-key the matrix.

## Next actions
1. `run.json`/`summary.md` writers (called by runner).
2. `write_comparison` (same-suite guard, union matrix, side-by-side md).
3. Integration test: two fake subjects → comparison.
