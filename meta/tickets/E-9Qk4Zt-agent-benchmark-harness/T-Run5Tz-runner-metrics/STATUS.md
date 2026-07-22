# STATUS

- ID: `T-Run5Tz-runner-metrics`
- Updated At: 2026-07-22
- State: Done
- Owner: developer agent

## This update
- By: Claude · Role: developer · Date: 2026-07-22
- Comment: Implemented `run_suite(suite_path, subject_path, ...)` (design §4.5): loads
  + validates both specs, iterates `sorted(suite.tasks, key=id)` deterministically,
  materializes a fresh path-guarded workspace per task (`bench/workspace.py`, T-Sbj9Ka),
  dispatches `SUBJECT_REGISTRY[subject.type]`/`GRADER_REGISTRY[task.grader.type]`, and
  persists a `BenchRunRecord` (`run.json`) after **every** task via write-temp + atomic
  rename (mirrors `runstate.RunStateStore.save`) so a crash mid-run always leaves a
  valid, resumable file. Resume skips tasks already present in an existing `run.json`
  for the same `bench_run_id` unless `force=True`; a `task_filter` can restrict a call
  to a task subset without touching previously recorded ones. One task's `Subject.run`/
  `Grader.grade` failure — `BenchError` subclass OR a genuinely unexpected exception —
  is isolated to that task (`subject_status="error"`) and never aborts the run (the
  mandatory integration note from `T-Sbj9Ka`/`T-Grd7Vx`: a single try/except spans both
  calls). Structured `bench.run.start`/`bench.task.start`/`bench.task.end`/
  `bench.run.end` events (design §8) are emitted via the reused core `logging_setup`
  (`attach_run_handler`/`get_run_logger`/`detach_run_handler`, import-only) to a
  per-run log file under the gitignored workspace tree, never inside the committed
  results dir. `src/agent_orchestrator/` outside `bench/` is untouched (SI-1).

## Deviations from the design doc / TASK.md (recorded, not silent)
1. **`run_suite` takes spec PATHS, not pre-loaded models.** Design doc §4.5's
   pseudocode signature is `run_suite(suite, subject_spec, opts)` (already-loaded
   objects); the explicit assigning message's signature —
   `run_suite(suite_path, subject_path, *, out_dir=None, force=False,
   task_filter=None, budget_total=None, max_turns=None, default_timeout=None) ->
   BenchRunRecord` — is more specific and authoritative, and matches how `T-Cli8Nf`
   will actually invoke it from the CLI (`--suite <path> --subject <path>`).
   Implemented exactly that signature; `run_suite` itself calls `load_suite`/
   `load_subject` as its first step ("load+validate specs" per the assigning
   message). No separate `RunOptions` type was introduced — the TASK.md interface
   note's `RunOptions{force,budget_total,max_turns,default_timeout}` grouping is
   expressed as plain keyword-only parameters instead; semantically identical.
2. **`run_suite` returns the persisted `BenchRunRecord` directly, not a separate
   `RunResult` wrapper.** Design §4.5 pseudocode returns
   `RunResult(bench_run_id, result_dir, metrics, aggregate)`; the assigning message's
   explicit return annotation is `-> BenchRunRecord`. `BenchRunRecord` already carries
   `bench_run_id`/`tasks`/`aggregate`; `compute_bench_run_id`/`resolve_result_dir` are
   exposed as public functions so a caller (or `T-Cli8Nf`) can independently derive the
   result directory without a redundant field baked into the persisted JSON itself.
