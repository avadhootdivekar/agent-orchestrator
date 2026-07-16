# TASK: T-j8YLGd-wave-barrier-scheduler

## Metadata
- Task ID: `T-j8YLGd-wave-barrier-scheduler`
- Epic ID: `E-IasNXu-parallel-execution`
- Owner: developer agent
- Created: 2026-07-15
- Last Updated: 2026-07-15
- Status: Done
- Estimate: 3.0 days

## Requirements Mapping
- FR-3, FR-4, FR-5 (mechanism), NFR-1, NFR-4

## Description
Replace the serial cursor walk in `Orchestrator.run()` (`engine.py:252` `while cursor < len(order):`)
with a **wave/barrier scheduler** that dispatches up to `self._max_parallel` independent ready tasks
on a `concurrent.futures.ThreadPoolExecutor`, while keeping the **entire engine core serialized on
the main thread**. Only `_run_with_retries(...)` (which calls `self._executor.execute(ctx)`) runs on
worker threads — it is already a pure reader (reads `task`/`workflow`/`agents`/`repo_paths` and only
`state.run_id`, mutates nothing, verified `engine.py:1514-1649`).

The single most important constraint: **at `N=1` the engine must be byte-identical to today**. Achieve
this by extracting the existing post-dispatch body (`engine.py` ~L565–L1063) **verbatim** into a new
method `_settle_completed_task(...)` that returns a control signal, and by draining exactly one
completion at a time. At `N=1` the wave holds one task, dispatches it, drains it, and settles it in the
same statement order as today.

This task delivers the scheduler mechanism + happy-path parallel dispatch + the barrier rule + the
`N=1` gate. The precise concurrency semantics of the failure/requeue/cancel paths are hardened and
exhaustively tested in `T-VSfAUN` (this task carries them through structurally via the returned
signals, but their full drain-vs-cancel correctness + tests are that task's scope).

### Sub-deliverables
1. New helpers on `Orchestrator`:
   - `_is_barrier(task, workflow) -> bool`: `task.emit_tasks` OR `_loop_for_gate(workflow, task.id) is
     not None` OR `_router_for_task(workflow, task.id) is not None`.
   - `_predecessors(graph) -> dict[str, set[str]]`: invert `graph.adjacency()` (successors →
     predecessors). (Optionally add `Graph.predecessors()` to `dag.py` instead — either is fine.)
   - `_ready_ids(order, preds, state, done, in_flight_ids) -> list[str]`: tasks in `order` whose every
     predecessor status ∈ {succeeded, skipped, not_taken}, not in `done`/`in_flight`, and whose own
     status is not already succeeded/skipped/not_taken/running.
2. Extract the current pre-dispatch checks (not_taken, done, should_skip, apply_join, missing-inputs,
   budget gate/charge, mark-running/started_at, resolve manifest+gate paths) into
   `_prepare_and_maybe_dispatch(tid, ...)` returning `DISPATCH | SKIPPED | HALT | BLOCKED` (BLOCKED =
   budget "wait" deferred; wired fully in `T-VSfAUN`, here it may simply mean "don't fill further").
3. Extract the post-dispatch body **verbatim** into `_settle_completed_task(tid, result, ...) ->
   Settle` where `Settle ∈ {SETTLED, REQUEUE, HALT, RESHAPED}`. Replace each `cursor -= 1; continue`
   with `return REQUEUE`, each terminal `break`/`failed=True` with `return HALT`, and each
   inject/loop DAG-rebuild with `return RESHAPED` (the caller rebuilds `graph`/`order`/`preds`).
4. Rewrite `run()`'s loop into the wave loop (design doc §6): FILL ≤N ready non-barrier tasks (a
   barrier is launched only when `in_flight` is empty, and launching it `break`s the fill), DRAIN the
   first completed future, settle it, act on the signal, `save`. Wrap the pool in `try/finally` with
   `pool.shutdown(wait=True)` so threads never leak (NFR-4), including on exception.

## Acceptance Criteria
1. **N=1 byte-identical (blocking gate).** Given the full existing engine test suite
   (`tests/test_engine*.py`, budget/breaker/routing/loop/injection/monitoring), When run with the
   default `max_parallel=1`, Then every test passes **unedited**. Additionally a dedicated test runs a
   representative workflow (diamond + emit_tasks + loop + router + budget) at `N=1` and asserts the
   final `RunState` (task statuses, attempts, budget counters, tripped breakers) and the ordered
   `run.log` event list are identical to a captured pre-change baseline.
2. **Parallel dispatch proof.** Given a workflow with ≥2 independent ready tasks and `max_parallel=2`
   plus a gated executor (from `T-TNleFt`, or a local test double), When `run()` executes, Then both
   tasks' `execute()` are observed in flight simultaneously (task B enters before task A is released).
3. **Ready-set correctness.** `_ready_ids` unit tests: a task is returned only when all predecessors
   are settled (succeeded/skipped/not_taken); a task with a `running` or `pending` predecessor is not;
   `not_taken` predecessors count as settled; results follow the deterministic sorted-Kahn `order`.
