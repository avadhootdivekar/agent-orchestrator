# STATUS

- ID: `T-Cli8Nf-bench-cli-make-entrypoints`
- Updated At: 2026-07-22
- State: Draft
- Owner: developer agent

## This update
- By: architect · Role: architect · Date: 2026-07-22 · Comment: Task specified from design §7 / ADR-0008 D4-D5. Standalone `ao-bench` script keeps core `ao` untouched (SI-1).

## Evidence
- Design: `docs-md/benchmarking-framework-hld.md` §7; `docs-md/adr/ADR-0008-benchmark-harness-approach.md` D4/D5.

## Risks / Blockers
- SI-1: no core→bench import — enforced by an import-graph test (T-Tst4Ln).

## Next actions
1. Typer `ao-bench` app (validate/run/report/list) + `pyproject` script entry.
2. `make bench-*` recipes appended (no edits to existing targets).
3. CliRunner e2e with fake subjects; SI-1 regression gate.
