# STATUS

- ID: `T-Bg2Wq4-usd-budget-enforcement`
- Updated At: 2026-07-22
- State: **Done**
- Owner: developer agent

## This update
- By: developer agent
- Role: developer
- Date: 2026-07-22
- Comment: Implemented the per-`ao-bench run` USD cost budget end-to-end per TASK.md
  and ADR-0009 D3: `run_suite(..., cost_budget_usd: float | None = None)` accumulates
  cumulative `cost_usd` over non-`skipped_budget` recorded tasks, checks
  BEFORE scheduling each task, and records every task past the cap with
  `subject_status="skipped_budget"` (solved=False, score=0, cost=0.0), no
  workspace/subprocess. Resume treats a recorded `skipped_budget` task as the ONE
  exception to "already recorded -> skip" (re-attempted under the current cap); every
  other recorded status is unchanged. `cost_budget_usd` never enters `overrides`/
  `config_fingerprint` (control-flow, like `force`/`task_filter`). CLI resolves the
  effective cap (`--cost-budget-usd` > the suite's tier default via `bench/tiers.py`,
  `is not None` precedence so an explicit `0` is a real cap) and `skipped_budget` is
  excluded from `_HARNESS_FAILURE_STATUSES` (was already true structurally; made the
  exclusion explicit in a comment). Kept strictly serial per the Handoff Boundary --
  no thread pool, no lock (T-Pl3Rx7 adds both next).

## Evidence (AC-by-AC)
1. **AC1 (check-before-schedule boundary, bounded 1-task overshoot)**:
   `tests/bench/test_budget.py::test_budget_boundary_overshoot_then_skips_remaining` --
   fake subject scripted `fake_cost=2.0`/task, `cost_budget_usd=5.0`, 6 tasks: t1/t2/t3
   run for real (cumulative 0, 2, 4 all `<5`; t3 is the crossing task, pushing
   cumulative to 6), t4/t5/t6 recorded `skipped_budget` (solved=False, score=0,
   cost=0.0, `workspace=""` -- never materialized). Companion
   `test_budget_skip_logs_skip_budget_event_with_task_running_cost_and_cap` asserts the
   `bench.task.skip_budget {task_id, running_cost, cap}` log event (TASK.md's
   Schemas/Interface Notes bullet).
2. **AC2 (skipped_budget never fails the CLI exit)**: `_HARNESS_FAILURE_STATUSES`
   comment made explicit in `cli.py`; CLI tests
   `test_run_explicit_cost_budget_usd_caps_the_run` and
   `test_run_only_skipped_budget_tasks_still_exits_zero` (cap=$0 -> every task
   `skipped_budget`, zero successes, still exit 0) in `tests/bench/test_cli_bench.py`.
3. **AC3 (resume re-attempts skipped_budget; succeeded tasks untouched)**:
   `test_budget_resume_reattempts_skipped_leaves_succeeded_alone` (raise cap $5->$100:
   only the previously-skipped task is re-invoked, call-count-asserted via a local
   counting `FakeSubject` subclass; t1/t2 call count unchanged) and
   `test_budget_resume_still_capped_if_new_cap_still_too_low` (cap raised but still
   insufficient -> the task is considered/re-attempted but re-skipped, not silently
   left alone) in `tests/bench/test_budget.py`.
4. **AC4 (tier-default resolution)**: CLI tests
   `test_run_no_cost_budget_flag_defaults_to_suite_tier_small_cap` (no `tier` field ->
   defaults "small" -> committed `benchmarks/tiers.json` `$5`/subject: a $10/task
   subject runs task 1, skips task 2) and
   `test_run_no_cost_budget_flag_defaults_to_suite_tier_medium_cap` (`tier: "medium"` ->
   `$50`/subject: same two $10 tasks both succeed) in `test_cli_bench.py`;
   `test_run_explicit_cost_budget_usd_overrides_tier_default` proves the CLI flag beats
   even a more generous tier default.
