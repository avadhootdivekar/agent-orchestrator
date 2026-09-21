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
`src/agent_orchestrator/engine.py`, per HLD §5 mechanics and §6 failure-semantics table. Depends
on T-AHvmYR's models landing first.

## Acceptance Criteria
1. New private helper `_run_hook(self, hook: HookSpec, *, kind, task, state, cycle, capture_dir,
   hook_paths, agent_result) -> HookOutcome` near `_run_with_retries`. **Never raises** (mirrors
   `bench/graders.py::_run_command`'s contract) — a timeout/missing-binary/malformed result file
   degrades to a `HookOutcome` with `status`/`error` set, never an exception.
2. `_run_hook` writes `context.json` (paths only, NFR-1) to `<capture_dir>/context.json`, spawns
   `subprocess.run(hook.command, shell=False, cwd=..., env=..., timeout=hook.timeout_seconds,
   capture_output=True)`, writes `stdout.txt`/`stderr.txt` to `capture_dir`, and — if
   `AO_HOOK_RESULT_PATH` exists and is a valid bounded JSON object (reuse
   `artifacts.read_control`, catch `ControlFileError`) — records it verbatim as
   `HookOutcome.detail`. Exit code (not the JSON file) is the sole `status` source (D3).
3. Pre-hook call site: inside `_run_with_retries`, before the `for attempt in range(...)` loop.
   `if task.pre_hook is None:` skip entirely (no dict/dir/subprocess work — FR-4). On failure with
   `resolve_hook_on_failure(task.pre_hook, "pre_hook") == "fail_task"`: return
   `TaskResult(status="failed", attempts=0, error="pre_hook failed: ...",
   pre_hook_result=outcome)` immediately, executor never invoked. Otherwise proceed to the attempt
   loop with `pre_hook_result` stashed for the eventual returned `TaskResult`.
4. Post-hook call site: a small helper (e.g. `_finalize_with_post_hook(self, task, result, ...) ->
   TaskResult`) whose first statement is `if task.post_hook is None: return result` (FR-4). Wrap
   ONLY the `"succeeded"` early return and the final post-loop `return last_result` — leave the
   `cancelled` return and the `claude_quota_exhausted` early return untouched (HLD §6 table).
5. Post-hook `on_failure="fail_task"` downgrades a `"succeeded"` result to `"failed"`
   (`error="post_hook_failed: ..."`); never touches an already-`"failed"`/`"timed_out"` result
   beyond recording `post_hook_result` on it.
6. `_settle_completed_task`: mirror `result.pre_hook_result`/`post_hook_result` onto
   `TaskRunState` next to the existing `output_artifact_path` mirror (~engine.py line 1693-1694) —
   two lines, same pattern (`if result.pre_hook_result: ts.pre_hook_result = ...` etc., or
   unconditional assignment — match whichever style the existing line already uses).
7. Capture dirs: `<output_dir-for-this-cycle>/pre_hook/` and `.../post_hook/` (siblings of
   `attempt-<n>/`, NOT per-attempt — hooks fire once per dispatch cycle).
8. Structured log events on the existing `task_log`/`run_log` convention (mirrors `task.skip`/
   `task.fail` naming): `task.pre_hook.start`/`.passed`/`.failed`, `task.post_hook.start`/
   `.passed`/`.failed` (or equivalent — match existing event-naming style in this file).
9. FR-4 provable no-op: `Orchestrator._run_hook` is never called for a task with no declared
   hooks — verified by T-jI3P4p's `Mock`-patch test, not just asserted here.
10. `ruff check`/`ruff format --check`/`mypy` clean on the diff. Full existing pytest suite passes
    with zero regressions (`pytest -q`, actual run, report pass/fail counts).

## Risks
- `engine.py` is ~4000 lines with many existing invariants (NFR-1, NFR-3, R-21 dispatch-cycle
  keying, ADR-0007 worker/main-thread split). Mitigation: hooks live ENTIRELY inside
  `_run_with_retries` (worker thread, no `RunState` mutation) — no other call site changes.
- `_run_with_retries` has 3 early-return points; only 2 should get post-hook wrapping (see AC-4).
  Mitigation: this ticket is explicit about which; T-6gR2ya (review) re-checks this specifically.

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
