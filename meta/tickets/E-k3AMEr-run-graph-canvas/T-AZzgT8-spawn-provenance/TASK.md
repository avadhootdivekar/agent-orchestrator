# TASK: T-AZzgT8-spawn-provenance

## Metadata
- Task ID: `T-AZzgT8-spawn-provenance`
- Epic ID: `E-k3AMEr-run-graph-canvas`
- Owner: `developer` (Dev A). **Pair with `reviewer`**: this touches core injection and resume.
- Created: `2026-09-27`
- Last Updated: `2026-09-27`
- Status: `Draft`
- Estimate: `14 focus hours (< 2 days)`

## Requirements Mapping
- Requirement IDs: FR-1, U-4, NFR-1, NFR-5, NFR-7 · HLD §8.1 · ADR-0017 D1

## Description
Record **which task created each dynamically injected task**, at the single point of truth
(`engine.py::_inject`), in a structure that survives resume. Also carry `TaskRunState.origin`
across the three places that replace `TaskRunState` wholesale.

Verified inventory at main @ 191da69:
- `_inject` is at `engine.py:~4105`. It has exactly **two** call sites: the emit settle
  (`engine.py:~2052`) and the loop-gate settle (`engine.py:~2162`).
- Router activation (`_on_router_success`, `~3107`) does **not** inject. Do not add a spawn record there.
- The reset sites are `runstate.py::prepare_resume` (`~278`), `engine.py:~1051` (worktree
  collision), and `engine.py:~1112` (missing inputs).

## Acceptance Criteria
1. **Model.** `models.py` gains `SpawnRecord{parent_task_id: str, parent_dispatch_cycle: int,
   origin: str, injected_at: str, loop_id: str | None = None, iteration: int | None = None}`,
   the constants `SPAWN_ORIGIN_INJECTED = "injected"` and `SPAWN_ORIGIN_LOOP = "loop"`, and
   `RunState.spawned_by: dict[str, SpawnRecord] = {}`. `origin` is **`str`, not `Literal`** (forward compatibility, ADR-0017 D1).
2. **Signature.** `_inject(..., route=None, *, parent_task_id: str, loop_id: str | None = None,
   iteration: int | None = None)`. `parent_task_id` is keyword-only and **required**, so `mypy src`
   fails if a call site omits it. Verify this by temporarily removing the kwarg locally and recording
   the mypy error in STATUS.
3. **Emit.** Given a workflow where static task `a` (`emit_tasks: true`) emits `d,e,f`, when the run
   completes, then `state.spawned_by[x] == SpawnRecord(parent_task_id="a",
   parent_dispatch_cycle=<a's dispatch_cycle>, origin="injected", injected_at=<fixed test clock iso>,
   loop_id=None, iteration=None)` for `x` in `d,e,f`.
4. **Nested emit.** Given `a` emits `b` (itself `emit_tasks: true`) and `b` emits `c`, then
   `spawned_by["b"].parent_task_id == "a"` and `spawned_by["c"].parent_task_id == "b"`.
5. **Loop.** Given loop `L` (body `[dev, gate]`, gate `gate`) iterating 3 times:
   - `spawned_by["dev__iter2"]` and `spawned_by["gate__iter2"]` have `parent_task_id == "gate"`,
     `origin == "loop"`, `loop_id == "L"`, and `iteration == 2`.
   - The iteration-3 clones have `parent_task_id == "gate__iter2"` and `iteration == 3`.
   - Iteration-1 body tasks have **no** record.
6. **Invariant** (reviewer). For every id in `state.injected_tasks`,
   `state.spawned_by[id].origin == state.tasks[id].origin`. This holds after a fresh run **and**
   after a failed-then-resumed run.
7. **Resume preserves.**
   - Given a run where injected task `e` failed, when it is resumed via `prepare_resume` + `run`,
     then `spawned_by` is byte-identical to before resume, and `state.tasks["e"].origin == "injected"`
     (previously this was reset to `"static"`).
   - The same holds for `engine.py:~1051` and `~1112`: a failed task keeps its `origin`, as
     unit-tested by driving each path.
8. **Duplicate id.** Given an emit batch `[x, y, x_dup]` where `x_dup.id` collides with an existing
   id, then `InjectionError` is raised, and `spawned_by` has **no** entry for the colliding id.
   Entries for earlier specs in the batch match today's partial-injection behavior. This is pinned
   by a test; E-Grpp0X may later tighten it.
