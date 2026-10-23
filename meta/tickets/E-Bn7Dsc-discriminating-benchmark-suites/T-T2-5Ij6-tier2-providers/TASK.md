# TASK: T-T2-5Ij6-tier2-providers

- Epic: `E-Bn7Dsc-discriminating-benchmark-suites`
- Title: repo_mutation + pr_mined providers, pytest_targeted, import-prs, dev-medium migration
- Status: Done (code complete on branch `ad/bench-tier2`, not merged)
- Design: `docs-md/benchmark-discriminating-suites-hld.md` (§8 maps each piece; §4.2a records the as-built notes), `docs-md/bench-probe-dsl.md`

## Acceptance
- [x] Implements the §8 rows named in the title against the committed seams (registries, `grading_dir`, `GraderConfig.model_extra`, `RunContext`).
- [x] Unit + e2e tests fake-tier (no LLM/network); docker-dependent tests gated by `AO_E2E_DOCKER=1` (none needed here); real-clone tests gated by `AO_E2E_NETWORK=1`.
- [x] `ruff`, `mypy src/agent_orchestrator/bench`, and `pytest tests/bench` green.

## Deliverables (as built)
1. `bench/mutation_provider.py` -- `repo_mutation` provider (recipe: `inject|excise|enhance`, exact-count `edits`, `remove`, `hidden`, `hidden_from_repo`, `fail_to_pass`/`pass_to_pass`/`test_targets`, `install`, `test_command`, `verified`); hidden tests only in `grading_dir/hidden`, pristine files in `grading_dir/reference`, contract file `recipe.resolved.json`.
2. `bench/pytest_targeted.py` -- grader (restore hidden, install once, ONE bounded pytest run parsed from junit, `0.7*f2p + 0.3*p2p`, collateral diff stats, `agent_tests_added`, timeouts).
3. `bench/pr_mined_provider.py` + `bench/pr_mined_import.py` -- `ao-bench import-prs` via `gh api`; provider hides the merge commit's test files, parks the gold patch, snapshots the repo.
4. `bench/tier2_common.py` (shared checkout/snapshot helpers; reuses `swebench_provider`'s cache, generalised to full clone URLs).
5. dev-medium migration: `fixture/.grading/` -> `tasks/<id>/grading/` (`git mv`), `grader.assets` + `{grading_dir}/{repo_dir}` command, `run.py --repo`, instructions/conftest no longer mention a held-out harness, fingerprint-change note in the suite description.
6. `benchmarks/suites/repo-mutant-mini/` -- 3 inject + 1 excise recipes (click, attrs x2, rich), all network-verified.
7. Tests: `tests/bench/{test_mutation_provider,test_pr_mined,test_pr_mined_import,test_repo_mutant_mini_suite}.py`, updated `test_dev_medium_suite.py`, `tier2_fixtures.py`, `conftest.py` network gate.
8. Docs: `benchmarks/README.md` (held-out grading + Tier-2 section), HLD §4.2a.

## Out of scope / follow-ups
- Calibration/verification step that fills `fail_to_pass`/`pass_to_pass` on `pr_mined` instances (provider already honours them).
- Epic rollup (`EPIC.md`/epic `STATUS.md`) left to the manager to avoid cross-track merge conflicts.
