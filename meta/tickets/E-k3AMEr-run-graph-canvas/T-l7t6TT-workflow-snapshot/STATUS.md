# STATUS

- ID: `T-l7t6TT-workflow-snapshot`
- Updated At: `2026-09-27`
- State: `Done`
- Owner: `Dev A (developer)`
- Scope: `MVP` · Sprint: `S1` · Estimate: `12 h`

## This update
- By: architect · Role: architect · Date: 2026-09-27 · Comment: Task created as part of the
  E-k3AMEr design package (design + tickets only; **no code written**). Acceptance criteria,
  pseudocode, and interfaces are in `TASK.md`, and the authoritative design is
  `docs-md/run-graph-canvas-hld.md`. Hardened by the Phase-4 consultations (HLD §23.1).
- By: developer · Role: developer · Date: 2026-09-27 · Comment: Implemented to all 11 acceptance
  criteria (see Evidence). `models.py` gains `SpecSession`/`WorkflowSnapshot` + `RunState.
  spec_sessions`; `runstate.py` gains the named constants, `canonical_spec_json`,
  `load_workflow_snapshot_at` (module-level, shared parser/size-cap), and
  `RunStateStore.record_spec_session` / `.load_workflow_snapshot` (the latter delegates to the
  module-level helper, AC-10). `engine.py`'s `Orchestrator.run` gains the single insertion point
  specified in `TASK.md` -- immediately after `state = run_state or self._runstate.new_run
  (workflow)` and before the run's first `self._runstate.save(state)` -- wrapping
  `record_spec_session` in `try/except OSError` so a snapshot-write failure never fails the run.
  Added `tests/test_workflow_snapshot.py` (19 tests, all new). No other epic-task's scope touched:
  `dag.py`, `ui/`, and `_inject`/its call sites are untouched.