4. **Barrier isolation.** Given a workflow mixing a barrier task (emit_tasks / loop-gate / router)
   with independent non-barrier siblings and `max_parallel=4`, When it runs, Then the barrier's
   `execute()` never overlaps any sibling's (assert via the gated executor: `in_flight == {}` when the
   barrier is submitted, and no sibling submitted until the barrier settles). Two barriers ready
   together run strictly sequentially.
5. **RESHAPED handling.** After an `emit_tasks` (or loop-iterate) barrier settles, `graph`/`order`/
   `preds` are recomputed and newly injected/cloned tasks become eligible in subsequent waves;
   existing `emit_tasks` and loop tests pass at `N=1` and at `N=4`.
6. **No thread leak (NFR-4).** A test forcing a worker to raise (not return a failed result) inside
   `_run_with_retries` causes `run()` to finalize `failed` after draining, and
   `ThreadPoolExecutor.shutdown` is called (no dangling threads; assert via `threading.active_count()`
   returning to baseline, or a shutdown spy).
7. `ruff`/`mypy src` clean; `uv run pytest` green.

## Risks
- **R1 (highest): N=1 parity.** The settle extraction MUST be verbatim (same statements, same order,
  same `save` sites). Any reordering breaks NFR-1. Mitigate by diffing the extracted method against the
  original inline block line-by-line and by AC-1.
- Barrier predicate must include loop `__iter` clones — reuse `_loop_for_gate` (it already handles the
  `__iter` suffix, `engine.py:1705-1721`), do not hand-roll id matching.
- `as_completed`/`wait(FIRST_COMPLETED)` must pop exactly one future and not busy-spin; keep the
  in-flight map keyed by future.

## Dependencies
- `T-JXiI9j` (needs `self._max_parallel`).
- Reuses unchanged: `_run_with_retries`, `_loop_for_gate`, `_router_for_task`, `_on_router_success`,
  `_inject`, `build_dag`, `_recompute_order` (superseded by ready-set but its helpers stay),
  `_apply_join`, budget/breaker/monitor blocks.

## Pseudocode / Algorithm
```text
def run(workflow, reposets, agents, run_state=None):
    ... setup: build_dag, order, cones, membership, done, attach logger ...
    preds = self._predecessors(graph)
    failed = False
    with ThreadPoolExecutor(max_workers=self._max_parallel) as pool:
        in_flight: dict[Future, str] = {}
        while True:
            if self._cancel_fn():
                state.status = "cancelled"; failed = True; self._drain_remaining(in_flight, ...); break
            ready = self._ready_ids(order, preds, state, done, set(in_flight.values()))
            if not ready and not in_flight: break
            for tid in ready:
                if len(in_flight) >= self._max_parallel: break
                barrier = self._is_barrier(workflow.task(tid), workflow)
                if barrier and in_flight: break                 # barrier waits for empty
                disp = self._prepare_and_maybe_dispatch(tid, ...)   # SKIPPED|DISPATCH|HALT|BLOCKED
                if disp == SKIPPED: continue
                if disp == HALT: failed = True; break
                if disp == BLOCKED: break                        # budget wait; drain then retry (T-VSfAUN)
                fut = pool.submit(self._run_with_retries, task, workflow, agents, repo_paths, state,
                                  dyn_inputs, task_manifest_path=..., gate_output_path=...)
                in_flight[fut] = tid
                if barrier: break                                # launched solo → go drain
            if failed: break
            if not in_flight: continue                           # everything blocked → re-loop
            done_fut = next(as_completed(list(in_flight)))
            tid = in_flight.pop(done_fut); result = done_fut.result()
            signal = self._settle_completed_task(tid, result, workflow, state, done, cones, ...)
            if signal == HALT: failed = True; self._drain_remaining(in_flight, ...); break
            elif signal == REQUEUE: state.tasks[tid].status = "pending"
            elif signal == RESHAPED:
                graph = build_dag(workflow); order = graph.topological_order(); preds = self._predecessors(graph)
            self._runstate.save(state)
        # pool.shutdown(wait=True) via `with`
    if not failed and state.status == "running": state.status = "succeeded"
    self._runstate.save(state); return state
```

## Schemas / Interface Notes
- Interface / API: new private methods `_is_barrier`, `_predecessors`, `_ready_ids`,
  `_prepare_and_maybe_dispatch`, `_settle_completed_task`, `_drain_remaining` (stub ok here; filled in
  `T-VSfAUN`); `run()` internals only — public `run(...) -> RunState` signature unchanged.
- Data schema: none. Triggers/events: none. Artifacts: per-task `output_dir` unchanged (already
  unique per task/attempt → concurrency-safe).

## Handoff Boundary
- Upstream: `T-JXiI9j`.
- Downstream: `T-VSfAUN` fills in the concurrency-correct budget/requeue/cancel semantics inside
  `_prepare_and_maybe_dispatch` / `_settle_completed_task` / `_drain_remaining`; `T-TNleFt` supplies
  the gated executor + the deterministic parallelism proofs and the N=1 baseline capture.

## Artifacts
- Docs/comments: `meta/tickets/E-IasNXu-parallel-execution/T-j8YLGd-wave-barrier-scheduler/`
- Large outputs: none (N=1 baseline capture is a test fixture, not a root `output/` artifact).