3. **This task's ownership includes writing `run.json` to disk; `summary.md`/
   comparison stay with `T-Rpt3Wq`.** `TASK.md`'s own "Handoff Boundary" section says
   "T-Rpt3Wq owns the `run.json`/`summary.md` writer bodies", which conflicts with the
   Description section's explicit "PERSIST the run record JSON to disk after EVERY
   task" and the assigning message's identical, more detailed instruction. Followed
   the more specific, explicit instruction (the resume/crash-resumable contract is
   meaningless unless `run.json` is written by this task): `runner.py` owns the
   `BenchRunRecord`/`BenchTaskRecord` pydantic models AND their atomic persistence.
   Design §4.5's pseudocode also calls `write_summary_md(...)` inline at the end of
   `run_suite`; that function does not exist yet (`results.py` is `T-Rpt3Wq`'s
   undelivered file), so `run_suite` does not call it — `T-Rpt3Wq` builds `summary.md`
   + `comparison.*` writers in `results.py` **on top of** the `run.json` shape defined
   here (e.g. via `BenchRunRecord.model_validate(json.loads(...))`), rather than this
   module reaching forward into a sibling file it does not own. Flagged explicitly for
   `T-Rpt3Wq`'s own kickoff.
4. **`BenchTaskRecord` (local subclass of `TaskMetric`) adds `raw_error` /
   `grader_detail` / `grader_raw_tail`.** The assigning message requires "per-task
   `subject_status`/`raw_error`/grader `detail`" in the persisted record;
   `TaskMetric` (`bench/metrics.py`, owned by `T-Grd7Vx`, outside this task's file
   scope) carries `subject_status` but not `raw_error`/grader `detail`/`raw_tail`.
   Rather than editing `metrics.py`, `runner.py` defines `class BenchTaskRecord
   (TaskMetric)` locally with the three additional fields — a flat, backward-inheriting
   pydantic subclass (all `TaskMetric` fields directly accessible, no nesting) so
   `T-Rpt3Wq`'s summary-table writer can iterate `record.tasks` without unwrapping
   anything. `BenchRunRecord.tasks: list[BenchTaskRecord]`.
