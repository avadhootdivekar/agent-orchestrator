# TASK: T-pYt478-emit-settle-atomicity

## Metadata
- Task ID: `T-pYt478-emit-settle-atomicity`
- Epic ID: `E-YAAGhk-overseer-runner-template`
- Owner: developer
- Created: 2026-09-26
- Last Updated: 2026-09-26
- Status: **Done** (implemented + tested + reviewed on branch `fix/emit-settle-atomicity`, commit
  `3692eac`, local/unpushed — see STATUS.md for full evidence)
- Estimate: 3 days (24 h). This includes the test blast radius.

## Requirements Mapping
- Requirement IDs: FR-13, NFR-7 · Design: `docs-md/overseer-runner-hld.md` §7 (G5), §8.4 M5 · ADR-0016 D7

## Description
Fix a verified engine defect. When a circuit breaker trips, or the process crashes, at an
`emit_tasks` task's own settle boundary, the manifest is **never injected**, and a resumed run ends
`succeeded` with the emission silently lost.

Root cause, in `engine.py::_settle` at `8c13320`:
1. The emitter is saved as `succeeded` (~L1992).
2. `evaluate_breakers` runs (~L2008) and returns `halt`.
3. The `emit_tasks` block (~L2057) is only reached after step 2.
4. `runstate.prepare_resume` keeps succeeded-with-outputs tasks, so the manifest is never read again.

Reproduction: `output/E-YAAGhk-overseer-runner-template/repro_emit_lost_on_breaker_trip.py`.
Observed output: `run1: failed {'emit': 'succeeded'} injected= []` then
`run2: succeeded {'emit': 'succeeded'} injected= []`.

The fix: run the injection before the save + breaker block, so the emitter's success and its
injection persist in **one** `RunState` save. Then evaluate breakers. A halt leaves the injected
tasks `pending`, and resume runs them. Ship this as its **own PR to `main`**, ahead of the template.

- Inputs: `src/agent_orchestrator/engine.py` (`_settle`, `_inject`, `_recompute_order`)
- Outputs: the reordered `_settle`; a new `tests/test_emit_settle_atomicity.py`; updated existing tests; an NFR-2 gate allowlist entry; a release-note line.

## Acceptance Criteria
1. `tests/test_emit_settle_atomicity.py::test_breaker_trip_at_emitter_keeps_injection`. Given an
   emitter with a `stop_file` breaker whose flag exists at settle: when `run()` returns, then
   `state.status == "failed"`, the child id is in `state.injected_tasks`, and its status is
   `pending`. When the flag is removed and the run resumes (`prepare_resume` + `run`), then the child
   is `succeeded` and the run is `succeeded`. This is the repro with its assertions inverted.
2. `test_single_save_contains_success_and_injection`. A spy on `RunStateStore.save` shows that the
   first save after the emitter's executor returns already contains both `tasks[emitter].status ==
   "succeeded"` and the injected ids. No save persists the success without the injection.
3. `test_injected_task_count_trips_at_emitting_boundary`. An `injected_task_count` breaker with
   threshold below the manifest size trips at the emitter's own boundary (it used to trip one
   boundary later), and the injected tasks remain `pending`.
4. `test_manifest_error_path_unchanged`. A malformed manifest still marks the emitter `failed`,
   gives run status `failed`, and injects nothing (the same observable result as before).
5. `test_plain_resume_after_trip_dispatches_injected` documents the unchanged latch semantics. After
   a trip, a plain resume without `--extend-breaker` dispatches the persisted injected tasks. The
   test docstring explains why re-arming is out of scope (ADR-0016 D7).
6. Monitor consult `extend` path. With a `mode:"recommend"` breaker tripping at an emitter
   boundary and a monitor verdict of `extend`, the run continues and the injected tasks run.
7. Existing suites. `tests/test_mvp_breaker_conditions.py` (injected_task_count fixtures) and
   `tests/test_resume_replay.py` (AC4) are updated where the boundary moved. Each change has an
   entry in `tests/test_nfr2_regression_gate.py` **in the same commit**, with rationale text. The
   following suites pass unmodified, or are changed only with gate entries: `test_dynamic_injection`,
   `test_wave_scheduler`, `test_engine_conflict_escalation`, `test_isolation_*`,
   `test_engine_routing`, `test_routing_breaker_models`, `test_loop_construct`,
   `test_engine_breakers`, `test_monitoring_breaker_consult`.
8. Full `pytest -q` passes, and `ruff check`, `ruff format --check`, and `mypy` are clean. Record the
   pass/fail counts in STATUS.md.
9. A release note (CHANGELOG or PR body) states: "emit_tasks injection now persists before breaker
   evaluation; `injected_task_count` trips one boundary earlier".
10. A `reviewer`-agent review is recorded in STATUS.md.

## Risks
- Hidden ordering assumptions in the wave/barrier scheduler (`_ready_ids`, preds rebuild). Mitigation: AC 7's suite list.
- The monitor consult path (`_consult_breaker_trips`) must still fall through to `reshaped`.

## Dependencies
- None. This is first on the critical path. Downstream: T-vmI0jI scenario (e), T-WruPiv.

## Pseudocode / Algorithm
```text
# _settle, after outcome handling and the router-success hook (unchanged), BEFORE the first save:
injected = False
IF ts.status == "succeeded" AND task.emit_tasks:
    TRY new_specs = read_task_manifest(store, task.task_manifest_path)
    EXCEPT ValueError: log manifest_error; ts.status="failed"; ts.outputs_present=False
                       state.status="failed"; save; RETURN SettleResult("halt")
    TRY warnings = validate_isolation(workflow, [*workflow.tasks, *new_specs]); log each
    EXCEPT SpecValidationError: log; ts.status="failed"; state.status="failed"; save; RETURN halt
    TRY self._inject(new_specs, workflow, state, origin="injected", route=ts.route)
    EXCEPT InjectionError: log; ts.status="failed"; state.status="failed"; save; RETURN halt
    graph = build_dag(workflow); order, cursor = self._recompute_order(graph, ctx.done)
    injected = True
self._runstate.save(state)                       # ONE save: success + injection
<existing breaker evaluation + monitor consult block, unchanged>   # may RETURN halt
IF ts.status not in ("succeeded","skipped"): state.status="failed"; RETURN halt   (unchanged)
IF injected: log "Injected %d tasks; order recomputed"; save; RETURN SettleResult("reshaped", graph, order)
<existing loop-gate block, unchanged>
```

## Schemas / Interface Notes
- Interface / API: no public signature change. `SettleResult` signals are unchanged.
- Spec / data schema: none.
- Triggers / events: `task.injected` is now logged before any `breaker.trip` at the same boundary.
- Artifacts: none.

## Handoff Boundary
- Upstream: the design package.
- Downstream: the tester (T-vmI0jI scenario (e)) and dev-epic (merge ordering).

## Artifacts
- Repro: `output/E-YAAGhk-overseer-runner-template/repro_emit_lost_on_breaker_trip.py`
