# STATUS

- ID: `T-6gR2ya-review-and-hardening`
- Updated At: 2026-09-21
- State: Done
- Owner: reviewer (delegated by dev-epic), fixes applied by dev-epic

## This update
- By: dev-epic
- Role: manager
- Date: 2026-09-21
- Comment: Ticket drafted.

## Evidence
- (pending)

## Risks / Blockers
- None yet.

## Next actions
1. Run after T-jI3P4p is green.

## Critical review pass — findings
- By: reviewer
- Role: reviewer
- Date: 2026-09-21
- Comment: Reviewed the full diff `02043ec..HEAD` (models.py, hooks.py [new],
  engine.py, spec.py, isolation/escalation.py, specs/workflow.schema.json,
  specs/examples/*, and all 4 new test files) against HLD Rev 2 and this
  ticket's 6 ACs. Read every touched hunk directly (not summaries); ran the
  full specified pytest subset myself.

  **AC-1 (byte-identical hookless behavior) — PASS.** `tests/test_engine.py`
  is untouched by the diff (`git diff --stat` confirms) and all 26 of its
  tests pass unmodified. Read the two no-op gates directly in `engine.py`:
  the pre-hook check is `if task.pre_hook is not None:` before the attempt
  loop, and `_finalize_with_post_hook`'s first statement is
  `if task.post_hook is None: return result` — zero dict/dir/subprocess/
  `WorkflowSpec.hooks` work for a hookless task, matching HLD §5's "true
  no-op" requirement.

  **AC-2 (early-return wiring matches HLD §6) — PASS.** Verified directly in
  `engine.py::_run_with_retries`: the pre-hook gate runs once before the
  `for attempt in range(...)` loop; `_finalize_with_post_hook` wraps only the
  `"succeeded"` return (`return _finalize_with_post_hook(result)`) and the
  final exhausted-attempts return (`return _finalize_with_post_hook(last_result)`).
  The `cancelled` return attaches `pre_hook_result=pre_hook_result` but does
  NOT call the post-hook finalizer; the `claude_quota_exhausted` early return
  is likewise untouched by the finalizer. Matches HLD §6's table exactly.

  **AC-3 (`run_hook`/`_run_hook_inner` never raises) — PASS.** Read
  `hooks.py` in full: `subprocess.TimeoutExpired` and `OSError` are caught
  explicitly inside `_run_hook_inner` and degrade to `HookOutcome(status=
  "timed_out"/"error", ...)`; `_write_capture`'s own `OSError` (disk-full,
  permission) is caught and logged, never propagated; `_populate_result_file`
  catches `ControlFileError` from a malformed result file and folds it into
  `HookOutcome.error` without ever raising. `run_hook`'s outer
  `except Exception` is a last-resort guard (e.g. a non-JSON-serializable
  `context_fields` value) that also degrades to a `HookOutcome`. No path
  reaches the worker thread's `ThreadPoolExecutor` future as an exception.

  **AC-4 (NFR-1 paths-only) — PASS.** `engine.py`'s `_hook_context_fields`
  closure builds `context.json` entirely from already-resolved absolute
  paths (`instruction_path`, `input_paths`, `output_paths`,
  `dynamic_input_paths`, `repo_paths`, `output_dir`) plus terminal
  `TaskResult` metadata (`status`/`attempts`/`exit_code`/`error`/
  `output_artifact_path`) — never file content. The optional result-file
  read goes exclusively through the existing bounded `artifacts.read_control`
  (unchanged, reused). The example `specs/examples/hooks/grade.py` only
  calls `os.path.exists(p)` on `context["output_paths"]`, never opens/reads
  those files — consistent with the NFR-1 contract it's demonstrating.

  **T2/T3 suppression fix — PASS.** `isolation/escalation.py::
  build_resolver_dispatch`'s `task.model_copy(update={...})` now includes
  `"pre_hook": None, "post_hook": None`; `build_rerun_task` is untouched
  (confirmed via diff — zero lines changed in that function). Both directly
  unit-tested in `test_engine_hooks_resolver.py` against the real
  `build_resolver_dispatch`/`build_rerun_task` functions (not reimplemented
  fakes) — accepted as sufficient per dev-epic's documented scoping call.

  **AC-5 (magic literals / duplication / docstrings) — PASS, nothing to
  flag beyond what HLD §5 already discloses.** All new constants
  (`HOOK_CAPTURE_CAP_BYTES`, `HOOK_ERROR_STDERR_TAIL_CHARS`,
  `DEFAULT_HOOK_TIMEOUT_SECONDS`, etc.) are named, not inlined. The 5th
  bounded-subprocess-implementation DRY trade-off is the one already
  recorded in HLD §5/§9 as deliberate — not re-flagged. All new public
  classes/functions in `models.py`/`hooks.py` are docstringed. Minor,
  non-blocking: the three new private closures in `engine.py`
  (`_hook_context_fields`, `_dispatch_hook`, `_finalize_with_post_hook`) —
  only the last has a docstring; the other two rely on adjacent block
  comments instead. Not required (private, local, already explained inline)
  but would read slightly cleaner with one-line docstrings each.

  **BLOCKING — test-suite gap, not a production defect.**
  `tests/test_hooks.py::TestHookNoOpWhenNone` (lines 536-551,
  `test_prehook_none_means_no_subprocess` / `test_posthook_none_means_no_
  subprocess`) is the test suite's only attempt at HLD §5's explicitly
  promised proof: *"True no-op path (locked-in requirement)... Proven by a
  Mock-patched `hooks.run_hook` call-count assertion (§8)."* Both tests only
  do `with mock.patch("agent_orchestrator.hooks.run_hook") as mock_run:
  assert mock_run is not None` — no orchestrator code is invoked inside the
  `with` block at all, so the assertion is trivially true unconditionally
  (a `mock.patch` context manager never yields `None`). This is the exact
  "test doesn't exercise the code path it claims" pattern this ticket's
  brief specifically asked to scrutinize for (the same category as the 4
  bugs dev-epic already found and fixed in this same suite) — and it is
  the *only* place in all 4 new test files that patches `hooks.run_hook`
  directly with a call-count-shaped assertion, so the locked-in no-op
  requirement currently has no real automated proof anywhere in the suite.
  I independently verified the underlying production code IS correct by
  direct reading (see AC-1 above) — this is not a runtime risk — but the
  test suite does not prove it, contrary to what `TestHookNoOpWhenNone`'s
  own class docstring claims ("AC-5: When a task declares no hooks,
  run_hook is never called"). dev-epic's own STATUS.md entry for T-jI3P4p
  states this file's 50 tests were independently re-run and "genuine, no
  issues found there" — that check evidently did not extend to inspecting
  what each assertion actually proves, only that they pass.
  **Fix**: replace both tests with one that patches
  `agent_orchestrator.hooks.run_hook`, drives a hookless task through
  `Orchestrator.run()` (or calls `_run_with_retries` directly) with a
  `FakeExecutor`, and asserts `mock_run.assert_not_called()`.

  **NON-BLOCKING — weak AC-11 self-heal assertion.**
  `tests/test_engine_hooks.py::TestPostHookSelfHealInteraction::
  test_hook_downgrade_picked_up_by_self_heal` (lines 415-467): the
  docstring states "This test documents that self-heal is INVOKED when a
  hook downgrades, not whether it succeeds," but the only assertion is
  `assert state.tasks["t1"].post_hook_result is not None` — that would
  pass identically even if self-heal were completely disabled/never
  triggered, since `post_hook_result` is set on the very first dispatch
  regardless of any retry. It does not assert on `attempts > 1`, a monitor
  decision record, or any other signal that self-heal actually fired.
  Companion test `test_hook_downgrade_error_summary_nonempty` right below
  it is fine (asserts `exit_code`/`status`/final task `status` concretely).
  **Fix (non-blocking, can land alongside the BLOCKING item above)**:
  strengthen the first test to assert something that would fail if
  self-heal were never invoked (e.g. `state.tasks["t1"].attempts >= 1` is
  already true trivially — assert on the monitor's decision record count,
  or restructure `HealableExecutor` so first-attempt-succeeds-but-hook-
  fails is distinguishable from a scenario where self-heal never re-runs
  it).

  **Other dimensions walked, nothing further to flag:**
  SOLID/KISS — `hooks.py` is a clean, single-responsibility module; the two
  engine.py call sites are thin, as designed. DRY — the one known trade-off
  (5th bounded-subprocess impl) is already documented, not re-flagged; no
  other 2+-occurrence duplication found in the new code. Pluggability — N/A,
  hooks are argv-command-shaped by design (D2), not a new executor/backend
  seam. Spec/DAG correctness — `cross_validate`'s new rule mirrors the
  existing unknown-agent-id check exactly; schema `$defs/hook`/`$defs/
  hookRef` match the pydantic models field-for-field. Determinism/resume
  safety — hooks fire exactly once per dispatch cycle (no hidden retry),
  RNG/clock not touched by this diff; the two engine tests that need a fake
  clock/sleeper (`test_posthook_not_fired_on_quota_exhausted`) correctly
  supply both, unlike the pattern dev-epic had to fix elsewhere in this
  suite. Concurrency — capture dirs are keyed per task+cycle, no shared
  mutable state between workers. Errors/logging — one authoritative
  info/warning log per hook dispatch at the `_dispatch_hook` boundary, no
  duplicate spam across layers.

  **Full pytest run (specified subset, run by me, not reused from an
  earlier report)**:
  `pytest -q tests/test_engine.py tests/test_engine_hooks.py tests/test_hooks.py
  tests/test_engine_hooks_resolver.py tests/test_hooks_schema_validation.py
  tests/test_isolation_models.py tests/test_engine_conflict_escalation.py`
  → **201 passed** in 4.83s. `tests/test_engine.py` alone: **26 passed**.

  **Verdict: BLOCKED on one test-suite fix** (the `TestHookNoOpWhenNone`
  vacuous-assertion gap above). Production code (`hooks.py`, `engine.py`,
  `models.py`, `spec.py`, `isolation/escalation.py`) is sound and matches
  the reviewed HLD Rev 2 design with no BLOCKING defects found. Leaving
  `TASK.md` Status as `Draft` (not `Done`) pending that fix + a re-run of
  the affected test file; re-review needed only of the replaced test(s),
  not the full diff.

## BLOCKING finding fixed; non-blocking finding also fixed — ticket closed
- By: dev-epic
- Role: manager
- Date: 2026-09-21
- Comment: Verified the reviewer's BLOCKING finding directly (re-read the exact vacuous test
  lines myself before touching anything). Fixed it (commit `2032e3d`): removed
  `tests/test_hooks.py::TestHookNoOpWhenNone` (both tests only asserted `mock_run is not None`
  inside an unexercised `with mock.patch(...)` block) and replaced it with a real proof in
  `tests/test_engine_hooks.py::TestHookNoOpWhenNone` — patches `agent_orchestrator.hooks.run_hook`,
  drives a hookless task through a REAL `Orchestrator.run()` across all three terminal
  `FakeExecutor` behaviors (succeed/fail/timeout), asserts `mock_run.assert_not_called()` for
  each. **Verified this replacement is not itself vacuous** with a negative control: temporarily
  forced the pre-hook gate to always fire regardless of `task.pre_hook`, confirmed all 3 new
  tests fail with a clear assertion, reverted (`git checkout --`, confirmed zero diff), re-ran
  to confirm green again.

  Also fixed the NON-BLOCKING finding (commit `c447526`): strengthened
  `TestPostHookSelfHealInteraction::test_hook_downgrade_picked_up_by_self_heal` to assert on a
  real `RunState.monitor_decisions` "task_failure" consult record rather than the previous
  `post_hook_result is not None` (true regardless of whether self-heal fired). Also verified
  with a negative control (self_heal_enabled=False makes it fail as expected). One incidental
  finding along the way: `RuleBasedMonitor` genuinely consults on this failure shape but its own
  heuristics decide "accept_failure" rather than "retry" for it — legitimate pre-existing
  behavior, not a bug; the test now asserts "was consulted" (the true, documented claim), not
  "was retried" (a claim that happened to be false for this specific monitor/error-shape
  combination).

  Full suite re-verified one final time after both fixes: `pytest -q -m "not real_llm and not
  swebench"` → **3898 passed, 1 failed (the same pre-existing, epic-unrelated
  test_nfr2_regression_gate.py failure), 1 skipped, 7 deselected**. `ruff check .`/
  `ruff format --check .` clean on every touched file.

## Evidence
- Reviewer's full findings: recorded above ("Critical review pass — findings").
- BLOCKING fix: commit `2032e3d` (production code diff was already sound per the reviewer's own
  AC-1..AC-5 verification — this was a test-suite-only fix).
- Non-blocking fix: commit `c447526`.
- Both fixes verified with negative controls (not just "it passes now").

## Risks / Blockers
- None. Ticket closed.
