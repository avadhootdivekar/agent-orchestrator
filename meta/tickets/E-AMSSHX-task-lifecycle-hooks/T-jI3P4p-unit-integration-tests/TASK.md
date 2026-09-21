# TASK: T-jI3P4p-unit-integration-tests

## Metadata
- Task ID: `T-jI3P4p-unit-integration-tests`
- Epic ID: `E-AMSSHX-task-lifecycle-hooks`
- Owner: tester (delegated)
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: Draft
- Estimate: 1 day

## Requirements Mapping
- Requirement IDs: FR-1..FR-6, FR-8 (epic)

## Description
Unit + integration tests for the new hook-dispatch mechanism, following this repo's existing
`tests/test_engine*.py` conventions (deterministic, fixed clock/sleeper where relevant, no real
subprocess spawn required for most cases except a handful of real-subprocess integration tests
using tiny inline Python scripts). Run the FULL existing suite afterward and report actual
pass/fail counts — no regressions.

## Acceptance Criteria
1. `models.py`: `HookSpec`/`HookOutcome` validation tests (empty `command` rejected,
   `timeout_seconds < 1` rejected, `resolve_hook_on_failure` default-per-kind behavior).
2. `engine.py` pre-hook: passes → attempt loop runs; fails + `fail_task` (default) → executor
   never called (assert on a `Mock`/`FakeExecutor` call count), `TaskResult.status == "failed"`,
   `attempts == 0`; fails + `on_failure="ignore"` → executor still runs.
3. `engine.py` post-hook: succeeded + hook passes → status stays `"succeeded"`; succeeded + hook
   fails + `on_failure="ignore"` (default) → status stays `"succeeded"`, `post_hook_result.status
   == "failed"`; succeeded + hook fails + `on_failure="fail_task"` → status becomes `"failed"`;
   already-failed agent result + failing post_hook → status stays `"failed"` (never "upgrades").
4. Post-hook NOT invoked on cancelled (`cancel_fn` True) and NOT invoked on
   `claude_quota_exhausted` early return — explicit tests for both (HLD §6 table rows).
5. **FR-4 no-op proof**: a test that patches `Orchestrator._run_hook` with `unittest.mock.Mock`
   and asserts `call_count == 0` for a task with `pre_hook=None, post_hook=None`, across
   succeeded/failed/timed_out FakeExecutor behaviors — not merely a fast-path timing check.
6. A best-effort timing micro-benchmark (script under `scripts/helper/epics/E-AMSSHX/`, not a
   flaky CI assertion) comparing `_run_with_retries` call overhead with vs. without hooks
   declared, recorded as evidence in this ticket's STATUS.md and the epic STATUS.md.
7. Hook result-file contract: a real (subprocess) integration test where the hook script writes
   `{"solved": true, "score": 0.9}` to `AO_HOOK_RESULT_PATH` and exits 0 → `HookOutcome.detail ==
   {"solved": True, "score": 0.9}`; a variant that exits 1 despite writing `{"solved": true}` →
   `status == "failed"` (exit code wins over file content, per D3) — this specifically defends
   the Epic B forward-compat contract (HLD §7).
8. Hook timeout: a script that sleeps past `timeout_seconds` → `HookOutcome.status ==
   "timed_out"`, no uncaught exception.
9. Malformed/oversized `AO_HOOK_RESULT_PATH` content → `HookOutcome.detail == {}`,
   `HookOutcome.error` set, still no uncaught exception (mirrors `read_control`'s
   `ControlFileError` handling).
10. `specs/workflow.schema.json` validation test: a workflow with a `hooks` registry +
    `pre_hook`/`post_hook` references passes schema validation + cross-validation; one with an
    empty `command` array, unknown `on_failure` value, OR (new, Rev 2) a `pre_hook`/`post_hook`
    referencing an undeclared hook name fails it with a clear `SpecValidationError`.
11. Self-heal interaction: a `fail_task` post_hook downgrading a succeeded result to failed is
    picked up by the existing self-heal consult path (`self_heal_enabled=True`) with NO changes
    needed to `_consult_task_failure_heal` — a regression test proving this "falls out for free"
    claim in HLD §6 — AND asserts `build_task_failure_summary`'s derived summary is non-empty
    (i.e. the hook's exit code + stderr tail were actually folded into `result.error`, not a bare
    marker string — this is the specific gap early-gate `architect` review flagged as BLOCKING).
12. **(NEW, BLOCKING early-gate finding, both reviews independently)** T2 conflict-resolver
    dispatch (`mode == "resolve"`) test: a task with `pre_hook`/`post_hook` declared that reaches
    a genuine merge conflict → assert hooks do NOT fire for the resolver's substituted dispatch
    (`hooks.run_hook` call count reflects only the ORIGINAL task's own dispatch cycles, never the
    resolver one). A companion T3 rerun-mode test confirms hooks DO fire normally there (no
    suppression).
13. **(NEW)** `attempts == 0` sanity check: a pre_hook-blocked `TaskResult` has `attempts=0` —
    grep/assert nothing downstream (self-heal summary formatting, any `attempts - 1` style
    indexing) breaks on this previously-impossible value.
14. **(NEW)** Result-file sequencing: `exists()`-false path (no result file written) yields
    `HookOutcome(detail={}, error=None)`; a result file present but malformed/oversized yields
    `HookOutcome(detail={}, error=<set>)` — these two cases must be distinguishable, not both
    silently folded into the same `detail={}` outcome (early-gate review Warning).
15. Full existing suite run (`pytest -q`) — report exact pass/fail/skip counts, zero regressions
    vs. the pre-epic baseline. `ruff check .`, `ruff format --check .`, `mypy .` all clean.

## Risks
- Real-subprocess tests (timeout, result-file) can be slow/flaky in CI — mitigate with small,
  short (sub-second) sleeps/timeouts and generous-but-bounded margins, consistent with how
  `bench/graders.py`'s own tests already handle `_run_command` timeouts.

## Dependencies
- T-AHvmYR, T-lzQEyy, T-DgheoA, T-fbQIFX.

## Pseudocode / Algorithm
```text
N/A — see acceptance criteria.
```

## Schemas / Interface Notes
- Interface / API: N/A.
- Spec / data schema (JSON/YAML): exercises `specs/workflow.schema.json`.
- Triggers / events: N/A.
- Artifacts (inputs/outputs by path): new test files under `tests/`.

## Handoff Boundary
- Upstream: T-AHvmYR, T-lzQEyy, T-DgheoA, T-fbQIFX.
- Downstream: T-6gR2ya (review), T-FCC8mT (late gate) both depend on this suite being green.

## Artifacts
- Docs/comments: `meta/tickets/E-AMSSHX-task-lifecycle-hooks/T-jI3P4p-unit-integration-tests/`
- Large outputs: N/A
