# STATUS

- ID: `T-t4m8x1-resume-replay`
- Updated At: 2026-07-09
- State: Done
- Owner: developer

## This update
Ticket created from LLD §9. Route decisions + tripped-breaker facts survive resume; not_taken re-derived deterministically.

By: architect · Role: architect · Date: 2026-07-09 · Comment: Wave-4. Router never re-runs; not_taken re-derived from persisted `route_decisions` (no verdict re-read, NFR-2). Shares the RunState backward-compat pattern with epic `E-st5p3q`.

## Evidence
- Design: `docs-md/lld-run-control-routing-breakers.md` §9, FR-CB5.

## Risks / Blockers
- Depends on T-m2h5t7, T-x8v4d3, T-c4w6p1.

## Next actions
1. Extend `prepare_resume`; add resume tests (engine-API + CliRunner).
2. Verify cleared-vs-still-true breaker re-evaluation.

---

## Completion (2026-07-09)

Implemented entirely inside `src/agent_orchestrator/runstate.py`'s `prepare_resume`
(no other production file touched, per scope):

1. Added an `elif ts.status == "not_taken": pass` branch **before** the existing
   catch-all `elif ts.status != "pending": state.tasks[task.id] = TaskRunState(status="pending")`
   reset, so a persisted routing verdict is never clobbered back to pending on resume.
2. Added a re-derivation block at the end of the method (after the per-task loop,
   before `state.status = "running"`): `build_dag(workflow)` + `compute_cones(workflow, graph)`
   over the fully re-attached (injected-tasks-merged) graph, then for each
   `state.route_decisions[router_id]` walks every route NOT in the selected set and
   marks every task in that route's cone `not_taken` — unless the task is already
   `succeeded`/`skipped`/`failed` (guard mirrors `engine.py::_on_router_success`
   exactly, same field semantics: `route="<router_id>:<route_id>"`,
   `not_taken_reason="router=<router_task_id> route=<route_id> not selected (resumed)"`).
   Never reads a verdict file — sources only `state.route_decisions` (NFR-2).
3. Added `from .dag import build_dag, compute_cones` at the top of the module (no
   circular import — `dag.py` only imports `.errors`/`.models`).

One mypy fix needed: the re-derivation loop's `state.tasks.get(t)` (returns
`TaskRunState | None`) collided with the earlier per-task loop's `ts` variable (mypy
infers a single type per name across the whole function body); renamed to
`settled`/`cone_ts` to resolve — no behavior change.

**Finding to flag for the breaker-owning tickets (T-x8v4d3/T-q5n7k2/T-r3j9b6), not
fixed here since `breakers.py`/`engine.py` are out of scope for this ticket**:
`evaluate_breakers`'s latch (`already_tripped = {tb.id for tb in state.tripped_breakers}`)
is keyed off `state.tripped_breakers`, which is loaded from disk on resume. So a
breaker id that had **already** recorded a trip before the run stopped will **not**
re-halt a resumed run even if its condition is still true (e.g. the operator resumes
without pruning below an `injected_task_count` cap, or without removing a `stop_file`
that caused the original trip) — `evaluate_breakers` returns `None` in that case
because the id is already latched, so the resumed run sails past the still-true
condition. This only matters when the SAME id already tripped pre-resume; a condition
that becomes/remains true but was never evaluated before the run stopped (the scenario
AC3/AC4 actually describe, and the one this ticket's tests exercise) works correctly
today with zero code changes, exactly as the LLD claims. I did not touch `breakers.py`
to fix the already-latched case — flagging it here as a real gap for whoever owns
resume+breaker semantics next, rather than silently working around it or silently
declaring ACs 3/4 satisfied without qualification.

Tests added: `tests/test_resume_replay.py` (9 cases):
- `TestRouterNotRerunAndNotTakenPersists` (AC1): full stop-then-resume-to-success via
  the engine API; router kept `succeeded` with unchanged `attempts` (never
  re-dispatched), previously-`not_taken` task stays `not_taken`, `route_decisions`
  replay unchanged.
- `TestPendingTaskInUnselectedConeRederived` (AC2, 2 cases): a `not_taken` task rolled
  back to `pending` (simulating the narrow crash window between `_on_router_success`
  persisting `route_decisions` and marking the cone) is re-derived deterministically
  and idempotently across two `prepare_resume` calls; a settled-`succeeded` task in
  the same cone is never clobbered (Risks guard).
- `TestStopFileBreakerResume` (AC3, 2 cases): run1 halts on an unrelated task failure
  before the `stop_file` breaker ever evaluates; removing the file before resume
  completes clean, leaving it present trips on resume.
- `TestInjectedTaskCountBreakerResume` (AC4, 1 case) + `TestFailureCountBreakersStartCleanOnResume`
  (AC4, 1 case): a persisted over-cap `injected_task_count` re-trips immediately on
  the first resumed boundary; `task_failures` starts at 0 after `prepare_resume`
  resets the failed task to pending, and resume completes without a new trip.
- `TestOldStateJsonFixtureResumes` (AC5): reuses the existing
  `tests/fixtures/state_pre_routing_breakers.json` fixture (no new fixture needed) —
  loads, defaults confirmed empty, `prepare_resume` + a full resumed run succeed.
- `TestE2EResumeReplayRouting` (AC6, CliRunner companion): real `ao run` /
  `ao resume` invocations over a routing workflow, stopped via a missing-required-input
  failure (no executor-behavior injection available through the CLI's plain
  `DispatchExecutor`), proving the same replay behavior through the actual CLI layer.

Verification: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy .`
all clean on the two files this ticket touched (`src/agent_orchestrator/runstate.py`,
`tests/test_resume_replay.py`); pre-existing/concurrent findings in untouched files
(`tests/test_e2e_cli.py`, `tests/test_breakers.py`, `tests/test_engine.py`,
`tests/test_engine_budget.py`, `tests/test_project_config.py`, `tests/test_executor.py`)
predate this change or belong to sibling tickets actively landing in the same wave —
out of scope. `uv run pytest -q` → 570 passed, 3 skipped (551 baseline stated in
TASK.md + 19 new: 9 from this ticket, ~10 from sibling tickets landing concurrently in
the same shared working tree) — no regressions attributable to this change.

All 6 TASK.md acceptance criteria verified by the tests above; risk guards from
TASK.md's Risks section (never clobber a legitimately-succeeded task; NFR-2
determinism — no verdict re-read anywhere in the new code) explicitly covered.

By: developer · Role: developer · Date: 2026-07-09 · Comment: T-t4m8x1
resume-replay DONE. `prepare_resume` extended exactly to scope (only
`src/agent_orchestrator/runstate.py` touched; `write_status` in the same file
untouched per the sibling-ticket boundary). 9 new tests added, full suite green, no
regressions. One out-of-scope finding flagged above for the breaker-owning tickets:
`evaluate_breakers`'s per-id latch persists across resume via `state.tripped_breakers`,
so an id that already tripped pre-resume will not re-halt post-resume even if its
condition remains true — worth a follow-up ticket if that's not the intended semantic.
