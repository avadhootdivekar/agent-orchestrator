# STATUS

- ID: `T-Fx6Dp0-mvp-dev-suite-fixtures`
- Updated At: 2026-07-22
- State: Draft
- Owner: developer agent (tester review)

## This update
- By: architect · Role: architect · Date: 2026-07-22 · Comment: Task specified from design §9/§18 (G6). MVP finish line — curated suite + all-3-subjects haiku smoke + committed results + working make recipe. Resolves Q1 (ao-epic template) and Q2 (model ids).

## Evidence
- Design: `docs-md/benchmarking-framework-hld.md` §9, §11, §18.

## Risks / Blockers
- R4 real-LLM cost/flakiness — deterministic gate is the fake-subject pass; real smoke is cheap haiku.
- Q1/Q2 must be resolved here.

## Next actions
1. Author 4–8 tiny fixture tasks (bugfix/feature/refactor/test) + graders + reference solutions.
2. Author subject configs (opus/sonnet/haiku + ao-epic[-haiku] + fake) + ao-epic template.
3. `make bench-smoke` at haiku for all 3 subjects; commit results; `ao-bench report`.
