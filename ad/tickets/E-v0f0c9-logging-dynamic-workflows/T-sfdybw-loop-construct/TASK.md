# TASK: T-sfdybw-loop-construct

## Metadata
- Task ID: `T-sfdybw-loop-construct`
- Epic ID: `E-v0f0c9-logging-dynamic-workflows`
- Owner: developer
- Created: 2026-06-18
- Last Updated: 2026-06-18
- Status: Done
- Estimate: `< 3 days`

## Requirements Mapping
- Requirement IDs: FR-9, FR-10, FR-11, FR-12, NFR-1, NFR-2, NFR-3, NFR-5, NFR-6

## Description
Add a `LoopSpec` workflow-level construct that repeats an ordered body of tasks
as a unit until a gate task's JSON verdict says stop or `max_iterations` is
reached. Implemented on top of `T-17av6o`'s injection mechanism: each additional
iteration clones the body with a deterministic `__iterN` suffix (rewriting
intra-body `depends_on` and chaining iteration N+1 after iteration N), then
injects it. Covers both iterative dev↔review loops (2b) and post-development
review/audit cycles (2c) — same construct. Static tasks may declare
`depends_on: [<loop_id>]` to run after the loop's final iteration.

## Acceptance Criteria
1. Given a `LoopSpec` whose gate verdict stays `continue=true`, when run, then the
   body repeats up to `max_iterations` times with ids suffixed `__iter2`, `__iter3`, …
   (iteration 1 = un-suffixed authored body) and then stops.
2. Given the gate verdict becomes `continue=false` at iteration K (K ≤ max), when
   read, then no iteration K+1 is materialized and the run proceeds.
3. Given `max_iterations` is reached while the gate still says continue, when
   evaluated, then the loop stops cleanly (no error) and downstream tasks run.
4. Given a missing gate file, an absent `gate_field`, or a non-boolean value,
   when read, then the run fails with a structured error.
5. Given iteration cloning, when produced, then intra-body `depends_on` are
   rewritten to the suffixed ids, iteration N+1's first task depends on iteration
   N's last task, and the per-iteration gate path is suffixed so each iteration
   writes its own verdict.
6. Given a static task `depends_on: [<loop_id>]`, when the loop completes, then
   that task runs after the final materialized iteration's last task.
7. Given an interrupted run mid-loop, when resumed, then `RunState.loop_iterations`
   prevents re-cloning already-materialized iterations (idempotent).
8. Given a fixed clock + FakeExecutor, when the same loop spec runs twice, then
   the materialized graph (ids + edges) is identical (NFR-2).
9. `LoopSpec` + `WorkflowSpec.loops` added to `models.py` AND `specs/workflow.schema.json`;
   cross-validation enforces body/gate constraints (HLD §3.2). No nested loops.
10. `ruff`, `mypy`, `pytest` pass.

## Risks
- R2 loop-induced cycles (body↔non-body edges) → re-run cycle detection after each clone; AC handled by T-17av6o's rebuild.
- `depends_on:[loop_id]` resolution is a special edge case → resolve loop-id deps to the final iteration's last task during DAG build; covered by AC-6.
- Suffix collisions if author already uses `__iterN` ids → validation rejects authored ids containing `__iter`.

## Dependencies
- `T-17av6o` (injection mechanism, re-entrant engine loop, `read_gate` sibling of `read_task_manifest`).

## Pseudocode / Algorithm
```text
# models.py
class LoopSpec(BaseModel):
  id: str; body: list[str]; gate_task_id: str
  gate_output_path: str; gate_field: str = "continue"; max_iterations: int = 5
WorkflowSpec += loops: list[LoopSpec] = []

# spec.py cross_validate (per loop)
ensure body ⊆ task_ids; gate_task_id ∈ body; max_iterations >= 1
ensure no task id in two loop bodies; gate task is not emit_tasks
ensure no authored id/loop body id contains "__iter"

# artifacts.py
def read_gate(store, path, field) -> bool:
  data = json.load(resolve(path))
  if field not in data or not isinstance(data[field], bool):
    raise ValueError(f"gate field {field!r} missing or non-bool")
  return data[field]

# engine.py — after a gate task terminal-success
def maybe_expand_loop(loop, state, store, workflow):
  cur = state.loop_iterations.get(loop.id, 1)
  if cur >= loop.max_iterations: return False
  if not read_gate(store, gate_path_for(loop, cur), loop.gate_field): return False
  nxt = cur + 1
  clones = clone_body(loop, nxt)          # see below
  _inject(clones, workflow, state, origin="loop")
  state.loop_iterations[loop.id] = nxt
  return True

def clone_body(loop, n):
  suffix = f"__iter{n}"
  prev_last = (loop.body[-1] + (f"__iter{n-1}" if n>2 else "")) if n>1 else loop.body[-1]
  clones = []
  for i, tid in enumerate(loop.body):
    base = workflow.task(tid)
    c = base.model_copy(deep=True)
    c.id = tid + suffix
    c.depends_on = [d+suffix if d in loop.body else d for d in base.depends_on]
    if i == 0: c.depends_on.append(prev_last)         # chain iterations
    if tid == loop.gate_task_id:
      c.gate-related output path suffixed (rewrite gate_output_path artifact path with suffix)
    clones.append(c)
  return clones

# DAG build: a depends_on entry equal to a loop.id resolves to the last task of
#   the highest materialized iteration of that loop.
```

## Schemas / Interface Notes
- Interface / API: `models.LoopSpec`, `WorkflowSpec.loops`, `artifacts.read_gate(store, path, field) -> bool`, engine `maybe_expand_loop` / `clone_body`.
- Spec / data schema (workflow.schema.json):
  ```json
  "loops": { "type": "array", "default": [], "items": { "$ref": "#/$defs/loop" } },
  "$defs.loop": {
    "type":"object","additionalProperties":false,
    "required":["id","body","gate_task_id","gate_output_path"],
    "properties":{
      "id":{"type":"string","pattern":"^[a-z0-9][a-z0-9-_]*$"},
      "body":{"type":"array","minItems":1,"items":{"type":"string"}},
      "gate_task_id":{"type":"string"},
      "gate_output_path":{"type":"string"},
      "gate_field":{"type":"string","default":"continue"},
      "max_iterations":{"type":"integer","minimum":1,"default":5}
    }
  }
  ```
  Gate control-file schema: `{"<gate_field>": true|false, ...}`.
- Triggers / events: N/A
- Artifacts: reads gate control file per iteration; writes cloned tasks via RunState.

## Handoff Boundary
- Upstream: `T-17av6o` (injection + re-entrant loop + control-read helper pattern).
- Downstream: `T-5isej3` (tests for loop + post-dev review cycles).

## Artifacts
- Code: `src/agent_orchestrator/models.py`, `engine.py`, `artifacts.py`, `spec.py`, `specs/workflow.schema.json`.
