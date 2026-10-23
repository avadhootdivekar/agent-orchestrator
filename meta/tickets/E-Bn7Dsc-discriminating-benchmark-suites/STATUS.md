# STATUS

- ID: `E-Bn7Dsc-discriminating-benchmark-suites`
- Updated At: 2026-10-23
- State: **Done** (6/6; follow-ons in HLD §12)
- Owner: Claude (manager) for avadhoot

## This update
- By: Claude · Role: manager · Date: 2026-10-23 · Comment: **Four parallel tracks merged** into
  `ad/enhancements-20oct` (merge commits `dc874d0` stats, blackbox, `3db0e07` tier-2, `8be2a6d` gen).
  Bench suite 1144 passed / 21 skipped (opt-in tiers: docker 7/7, network 10/10 on the merged tree);
  mypy clean (41 files); NFR-2 gate green with reviewed exceptions for the deliberate contract
  changes (grader extra=allow, winners line, conftest gates, dev-medium grading_dir migration).
  Deliverables: `blackbox` grader + docker/process sandbox + `ao-bench images`; gen families
  crud-relational (http) + job-queue (jsonl) in py/ts/go skeletons, `generated` provider,
  `ao-bench gen`, `gen-services-mini`; stats (paired bootstrap, Wilcoxon), repeat-aware comparison
  (schema 2.0: parity refusal, infra/suspect table, per-family/per-language, pairwise), staged-repeat
  campaign, `ao-bench calibrate`; `repo_mutation` + `pr_mined` providers, `pytest_targeted`,
  `ao-bench import-prs`, `repo-mutant-mini` (4 network-verified recipes), dev-medium hidden grading
  moved to grading_dir. Next: end-to-end integration (generated reference → blackbox grade in
  docker), README/Makefile/HLD as-built, real haiku smoke (T-Dk6Lm7).
- By: Claude · Role: architect · Date: 2026-10-23 · Comment: Design accepted by owner with four review
  decisions (parity hard requirement, Python+Go+TS, sqlite3 + lightweight Mongo-equivalent only, light Docker
  sandbox, all three run contracts). `T-Cs1Ab2` core seams landed with 14 new fake-tier tests
  (bench suite 431 passed, mypy clean). Tracks T-Bx2Cd3 / T-Gn3Ef4 / T-St4Gh5 / T-T2-5Ij6 dispatched in parallel
  on worktrees from this commit.
