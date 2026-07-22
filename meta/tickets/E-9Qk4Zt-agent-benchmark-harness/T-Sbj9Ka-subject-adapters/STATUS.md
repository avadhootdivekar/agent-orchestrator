# STATUS

- ID: `T-Sbj9Ka-subject-adapters`
- Updated At: 2026-07-22
- State: Done
- Owner: developer agent

## This update
- By: Claude · Role: developer · Date: 2026-07-22
- Comment: Implemented the `Subject` ABC + `SubjectResult` + the three MVP subjects
  (`ClaudeCliSubject`, `AoWorkflowSubject`, `FakeSubject`), all self-registering into
  `SUBJECT_REGISTRY` at import time, plus the path-guarded workspace materialization
  helper (`bench/workspace.py`, also home to the new `RunContext` model). Real
  subjects reuse core's `parse_usage_and_429`/`parse_transcript_events`
  (claude_cli) and `compute_run_usage_totals`/`RunStateStore` (ao_workflow) exactly
  as directed (C4) — verified with one live haiku `claude -p` run (see below).
  `src/agent_orchestrator/` outside `bench/` is byte-unchanged (SI-1; grep-verified).

## Deviations from the design doc (recorded, not silent)
1. **`AO_BUDGET_TOTAL` env var does not exist in core.** Design doc §4.2 pseudocode
   has `AoWorkflowSubject` set an `AO_BUDGET_TOTAL` env var. Core `cli.py`'s `run`/
   `resume` commands read budget **only** via the `--budget-total` CLI flag
   (`_resolve_run_settings` wires env vars for `AO_MAX_ATTEMPTS`/`AO_MAX_TURNS`/
   `AO_MODEL`/`AO_EFFORT`/`AO_MAX_PARALLEL`/quota knobs — budget is not among them).
   `AoWorkflowSubject` passes `--budget-total <n>` as an argv flag instead; a code
   comment at the call site cross-references this. `ctx.budget_total` (bench-run-level
   override) wins over `spec.budget_total` (subject default), mirroring core's own
   `_build_effective_budget` "CLI > spec > unset" precedence — same layering applied
   to `AO_MAX_TURNS`/`ctx.max_turns` vs `spec.max_turns` for both real subjects.
2. **`INSTRUCTION.md` is written by `workspace.materialize_workspace`, not by
   `AoWorkflowSubject.run`.** Design doc §4.2's `AoWorkflowSubject` pseudocode writes
   `task.instruction` into `ctx.repo_dir/INSTRUCTION.md` itself; this task's own
   delegation message explicitly scoped "write INSTRUCTION.md" into the workspace
   materialization helper instead (my `TASK.md`, "Files you own" list), which is more
   general — `ClaudeCliSubject` benefits too (its rendered prompt references
   `ctx.instruction_path`, which now always points at a real on-disk copy inside the
   workspace, not just the suite's original path). Followed the more specific,
   explicit instruction.
3. **`RunContext.subject_base_dir` (additive field, not in design doc §6's listing).**
   `subject.schema.json` documents `workflow`/`reposets`/`agents` as paths "relative to
   this subject.json", but `bench/spec.py`'s `load_subject` (T-Sc4Hm2, not owned by
   this task) does not resolve them against the subject.json's directory — only
   `BenchTask` paths get that treatment in `load_suite`. Added an optional,
   defaulted `RunContext.subject_base_dir` field that `AoWorkflowSubject` resolves
   `spec.workflow`/`.reposets`/`.agents` against when set (falls back to CWD
   otherwise; an already-absolute path in the subject.json works either way with no
   runner change needed). **Needs T-Run5Tz (the runner) to populate
   `ctx.subject_base_dir = Path(subject_json_path).parent`** when constructing
   `RunContext` for an `ao_workflow` subject — flagged for the orchestrator to
   route to that task; nothing else needs it (a `claude_cli`/`fake` subject ignores
   the field entirely).
4. **`SubjectResult` gained `argv`/`resolved_model`/`resolved_permission_mode`**
   (additive beyond design doc §6's field list) per this task's own delegation
   message: "record the exact argv + model + permission mode into the result (for
   `config_fingerprint` later)". Populated by all three subjects (`FakeSubject`:
   `argv=[]`, no real invocation).
5. **`FakeSubject`'s "configurable status" (delegation message) implemented via the
   *existing* `scripted_effect` field** — `"fail"`/`"timeout"`/`"error"` map directly
   to that `SubjectResult.status`, alongside the design doc's `"copy-solution"`/
   `"noop"` (which mutate the repo but always report `"succeeded"`, exactly as
   pseudocoded). No schema/model change (SI-1): `subject.schema.json` and
   `SubjectSpec.scripted_effect` are both untouched, still just `type: string`.
   `"copy-solution"` overlay-copies a `.bench-solution/` marker directory that ships
   **inside** the task fixture (so it rides the normal `copytree`) onto the repo, then
   deletes the marker — a new, self-contained bench-test convention (not part of any
   committed schema), documented in `FakeSubject`'s docstring for `T-Run5Tz`/
   `T-Tst4Ln`'s integration-test fixtures to follow.