5. **AC5 (`cost_budget_usd` excluded from `config_fingerprint`)**:
   `test_budget_cap_excluded_from_config_fingerprint` -- two independent (non-resuming)
   `run_suite` calls, same suite/subject, `cost_budget_usd=5.0` vs `999.0` ->
   `config_fingerprint` identical. Also implicitly proven by AC3's resume test (a
   fingerprint mismatch there would have raised `BenchError`, not succeeded) --
   asserted explicitly there too (`second.config_fingerprint == first.config_fingerprint`).
6. **AC6 (`cost_usd is None` treated as $0, documented in code)**:
   `test_budget_none_cost_counts_as_zero_for_running_total` (5 tasks, subject reports
   `cost_usd=None` for every one, cap=$1 -> ALL run, none skipped) and
   `test_budget_resume_recomputes_running_cost_treating_none_as_zero` (resume path
   specifically: an existing non-skipped record with `cost_usd=None` recomputes to $0,
   so a newly-added task under a low cap still runs). Documented in
   `runner.py`'s `run_suite` docstring and inline at both the initial
   `running_cost` computation and the post-task `+=` update.
   `_budget_skipped_result()` itself (`subjects.py`) unit-tested directly in
   `tests/bench/test_subjects.py` (`test_budget_skipped_result_defaults`,
   `test_budget_skipped_result_accepts_a_capture_dir`,
   `test_subject_result_status_literal_accepts_skipped_budget`).
- **Backward compatibility**: `cost_budget_usd` defaults to `None` (unlimited, no
  check at all) in `run_suite` -- every pre-existing caller/test that does not pass it
  is unaffected (`test_budget_none_default_means_unlimited_no_skip` asserts this
  explicitly; the full pre-existing `test_runner.py` suite also still passes
  unmodified).

### Commands run (actual output)
- `uv run pytest tests/bench -q --ignore=tests/bench/test_ao_epic_plus_subject.py
  --ignore=tests/bench/test_spec.py --ignore=tests/bench/test_workspace.py
  --ignore=tests/bench/test_registries.py` -> **218 passed, 1 skipped** (scoped
  baseline immediately before this task's new tests were added: 202 passed, 1
  deselected -- zero regressions, +16 is exactly this task's new tests: 8 in
  `test_budget.py`, 3 in `test_subjects.py`, 5 in `test_cli_bench.py`).
- `uv run pytest tests/bench/test_runner.py tests/bench/test_cli_bench.py
  tests/bench/test_metrics.py tests/bench/test_results.py tests/bench/test_subjects.py
  tests/bench/test_budget.py -q -m "not real_llm"` -> **113 passed, 1 deselected**
  (every owned test file, run individually together).
- `uv run pytest -q -m "not real_llm"` (whole repo) -> **1156 passed, 4 deselected**.
- `uv run ruff check` + `ruff format --check` on every touched file (`runner.py`,
  `cli.py`, `subjects.py`, `test_budget.py`, `test_subjects.py`, `test_cli_bench.py`)
  -> all clean.
- `uv run mypy src/agent_orchestrator/bench/runner.py
  src/agent_orchestrator/bench/cli.py src/agent_orchestrator/bench/subjects.py` ->
  **Success: no issues found in 3 source files**.
- `uv run ao-bench run --help` -> shows `--cost-budget-usd FLOAT`.

## Deviations / Assumptions
- **Where the tier-default resolution lives**: TASK.md's own pseudocode sketches
  `cost_budget_usd = param or tiers.load_tier_config(suite.tier).cost_budget_usd_per_subject`
  as if resolved *inside* `run_suite`. Per this task's explicit assignment instructions
  (authoritative over the pseudocode sketch), resolution instead lives in
  `bench/cli.py`'s `run` command: it loads the suite once to read `.tier`, resolves
  `--cost-budget-usd` (if given) `> load_tier_config(tier).cost_budget_usd_per_subject`
  (`is not None`, not `or`, so an explicit `--cost-budget-usd 0` is a real cap, not
  "unset" -- mirrors T-Tr1Km8's own documented footgun-avoidance in
  `tiers.resolve_effective`), and passes the already-resolved float into `run_suite`.
  `run_suite`'s own default stays `None` = **unlimited, no check at all** -- this is
  deliberate and load-bearing: every pre-existing direct caller of `run_suite` (all of
  `test_runner.py`, `test_metrics.py` integration paths, etc.) that does not pass
  `cost_budget_usd` is completely unaffected, rather than suddenly inheriting a
  tier-based cap. `bench/cli.py` re-loads the suite a second time inside `run_suite`
  (cheap JSON parse + pydantic validation, no I/O side effects) -- accepted duplication
  over widening `run_suite`'s own resolution responsibility.
