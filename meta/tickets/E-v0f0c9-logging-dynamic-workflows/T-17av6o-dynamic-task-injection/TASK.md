# TASK: T-17av6o-dynamic-task-injection

## Metadata
- Task ID: `T-17av6o-dynamic-task-injection`
- Epic ID: `E-v0f0c9-logging-dynamic-workflows`
- Owner: developer
- Created: 2026-06-18
- Last Updated: 2026-06-18
- Status: Done
- Estimate: `< 3 days`

## Requirements Mapping
- Requirement IDs: FR-7, FR-8, FR-12, NFR-1, NFR-2, NFR-3, NFR-5, NFR-6

## Description
Implement run-time task discovery. A task with `emit_tasks: true` writes a
`task_manifest_path` JSON file listing new `TaskSpec` objects. On that task's
success, the engine reads + validates the manifest (control read), merges the
new tasks into the live `WorkflowSpec`, rebuilds the DAG (re-running cycle
detection), recomputes the topo order, and continues. Injected tasks are
persisted in `RunState.injected_tasks` so resume reconstructs the expanded
graph without re-running the emitter. This task also performs the **engine
main-loop refactor** (from a one-shot `for tid in order` to a re-entrant,
re-expandable loop) that `T-sfdybw` builds on.

## Acceptance Criteria
1. Given a task `emit_tasks=true` with a valid `task_manifest_path`, when it
   succeeds, then the engine injects the listed tasks, rebuilds the DAG, and
   runs them to completion within the same run.
2. Given a manifest whose injected task id collides with an existing task id,
   when read, then the emitter task fails with a structured error naming the
   collision (NFR-6) and the run fails.
3. Given injected tasks that introduce a cycle, when the DAG is rebuilt, then a
   `CycleError` naming the offending node ids is raised and the run fails.
4. Given a malformed manifest (not `{"tasks":[...]}`, or an invalid `TaskSpec`),
   when read, then the emitter task fails with `ValueError`-derived structured error.
5. Given a run that injected tasks and was interrupted, when resumed, then
   `prepare_resume` merges `RunState.injected_tasks` back into the workflow, the
   emitter is skipped (already succeeded), and only incomplete injected tasks run.
6. Given a fixed clock + FakeExecutor, when the same spec+manifest run twice,
   then the expanded graph and topo order are identical (NFR-2).
7. New fields `emit_tasks`, `task_manifest_path` added to `models.TaskSpec` AND
   `specs/workflow.schema.json` (`additionalProperties:false` honored); cross-validation
   enforces `emit_tasks ⇔ task_manifest_path set`.
8. `ruff`, `mypy`, `pytest` pass.

## Risks
- R1 resume correctness → persist full injected specs; rebuild before resume; covered by AC-5.
- R2 cycles from injection → re-run cycle detection every injection; AC-3.
- Infinite expansion (emitter emits another emitter that re-emits same ids) → duplicate-id rejection + ids must be new; bounded.

## Dependencies
- Conceptually independent of Area-1, but lands the engine-loop refactor; coordinate `run.log` attach/detach with `T-pd2vu2`.

## Pseudocode / Algorithm
```text
# models.py
TaskSpec += emit_tasks: bool = False
TaskSpec += task_manifest_path: str | None = None
RunState += injected_tasks: list[TaskSpec] = []
TaskRunState += origin: Literal["static","injected","loop"] = "static"

# spec.py cross_validate (per task)
if task.emit_tasks and not task.task_manifest_path: raise SpecValidationError(...)
if task.task_manifest_path and not task.emit_tasks: raise SpecValidationError(...)

# artifacts.py
def read_task_manifest(store, path) -> list[TaskSpec]:
  data = json.load(resolve(path))
  if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
    raise ValueError('manifest must be {"tasks":[...]}')
  return [TaskSpec(**t) for t in data["tasks"]]   # pydantic validation errors -> ValueError

# engine.py
def _inject(new_specs, workflow, state, origin):
  existing = {t.id for t in workflow.tasks}
  for s in new_specs:
    if s.id in existing: raise InjectionError(f"duplicate task id {s.id}")
    workflow.tasks.append(s); state.injected_tasks.append(s)
    state.tasks[s.id] = TaskRunState(origin=origin)

def run(...):  # refactor to re-entrant loop (see HLD §6.1)
  if run_state and run_state.injected_tasks:
    merge injected_tasks into workflow.tasks   # resume
  graph = build_dag(workflow); order = graph.topological_order()
  done = {tid for tid,ts in state.tasks if terminal-success}
  cursor = 0
  while cursor < len(order):
    tid = order[cursor]; cursor += 1
    if tid in done: continue
    ... existing per-task processing ...
    if task.emit_tasks and ts.status == "succeeded":
      new = read_task_manifest(store, task.task_manifest_path)
      _inject(new, workflow, state, "injected")
      graph = build_dag(workflow); order = graph.topological_order()
      cursor = next index in order whose tid not in done
    save(state)

# runstate.py prepare_resume: merge injected_tasks before build_dag (engine handles merge),
#   keep emitter 'succeeded' if outputs present.
```

## Schemas / Interface Notes
- Interface / API:
  - `TaskSpec.emit_tasks: bool`, `TaskSpec.task_manifest_path: str | None`
  - `RunState.injected_tasks: list[TaskSpec]`, `TaskRunState.origin`
  - `artifacts.read_task_manifest(store, path) -> list[TaskSpec]`
  - new error `InjectionError(OrchestratorError)` in `errors.py`
- Spec / data schema (workflow.schema.json `$defs.task` additions):
  ```json
  "emit_tasks": { "type": "boolean", "default": false },
  "task_manifest_path": { "type": "string",
    "description": "PATH to a JSON file {\"tasks\":[<TaskSpec>...]} written by an emit_tasks task; engine injects these at run time." }
  ```
  Task manifest control-file schema: `{"tasks": [ { ...TaskSpec fields... } ]}`.
- Triggers / events: N/A
- Artifacts: reads control file at `task_manifest_path`; writes nothing new besides RunState.

## Handoff Boundary
- Upstream: `T-pd2vu2` (logger wiring in refactored `run()`).
- Downstream: `T-sfdybw` (loops reuse `_inject` + re-entrant loop), `T-5isej3` (tests).

## Artifacts
- Code: `src/agent_orchestrator/models.py`, `engine.py`, `artifacts.py`, `spec.py`, `runstate.py`, `errors.py`, `specs/workflow.schema.json`.