## Cross-task item needing arbitration (not fixed — file not owned by this task)
`tests/bench/test_registries.py::test_registries_start_empty` (as read at the start of
this task) asserted `SUBJECT_REGISTRY == {}` — true only before this task's
`register_subject` calls land. **This was already resolved by the time this task
finished**: the concurrent `T-Grd7Vx` agent replaced that assertion with
`test_grader_registry_populated_on_import` (a superset-membership check after
importing `bench.graders`), and no test in the current `test_registries.py` asserts
either registry is empty. Verified by running the full suite (see Evidence) — no
failure related to registry population. Recorded here only because the conflict was
real at read-time and is exactly the kind of cross-task collision the assigning
message asked to be reported; no action was needed from this task since the other
agent's edit (to a file this task does not own) already superseded it.

## Evidence
- Files added (this task's ownership only):
  - `src/agent_orchestrator/bench/subjects.py` — `Subject` ABC, `SubjectResult`,
    `ClaudeCliSubject`, `AoWorkflowSubject`, `FakeSubject`, registered into
    `SUBJECT_REGISTRY` at import time.
  - `src/agent_orchestrator/bench/workspace.py` — `RunContext`, `BENCH_WORKSPACE_ROOT`,
    `materialize_workspace` (path-guarded, AC6).
  - `tests/bench/test_subjects.py` (38 tests, incl. 1 `real_llm`), `tests/bench/test_workspace.py`
    (13 tests) — unit tests with `FakeSubject` + subprocess-mocked
    `ClaudeCliSubject`/`AoWorkflowSubject` (monkeypatching the shared
    `subjects._run_with_timeout` seam), a real (non-`claude`, non-network) `python -c
    "time.sleep(...)"` process-group-kill test for that seam, and the one
    `@pytest.mark.real_llm` end-to-end `ClaudeCliSubject` haiku test.
- `uv run pytest tests/bench -q` → **116 passed, 1 skipped** (the `real_llm` test
  self-skips without `AO_E2E_REAL_LLM=1`, via a local guard inside the test itself —
  `tests/bench/conftest.py` is not owned by this task, so no collection-hook was
  added there; mirrors `tests/playground/harness.py`'s `requires_claude()` pattern).
- `uv run pytest -q -m "not real_llm"` → **973 passed, 4 deselected** (whole repo).
  Baseline quoted for this task was 905 passed; the concurrent `T-Grd7Vx` task also
  landed in this window (its own tests account for part of the delta). This task's
  own two test files contribute **37 passed** (+1 `real_llm`, run separately below) —
  confirmed by `uv run pytest tests/bench/test_subjects.py tests/bench/test_workspace.py
  -q -m "not real_llm"`. Non-bench suite re-run in isolation
  (`--ignore=tests/bench`) → **857 passed, 3 deselected, zero failures** — confirms
  SI-1 (core engine/CLI test suite is unaffected by this task).
- `AO_E2E_REAL_LLM=1 uv run pytest tests/bench/test_subjects.py::test_claude_cli_subject_real_haiku_smoke -q -m real_llm`
  → **1 passed** (6.9s). Inspected the produced `capture/transcript.jsonl`: a real
  `claude -p --model haiku` run, `total_cost_usd: 0.0260162`, `usage.input_tokens: 10`,
  `usage.output_tokens: 63`, `num_turns: 1` — confirms the reused core parser
  (`parse_usage_and_429`) genuinely extracts real cost/token actuals through the bench
  subject, not just a mocked path. Workspace preserved under
  `playground/.tmp/bench/tests/ws-556481c6b2e7/` (gitignored) for audit, per the
  repo's own "workspaces preserved" convention.
- `uv run ruff check .` → clean except the 2 pre-existing `tests/test_e2e_cli.py`
  errors (confirmed present before this task's changes, unrelated file — untouched).
- `uv run ruff format --check .` → all files formatted (90 files).
- `uv run mypy src/agent_orchestrator/bench` → **Success: no issues found in 9 source
  files** (includes this task's 2 files + the 7 files already landed by
  `T-Sc4Hm2`/`T-Grd7Vx`).
- SI-1 grep verification: `grep -rn "import" src/agent_orchestrator/*.py
  src/agent_orchestrator/executors/*.py | grep -F bench` → no matches (core does not
  import `bench/`); `bench/subjects.py` imports read-only from
  `..artifacts`/`..executors.claude_cli`/`..models`/`..runstate` only.

## Acceptance criteria verification
1. **AC1** (`FakeSubject` copy-solution → succeeded + solution present; noop →
   unchanged; deterministic, no network): `test_fake_subject_copy_solution_applies_fix_and_removes_marker`,
   `test_fake_subject_noop_leaves_workspace_unchanged`, `test_fake_subject_default_effect_is_noop`,
   `test_fake_subject_no_sleeping`.
2. **AC2** (`claude`/`ao` absent from PATH → `status="error"`, not an unhandled
   exception): `test_claude_cli_subject_missing_binary_returns_error_not_exception`,
   `test_ao_workflow_subject_uv_missing_returns_error_not_exception`.
3. **AC3** `[real_llm]` (haiku `ClaudeCliSubject` → `cost_usd`/tokens populated from
   the reused core parser, `capture/transcript.jsonl` exists):
   `test_claude_cli_subject_real_haiku_smoke` — run live, see Evidence above.
4. **AC4** `[real_llm]` (`AoWorkflowSubject` → exactly one run dir; cost/tokens via
   `compute_run_usage_totals`): not run live this task (a real `ao_workflow` subject
   needs a committed `workflow.json`/`reposet.json`/`agents.json` template, which is
   `T-Fx6Dp0`'s deliverable and does not exist yet — the delegation message capped
   real invocations at "at most ONE real haiku `claude -p`", i.e. AC3, and directed
   "everything else: mocked"). Fully covered deterministically instead:
   `test_ao_workflow_subject_success_computes_usage_from_state` (writes a real
   `state.json` via `RunStateStore`, asserts `compute_run_usage_totals` reuse end to
   end) + `test_ao_workflow_subject_zero_run_dirs_raises_subject_error` /
   `test_ao_workflow_subject_multiple_run_dirs_raises_subject_error` (the
   exactly-one-run-dir invariant, both directions).
5. **AC5** (timeout → `status="timed_out"`, partial capture retained;
   `claude_quota_exhausted` → `status="error"` with a quota reason, not a solve):
   `test_claude_cli_subject_timeout_returns_timed_out_with_partial_capture`,
   `test_claude_cli_subject_quota_exhausted_returns_error_not_solve`,
   `test_run_with_timeout_kills_process_group_on_timeout` (real kill mechanics, no
   `claude`/`ao`), `test_ao_workflow_subject_timeout_still_grades_partial_run`.
6. **AC6** (workspace materialization refuses a `ws` path that escapes
   `playground/.tmp/bench/` via `..`/symlink with `SubjectError`; committed fixture
   never mutated): `test_materialize_workspace_rejects_dotdot_traversal`,
   `test_materialize_workspace_rejects_symlink_escape`,
   `test_materialize_workspace_never_mutates_committed_fixture`,
   `test_materialize_workspace_fresh_copy_removes_stale_files`.
7. **AC7** (`bench/` imports from core, core never imports `bench/`; `mypy`/`ruff`
   clean on `bench/`): see SI-1 grep verification + Evidence above.

## Risks / Blockers
- None blocking handoff. `T-Run5Tz` (runner) needs to: (a) set
  `RunContext.subject_base_dir` for `ao_workflow` subjects (deviation #3 above), (b)
  catch `SubjectError` around `Subject.run()` per the design doc §4.5 pseudocode (this
  task's `AoWorkflowSubject`/`FakeSubject` both raise it for a hard, non-gradeable
  condition — zero/multiple run dirs, or a totally unrecognized `scripted_effect`).
- `T-Fx6Dp0` (MVP fixtures + the `ao-epic` workflow/reposet/agents templates) is the
  dependency that unblocks a **live** AC4 run and a live `AoWorkflowSubject` smoke
  test; today AC4 is proven via mocked-subprocess + real `RunStateStore` I/O only (see
  AC4 above) — recommend a live ao_workflow haiku smoke once those templates land.
- `T-Tst4Ln`/`T-Run5Tz` integration fixtures that want `FakeSubject`'s
  `"copy-solution"` effect must ship a `.bench-solution/` directory inside the task
  fixture (new bench-local convention, not schema-enforced — see deviation #5).

## Next actions
1. Handed off to `T-Run5Tz-runner-metrics` (drives `Subject.run(task, ctx)` via
   `SUBJECT_REGISTRY`, constructs `RunContext` including `subject_base_dir` for
   `ao_workflow` subjects, catches `SubjectError`) and `T-Fx6Dp0-mvp-dev-suite-fixtures`
   (authors the `ao-epic` workflow/reposet/agents templates + committed `subject.json`s,
   and any `.bench-solution/` fixtures for `FakeSubject`-driven integration tests).
