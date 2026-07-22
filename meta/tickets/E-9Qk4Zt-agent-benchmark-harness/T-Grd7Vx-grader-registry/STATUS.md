# STATUS

- ID: `T-Grd7Vx-grader-registry`
- Updated At: 2026-07-22
- State: Done
- Owner: developer agent

## This update
- By: Claude · Role: developer · Date: 2026-07-22
- Comment: Implemented `bench/graders.py` (`Grader` ABC + `GradeResult` + `PytestGrader`/
  `CommandGrader`/`FileAssertionGrader`/`FakeGrader`, all registered into
  `GRADER_REGISTRY` at import time) and `bench/metrics.py` (`TaskMetric`/`Aggregate` +
  `build_task_metric`/`aggregate`), per design §4.3/§4.4/§6. Only the files in this
  task's ownership list were touched: `src/agent_orchestrator/bench/{graders.py,
  metrics.py}`, `tests/bench/{test_graders.py,test_metrics.py}`, this folder's
  `TASK.md`/`STATUS.md`. `bench/spec.py`, `bench/subjects.py`, schemas, `conftest.py`,
  `EPIC.md`/epic `STATUS.md` were not modified.

- **Contract gap found, NOT fixed here (outside owned files — flagged for
  arbitration):** `Assertion.golden` (`bench/spec.py`, docstring: "relative to the
  suite.json") is validated to exist at LOAD time by `load_suite` using `base =
  Path(suite_path).resolve().parent`, but `load_suite` never rewrites `golden` to an
  absolute path before returning the `BenchSuite`. Neither `GraderConfig` nor the
  (not-yet-landed) `RunContext` (design §6) carries the suite's base directory through
  to grade time, so `FileAssertionGrader` cannot reconstruct the same base path
  `load_suite` used when a downstream runner (T-Run5Tz) eventually calls `grade(cfg,
  ctx)` with only those two objects. **Needed fix (my recommendation, for arbitration):
  `load_suite` should resolve `Assertion.golden` to an absolute path in place** before
  returning the suite (small, natural change local to `spec.py`, which I don't own) —
  or alternatively thread a `suite_base_dir` through `RunContext`/`GraderConfig`.
  **Current behavior (implemented, documented in `graders._resolve_golden_path`):** an
  already-absolute `golden` is used directly; a relative one resolves against the
  process's current working directory; either way resolution NEVER raises — an
  unresolvable golden degrades the `equals_file` assertion to `passed=False`, not an
  exception. Covered by `test_file_assertion_grader_equals_file_matches_absolute_golden`
  and `test_file_assertion_grader_equals_file_missing_golden_no_exception` (both use
  absolute golden paths deliberately, to stay deterministic regardless of pytest's
  invocation cwd).

- **Deviation from the design §4.4 pseudocode's `build_task_metric` signature (adapted,
  not silently guessed):** the pseudocode reads `task.domain` directly, but `BenchTask`
  (`bench/spec.py`) has no `domain` field — design §6 marks it "domain:str(inherited)",
  i.e. inherited from the *suite*, not the task. `build_task_metric` therefore takes an
  explicit `domain: str` parameter instead; the future runner (which holds the
  `BenchSuite`) passes `suite.domain`. No acceptance criterion depends on the literal
  pseudocode signature.

- **`Aggregate` extended beyond the §4.4 pseudocode** to include token totals
  (`total_input_tokens`, `total_output_tokens`, `total_cache_creation_input_tokens`,
  `total_cache_read_input_tokens`) and `total_wall_clock_seconds`, per `TASK.md`'s own
  description ("totals: cost/tokens/wall-clock") which is broader than the pseudocode
  shown in the design doc.

- **`GraderContext`/`SubjectResultLike` are local structural `Protocol`s**, not imports
  of `bench.subjects` (concurrently written by `T-Sbj9Ka`), per the assigning
  instruction. Any object with the required attributes (`repo_dir` /
  `status,wall_clock_seconds,cost_usd,...`) — including the real `RunContext`/
  `SubjectResult` once `T-Sbj9Ka` lands — satisfies them without an import edge.

- **`FakeGrader`'s script vocabulary is invented** (documented in its docstring):
  `GraderConfig` has no dedicated fake-only fields (unlike `SubjectSpec`'s
  `scripted_effect`), so it repurposes `cfg.command == "false"` (not solved, mirrors
  `CommandGrader`) and `cfg.pass_threshold` (if set, becomes the literal returned
  score). Test-only, per design §3.1; open to arbitration if a different scripting
  knob is preferred.

## Evidence
- Files added: `src/agent_orchestrator/bench/{graders.py,metrics.py}`,
  `tests/bench/{test_graders.py,test_metrics.py}`.
- `uv run pytest tests/bench/test_graders.py tests/bench/test_metrics.py -q` →
  **31 passed**.
- `uv run pytest tests/bench -q` → **78 passed, 1 failed** — the failure is
  `tests/bench/test_registries.py::test_registries_start_empty`, a file this task does
  not own (T-Sc4Hm2's foundation suite). Its own in-file comment reads "no concrete
  Subject/Grader classes registered yet" — it is an assertion that `GRADER_REGISTRY ==
  {}`, which is structurally incompatible with this task's entire purpose (registering
  4 graders at import time) and was always going to need updating once either
  `T-Grd7Vx` or `T-Sbj9Ka` landed. Verified by temporarily removing
  `graders.py`/`metrics.py`: `tests/bench/test_registries.py` alone passes 5/5 without
  them. **Recommended fix (arbitration): update/retire that assertion** (e.g. assert
  registry membership grows monotonically, or drop the emptiness check once both
  registry-populating tasks are expected to have landed).
- `uv run pytest -q -m "not real_llm"` → **935 passed, 1 failed, 3 deselected**
  (939 collected). Baseline immediately before this task's 2 new files (verified by
  temporarily moving them aside): **908 passed, 3 deselected, 0 failed** — net **+31
  passing tests, zero regressions from a bug in this task's code**; the 1 failure above
  is the anticipated `test_registries.py` staleness, not a new defect.
- `uv run ruff check src/agent_orchestrator/bench tests/bench/test_graders.py
  tests/bench/test_metrics.py` → clean.
- `uv run ruff format --check` on the same 4 files → clean (all pass, no reformat
  needed after an initial `ruff format` pass during development).
- `uv run mypy src/agent_orchestrator/bench` → **Success: no issues found in 7 source
  files**.
- No engine/schema files touched; no file outside this task's ownership list edited
  (verified by `git status` — only `graders.py`, `metrics.py`, the two new test files,
  and this ticket's `TASK.md`/`STATUS.md` are new/modified under this task's scope).

## Acceptance criteria verification
1. **AC1** (pytest: all pass → solved/1.0; 1-of-4 failing → not solved/0.75; 0 tests
   collected → not solved): `test_pytest_grader_all_pass`,
   `test_pytest_grader_partial_fail`, `test_pytest_grader_zero_tests_collected`
   (real `sys.executable -m pytest` subprocess runs on tmp_path mini-projects).
2. **AC2** (`pass_threshold=0.5` on score 0.75 → solved; `0.9` → not solved):
   `test_pytest_grader_pass_threshold_below_score_solves`,
   `test_pytest_grader_pass_threshold_above_score_fails`.
3. **AC3** (`command="true"`/`"false"`; timeout/missing → solved=False,score=0.0,
   detail recorded, no exception): `test_command_grader_true_solves`,
   `test_command_grader_false_does_not_solve`, `test_command_grader_timeout`,
   `test_command_grader_missing_binary`.
4. **AC4** (exists+contains mix → fraction; all pass → solved):
   `test_file_assertion_grader_mixed_exists_and_contains`,
   `test_file_assertion_grader_all_pass_solves`.
5. **AC5** (aggregate: one `cost_usd=None` excludes from sum + `cost_available:false`;
   `solved==0` → `cost_per_solved is None`): `test_aggregate_excludes_none_cost_and_flags_unavailable`,
   `test_aggregate_zero_solved_cost_per_solved_is_none_no_zerodivision`.
6. **AC6** (unknown grader type rejected via the real registry; mypy/ruff clean; no
   core edit): `test_grader_registry_has_all_mvp_types`,
   `test_unknown_grader_type_via_registry_lookup_raises_keyerror`; `spec.py`'s
   `KNOWN_GRADER_TYPES` (`{"pytest","command","file_assertion","fake"}`) now matches
   `GRADER_REGISTRY`'s keys exactly, so `load_suite`'s existing unknown-type rejection
   path (`T-Sc4Hm2`'s `test_load_suite_unknown_grader_type_rejected`) continues to
   guard the same real set of types.

## Risks / Blockers
- The `Assertion.golden` base-path gap (above) is the one open item that could affect
  `T-Run5Tz`: until resolved, `equals_file` assertions only work reliably with an
  absolute `golden` path in the suite spec, or a caller that `cd`s appropriately before
  invoking the runner.
- `test_registries.py::test_registries_start_empty` will also break the moment
  `T-Sbj9Ka` registers into `SUBJECT_REGISTRY` (independently of this task) — same fix
  needed either way.

## Next actions
1. Handed off to `T-Run5Tz-runner-metrics` (calls `grade(cfg, ctx)` +
   `build_task_metric`/`aggregate`) and `T-Rpt3Wq-results-comparison-report` (consumes
   `TaskMetric`/`Aggregate`) — both unblocked on this task's deliverables.
2. Arbitration needed on the two flagged gaps above (golden base-path threading;
   `test_registries_start_empty` staleness) before/alongside `T-Run5Tz`.