9. **`max_parallel`.** The emit test in AC-3 also passes with `max_parallel: 4`, producing the same
   `spawned_by` (settle runs on the main thread, per ADR-0007).
10. **Backward and forward compatibility (NFR-1).**
    - A pre-epic `state.json` fixture (no `spawned_by`) loads with `spawned_by == {}`.
    - A new-format `state.json` containing `spawned_by` loads into a copy of the pre-epic
      `RunState` model without error (pins `extra="ignore"`; no `model_config` exists in `models.py`
      today).
    - A record with `origin: "future-kind"` loads without error.
11. **No behavior change.** Grep confirms that `TaskRunState.origin` has no behavioral reader outside
    `ui/runs.py` (re-verify A-6 and record the grep in STATUS). The full `pytest -q`, `ruff check .`,
    `ruff format --check .`, and `mypy src` pass with no new failures versus baseline.
12. `status.json` output is unchanged for an identical run (snapshot-compare test).

## Risks
- Concurrent edit of `_inject` by `E-Grpp0X` (R-1). Keep the `spawned_by` write in the **same
  per-spec step** as `state.injected_tasks.append`. Coordinate via the epic STATUS.
- Carrying `origin` could surprise a hidden reader. Mitigated by AC-11.
- Do **not** carry `route` across resets. That is finding F-2, with separate behavioral reach.

## Dependencies
- None (first task). Blocks `T-M4qboy-run-graph-builder`.

## Pseudocode / Algorithm
```text
# models.py
SPAWN_ORIGIN_INJECTED = "injected"; SPAWN_ORIGIN_LOOP = "loop"
class SpawnRecord(BaseModel): parent_task_id: str; parent_dispatch_cycle: int; origin: str
                              injected_at: str; loop_id: str | None = None; iteration: int | None = None
class RunState(...): spawned_by: dict[str, SpawnRecord] = {}

# engine.py::_inject
def _inject(self, new, workflow, state, origin, route=None, *, parent_task_id, loop_id=None, iteration=None):
    assert (origin == SPAWN_ORIGIN_LOOP) == (loop_id is not None and iteration is not None)
    now_iso = self._clock().isoformat()
    parent_cycle = state.tasks[parent_task_id].dispatch_cycle
    existing = {t.id for t in workflow.tasks}
    for spec in new:
        if spec.id in existing: raise InjectionError(...)          # unchanged
        workflow.tasks.append(spec); existing.add(spec.id)
        state.injected_tasks.append(spec)
        state.tasks[spec.id] = TaskRunState(origin=origin, route=route)
        state.spawned_by[spec.id] = SpawnRecord(parent_task_id=parent_task_id, parent_dispatch_cycle=parent_cycle,
                                                origin=origin, injected_at=now_iso, loop_id=loop_id, iteration=iteration)

# call sites
~2052: self._inject(new_specs, workflow, state, origin="injected", route=ts.route, parent_task_id=tid)
~2162: self._inject(clones, workflow, state, origin="loop", route=ts.route, parent_task_id=tid,
                    loop_id=loop.id, iteration=next_iter)

# resets: add origin=<previous origin>
runstate.py ~278:  TaskRunState(status="pending", dispatch_cycle=..., cumulative_*=..., origin=ts.origin)
engine.py ~1051:   TaskRunState(status="failed", dispatch_cycle=ts_pre.dispatch_cycle, origin=ts_pre.origin)
engine.py ~1112:   prev = state.tasks.get(tid); TaskRunState(status="failed", origin=prev.origin if prev else "static")
```

## Schemas / Interface Notes
- Interface / API: private `_inject` signature (HLD §14.1). No public or CLI change.
- Spec / data schema: `state.json` adds `spawned_by` (HLD §13.2). No workflow spec change.
- Triggers / events: N/A
- Artifacts: `state.json` only.

## Handoff Boundary
- Upstream: none.
- Downstream: `T-M4qboy` reads `state.spawned_by`. **Gate G1**: reviewer and tester sign off before
  T-M4qboy starts building on it.

## Artifacts
- Docs/comments: `meta/tickets/E-k3AMEr-run-graph-canvas/T-AZzgT8-spawn-provenance/`
- Large outputs: N/A
