# STATUS

- ID: `T-Rpt3Wq-results-comparison-report`
- Updated At: 2026-07-22
- State: Done
- Owner: developer agent

## This update
- By: Claude · Role: developer · Date: 2026-07-22
- Comment: Implemented `bench/results.py` on top of `runner.py`'s already-landed
  `BenchRunRecord`/`BenchTaskRecord` shape (accepted deviation: the runner itself
  persists `run.json`, this task builds the writers on top by reading it back via
  `BenchRunRecord.model_validate`). Delivered `load_run(run_dir)` (validating loader,
  accepts a run dir or a `run.json` file directly), `write_summary_md(record | run_dir,
  run_dir=...)` (per-task markdown table: task/category/solved/score/wall/cost/tokens/
  turns/status + an aggregate footer), and `build_comparison(run_dirs, *, allow_mixed=
  False, clock=...)` + `write_comparison(comparison, out_dir)` (union-task-id-keyed
  per-task x per-subject matrix, per-subject aggregate table, and a "winner" line per
  axis: solve_rate, total cost, cost per solved, wall-clock).
- **One scoped addition beyond the "files you own" list:** added a single new
  `ResultsError(BenchError)` class to `bench/errors.py` (co-located with the existing
  `SubjectError`/`GraderError`, matching that file's own established
  one-error-type-per-producing-module convention) so `load_run`/`build_comparison` can
  raise a typed error (per this task's own AC3: "REFUSE (typed error)") rather than a
  bare `BenchError`. No other file outside `results.py`/`tests/bench/test_results.py`
  was touched.
- **Interpretation of the "mixed config_fingerprint for the SAME subject id" guard:**
  implemented as first-seen-fingerprint-wins tracking across the given `run_dirs` list;
  a later dir for a subject id already seen with a *different* `config_fingerprint`
  raises `ResultsError` unless `allow_mixed=True`, in which case the later run's data
  overwrites the earlier one (last-wins, documented in the docstring) — a genuinely
  underspecified corner of TASK.md's pseudocode, resolved with the simplest
  deterministic behavior rather than inventing a dual-identity scheme.
- **"Missing subject run.json" subject-id derivation:** `bench_run_id`/compare-dir names
  are `<date>-<suite_id>-<subject_id>`, and both ids may themselves contain hyphens, so a
  wholly missing run dir's subject id is recovered by stripping the date + a *known*
  suite id (learned from any sibling dir that DID load) off the dir's basename; if
  literally every given dir is missing, `build_comparison` raises `ResultsError`
  ("cannot determine the suite being compared") rather than guessing — not explicitly an
  AC, but the only sane behavior for zero-information input, covered by
  `test_build_comparison_all_missing_raises_results_error`.

## Evidence
- Files added: `src/agent_orchestrator/bench/results.py`, `tests/bench/test_results.py`.
- Files touched (1-class addition only): `src/agent_orchestrator/bench/errors.py`
  (`ResultsError`).
- `uv run pytest tests/bench -q` → **152 passed, 1 skipped** (was 133 passed/1 skipped
  before this task on the same branch — net +19, zero regressions).
- `uv run pytest -q -m "not real_llm"` → **1009 passed, 4 deselected** (baseline before
  this task 990 passed/4 deselected — net +19, zero regressions elsewhere).
- `uv run ruff check .` / `uv run ruff format --check .` clean on every touched file.
- `uv run mypy src/agent_orchestrator/bench tests/bench/test_results.py` → **Success: no
  issues found in 12 source files**.
- NFR-1 / SI-1: no file outside `bench/`/`tests/bench/` touched; `results.py` imports
  only sibling `bench/` modules (`runner`, `metrics`, `errors`) — nothing in core or in
  `bench/{spec,subjects,graders,metrics,runner}.py` imports `results.py`.

## Acceptance criteria verification
1. **AC1** (`run.json` + `summary.md` after a runner run; one row per task + aggregate
   footer): `test_write_summary_md_from_record_has_header_rows_and_footer`,
   `test_write_summary_md_from_run_dir_loads_and_writes`.
2. **AC2** (`write_comparison` over two subjects, same suite → matrix + per-subject
   aggregates + side-by-side `comparison.md`; `cost_per_solved` None when solved==0):
   `test_build_and_write_comparison_two_subjects_same_suite`.
3. **AC3** (different suite ids → refuse, typed error, no misleading output):
   `test_build_comparison_refuses_different_suites`,
   `test_build_comparison_refuses_different_schema_version`,
   `test_build_comparison_mixed_fingerprint_same_subject_refused_then_allowed`.
4. **AC4** (missing subject `run.json` → "not run", no crash):
   `test_build_comparison_missing_run_json_shown_as_not_run`.
5. **AC5** (`workspace` stored as a path pointer only; `mypy`/`ruff` clean; no core
   edit): unchanged from `runner.py`'s existing behavior (results.py never touches
   `workspace`/capture content, only reads `run.json`'s already-bounded fields) —
   verified by inspection + the mypy/ruff runs above.
- Extra coverage beyond the AC list: `load_run` missing/corrupt/schema-mismatch,
  `write_summary_md` on a zero-task run, `write_summary_md(record)` without `run_dir`
  raising clearly, None-cost/None-token em-dash rendering, divergent task-set union-
  keying, single-subject "comparison" degrading gracefully, and an all-run-dirs-missing
  refusal.

## Risks / Blockers
- None blocking handoff.

## Next actions
1. Handed off to `T-Cli8Nf-bench-cli-make-entrypoints` (`ao-bench run` calls
   `write_summary_md`; `ao-bench report` calls `build_comparison`/`write_comparison`).