## Evidence
- `pytest -q` (full suite): **4474 passed, 8 skipped, 0 failed** (before: 4455 passed / 8 skipped
  on `main`+epic-design commit, i.e. the 19 new tests in `tests/test_workflow_snapshot.py` are the
  only delta; 0 regressions). One flaky, timing-based failure was observed on a single full-suite
  run in `tests/test_wave_scheduler.py::TestParallelDispatchProof::
  test_two_independent_tasks_overlap_at_max_parallel_two` (a `threading`/gate-release race,
  unrelated to this change's files); it passed 3/3 in isolation and the very next full-suite rerun
  was clean (4474 passed / 8 skipped), confirming it is a pre-existing flake, not a regression.
- `ruff check src/agent_orchestrator/models.py src/agent_orchestrator/runstate.py
  src/agent_orchestrator/engine.py tests/test_workflow_snapshot.py` -> **All checks passed!**
  (`ruff check .` on the whole repo shows exactly one pre-existing, out-of-scope finding in
  `output/E-YAAGhk-overseer-runner-template/repro_emit_lost_on_breaker_trip.py`, committed at
  `191da69`, before this task -- confirmed via `git log` on that file.)
- `ruff format --check` on the same 4 files -> **4 files already formatted**. (`ruff format
  --check .` on the whole repo shows the same one pre-existing file as the only reformat
  candidate.)
- `mypy src/agent_orchestrator/models.py src/agent_orchestrator/runstate.py
  src/agent_orchestrator/engine.py` -> **Success: no issues found in 3 source files**. (`mypy src`
  on the whole repo shows 4 pre-existing errors in `src/agent_orchestrator/_version.py`, unrelated
  to this task -- confirmed identical on both HEAD and a temporarily-stashed pre-change baseline
  via `git stash`/`git stash apply` in this worktree.)
- Acceptance criteria, each backed by a passing test in `tests/test_workflow_snapshot.py`:
  - AC-1 (models/constants): `TestModelsAndConstants` (4 tests) -- `SpecSession`,
    `WorkflowSnapshot`, `RunState.spec_sessions` default `[]`, and all 5 named constants
    (`WORKFLOW_SNAPSHOT_PREFIX`/`SUFFIX`/`SHA_CHARS`/`SCHEMA_VERSION`/`MAX_BYTES`).
  - AC-2 (canonical sha determinism): `TestCanonicalSpecJson::
    test_stable_across_dict_key_insertion_order` -- two specs differing only in the `hooks` dict's
    insertion order hash identically; `test_semantically_different_specs_hash_differently` as a
    sanity counter-check.
  - AC-3 (fresh run): `TestFreshRunViaCli::
    test_fresh_run_writes_one_session_and_one_matching_snapshot` -- real `ao run` via `CliRunner`
    + fake executor; asserts `session==1`, exactly one `workflow.snapshot.<sha12>.json`, its
    `spec_sha256` matches the session, and `workflow.tasks` ids match spec order.
  - AC-4 (injected excluded, stable on resume):
    `TestInjectedTasksExcludedAndStableAcrossResume::
    test_injected_ids_excluded_and_sha_unchanged_on_resume` -- an `emit_tasks` workflow injects
    `d`/`e`; resumed session's snapshot `workflow.tasks == ["emitter"]` (no `d`/`e`) and its sha
    equals session 1's (no new file).
  - AC-5 (resume, unchanged spec): `TestResumeUnchangedSpec::
    test_two_sessions_same_sha_one_file_no_rewrite` -- 2 sessions, same sha, exactly 1 file,
    `st_mtime_ns` and raw bytes unchanged across resume.
  - AC-6 (resume, changed spec): `TestResumeChangedSpec::
    test_changed_spec_writes_second_file_keeps_first_and_warns` -- differing `timeout_seconds`
    changes the sha; 2 files exist, the first is byte-identical to before, and exactly one
    `run.spec_changed_on_resume` warning is asserted via `caplog`.
  - AC-7 (tolerant load): `TestLoadWorkflowSnapshotAtTolerant` (6 tests) -- missing file; oversized
    (monkeypatched `WORKFLOW_SNAPSHOT_MAX_BYTES` + intentionally-invalid body content, asserting
    the "exceeds cap" warning fires and the "unreadable or invalid" one does NOT, proving the
    `stat()` check runs before any parse attempt); invalid JSON; schema-invalid body; sha
    mismatch; plus a positive "valid snapshot loads" control.
  - AC-8 (failure never fails run): `TestSnapshotWriteFailureNeverFailsRun::
    test_oserror_on_snapshot_write_logs_warning_and_run_still_succeeds` -- monkeypatches the new
    `RunStateStore._write_snapshot_atomic` helper to raise `OSError`; run still reaches
    `succeeded`, `spec_sessions` still has 1 entry, no snapshot file is written, `state.json` is
    still written (untouched by the narrow monkeypatch), and exactly one `run.snapshot_failed`
    warning is logged.
  - AC-9 (backward compat): `TestBackwardCompatibility::
    test_pre_epic_state_json_without_spec_sessions_key_still_loads` -- deletes the
    `spec_sessions` key from a saved `state.json` and reloads it; `spec_sessions == []`.
  - AC-10 (shared helper, DRY): `TestLoadWorkflowSnapshotDelegatesToSharedHelper::
    test_delegates_to_module_level_load_workflow_snapshot_at` -- monkeypatches the module-level
    `runstate.load_workflow_snapshot_at` and asserts `RunStateStore.load_workflow_snapshot` calls
    it with `(run_dir, sha)` and returns its result verbatim.
  - AC-11 (pytest/ruff/mypy clean, no regression): see the command outputs above.
- File:line anchors: `src/agent_orchestrator/models.py` (`SpecSession`/`WorkflowSnapshot` classes
  immediately before `TaskRunState`; `RunState.spec_sessions` field appended after
  `task_integration`); `src/agent_orchestrator/runstate.py` (constants + `canonical_spec_json` +
  `_workflow_snapshot_filename` + `load_workflow_snapshot_at` near the top of the module;
  `RunStateStore.record_spec_session` / `._write_snapshot_atomic` / `.load_workflow_snapshot`
  between `save()` and `load()`); `src/agent_orchestrator/engine.py` (`Orchestrator.run`, the
  `try/except OSError` block inserted between `state = run_state or self._runstate.new_run
  (workflow)` and `self._runstate.save(state)`).

## Risks / Blockers
- Blockers: none. Task is complete.
- Dependencies: none (as designed). Gate G1 is now open for `T-M4qboy` (run-graph builder,
  consumes the snapshot as its input) and `T-AsQ77e` (dashboard endpoint, reuses
  `load_workflow_snapshot_at` via `RunStateStore.load_workflow_snapshot`).
- Residual risk carried forward per `TASK.md`'s own Risks section: one extra file per distinct
  static spec per run (size of the spec file) -- accepted, retention follows the run dir.

## Next actions
1. None for this task. Downstream tasks `T-M4qboy` and `T-AsQ77e` can now build on
   `RunState.spec_sessions` / `RunStateStore.load_workflow_snapshot` / `runstate.
   load_workflow_snapshot_at`.
2. Epic-level `EPIC.md`/top-level `STATUS.md` rollup is owned by the epic coordinator, not this
   task (per instruction) -- not updated here.