5. **A run-level `config_fingerprint` was added alongside the existing per-task one.**
   TASK.md's own fingerprint formula (`sha256(canonical(task)+canonical(subject)+
   ao_version())`) is per-TASK and already flows straight into `BenchTaskRecord.
   config_fingerprint` via `build_task_metric` (T-Grd7Vx's existing field, AC6). The
   assigning message separately describes "config_fingerprint (stable hash over suite
   id/version + subject spec + relevant overrides + ao/claude versions)" — a
   suite-level description, distinct from the per-task one. Implemented both:
   `_task_config_fingerprint` (per task, feeds `BenchTaskRecord`) and
   `_run_config_fingerprint` (once per run, feeds the top-level
   `BenchRunRecord.config_fingerprint`) — sharing one `_sha256_of` helper. Only
   overrides that affect subject/grader BEHAVIOR (`budget_total`, `max_turns`,
   `default_timeout`) are hashed into the run-level fingerprint; `force`/`task_filter`
   are control-flow, not "config that produced these metric values".
6. **`_effective_timeout` adds `suite.defaults.timeout_seconds` as a middle tier.**
   Design §4.5's own ctx-construction line is `task.timeout_seconds or
   opts.default_timeout` — it never consumes `BenchSuiteDefaults.timeout_seconds`
   (`bench/spec.py`, T-Sc4Hm2), which would otherwise be a dead schema field. Effective
   precedence implemented: task's own explicit bound → run-level `default_timeout`
   override → suite's `defaults.timeout_seconds` → builtin `DEFAULT_TASK_TIMEOUT_
   SECONDS = 1800` last resort (matches the `1800` example in design §5.1). Covered by
   3 dedicated tests (one per tier).
7. **No-op resume writes nothing to disk (not merely "unchanged content").** AC2 says
   "run.json unchanged" on a fully-skipped resume; implemented literally — when zero
   tasks actually run in a call (every considered task already recorded, or an empty
   `task_filter` match), `run_suite` returns the existing/in-memory record WITHOUT
   opening `run.json` for writing at all (byte-identical file, unchanged mtime, zero
   redundant IO), rather than recomputing and rewriting an identical payload.
8. **A per-run structured-log file is written under the gitignored workspace tree
   (`playground/.tmp/bench/<bench_run_id>/bench.log`), not inside the committed
   `benchmarks/results/` dir.** Design §8 mandates the 4 structured events but does not
   specify a log-file location; §9's layout lists only `run.json`/`summary.md` as
   committed per result dir. Keeping the log file inside the gitignored workspace (like
   `capture/transcript.jsonl`) avoids polluting the committed tree with an
   observability artifact, while still reusing the exact core `attach_run_handler`/
   `get_run_logger`/`detach_run_handler` pattern (`engine.py`'s own `run_dir/run.log`
   precedent) as directed.

## Evidence
- Files added (this task's ownership only):
  - `src/agent_orchestrator/bench/runner.py` — `run_suite`, `BenchRunRecord`/
    `BenchTaskRecord`/`BenchRunSubjectInfo`/`BenchRunEnv` (the `run.json` shape),
    `compute_bench_run_id`/`resolve_result_dir` (public, for `T-Cli8Nf`), fingerprint
    + atomic-persist + best-effort env-probe helpers.
  - `tests/bench/test_runner.py` (17 tests) — `FakeSubject`/`FakeGrader` (or a small
    local subclass registered over the real registry entry via `monkeypatch.setitem`,
    auto-restored) throughout; the happy-path test additionally pairs `FakeSubject`
    with a REAL `PytestGrader` subprocess over a real tiny fixture (HLD §13's own
    strategy) to prove the full pipeline end to end, not just mocked plumbing.
- `uv run pytest tests/bench -q` → **133 passed, 1 skipped** (was 116 passed/1 skipped
  before this task; +17, the `real_llm` skip is pre-existing and untouched).
- `uv run pytest -q -m "not real_llm"` (whole repo) → **990 passed, 4 deselected**
  (baseline quoted for this task was 973 passed/4 deselected; +17, zero regressions).
- `uv run ruff check .` → clean except the 2 pre-existing, untouched
  `tests/test_e2e_cli.py` errors (named in the assigning message; confirmed unrelated
  to this task's files).
- `uv run ruff format --check .` → 92 files already formatted.
- `uv run mypy src/agent_orchestrator/bench` → **Success: no issues found in 10 source
  files** (includes `runner.py`, the 9 already-landed files unchanged). One Protocol-
  variance mypy note: `SubjectResult.status` is a `Literal[...]` (`subjects.py`) while
  `metrics.SubjectResultLike.status: str` — a true subtype, but mypy's structural
  Protocol attribute matching is invariant for plain attributes, so `build_task_metric`
  is called with an explicit `cast(SubjectResultLike, sr)` at the one call site
  (documented inline; a mypy-only annotation, no runtime effect, both files behind the
  cast are outside this task's ownership).
- `uv run mypy src` → same 4 pre-existing, untouched `_version.py` errors as the last
  task's baseline — nothing new from `runner.py`.
- Live smoke (outside the test suite, not committed): ran `run_suite` directly against
  a hand-built suite/subject pair — confirmed the real, un-mocked `run.json` shape
  (below), that `bench.run.start/task.start/task.end/run.end` all fire with the exact
  §8 field sets, and that the best-effort env probes resolve for real in this
  environment (`ao_version: "ao 0.1.0 (0e4ca7d.dirty) built ..."`,
  `claude_version: "2.1.217 (Claude Code)"`, real `git_sha`). Scratch workspace
  deleted afterward (not a real test artifact).

## Acceptance criteria verification
1. **AC1** (3-task fake suite, id-sorted, `run.json` lists 3 metrics, `bench.task.end`
   logged with solved/cost/wall): `test_run_suite_happy_path_three_tasks_sorted_order`
   (real `PytestGrader`) + `test_run_suite_executes_tasks_in_sorted_id_order`
   (execution-order proof, decoupled from the always-sorted output list).
2. **AC2** (re-run → all skipped, `run.json` unchanged; `force=True` → all re-run):
   `test_run_suite_resume_skips_completed_then_force_reruns` (a class-level call
   counter proves zero/3/3 additional invocations across the three calls; asserts the
   on-disk file is byte-identical after the no-op resume).
3. **AC3** (interrupt after task 2 (simulated via `task_filter`) → `run.json` holds 2
   valid metrics; resume runs only task 3): `test_run_suite_task_filter_then_resume_
   runs_remaining`. Per-task persist itself (not batched) additionally proven by
   `test_run_suite_persists_after_each_task_not_batched` — a spy `Subject` reads
   `run.json` from disk mid-run and asserts task 1 is already durably present before
   task 2 even starts.
4. **AC4** (a task whose subject raises → `subject_status="error"`, remaining tasks
   still run): `test_run_suite_subject_bencherror_isolated_run_continues` (raised
   `SubjectError`) + `test_run_suite_subject_unexpected_exception_isolated_run_
   continues` (a plain `ValueError`, per the mandatory "also catch unexpected
   exceptions" note) + `test_run_suite_grader_error_isolated_run_continues` (raised
   `GraderError`, proving the shared try/except spans both calls).
5. **AC5** (a workspace path escaping `playground/.tmp/bench` → rejected, task not run
   out-of-sandbox): the guard itself lives in and is unit-tested by `T-Sbj9Ka`'s
   `bench/workspace.py`/`test_workspace.py` (ids are schema-pattern-validated, so a
   composed `ws_root/subject.id/task.id` path can never actually escape at the runner
   layer — see Risks below). This task adds the complementary POSITIVE proof requested
   ("path-guard (workspace stays under BENCH_WORKSPACE_ROOT)"):
   `test_run_suite_workspaces_stay_under_bench_workspace_root` asserts every recorded
   task workspace resolves under `BENCH_WORKSPACE_ROOT` after a real run.
6. **AC6** (`config_fingerprint` stable for identical inputs, changes with any):
   `test_task_config_fingerprint_stable_and_sensitive_to_changes` (per-task) +
   `test_run_config_fingerprint_stable_and_sensitive_to_changes` (run-level, deviation
   #5 above) — each asserts equality across two identical calls and inequality when
   task/subject/suite/override/version inputs change one at a time.
7. **AC7** (committed fixtures never mutated, only `ws/repo` copies; workspaces persist,
   no auto-delete; `mypy`/`ruff` clean; no core edit): fixture-immutability +
   workspace-persistence assertions folded into
   `test_run_suite_happy_path_three_tasks_sorted_order`; `mypy`/`ruff` — see Evidence;
   no core edit — see SI-1 note below.

## Risks / Blockers
- None blocking handoff. AC5's *negative* case (an actual escape attempt) is
  structurally unreachable through `run_suite` today: `bench_run_id`/`subject.id`/
  `task.id` are all schema-pattern-validated (`^[a-z0-9][a-z0-9-_]*$`, no `.`/`/`)
  before a `BenchSuite`/`SubjectSpec` can even be constructed, so the composed
  workspace path the runner builds can never itself contain a traversal — the guard
  that matters is exercised at `materialize_workspace`'s own layer (`T-Sbj9Ka`,
  already tested there). If a future subject/grader type ever derives a path from
  less-trusted input, re-verify this assumption.
- `T-Rpt3Wq` needs to read `run.json` back via `BenchRunRecord.model_validate(...)`
  (or independently reconstruct the same field set) rather than assuming design §4.6's
  literal pseudocode shape — see deviations #2–#4 above for the actual persisted shape.
- `T-Cli8Nf` should call `runner.compute_bench_run_id`/`runner.resolve_result_dir`
  (public, exposed for exactly this reuse) rather than re-deriving the `bench_run_id`
  format itself, to stay in sync if the date format ever changes.

## Next actions
1. Handed off to `T-Rpt3Wq-results-comparison-report` (writes `summary.md` +
   `comparison.{json,md}` in `results.py`, reading the `run.json` shape defined here —
   see deviations #2–#4 for the exact field list) and `T-Cli8Nf-bench-cli-make-
   entrypoints` (wires `ao-bench run` to `run_suite`, using the public
   `compute_bench_run_id`/`resolve_result_dir` helpers).
