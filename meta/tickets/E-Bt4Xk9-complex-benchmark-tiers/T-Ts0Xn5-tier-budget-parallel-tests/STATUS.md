# STATUS

- ID: `T-Ts0Xn5-tier-budget-parallel-tests`
- Updated At: 2026-07-22
- State: **Done**
- Owner: tester (test specialist)

## This update
- Deterministic network-free tests for tier/budget/parallel/provider/campaign; swebench behind marker; coverage >=80%.
- CI wiring: bench job now excludes swebench marker for network-free runs.
- Cross-feature integration tests (test_tier_integration.py) cover end-to-end campaign workflows, resume-under-budget-change, disabled-tier gates, and SI-1 regression.
- Optional-dep guard tests confirm ao-bench validate works without swebench extra installed.

## Evidence

### Coverage (benchmark suite, non-real_llm, non-swebench markers excluded)
- **TOTAL: 97%** (1757 stmts, 56 missed)
- Per-module breakdown:
  - campaign.py: **100%** (95 stmts)
  - cli.py: **100%** (158 stmts)
  - runner.py: **100%** (226 stmts)
  - tiers.py: **100%** (34 stmts)
  - workspace.py: **98%** (65 stmts, 1 miss: line 155 – subprocess timeout path, defensive safety)
  - results.py: **99%** (188 stmts, 1 miss: line 460 – summary_md edge case)
  - subjects.py: **97%** (217 stmts, 7 misses: error paths in optional-dep mode)
  - spec.py: **95%** (160 stmts, 8 misses: optional-dep error paths)
  - graders.py: **96%** (152 stmts, 6 misses: error paths in optional-dep mode)
  - swebench_import.py: **91%** (135 stmts, 12 misses: network/Docker-gated paths – behind swebench marker)
  - swebench_provider.py: **90%** (77 stmts, 8 misses: Docker checkout paths – behind swebench marker)
  - swebench_grader.py: **92%** (162 stmts, 13 misses: Docker eval + cleanup – behind swebench marker)

### Tests added
- **New file: tests/bench/test_tier_integration.py** — 8 cross-feature integration tests:
  1. `test_campaign_e2e_medium_tier_defaults_applied`: medium-tier campaign, tier defaults verified, budget enforced, campaign.json/comparison.json written.
  2. `test_campaign_e2e_with_parallel_reaches_pool_worker_count`: parallel (4-worker medium tier), all tasks recorded id-sorted, no loss.
  3. `test_campaign_resume_under_budget_change`: low cap → skipped_budget → raised cap → resume completes, fingerprint unaffected.
  4. `test_campaign_disabled_xlarge_tier_rejected_without_enable_flag`: xlarge disabled, rejected without --enable-xlarge.
  5. `test_campaign_enabled_tier_proceeds_normally`: small/medium/large enabled, all proceed without --enable-xlarge.
  6. `test_core_import_does_not_load_bench`: SI-1 regression – `import agent_orchestrator` never loads `bench/` modules.
  7. `test_core_import_does_not_load_swebench_submodules`: SI-1 extended – swebench_*.py modules never loaded by core.
  8. `test_ao_bench_validate_works_without_swebench_extra`: optional-dep negative test – `ao-bench validate` works without swebench installed.

### Test suite results
- Full suite (tests/): **1266 passed**, 3 skipped, 4 deselected (baseline 1258 + 8 new integration tests)
- Bench only (tests/bench): **409 passed**, 4 deselected (swebench marker-excluded)
- No flakes, all deterministic ✓

### CI wiring (.github/workflows/ci.yml)
- Updated bench job step to `pytest tests/bench -q -m "not real_llm and not swebench"` (explicit swebench marker exclusion for network-free CI)
- `--cov-fail-under=80` gate covers all agent_orchestrator.bench modules → confirmed 97% ✓
- YAML syntax validated ✓

### Ruff + format compliance
- test_tier_integration.py: lint ✓, format ✓

## Gaps (consciously uncovered, documented per CLAUDE.md)

### swebench modules (optional-dep, Docker-gated)
- Lines 220-221 (swebench_grader.py): `OSError`/`TimeoutExpired` in `_extract_patch` – requires real git repo + timeout simulation, covered by opt-in real test.
- Lines 304, 349-350, 376-378, 387-388, 456, 515-516 (swebench_grader.py): Docker control-plane errors, image cleanup, harness failures – require Docker daemon + real harness, behind swebench marker, covered by opt-in real test (T-Sg6Jf2).
- Lines 156, 158-160, 196-203 (swebench_import.py): HuggingFace network errors, dataset fetch failures – require real network, behind swebench marker, covered by opt-in real test (T-Sw5Hd9).
- Lines 98-100, 102, 119-120, 134, 235 (swebench_provider.py): Git checkout errors, repo state validation – require real git repo + network, behind swebench marker, covered by opt-in real test.

### graders.py, spec.py, subjects.py (optional-dep fallback paths)
- Lines 184, 201, 272-273, 282-283 (graders.py): command execution fallback when grader missing – tested via fake grader, real paths exercised in opt-in tier.
- Lines 239-240, 251, 277-278, 283, 385-386 (spec.py): YAML parsing, schema edge cases – static fixtures cover happy path, error cases rare in real suites.
- Lines 155, 207-208, 428-429, 487-488 (subjects.py): optional-dep state loading, fallback cost computation – tested via fake subject, real paths exercised in opt-in tier.

Rationale: errors in optional-dep/Docker/network paths are:
1. Not exercisable in CI (network-free, Docker daemon absent).
2. Covered by opt-in real tests (T-Sg6Jf2, T-Sw5Hd9) running outside this task's deterministic suite.
3. Guarded by early `ImportError` checks (lazy import), so failures are loud, not silent.

## Acceptance Criteria (per TASK.md)

- [x] **AC1**: `uv run pytest tests/bench -q -m "not real_llm and not swebench"` passes with zero network; 1266 total suite ✓ (baseline 1258 + 8 new).
- [x] **AC2**: Budget cap + resume verified: low cap skips tasks, raised cap completes (test_campaign_resume_under_budget_change) ✓.
- [x] **AC3**: Parallelism: 8 tasks over medium-tier (default 4 workers) record all tasks id-sorted, no loss (test_campaign_e2e_with_parallel_reaches_pool_worker_count) ✓.
- [x] **AC4**: Provider: workspace_provider seam exercised end-to-end (campaign tests use fixture provider) ✓; unknown provider rejected (existing tests in test_workspace.py) ✓.
- [x] **AC5**: Optional-dep guard: `import agent_orchestrator` never loads swebench_*.py or bench/ modules; ao-bench validate works without extra (test_core_import_does_not_load_swebench_submodules, test_ao_bench_validate_works_without_swebench_extra) ✓.
- [x] **AC6**: Coverage: 97% of agent_orchestrator.bench (1757 stmts, 56 misses in optional-dep/Docker/network paths) ≥ 80% ✓.
- [x] **AC7**: CI: .github/workflows/ci.yml bench job explicitly excludes swebench marker; network-free ✓.

## Dependencies
- All feature tasks shipped (T-Tr1Km8...T-Cm9Tb4).
- No production code edits (test-only task, per TASK.md).

## Next actions
- None — task complete. Downstream: T-Dc1Yg7 (docs + ADR reconciliation) uses these test results.
