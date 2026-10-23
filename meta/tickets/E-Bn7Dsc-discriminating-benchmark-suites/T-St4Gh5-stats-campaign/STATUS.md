# STATUS

- ID: `T-St4Gh5-stats-campaign`
- Updated At: 2026-10-23
- State: **Done** (on branch `ad/bench-stats`; not merged, not pushed)

## This update
- By: Claude · Role: developer · Date: 2026-10-23 · Comment: implemented stats.py, repeat-aware comparison
  (parity refusal, infra table, per-family/language roll-ups, pairwise paired-bootstrap CI + Wilcoxon), staged repeats in
  `run_campaign` + `ao-bench campaign --repeats/--no-staged`, `ao-bench report` over repeat dirs, and `ao-bench calibrate`.
  Evidence: `uv run pytest -q tests/bench` 555 passed / 4 skipped (baseline 431 / 4; +124 new fake-tier tests in
  `test_stats.py`, `test_comparison_stats.py`, `test_campaign_stats.py`, `test_calibrate.py`; two existing winners
  assertions in `test_results.py` updated for the new "Highest mean score" line); `ruff format --check`, `ruff check`
  and `mypy src/agent_orchestrator/bench` clean.

## Staged-repeat task-selection rule (as built)
After each round the comparison is rebuilt over all repeat dirs. The loop stops when every subject pair's paired-bootstrap
95 % CI on the score difference excludes 0 (`ci_excludes_zero`), when `repeats` is reached, when the whole-run cap is
spent, when fewer than two subjects ran, or when no task qualifies. Otherwise repeat k+1 runs for every launched subject,
only on tasks that qualify: **disagree** (subjects' repeat-mean scores not all identical), **variance** (some subject's own
repeats differ, `score_sd > 0`), **infra-retry** (some subject has no attempted sample yet). Tasks every subject scores
identically and consistently are not re-run. Reasons/stop reason are recorded in `campaign.json`.

## Open issues / notes
- Epic `EPIC.md` / `STATUS.md` rollup (task list checkbox, "n/6") intentionally NOT edited here (parallel tracks edit the
  same lines; the manager syncs the rollup on merge).
- HLD / `benchmarks/README.md` not edited (owned by `T-Dk6Lm7-docs-skill-smoke`); the contract above is the source for it.
- `api_seconds` / `agent_seconds_sum` (HLD §5.3) have no seam in `BenchTaskRecord` yet, so comparison reports wall medians
  and `cost_per_score_point` (§5.4) only.
- `calibrate --write` rewrites suite.json with `json.dumps(indent=2)`: a hand-compacted suite (e.g. `dev-core`) is
  re-indented once on the first write; canonical (generated) suites are unchanged apart from `calibration`.
- Rule blind spot (by design, per HLD §6): tasks every subject fails (or solves) identically at repeat 1 are never re-run,
  so a later improvement on them is not observed until a fresh campaign.
