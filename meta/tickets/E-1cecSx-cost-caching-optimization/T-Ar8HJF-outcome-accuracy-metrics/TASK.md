# TASK: T-Ar8HJF-outcome-accuracy-metrics

## Metadata
- Task ID: `T-Ar8HJF-outcome-accuracy-metrics`
- Epic ID: `E-1cecSx-cost-caching-optimization`
- Owner: delegated `developer` agent
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: `Done`
- **Rev 2 note**: original design (in-engine `TaskSpec.settlement_hook`) rejected at early-gate
  review (architect: "needs rework") and replaced with `ADR-0015`'s post-run `ao report-outcomes
  --grade` design — `engine.py` ends up with ZERO changes from this task, an even narrower
  footprint than this ticket originally planned. See STATUS.md for the full record.
- Estimate: `2-3 days`

## Requirements Mapping
- Requirement IDs: FR-B3-1, FR-B3-2, FR-B3-3, NFR-B3-1

## Description
See `docs-md/cost-caching-optimization-hld.md` §3 in full — this is the most design-heavy task
in the epic. Three sub-deliverables:

1. **Local counts (§3.1)** — new `src/agent_orchestrator/outcomes.py`:
   `task_outcome_summary(state: RunState) -> list[TaskOutcomeSummary]`, one row per task, pure
   function over already-persisted fields (`TaskRunState.attempts`/`.dispatch_cycle`,
   `TaskIntegrationState.resolver_attempts`/`.reruns`, `RunState.monitor_decisions` filtered by
   `consult_point == "task_failure"`, `RunState.tripped_breakers`).

2. **Deterministic grading hook script (§3.2) — ALREADY WRITTEN, see below.**
   `specs/examples/hooks/grade_command.py` (note: relocated from the original
   `scripts/helper/hooks/grade_task.py` path in an earlier draft of this ticket, to match
   Epic A's OWN established convention — its example hooks live at
   `specs/examples/hooks/check_disk_space.py`/`grade.py`, alongside the demo `WorkflowSpec`
   that references them by relative path; a spec-referenced hook script is not the
   throwaway/epic-scoped tooling CLAUDE.md's `scripts/helper/` convention is for).
   Generalizes `bench/graders.py`'s `CommandGrader`/`PytestGrader` (imported directly, not
   reimplemented — DRY) per Epic A HLD §7's 5-step recipe, configured via env vars
   (`AO_GRADE_COMMAND`/`AO_GRADE_MODE`/`AO_GRADE_CWD`/`AO_GRADE_TIMEOUT_SECONDS`/
   `AO_GRADE_PASS_THRESHOLD` — see the script's own docstring for the full contract) so one
   script instance works for every task that wires it. **Deterministic/scripted only — no LLM
   calls** (Epic A HLD §7's explicit non-billable boundary; hard constraint, already respected
   — verify this remains true if you touch the script). **Your remaining work here**: write
   its unit tests (subprocess-invocation style, matching how `tests/test_engine_hooks.py`
   already tests throwaway hook scripts — assert exit code + `AO_HOOK_RESULT_PATH` JSON shape
   for: a passing "command" mode run, a failing one, "pytest" mode with a real small pytest
   invocation, and the missing-`AO_GRADE_COMMAND` configuration-error path). Do not duplicate
   Epic A's own `grade.py` — that stays as-is, a different (deliberately trivial) example.

3. **Post-settlement hook trigger (§3.3)** — the one item that touches `engine.py`. Read design
   doc §3.3 IN FULL before writing any code — it documents a real correction to Epic A HLD §7's
   claim (a single `_settle_completed_task` call site does NOT cover the skip path; two call
   sites are required) verified against current `engine.py` line numbers. Implement exactly as
   specified there:
   - `models.py`: `TaskSpec.settlement_hook: HookRef | None = None` (NEW field, distinct from
     `pre_hook`/`post_hook` — do not reuse those fields or their existing call sites);
     `TaskRunState.settlement_hook_result: HookOutcome | None = None`.
   - `spec.py::cross_validate`: one new rule resolving `settlement_hook.use` against
     `WorkflowSpec.hooks`, mirroring the existing `pre_hook`/`post_hook` rule exactly (same
     error shape).
   - `engine.py`: one new private helper (e.g. `_fire_settlement_hook`) calling
     `hooks.run_hook` (reuse, do not reimplement), called from exactly two sites:
     (a) `_settle_completed_task`, right after `ts.status` reaches its final post-integration
     value (both the succeeded and non-succeeded branches must reach it — read the surrounding
     ~60 lines before editing to place it correctly);
     (b) `_prepare_and_maybe_dispatch`'s skip branch, immediately after `ts.status = "skipped"`
     is set (around line 1007-1017 as of this task's creation — confirm current line numbers
     before editing, code may have shifted).
   - **NFR-B3-1 (hard constraint): `settlement_hook` must NEVER mutate `ts.status` or any other
     control-flow-relevant field.** It only writes `ts.settlement_hook_result`. This is a
     deliberate, tested invariant — write a unit test that a settlement hook exiting non-zero
     leaves `ts.status` byte-identical to what it would have been without the hook.

## Acceptance Criteria
1. `outcomes.py::task_outcome_summary` unit-tested against a synthetic `RunState` carrying
   self-heal `monitor_decisions`, `TaskIntegrationState.resolver_attempts`/`.reruns`, and
   `tripped_breakers` — assert exact counts.
2. `scripts/helper/hooks/grade_task.py` unit-tested standalone (subprocess or direct function
   call) against a fixture `AO_HOOK_CONTEXT_PATH`/output directory — assert `score`/`detail`
   written correctly and exit code matches pass/fail.
3. `models.py`/`spec.py` changes: unit tests for the new cross-validate rule (valid + invalid
   `settlement_hook.use` reference), and a byte-identical-serialization regression test for a
   `TaskSpec`/`TaskRunState` with `settlement_hook`/`settlement_hook_result` unset (mirrors the
   existing pattern Epic A used for `pre_hook_result`/`post_hook_result`).
4. `engine.py` changes: integration test covering BOTH call sites in ONE run —
   (a) a freshly-dispatched task with `settlement_hook` wired to a grader script, assert
   `TaskRunState.settlement_hook_result.score` is populated after the run;
   (b) a `skip_if_outputs_exist: true` task, pre-seeded with existing outputs so it takes the
   skip path, ALSO with `settlement_hook` wired — assert `settlement_hook_result` is STILL
   populated (this is the entire point of building call site (b); a test that only exercises
   the dispatched path does not prove the fix).
5. NFR-B3-1 regression test: a `settlement_hook` script that exits 1 (fails) — assert
   `ts.status` is unaffected (still `"succeeded"` for an otherwise-successful task).
6. Full existing test suite still passes (`pytest -q`) — no regression to `pre_hook`/`post_hook`
   behavior or any other engine path.
7. `ruff`/`mypy` clean on all touched/new files.

## Risks
- This is the epic's highest-risk task (only one touching `engine.py`'s shared dispatch/settle
  functions). Mitigation: both call sites are additive appends at already-understood points; no
  existing line is modified, only new lines inserted. Read the design doc §3.3 and the exact
  current `engine.py` region before editing — line numbers may have drifted since this ticket
  was written.
- **Implementation note confirmed by direct read of `hooks.py` (2026-09-21):**
  `run_hook`'s `kind` parameter AND `HookOutcome.kind` (`models.py`) are currently
  `Literal["pre_hook", "post_hook"]` — a hard-coded two-value Literal. Widen BOTH to
  `Literal["pre_hook", "post_hook", "settlement_hook"]` (additive, non-breaking: existing
  callers keep passing the same two string literals unchanged; JSON schema/pydantic widening
  of a Literal is backward-compatible for any already-serialized `HookOutcome`). Do this
  narrowly — do not touch anything else in `hooks.py`; `run_hook`'s own logic needs zero other
  changes, it already treats `kind` as an opaque string used only for the returned
  `HookOutcome.kind` field.
- **cwd/env for the SKIP-path call site:** `_prepare_and_maybe_dispatch`'s should_skip branch
  runs BEFORE isolation/worktree resolution for that task (a task about to be skipped never
  gets a worktree — see the comment immediately below the skip branch in current `engine.py`).
  There is no per-task isolated `cwd`/`env_overlay` available at that call site. Use the run's
  own workspace root / `self._store`'s root and an empty `env_overlay` (mirrors the
  non-isolated dispatch path's own convention elsewhere in this codebase) — a skipped task's
  declared outputs are, by definition, already present in the shared checkout, so a grading
  hook reading `context.json`'s output paths does not need a worktree-scoped cwd to find them.
  Document this choice explicitly in the code comment at that call site.

## Dependencies
- Depends on Epic A's `hooks.py::run_hook`, `HookRef`/`HookOutcome` (already landed, reuse
  unchanged — do not modify `hooks.py`).

## Pseudocode / Algorithm
See design doc §3.1 (local counts table) and §3.3 (settlement hook call sites) for exact
placement and semantics.

## Schemas / Interface Notes
- Interface: `outcomes.py` pure functions; `models.py` additive fields; `hooks.run_hook` reused.
- Spec / data schema: `specs/workflow.schema.json` — add `settlement_hook` to the task def's
  properties (same `$defs/hookRef` reference `pre_hook`/`post_hook` already use).
- Artifacts: `settlement_hook`'s context/result files reuse `hooks.py`'s existing capture-layout
  convention (a new `settlement_hook/` sibling directory next to `pre_hook/`/`post_hook/` under
  the task's output dir, or equivalent — implementer's call, document the choice).

## Handoff Boundary
- Upstream: Epic A's `hooks.py` (read-only dependency).
- Downstream: `T-UJElTR` (e2e demonstration exercises the grading hook end-to-end).

## Artifacts
- Docs/comments: `meta/tickets/E-1cecSx-cost-caching-optimization/T-Ar8HJF-outcome-accuracy-metrics/`
- Design doc: `docs-md/cost-caching-optimization-hld.md` §3
