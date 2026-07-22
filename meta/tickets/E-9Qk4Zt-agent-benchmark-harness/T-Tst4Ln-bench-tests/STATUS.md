# STATUS

- ID: `T-Tst4Ln-bench-tests`
- Updated At: 2026-07-22
- State: Draft
- Owner: tester agent

## This update
- By: architect · Role: architect · Date: 2026-07-22 · Comment: Task specified from design §13. Fake harness should start in Sprint 1 (parallel with T-Run5Tz) to de-risk the runner; full suite + CI land in Sprint 2.

## Evidence
- Design: `docs-md/benchmarking-framework-hld.md` §13, §14.

## Risks / Blockers
- Grader pytest runs must be confined to `ws/repo` (not repo root).

## Next actions
1. `conftest.py` fake harness + `bench_workspace` fixture (early).
2. Unit + integration + CliRunner e2e + SI-1 import-graph test.
3. `[real_llm]` haiku tier; CI job + coverage gate; report actual numbers.
