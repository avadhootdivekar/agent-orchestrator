# TASK: T-lzQEyy-engine-hook-dispatch

## Metadata
- Task ID: `T-lzQEyy-engine-hook-dispatch`
- Epic ID: `E-AMSSHX-task-lifecycle-hooks`
- Owner: developer (delegated)
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: Draft
- Estimate: 1-2 days

## Requirements Mapping
- Requirement IDs: FR-2, FR-3, FR-4, FR-6 (epic)

## Description
Wire pre_hook/post_hook dispatch into `Orchestrator._run_with_retries` in
`src/agent_orchestrator/engine.py`, calling into a **new `src/agent_orchestrator/hooks.py`
module** (Rev 2 — keeps engine.py, already ~4000 lines, from growing further for a
self-contained chunk of logic; an early-gate review suggestion), per HLD §5 mechanics and §6
failure-semantics table (now including the T2-resolver-suppression row, a BLOCKING early-gate
finding). Depends on T-AHvmYR's models landing first.

## Acceptance Criteria
1. New module `src/agent_orchestrator/hooks.py`: `run_hook(hook: HookSpec, *, kind, hook_name,
   run_id, task_id, cycle, capture_dir, context_fields: dict, env_overlay: dict[str, str],
   cwd: str) -> HookOutcome`. **Never raises** (mirrors `bench/graders.py::_run_command`'s
   contract — noted in this module's docstring as a deliberate 5th reuse of that idiom in this
   codebase; DRY trade-off recorded, not hidden, per early-gate review) — a
   timeout/missing-binary/malformed-result-file all degrade to a `HookOutcome` with
   `status`/`error` set, never an exception.
2. `run_hook` writes `context.json` (`version: 1` + `context_fields`, paths only — NFR-1,
   absolute paths) to `<capture_dir>/context.json`, spawns `subprocess.run(hook.command,
   shell=False, cwd=cwd, env={**os.environ, **env_overlay, AO_RUN_ID=..., AO_TASK_ID=...,
   AO_HOOK_KIND=..., AO_HOOK_NAME=hook_name, AO_DISPATCH_CYCLE=..., AO_HOOK_CONTEXT_PATH=...,
   AO_HOOK_RESULT_PATH=...}, stdin=subprocess.DEVNULL, timeout=hook.timeout_seconds,
   capture_output=True)`. Reuse the `AO_RUN_ID`/`AO_TASK_ID` env var NAMING
   `isolation/integrator.py::_base_env` already uses (don't invent new names for the same
   concept).
3. `stdout.txt`/`stderr.txt` written to `capture_dir`, truncated to a new named
   `HOOK_CAPTURE_CAP_BYTES` constant before writing (mirrors `isolation/integrator.py`'s
   `VERIFY_CAPTURE_CAP_BYTES` precedent — unbounded capture of a runaway hook's output is a real
   disk/memory-exhaustion surface, an early-gate review finding).
4. Result-file read sequencing (early-gate review finding — must be structurally distinct, not
   folded into one `try/except`): call `artifact_store.exists(result_path)` FIRST. Not present →
   `HookOutcome.detail={}, error=None, score=None` (normal — hook chose not to write one).
   Present → call `artifacts.read_control`; any `ControlFileError` → `HookOutcome.error` set,
   `detail={}` (this IS the malformed case FR-6 wants surfaced). Valid → `detail` = the JSON
   object minus a `score` key if present and numeric, which populates the typed
   `HookOutcome.score` field instead. Exit code (never the JSON file) is the sole `status`
   source (D3) — a `"score"`/any JSON content NEVER overrides pass/fail.
