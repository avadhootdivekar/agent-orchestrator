# TASK: T-St4Gh5-stats-campaign

- Epic: `E-Bn7Dsc-discriminating-benchmark-suites`
- Title: Paired stats, comparison/campaign repeats, infra table, calibrate
- Status: Done (branch `ad/bench-stats`, not merged)
- Design: `docs-md/benchmark-discriminating-suites-hld.md` (§8 maps each piece), `docs-md/bench-probe-dsl.md`

## Acceptance
- [x] Implements the §8 rows named in the title against the committed seams (registries, `grading_dir`, `GraderConfig.model_extra`, `RunContext`).
- [x] Unit + e2e tests fake-tier (no LLM/network); docker-dependent tests gated by `AO_E2E_DOCKER=1` (none needed here).
- [x] `ruff`, `mypy src/agent_orchestrator/bench`, and `pytest tests/bench` green.

## Delivered (contract summary; evidence in STATUS.md)
- `bench/stats.py` -- pure, seeded (`random.Random(seed)`), no scipy/numpy: `bootstrap_ci_mean`, `paired_bootstrap_ci`
  (percentile CI of a mean of per-task diffs), `wilcoxon_signed_rank` (zeros dropped, tie-averaged ranks, exact p up to
  40 non-zero diffs else tie-corrected normal approx), `median`/`mean`/`sd`/`summarize`/`format_ci`/`ci_excludes_zero`.
- `bench/results.py` -- `build_comparison` aggregates several run dirs of one subject as repeats (cell mean/sd/n, solve
  rate, cost/wall medians); refuses differing `parity_fingerprint` (unless `allow_mixed` -> `parity_mixed=True`);
  `comparison.json` schema **2.0** adds `parity_fingerprint`, `parity_mixed`, `parity_by_subject`, `repeats_per_subject`,
  `subject_stats` (attempted-based rates), `per_family`, `per_language`, `infra` (`count`, `task_ids`,
  `suspect_task_ids`), `pairwise`, `unreliable_subjects`; `load_comparison` still reads 1.0. `comparison.md` keeps the
  three original tables and adds Parity / Attempted-based rates / Pairwise / Per family / Per language / Infra sections
  + an UNRELIABLE banner (>10 % infra for any subject). Winners rank on attempted rate and mean score.
- `bench/campaign.py` -- `run_campaign(repeats=N, staged=True)`: stage 1 = repeat 1; while a pair's CI includes 0 (or has
  no common attempted task) and repeats/cap allow, repeat k+1 runs for ALL launched subjects on `select_repeat_tasks`
  (tasks where subjects disagree, a subject's own repeats vary, or a subject has no attempted sample yet). `staged=False`
  repeats every task for every subject. `CampaignRecord` gains `repeats_requested`, `repeats_done`, `staged`,
  `stage_log[{repeat, task_ids, reason, subjects}]`, `stage_stop_reason`; subjects gain `repeat_run_dirs`,
  `repeat_cost_usd`. The whole-run cap covers every repeat.
- `bench/cli.py` (`campaign` + `report` only) -- `campaign --repeats N --no-staged`; `report` accepts repeat dirs
  (`--run-dir` or `--results-root` discovery grouping `-r<n>` dirs, skipping compare/campaign dirs), prints pairwise CI
  lines and non-parity/unreliable warnings; `--allow-mixed` now also covers parity mismatches.
- `bench/calibrate.py` -- `ao-bench calibrate --suite S --subject A [--subject B ...] --repeats 2 --band 0.2,0.8
  [--out-dir] [--write] [--task]`: pooled attempted pass rate per task, verdict in/above/below/no_data (band inclusive),
  suggestion raise/lower difficulty (has `difficulty` vector) or retire (fixed task); `--write` stores
  `calibration: {date, subjects, repeats, pooled_pass_rate, verdict}` in suite.json.
