# STATUS

- ID: `T-Grd7Vx-grader-registry`
- Updated At: 2026-07-22
- State: Draft
- Owner: developer agent

## This update
- By: architect · Role: architect · Date: 2026-07-22 · Comment: Task specified from design §4.3/§4.4. Parallelizable with T-Sbj9Ka (both depend only on T-Sc4Hm2).

## Evidence
- Design: `docs-md/benchmarking-framework-hld.md` §4.3, §4.4, §6.

## Risks / Blockers
- pytest-summary parsing brittleness — exit-code is source of truth.

## Next actions
1. `Grader` ABC + registry; `PytestGrader` (exit-code source of truth) first.
2. `CommandGrader`/`FileAssertionGrader`/`FakeGrader`; `metrics.py` aggregate.
3. Unit tests incl. `cost_usd is None` and `solved==0` edge cases.
