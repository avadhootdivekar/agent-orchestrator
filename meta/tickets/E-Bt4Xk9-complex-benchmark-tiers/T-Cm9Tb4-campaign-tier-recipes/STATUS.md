# STATUS

- ID: `T-Cm9Tb4-campaign-tier-recipes`
- Updated At: 2026-07-22
- State: **Done**
- Owner: developer agent

## This update
- By: developer agent
- Role: developer
- Date: 2026-07-22
- Comment: Implemented `--max-parallel` CLI wiring on `ao-bench run` per T-Pl3Rx7's own
  forward note, the disabled-tier `--enable-xlarge` gate (shared by `run` AND the new
  `campaign` command), `ao-bench campaign` (whole-run USD cap, ADR-0009 D3) in a new
  `bench/campaign.py`, and the `bench-medium`/`bench-large`/`bench-xlarge` Make
  recipes with a hard `AO_BENCH_CONFIRM=1` spend-confirmation gate.

## Evidence (AC-by-AC, TASK.md numbering)

1. **AC1 (whole-run cap: 3 fake subjects @ scripted cost, cap hits after 2nd -> 3rd
   recorded/reported skipped, comparison covers the ones that ran)**:
   `tests/bench/test_campaign.py::test_campaign_whole_run_cap_stops_third_subject` --
   3 subjects @ $5/subject, `--run-budget-usd 10`: subject 1 (cum 0->5) and subject 2
   (cum 5->10) both run in full; subject 3's `remaining = 10-10 = 0 <= 0`, so it is
   recorded `launched=false`/`status=skipped_budget` WITHOUT `run_suite` ever being
   called for it (asserted: no result dir was ever created for it) -- matches TASK.md's
   own Pseudocode/Algorithm section literally (`if remaining <= 0: skipped...; continue`).
   The comparison (`build_comparison`, reused as-is) is fed every subject's real-or-
   predicted dir (via `runner.compute_bench_run_id`, also reused) so it both covers the
   two that ran AND shows subject 3 in `not_run` -- asserted directly.
2. **AC2 (per-subject cap = min(remaining, per-model cap); bounded overshoot only)**:
   `test_campaign_per_subject_cap_bounded_by_remaining_headroom` -- subject 1 (1x$5
   task) consumes an explicit `--cost-budget-usd 5` base cap in full (cum->5 under
   `--run-budget-usd 7`); subject 2's remaining headroom is then $2, TIGHTER than the
   $5 base. Subject 2 has 3x$1 tasks: exactly 2 run (cum 0->1->2, third checked at
   cum=2>=2) and its aggregate cost is asserted `== 2.0`, never `3.0` -- proves the
   `min(remaining, base)` bound is actually APPLIED, not merely resolved and ignored
   (a bug using the base cap alone would let all 3 tasks run, costing $3).
   `test_campaign_subject_can_overshoot_whole_cap_by_bounded_per_task_amount` proves
   the complementary, documented bound: a SINGLE already-scheduled task can still push
   total spend over the whole cap by up to that one task's own cost (never unbounded) --
   subject 2's lone $5 task runs against only $2 of headroom (check-before-schedule
   only compares ALREADY-recorded cost, so a fresh subject's first task always passes),
   landing `total_spent_usd == 6.0` against a `$3` whole cap. Both assert `exit_code ==
   0` -- overshoot/budget-skip is never a harness failure (AC6).
3. **AC3 (disabled tier refused without --enable-xlarge; proceeds with it)** -- for
   BOTH `run` and `campaign`, against the REAL committed `benchmarks/tiers.json`
   (xlarge really is `enabled:false` there, no temp fixture needed):
   `test_cli_bench.py::test_run_disabled_tier_refused_without_enable_xlarge` /
   `test_run_disabled_tier_proceeds_with_enable_xlarge` and
   `test_campaign.py::test_campaign_disabled_tier_refused_without_enable_xlarge` /
   `test_campaign_disabled_tier_proceeds_with_enable_xlarge`. The error message names
   both the tier and the flag (asserted via substring checks); a single
   `campaign.enforce_tier_enabled` helper is shared by both CLI commands (no
   duplicated check/message).