- **`_budget_skipped_result()` cross-module import**: `runner.py` imports the
  underscore-prefixed `subjects._budget_skipped_result` directly (`from .subjects
  import SubjectResult, _budget_skipped_result`) rather than exposing a public name.
  Both files are owned by this same task/PR (TASK.md's own pseudocode names the
  function this way in `subjects.py` and implies `runner.py` calls it), so this is an
  internal contract between two files delivered together, not a new cross-task public
  API surface.
- **`skipped_budget` record's `workspace`/`capture_dir`**: left `""` (per TASK.md
  pseudocode's `capture_dir=ctx? or ""` -- no `ctx` exists at all for a skipped task
  since `materialize_workspace` is never called), rather than a would-be, never-created
  path. `results.py`'s existing `summary.md`/`comparison.md` status column already
  renders the raw `subject_status` string, so `"skipped_budget"` displays distinctly
  with **no edit needed** to `results.py` or `metrics.py` (`subject_status: str` already
  accepted any value) -- confirmed both stayed untouched (`git diff --stat` shows
  neither file in this task's changes).
- No other deviations from TASK.md's Acceptance Criteria/Risks.

## Risks / Blockers
- None outstanding. Dependency `T-Tr1Km8` landed (commit `c66ae68`) before this task
  started.
- **Concurrency note (for T-Pl3Rx7)**: this task is explicitly SERIAL per its Handoff
  Boundary -- the accounting is a plain `running_cost: float` local, updated
  read-check-then-write around the budget-check-and-append. The exact lock-in point is
  commented in `runner.py` at both (a) the initial `running_cost` computation
  (immediately after `tasks_dict` is built from `existing_record`) and (b) the two
  mutation sites (the `skipped_budget` branch's `tasks_dict[...] =` + `_persist_record`,
  and the normal-run branch's `tasks_dict[...] =` + `running_cost += ...` +
  `_persist_record`) -- all three need to move under ONE lock together once tasks can
  run concurrently, so a `running_cost >= cap` check and the following schedule/append
  are atomic across worker threads.

## Forward notes for T-Pl3Rx7 (parallel bench runner)
- Grep `LOCK-IN POINT` in `src/agent_orchestrator/bench/runner.py` -- two comments mark
  exactly where the lock must wrap: the budget check (`if cost_budget_usd is not None
  and running_cost >= cost_budget_usd: ...`) together with BOTH of its
  `tasks_dict[task.id] = ...` + `_persist_record(...)` write sites (the `skipped_budget`
  branch and the normal-completion branch), plus the `running_cost += sr.cost_usd or
  0.0` update. Today (serial, one task in flight) a plain float suffices; under
  `--max-parallel > 1` the read-check-write must be atomic or two workers can both pass
  the check when only one task's worth of headroom remains (unbounded overshoot instead
  of the documented bounded one).
- The resume rule (`tasks_dict[task.id].subject_status != "skipped_budget"` as the ONE
  exception to "already recorded -> skip") and the fingerprint exclusion (`overrides`
  dict in `run_suite` deliberately omits `cost_budget_usd`) are both structural, not
  timing-dependent -- should need no changes for parallelism, only the running-cost
  accounting itself needs the lock.
- `cost_budget_usd`'s CLI resolution lives in `bench/cli.py`'s `run` command (loads the
  suite once for `.tier`, then `is not None` precedence over `tiers.load_tier_config(...)
  .cost_budget_usd_per_subject`) -- `--max-parallel`'s own default resolution
  (`TierConfig.default_max_parallel` / `tiers.resolve_effective(...)["max_parallel"]`)
  should follow the same pattern for consistency.

## Next actions
- None -- task complete, ready for T-Pl3Rx7 (concurrency-correct budget) and T-Cm9Tb4
  (whole-run campaign cap, builds on this per-run primitive).
