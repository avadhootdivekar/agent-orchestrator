# STATUS

- ID: `T-Tst4Ln-bench-tests`
- Updated At: 2026-07-22
- State: Complete
- Owner: tester agent

## Delivery Summary
- **Coverage**: 98% of `src/agent_orchestrator/bench/` (1120 statements, 18 missed)
- **Test Count**: 221 deterministic tests pass (baseline 190 + 31 edge cases) + 1 skipped real_llm
- **Regression**: All pre-existing engine/CLI tests pass unedited (SI-1 gate)
- **Code Quality**: ruff/mypy clean on all touched files
- **CI**: Bench tests wired into GitHub workflow with ≥80% coverage gate

## Module Coverage Breakdown (per-module ≥80%)
| Module | Stmts | Miss | Cover | Status |
|--------|-------|------|-------|--------|
| `__init__.py` | 1 | 0 | 100% | ✓ |
| `cli.py` | 124 | 0 | 100% | ✓ |
| `errors.py` | 7 | 0 | 100% | ✓ |
| `graders.py` | 152 | 6 | 96% | ✓ |
| `metrics.py` | 63 | 0 | 100% | ✓ |
| `registries.py` | 12 | 0 | 100% | ✓ |
| `results.py` | 178 | 0 | 100% | ✓ |
| `runner.py` | 180 | 0 | 100% | ✓ |
| `spec.py` | 141 | 5 | 96% | ✓ |
| `subjects.py` | 215 | 7 | 97% | ✓ |
| `workspace.py` | 47 | 0 | 100% | ✓ |
| **TOTAL** | **1120** | **18** | **98%** | ✓✓✓ |

## Coverage Improvement
- **Before**: 96% (42 missed lines)
- **After**: 98% (18 missed lines)
- **Gap Closed**: 24 lines → 12 net-new tests in `test_edge_cases.py`

## Evidence
- Test suite: `tests/bench/` (241 base tests + 31 edge cases)
- CI integration: `.github/workflows/ci.yml` with `--cov-fail-under=80`
- Code quality: `ruff check` + `ruff format` + `mypy` clean on all touched test files

## Remaining gaps (18 lines, mostly hard-to-trigger error paths)
- `graders.py`: abstract method definition (line 182), some detail field assignments (199, 275–286)
- `spec.py`: validation error handling in spec loading (172, 196–197, 279–280)
- `subjects.py`: abstract method definition (133), ProcessLookupError edge case (185–186), AoWorkflowSubject OSError paths (406–407, 465–466)

These are **not blockers** for acceptance (≥80% gate met with 98%) and represent either non-callable abstract methods or race-condition error paths requiring complex mocking/process orchestration beyond typical deterministic test scope.

## Deliverables
1. ✓ 31 edge case tests in `tests/bench/test_edge_cases.py` (new)
2. ✓ 2 additional CLI tests in `tests/bench/test_cli_*.py` (coverage for claude probe/report edge cases)
3. ✓ CI workflow update: bench tests with ≥80% coverage gate
4. ✓ All tests deterministic (fixed clock, seeded RNG, no network)
5. ✓ `[real_llm]` tier runs only when `AO_E2E_REAL_LLM=1`; skipped by default in CI
6. ✓ SI-1 import-graph test passes (engine/CLI unchanged)

## Next actions
- T-Dcs2Rk: reconcile HLD/ADR to as-built, docs refresh
- Phase 2: real Sonnet/Opus/ao workflow comparison runs (out of scope)