4. **AC4 (`make bench-medium`/`bench-large` invoke `campaign` against the right
   suite/subjects; `make -n` dry-run shows the correct invocation)**: verified manually
   (`make -n bench-medium`, `make -n bench-large`, `make bench-xlarge`) -- see "Manual
   Make verification" below (Makefile recipes are shell, out of `pytest`'s reach; no
   `.PHONY`/recipe-parsing test framework exists in this repo to automate this, so this
   AC is evidenced by transcript rather than a test, per the task's own "your call"
   allowance).
5. **AC5 (campaign resumable: rerun skips completed subjects' tasks, recomputes
   cumulative spend)**: `test_campaign_resume_does_not_rerun_completed_subject` -- a
   `_CountingSubject` (monkeypatched into `SUBJECT_REGISTRY`) proves `Subject.run` is
   called exactly twice (one task x two subjects) on the FIRST campaign call and
   **zero** additional times on a same-day rerun with identical args; `total_spent_usd`
   is identical across both calls (`4.0`), recomputed from the resumed `run.json`s, not
   carried over in memory (each `run_campaign` call starts `spent=0.0` and rebuilds it
   entirely from `run_suite`'s own resumed `aggregate.total_cost_usd`).
6. **AC6 (exit 0 clean/budget-capped, exit 1 usage/spec error; budget-skip is not a
   failure)**: every AC1-5 test above that exercises a `skipped_budget`/overshoot path
   asserts `exit_code == 0`; the disabled-tier tests assert `exit_code == 1`. Unlike
   `ao-bench run`, `campaign` intentionally has NO exit-2-equivalent (TASK.md AC6 only
   specifies 0/1) -- a per-task harness failure inside one subject's run does not
   escalate the campaign's own exit code (documented deviation, see below).

## Additional coverage beyond the numbered ACs
- **`--max-parallel` CLI wiring (T-Pl3Rx7's forward note, this task's Deliverable 1)**:
  `test_cli_bench.py::test_run_max_parallel_flag_overrides_tier_default` /
  `test_run_max_parallel_defaults_to_suite_tier_medium_default` /
  `test_run_max_parallel_defaults_to_suite_tier_small_default` and the campaign-side
  mirrors `test_campaign.py::test_campaign_max_parallel_flag_overrides_tier_default` /
  `test_campaign_max_parallel_defaults_to_suite_tier` -- precedence
  `flag (is not None) > suite.tier.default_max_parallel > builtin 1`, resolved via
  `tiers.resolve_effective` exactly as directed (not re-derived). Each test stubs
  `run_suite` (at the module attribute the CALLER's lazy/module-level import actually
  reads from -- `agent_orchestrator.bench.runner.run_suite` for `run`'s lazy import,
  `agent_orchestrator.bench.campaign.run_suite` for `campaign`'s module-level one) to
  capture the resolved kwarg and raise a sentinel `BenchError`, isolating the CLI's OWN
  resolution logic from `run_suite`'s already-separately-tested real concurrency
  behavior (T-Pl3Rx7's own test suite) -- avoids re-proving genuine thread-pool
  concurrency here, which is out of this task's scope.
- **campaign.json shape**: `test_campaign_json_shape` asserts every field TASK.md's
  Schemas/Interface Notes call for (`suite`, `tier`, `caps`, per-subject `subject id,
  run dir, aggregate cost, solve rate, status`) plus `schema_version`/`campaign_id`/
  `generated_at`/`comparison_json`/`comparison_md`, and that `comparison.json`/`.md`
  physically exist alongside it.
- **`run_campaign([])` guard**: `test_run_campaign_direct_call_rejects_empty_subject_list`
  (direct unit call -- unreachable through the CLI, where Click's own required-option
  validation for `--subject` gets there first; that Click behavior is itself asserted
  by `test_campaign_missing_subject_is_a_click_usage_error`, exit code 2, distinct from
  this CLI's own `EXIT_USAGE_ERROR=1`).

## Manual Make verification (transcripts)
- `make -n bench-medium` -> prints the cost envelope + the exact command:
  `uv run ao-bench campaign --suite benchmarks/suites/dev-medium/suite.json --subject
  benchmarks/subjects/claude-sonnet.json --subject benchmarks/subjects/claude-opus.json
  --subject benchmarks/subjects/ao-epic-sonnet.json --subject
  benchmarks/subjects/ao-epic-plus-sonnet.json --out-dir benchmarks/results` (no
  `--max-parallel` -- resolves from `dev-medium`'s own `tier: medium` ->
  `default_max_parallel: 4`, exactly the PLAN's own ask).
- `make -n bench-large` -> same shape, `swe-verified-mini` x 3 subjects, with
  `AO_BENCH_SWEBENCH_KEEP_IMAGES=1` exported ahead of the command (T-Sg6Jf2's forward
  note).
- `make bench-medium` / `make bench-large` (no `AO_BENCH_CONFIRM`) -> print the cost
  envelope + command, then `exit 1` ("Refusing to launch... Set AO_BENCH_CONFIRM=1")
  -- verified real (non-dry-run) invocation, confirmed NOTHING was launched.
- `make bench-xlarge` (no `FORCE_XLARGE`) -> prints the disabled-by-design message,
  `exit 1`, nothing else. `make bench-xlarge FORCE_XLARGE=1` -> prints the same plus
  the `--enable-xlarge` command, still refuses without `AO_BENCH_CONFIRM=1`.
  `make bench-xlarge FORCE_XLARGE=1 AO_BENCH_CONFIRM=1` -> actually invokes
  `ao-bench campaign ... --enable-xlarge`, which fails cleanly with `ERROR: Spec file
  not found: benchmarks/suites/xlarge/suite.json` (expected and documented: no xlarge
  suite is committed by design, EPIC.md scope-out; this recipe only proves the
  `--enable-xlarge` plumbing reaches the CLI, not that an xlarge run is runnable).
- Real FAKE end-to-end (`ao-bench campaign --suite benchmarks/suites/dev-core/suite.json
  --subject benchmarks/subjects/fake-pass.json --out-dir <scratchpad>`): exit 0,
  produced `<scratchpad>/2026-07-22-dev-core-campaign/{campaign.json,comparison.json,
  comparison.md}` + `<scratchpad>/2026-07-22-dev-core-fake-pass/{run.json,summary.md}`;
  `campaign.json` shape verified by hand (schema_version, all caps, 1 subject row,
  solved 6/6); confirmed via `git status --porcelain -- benchmarks/results` (empty) that
  nothing landed in the committed tree; scratchpad output deleted after verification.

## Commands run (actual numbers)
- `uv run pytest tests/bench/test_cli_bench.py tests/bench/test_campaign.py -q` ->
  **39 passed** (27 in `test_cli_bench.py` [22 pre-existing/unedited-in-substance + 5
  new `--max-parallel`/disabled-tier tests + 1 pre-existing test adjusted, see
  Deviations] + 12 new in `test_campaign.py`).
- `uv run pytest tests/bench -q` -> **401 passed, 4 skipped** (baseline before this
  task's own new tests, scoped to `tests/bench`, was not separately re-measured; the
  repo-wide before/after delta below is the authoritative regression check).
- `uv run pytest -q -m "not real_llm"` (whole repo) -> **1258 passed, 3 skipped, 4
  deselected**, twice for stability. Stated baseline (dispatch message, latest commit
  `7e42023`): **1241 passed / 3 skipped / 4 deselected**. Delta: **+17 passed, 0
  skipped/deselected changed, 0 failures** -- exactly 17 new tests (5 in
  `test_cli_bench.py` + 12 in `test_campaign.py`), zero regressions.
- `uv run ruff check` + `ruff format --check` on every touched Python file
  (`campaign.py`, `cli.py`, `test_campaign.py`, `test_cli_bench.py`) -> all clean.
  (`Makefile` is not Python -- ruff does not apply; verified instead via `make -n`/
  real invocation transcripts above.)
- `uv run mypy src` (CI's own scope) -> the same 4 PRE-EXISTING `_version.py` errors
  every prior task in this epic has already documented (confirmed untouched by `git
  diff --stat -- src/agent_orchestrator/_version.py`, zero diff), zero new errors.
  `uv run mypy src/agent_orchestrator/bench/campaign.py
  src/agent_orchestrator/bench/cli.py` directly -> **Success: no issues found in 2
  source files**.
- `uv run ao-bench run --help` / `uv run ao-bench campaign --help` -> both list every
  new flag (`--max-parallel`, `--enable-xlarge` on `run`; `--suite`, `--subject`,
  `--max-parallel`, `--cost-budget-usd`, `--run-budget-usd`, `--out-dir`, `--force`,
  `--enable-xlarge` on `campaign`) -- verified by hand and by
  `test_run_help_documents_flags`/`test_campaign_help_documents_flags`.

## Deviations / Assumptions
- **One pre-existing test adjusted (not a new regression -- a design-mandated
  behavior change surfaced by this task's own wiring), in-scope
  (`tests/bench/test_cli_bench.py` is exclusively owned here)**:
  `test_run_explicit_cost_budget_usd_overrides_tier_default` used a `tier: medium`
  suite with no `--max-parallel` given. Before this task, `ao-bench run` NEVER passed
  `max_parallel` to `run_suite` at all (always its own default of `1`, serial). Wiring
  `--max-parallel` per T-Pl3Rx7's forward note means the SAME suite now resolves
  `max_parallel=4` (medium's own tier default) even with no flag -- the test's 2 tasks
  then dispatch concurrently, and the budget check's OWN documented bounded overshoot
  (ADR-0009 D4) could let both pass the $5 cap together before either's cost lands,
  breaking the test's serial-ordering assumption. Fixed by adding `--max-parallel 1`
  to that one test (it is about `--cost-budget-usd` precedence, not concurrency) --
  restores its original deterministic intent, does not touch runner.py/tiers.py.
  **This is a genuine, foreseen behavior change to `ao-bench run` itself**: any
  medium/large-tier suite now runs with real task-level concurrency BY DEFAULT (no
  flag needed) unless the caller passes `--max-parallel 1` explicitly -- exactly the
  design T-Pl3Rx7's forward note specifies (mirrors how `--cost-budget-usd` already
  defaults through the tier), called out here since it is an observable change to
  every OTHER existing/future `run` invocation against a medium/large suite.
- **Campaign exit code has no code-2 equivalent** (TASK.md AC6 only specifies 0/1,
  unlike `run`'s three-way 0/1/2): a per-task harness failure (`failed`/`timed_out`/
  `error`) inside one subject's run does not escalate `campaign`'s own exit code --
  only a usage/spec-level `SpecValidationError`/`BenchError` does. Per-subject
  outcomes (including a harness failure) are fully visible in `campaign.json`/stdout;
  not silently swallowed, just not surfaced as a distinct exit code, per AC6's literal
  text.
- **Skipped subjects ARE fed into `build_comparison`** (via their predicted-but-never-
  materialized result dir, `runner.compute_bench_run_id` reused for the prediction) so
  they show up as `not_run` in the comparison, rather than being omitted entirely.
  TASK.md's AC1 says "the comparison covers the subjects that ran" -- read as a
  minimum bar, not an exclusion; showing a skipped subject as `not_run` is strictly
  additional information and reuses `results.py`'s own purpose-built "missing
  run.json -> not run" handling instead of adding a parallel notion of "didn't run"
  here (smallest correct change / DRY).
- **`resolve_effective` reused for BOTH `max_parallel` and `cost_budget_usd`, in
  `campaign.py` only** (in `cli.py`'s `run` command, the pre-existing, already-tested
  `cost_budget_usd` ternary was left untouched -- only its `load_tier_config(...)` call
  was hoisted into a shared `tier_config` variable per T-Pl3Rx7's forward note, zero
  behavior change). In `run_campaign`, a single `resolve_effective(tier_config,
  cli_max_parallel=..., cli_cost_budget=...)` call resolves both, since campaign.py's
  `cost_budget_usd` parameter has the exact same "per-subject cap override" meaning
  `resolve_effective` already models -- avoids re-deriving the same ternary a second
  time in a second file.
- No `results.py` edit was needed (`build_comparison`/`write_comparison` reused as-is,
  including their existing `not_run`/missing-dir handling -- no helper was missing).
- File ownership honored exactly: only `bench/cli.py` (edited, additive), NEW
  `bench/campaign.py`, `Makefile` (edited, additive), `tests/bench/test_cli_bench.py`
  (extended, + the one adjusted pre-existing test above), NEW
  `tests/bench/test_campaign.py`, and this task's own `TASK.md`/`STATUS.md`. Zero
  edits to `runner.py`/`spec.py`/`tiers.py`/`registries.py`/`workspace.py`/
  `subjects.py`/`graders.py`/`swebench_*.py`/`results.py`/`benchmarks/**`/any other
  test file/`EPIC.md`/epic `STATUS.md`.

## Risks / Blockers
- None outstanding for this task's own scope. `--max-parallel` now defaulting through
  the tier for `ao-bench run` (see Deviations) is a real, intentional behavior change
  future callers/docs should be aware of -- flagged for T-Dc1Yg7 (docs) below.

## Forward notes for T-Ts0Xn5 (tier/budget/parallel/campaign test task)
- This task already ships `tests/bench/test_campaign.py` (12 tests) covering the
  campaign-specific ACs end to end via `CliRunner`; T-Ts0Xn5's own scope (per EPIC.md
  FR-10, "tests for tier/budget/parallel/provider/campaign") should treat this file as
  the campaign baseline to extend/consolidate, not duplicate from scratch -- check for
  coverage gaps (e.g. a `--force` re-run test for `campaign` specifically, or a
  multi-day `bench_run_id` rollover case) rather than re-authoring the whole-cap/
  per-subject-cap/resume scenarios already here.
- `benchmarks/tiers.json`'s xlarge entry is used DIRECTLY (no temp-fixture tiers.json)
  by the disabled-tier tests in both `test_cli_bench.py` and `test_campaign.py` -- if
  T-Ts0Xn5 adds a coverage-gate/CI check that tiers.json's `enabled` values must stay
  stable, note these tests have a soft dependency on `xlarge.enabled == false` staying
  true in the committed config.

## Forward notes for the PLAN runs (medium + large campaigns)
Exact commands the orchestrator should use (both require `AO_BENCH_CONFIRM=1`; neither
`make` target spends anything without it):

**Medium tier (EPIC.md PLAN Run 1, ~$75 expected, range $48-141, hard cap $150):**
```
make bench-medium AO_BENCH_CONFIRM=1
```
Equivalent direct CLI (identical to what the recipe prints/runs; `--max-parallel` is
deliberately omitted so it resolves from `dev-medium`'s own `tier: medium` ->
`default_max_parallel: 4`, matching the PLAN's own ask):
```
uv run ao-bench campaign \
  --suite benchmarks/suites/dev-medium/suite.json \
  --subject benchmarks/subjects/claude-sonnet.json \
  --subject benchmarks/subjects/claude-opus.json \
  --subject benchmarks/subjects/ao-epic-sonnet.json \
  --subject benchmarks/subjects/ao-epic-plus-sonnet.json \
  --out-dir benchmarks/results
```

**Large tier (EPIC.md PLAN Run 2, ~$155 expected, range $95-270, hard cap $800):**
```
make bench-large AO_BENCH_CONFIRM=1
```
Equivalent direct CLI (`AO_BENCH_SWEBENCH_KEEP_IMAGES=1` per T-Sg6Jf2's own forward
note -- avoids re-pulling each ~1GB SWE-bench image up to 3x across the 3 subjects;
`--max-parallel` again omitted, resolves to `swe-verified-mini`'s `tier: large` ->
`default_max_parallel: 3`):
```
AO_BENCH_SWEBENCH_KEEP_IMAGES=1 uv run ao-bench campaign \
  --suite benchmarks/suites/swe-verified-mini/suite.json \
  --subject benchmarks/subjects/claude-sonnet.json \
  --subject benchmarks/subjects/claude-opus.json \
  --subject benchmarks/subjects/ao-epic-sonnet.json \
  --out-dir benchmarks/results
```
Both write their own `campaign.json` + `comparison.{json,md}` under
`benchmarks/results/<date>-<suite>-campaign/` (committed, per design). Both are safe to
re-run if interrupted (same-day resume, per AC5) -- re-running with the SAME command
picks up exactly where a crashed/killed run left off, re-spending nothing for subjects
already fully recorded.

## Next actions
- None -- task complete. Downstream: T-Ts0Xn5 (test consolidation, see forward note
  above), T-Dc1Yg7 (docs: README/HLD need the `campaign` command, `--max-parallel`'s
  new tier-defaulted-by-default behavior on `run`, and `--enable-xlarge`), the PLAN
  runs (commands above).