5. Pre-hook call site: inside `_run_with_retries`, before the `for attempt in range(...)` loop.
   `if task.pre_hook is None:` skip entirely — no `WorkflowSpec.hooks` lookup, no dict/dir/
   subprocess work (FR-4). On failure with `resolve_hook_on_failure(task.pre_hook,
   workflow.hooks[task.pre_hook.use], "pre_hook") == "fail_task"`: return
   `TaskResult(status="failed", attempts=0, error=f"pre_hook '{name}' failed: exit={code} " +
   <bounded stderr tail>, pre_hook_result=outcome)` immediately, executor never invoked (fold the
   hook's own exit code + stderr tail into `error`, NOT a bare marker string — early-gate
   finding: self-heal's `build_task_failure_summary` derives its summary from `result.error`).
   Otherwise proceed to the attempt loop with `pre_hook_result` stashed for the eventual returned
   `TaskResult`.
6. Post-hook call site: a small helper (e.g. `_finalize_with_post_hook(self, task, workflow,
   result, ...) -> TaskResult`) whose first statement is `if task.post_hook is None: return
   result` (FR-4). Wrap ONLY the `"succeeded"` early return and the final post-loop `return
   last_result` — leave the `cancelled` return and the `claude_quota_exhausted` early return
   untouched (HLD §6 table).
7. Post-hook `on_failure="fail_task"` downgrades a `"succeeded"` result to `"failed"`
   (`error=f"post_hook '{name}' failed: exit={code} " + <bounded stderr tail>` — same
   error-detail-folding requirement as AC-5); never touches an already-`"failed"`/`"timed_out"`
   result beyond recording `post_hook_result` on it.
8. **T2 conflict-resolver suppression (BLOCKING early-gate finding, NEW)**:
   `_prepare_resolver_dispatch`'s existing `task.model_copy(update={...})` call (builds the
   resolver's substituted task) additionally sets `pre_hook=None, post_hook=None` on that copy.
   T3 rerun dispatch (`_prepare_rerun_dispatch`) is explicitly left UNCHANGED — hooks must
   continue to fire normally there (confirmed correct by both early-gate reviews).
9. `_settle_completed_task`: mirror `result.pre_hook_result`/`post_hook_result` onto
   `TaskRunState` next to the existing `output_artifact_path` mirror (~engine.py line 1693-1694).
10. Capture dirs: reuse the SAME per-cycle `output_dir` local variable `_run_with_retries`
    already computes for `attempt-<n>/` (handles both the flat cycle-1 and nested `cycle-<n>/`
    shapes automatically — don't reinvent this path logic, an early-gate review finding) —
    `<output_dir>/pre_hook/` and `<output_dir>/post_hook/`.
11. Structured log events on the existing `task_log`/`run_log` convention (mirrors `task.skip`/
    `task.fail` naming): `task.pre_hook.start`/`.passed`/`.failed`, `task.post_hook.start`/
    `.passed`/`.failed`.
12. FR-4 provable no-op: `hooks.run_hook` is never called for a task with no declared hooks —
    verified by T-jI3P4p's `Mock`-patch test, not just asserted here.
13. `ruff check`/`ruff format --check`/`mypy` clean on the diff. Full existing pytest suite
    passes with zero regressions (`pytest -q`, actual run, report pass/fail counts).

## Risks
- `engine.py` is ~4000 lines with many existing invariants (NFR-1, NFR-3, R-21 dispatch-cycle
  keying, ADR-0007 worker/main-thread split). Mitigation: hooks live ENTIRELY inside
  `_run_with_retries` + `_prepare_resolver_dispatch` (worker thread, no `RunState` mutation) —
  no other call site changes; subprocess mechanics extracted to `hooks.py`.
- `_run_with_retries` has 3 early-return points; only 2 should get post-hook wrapping (see AC-6).
  Mitigation: this ticket is explicit about which; T-6gR2ya (review) re-checks this specifically.
- The T2-resolver-suppression fix (AC-8) was a BLOCKING finding from BOTH early-gate reviews
  (reviewer + architect, independently) — treat this as load-bearing, not optional polish; a
  dedicated integration test in T-jI3P4p locks it in.

## Dependencies
- T-AHvmYR (models) must land first.

## Pseudocode / Algorithm
```text
See docs-md/task-lifecycle-hooks-hld.md §5-6 for full mechanics + the failure-semantics
decision table (which return sites get wrapped, on_failure defaults per kind, etc).
```

## Schemas / Interface Notes
- Interface / API: new private methods on `Orchestrator` — not part of any public API.
- Spec / data schema (JSON/YAML): consumes `TaskSpec.pre_hook`/`post_hook` (T-AHvmYR).
- Triggers / events: N/A.
- Artifacts (inputs/outputs by path): `context.json`/`result.json`/`stdout.txt`/`stderr.txt`
  under the per-cycle capture dir (paths only, NFR-1 — see HLD §5).

## Handoff Boundary
- Upstream: T-AHvmYR (models), HLD §5-6.
- Downstream: T-jI3P4p (tests exercise this), T-FCC8mT (e2e run exercises this via the real CLI).

## Artifacts
- Docs/comments: `meta/tickets/E-AMSSHX-task-lifecycle-hooks/T-lzQEyy-engine-hook-dispatch/`
- Large outputs: N/A
