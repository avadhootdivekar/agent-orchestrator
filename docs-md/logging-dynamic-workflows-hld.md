# HLD — Structured Logging & Traceability + Dynamic Workflow Support

- Epic: `E-v0f0c9-logging-dynamic-workflows`
- Status: Implemented (all tasks done; 207 tests, 89% coverage)
- Author: architect
- Date: 2026-06-18

This document is the High- and Low-Level Design for two feature areas on the
agent-orchestrator engine:

1. **Structured Logging & Traceability** — JSON logs, per-run log file, captured
   agent output artifacts, and a live `status.json` snapshot.
2. **Dynamic Workflow Support** — run-time task discovery (`emit_tasks`),
   iterative dev↔review loops, and post-development review/audit cycles
   (`LoopSpec`).

It complements the existing engine described in `CLAUDE.md` and reuses, rather
than replaces, the `output_manifest` / `read_manifest` control-file pattern.

---

## 1. Context & invariants we must not break

| Invariant | Source | Impact on this design |
|-----------|--------|-----------------------|
| NFR-1: engine never reads payload artifact content | `engine.py`, `artifacts.py` | New control reads (`task_manifest`, loop `gate`) are *machine-written control files*, read only via dedicated `artifacts.read_*` helpers — same exception class as `read_manifest`. |
| DAG-first, cycles rejected | `dag.py` | After each dynamic injection we re-run `build_dag()` → `topological_order()` (cycle detection). |
| Deterministic / idempotent / resumable | `engine.py`, `runstate.py` | Injected tasks + loop clones are persisted in `RunState`; iteration suffixes are deterministic (`__iterN`); fixed clock keeps timestamps reproducible. |
| Atomic run-state persistence | `runstate.py` | `status.json` is written through the same atomic write-then-rename path, derived purely from `RunState`. |
| Spec is `additionalProperties:false` | `specs/workflow.schema.json` | Every new field is added to the JSON Schema (NFR-5). |
| Pluggable, no domain logic in core | `CLAUDE.md` | Loop/dynamic constructs are generic; the *gate* decision lives in agent-written control files, not engine code. |

---

## 2. Architecture diagram (ASCII)

```
                          ┌─────────────────────────────────────────────────┐
                          │                  CLI (cli.py)                    │
                          │   ao run / ao resume / ao status                 │
                          └───────────────┬─────────────────────────────────┘
                                          │
                          ┌───────────────▼─────────────────────────────────┐
                          │            Orchestrator.run() (engine.py)        │
                          │                                                  │
   ┌──────────────────┐   │  build_dag ──> topo_order ──┐                    │
   │ logging_setup.py │◄──┤                              │                   │
   │  JSONFormatter   │   │   ┌──────────────────────────▼───────────────┐   │
   │  per-run handler │   │   │  for tid in order:  (re-expandable loop)  │   │
   └────────┬─────────┘   │   │   skip? inputs? execute(retry) outputs?   │   │
            │             │   │   ├─ emit_tasks? -> inject + rebuild DAG   │   │
   run.log  │ (JSON       │   │   ├─ loop gate?  -> clone iter + inject    │   │
 (JSON lines)│ lines)     │   │   └─ write status.json after each change   │   │
            ▼             │   └───────────────┬──────────────────┬─────────┘   │
 .orchestrator/runs/      │                   │                  │             │
   <run_id>/run.log       │            ┌──────▼──────┐   ┌───────▼────────┐    │
                          │            │ ArtifactStore│  │ RunStateStore  │    │
                          │            │ resolve/exist│  │ save/load      │    │
                          │            │ read_manifest│  │ +write_status  │    │
                          │            │ read_tasks   │  └───────┬────────┘    │
                          │            │ read_gate    │          │             │
                          │            └──────┬───────┘   state.json           │
                          └───────────────────┼──────────────────┼────────────┘
                                              │                  │
                          ┌───────────────────▼──────────────────▼────────────┐
                          │              Executor (DispatchExecutor)           │
                          │   ClaudeCliExecutor.execute(ctx)                   │
                          │     subprocess.run(...) -> capture stdout/stderr   │
                          │     write -> .orchestrator/runs/<run>/<task>/      │
                          │                 stdout.txt | stderr.txt            │
                          │     TaskResult.output_artifact_path = <dir/file>   │
                          └────────────────────────────────────────────────────┘

Run directory layout:
  .orchestrator/runs/<run_id>/
    ├── state.json          (RunState — authoritative)
    ├── status.json         (snapshot derived from RunState, refreshed each transition)
    ├── run.log             (structured JSON lines for the whole run)
    └── <task_id>/
        ├── stdout.txt
        └── stderr.txt
```

---

## 3. Schema extensions

### 3.1 `TaskSpec` (models.py + workflow.schema.json `$defs.task`)

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `emit_tasks` | `bool` | `False` | Marks an emitter task (2a). |
| `task_manifest_path` | `str \| None` | `None` | Path the emitter writes; JSON list of `TaskSpec` objects. Required when `emit_tasks=True`. |

Cross-validation: if `emit_tasks` is `True`, `task_manifest_path` must be set
(else `SpecValidationError`). If `emit_tasks` is `False`, `task_manifest_path`
must be unset.

### 3.2 New `LoopSpec` model + `WorkflowSpec.loops` (models.py + schema)

```python
class LoopSpec(BaseModel):
    id: str                       # loop id; namespaces cloned tasks
    body: list[str]               # ordered task ids forming one iteration
    gate_task_id: str             # body task whose output decides continue/stop
    gate_output_path: str         # path to the gate's JSON control file
    gate_field: str = "continue"  # boolean field read from that JSON
    max_iterations: int = 5       # hard cap (>=1)
```

`WorkflowSpec` gains `loops: list[LoopSpec] = []`.

Cross-validation (enforced at spec LOAD time by `spec.cross_validate`):
- every id in `body` exists in `tasks`; `gate_task_id ∈ body`;
- `gate_task_id` must not be `emit_tasks` (a task is either an emitter or a gate);
- `max_iterations >= 1`;
- a task id appears in at most one loop body (no overlapping loops);
- no nested loops in this epic (a body task id may not equal a loop id);
- **`__iter` is a RESERVED suffix** — any authored task id (or loop body id) containing `__iter` is rejected at spec load time with `SpecValidationError`. This is checked in `spec.cross_validate` (constant `_ITER_SUFFIX_MARKER = "__iter"`) before the workflow enters the engine. Iteration-suffixed ids (`<task_id>__iterN`) are exclusively created by the engine's `_clone_body` at run time. Operators writing specs must not use `__iter` in their task ids.

### 3.3 `TaskResult` (models.py)

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `output_artifact_path` | `str \| None` | `None` | Path (file or dir) where captured stdout/stderr live. Engine records it in `RunState`; never reads its content. |

### 3.4 `TaskRunState` (models.py)

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `output_artifact_path` | `str \| None` | `None` | Mirror of `TaskResult.output_artifact_path` for traceability. |
| `origin` | `Literal["static","injected","loop"]` | `"static"` | Provenance of a task in the run. |

### 3.5 `RunState` (models.py)

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `injected_tasks` | `list[TaskSpec]` | `[]` | Full specs of every task injected at run time (emit + loop clones), in injection order. Replayed on resume to rebuild the expanded workflow. |
| `loop_iterations` | `dict[str, int]` | `{}` | `loop_id -> highest iteration number already materialized`. Makes loop expansion idempotent on resume. |

### 3.6 `TaskContext` (models.py)

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `output_dir` | `str` | `""` | Resolved `.orchestrator/runs/<run_id>/<task_id>/`; executor writes captured output here. Paths only — NFR-1 safe. |
| `task_manifest_path` | `str \| None` | `None` | Resolved path for an `emit_tasks` task to write its task manifest (Area 2). Passed through to the executor so it knows where to write the control file. None when `task.emit_tasks` is False. Paths only — NFR-1 safe. |
| `gate_output_path` | `str \| None` | `None` | Resolved, iteration-suffixed path for a loop gate task to write its verdict (Area 2). Set by the engine in `_run_with_retries` from `_gate_path_for_iter(loop, cur_iter)`. None when the task is not a gate task. Paths only — NFR-1 safe. |

Cross-refs: §3.1 (`TaskSpec.task_manifest_path`) is the authored spec path; the engine resolves it through `ArtifactStore` and passes the result here (§6.1). §3.2 (`LoopSpec.gate_output_path`) likewise — the engine calls `_gate_path_for_iter` to suffix it per iteration before passing it into `TaskContext`.

---

## 4. Data flow per feature

### 4.1 Structured logging (FR-2, FR-3)

```
Orchestrator.run() start
  -> logging_setup.attach_run_handler(run_id, run_dir)
       adds a FileHandler(run.log) + StreamHandler(console), both using JSONFormatter
  -> every logger.info/warning/error in engine + executors emits:
       {"ts": "...", "level": "INFO", "logger": "...", "run_id": "...",
        "task_id": "...", "event": "task.start", "msg": "...", ...extra}
  -> Orchestrator.run() end (finally): detach_run_handler(run_id)  # no handler leak
```
`run_id`/`task_id` are injected via a `logging.LoggerAdapter` / contextual
`extra=` so callers don't repeat them. The handler is attached/detached per run
so concurrent or sequential runs don't cross-contaminate log files.

### 4.2 Agent output capture (FR-4, FR-5)

```
engine builds TaskContext with output_dir = runs/<run_id>/<task_id>/
ClaudeCliExecutor.execute(ctx):
   res = subprocess.run(argv, capture_output=True, text=True)
   mkdir -p ctx.output_dir
   write ctx.output_dir/stdout.txt  <- res.stdout
   write ctx.output_dir/stderr.txt  <- res.stderr
   TaskResult.output_artifact_path = ctx.output_dir
engine records ts.output_artifact_path = result.output_artifact_path
   (path only; engine never reads the files)
```
A downstream task can declare `inputs: [".orchestrator/runs/<run_id>/<task>/stdout.txt"]`
or receive the path via `dynamic_input_paths`, so later agents/auditors consume it.

### 4.3 Status snapshot (FR-1, FR-6)

```
RunStateStore.save(state):              # existing atomic write of state.json
   ...write state.json...
   write_status(state)                  # NEW: derive + atomic-write status.json
write_status: build a flat summary dict from RunState and write status.json
   {"run_id","workflow_id","status","updated_at",
    "counts": {"succeeded":n,"failed":n,"running":n,...},
    "current_task": "<first running/pending>",
    "tasks": [{"id","status","attempts","output_artifact_path","origin"} ...]}
ao status <run_id>: read status.json (fallback state.json) -> print table
```
Because `status.json` is written inside `save()`, it can never drift from
`state.json` (R4 mitigation).

### 4.4 Dynamic task injection — `emit_tasks` (FR-7, FR-8)

```
Engine main loop reaches emitter task E (emit_tasks=True):
  execute E with retries
  on success:
    new_specs = artifacts.read_task_manifest(store, E.task_manifest_path)  # control read
    validate each: id pattern, agent exists, depends_on resolvable-after-merge
    namespace check: reject ids already present (NFR-6)
    workflow.tasks += new_specs
    state.injected_tasks += new_specs
    for each new spec: state.tasks[id] = TaskRunState(origin="injected")
    graph = build_dag(workflow)        # re-run cycle detection (R2)
    order = graph.topological_order()  # recompute full order
    rebuild the iteration cursor: continue from tasks not yet terminal
  persist state (+status.json)
```
Engine refactor: the `for tid in order` loop becomes a re-entrant
`while` over a *recomputable* order with a `done: set[str]` of terminal task ids,
so re-expansion mid-run is natural. See LLD §6.1.

**Resume:** `prepare_resume` merges `state.injected_tasks` back into the
workflow *before* `build_dag`, so the emitter is skipped (already succeeded) yet
its children exist. (FR-8)

### 4.5 Loops — `LoopSpec` (FR-9, FR-10, FR-11)

A loop is implemented entirely on top of the injection mechanism (§4.4) — no
separate execution path:

```
When the engine has run all body tasks of iteration K (gate_task_id terminal):
  verdict = artifacts.read_gate(store, gate_output_path, gate_field)  # control read
  if verdict is True and K < max_iterations:
      iter = K + 1
      clones = _clone_body(loop, iter, workflow)  # ids suffixed __iter{iter}
        - rewrite intra-body depends_on to suffixed ids
        - first task of iter depends on last task of iter K (chain)
        - inputs=[], outputs=[], output_manifest=None cleared on every clone (see below)
        - emit_tasks=False, task_manifest_path=None cleared on clones
        - gate_output_path lives on LoopSpec, not TaskSpec; suffixing handled by
          _gate_path_for_iter when engine reads/writes the verdict
      inject clones (same path as §4.4), record loop_iterations[loop.id] = iter
      rebuild DAG + order via build_dag (which calls _resolve_loop_dep for any
        downstream tasks that depend on the loop id — see §6.1)
  else:
      loop complete; tasks with depends_on:[loop.id] are unblocked
        (semantic: "depends on the final materialized iteration's last task" —
         RESOLVED — see §9)
```
Iteration 1 is the static body as authored. Suffixing is deterministic, so
re-runs reproduce identical ids (NFR-2). `loop_iterations` makes re-expansion
idempotent on resume (FR-12).

**Why `_clone_body` clears `inputs` and `outputs`:** Loop-body clone tasks reuse
the same artifact paths as the original body (each iteration overwrites the same
working files). If the clone kept the original `inputs`/`outputs`, `build_dag`'s
inferred-edge logic would see cloned task outputs matching subsequent body-task
inputs and create inferred edges from a clone back to tasks that already ran
(e.g., `develop__iter2` outputs `impl.md` → inferred edge to `review` which
already succeeded in iteration 1). Those inferred edges do not represent real
ordering intent; they would also create false `CycleError`s because the
dependency chain runs back into tasks that are already terminal. Clearing
`inputs` and `outputs` on clones removes the inferred-edge ambiguity. Execution
ordering across iterations is fully handled by the rewritten `depends_on` chain
(`_clone_body` appends `prev_last` to the first task of each new iteration).

2c (post-dev review/audit) is the same construct: `body = [review, remediate]`,
`gate_task_id = review`, gate verdict `continue=true` when issues remain.

---

## 5. Key design decisions & alternatives (ADRs)

### ADR-001 — Reuse injection mechanism for loops instead of a separate LoopController
- Context: 2b/2c need repetition; 2a needs injection.
- Options: (a) standalone loop executor with its own state machine; (b) express loops as repeated dynamic injection.
- Decision: (b). A loop iteration = "inject the next cloned body".
- Reason: one code path for graph mutation, one place for cycle detection and resume, less surface area.
- Consequences: loops cannot do things injection can't (e.g., true parallel fan-out) — acceptable for this epic; nested loops deferred.

### ADR-002 — `status.json` derived inside `RunStateStore.save()`
- Options: (a) separate writer the engine calls; (b) derive inside `save()`.
- Decision: (b).
- Reason: guarantees `status.json` and `state.json` never diverge (R4); single atomic-write convention reused.
- Consequences: `RunStateStore` gains a tiny projection responsibility; acceptable (it already owns the run dir).

### ADR-003 — stdlib `logging` + custom `JSONFormatter`, attached per run
- Options: (a) `python-json-logger`/`structlog`; (b) stdlib + ~30-line `JSONFormatter`.
- Decision: (b).
- Reason: NFR-4 (no new dep); full control over fields; per-run handler attach/detach avoids global-state leakage.
- Consequences: must manage handler lifecycle in a `try/finally`.

### ADR-004 — Control-file reads stay on an explicit allow-list
- Context: NFR-1 forbids reading payloads.
- Decision: only `read_manifest`, `read_task_manifest`, `read_gate` may read content, all in `artifacts.py`, all on machine-written JSON control files.
- Reason: keeps the NFR-1 boundary auditable in one module.
- Consequences: any future control read must be added here deliberately and reviewed.

### ADR-005 — Engine loop becomes re-entrant `while order` with a `done` set
- Context: a `for tid in topological_order()` snapshot can't grow mid-run.
- Decision: drive execution from a recomputable order + `done: set`, re-deriving order after each injection.
- Reason: makes 2a/2b/2c natural without a parallel scheduler.
- Consequences: must guard against infinite expansion — bounded by `max_iterations` (loops) and "emitters can't emit emitters that re-emit" (validation rejects duplicate ids).

### ADR-006 — Deterministic iteration suffix `__iterN`
- Options: random ids, monotonically increasing counters, semantic suffix.
- Decision: `<task_id>__iter<N>`.
- Reason: deterministic, human-readable in logs/status, collision-checkable.
- Consequences: original (iteration-1) ids stay un-suffixed; clarified in docs.

### ADR-007 — `_clone_body` clears `inputs`/`outputs` on iteration clones
- Context: Loop-body tasks are cloned with `__iterN` suffixed ids. Body tasks
  reference the same artifact paths as the original (each iteration overwrites
  the working files). The DAG builder infers dependency edges when task A's
  output path matches task B's input path (with a warning). If a clone kept the
  original `inputs`/`outputs`, the inferred-edge logic would create edges from
  the clone (e.g., `develop__iter2`) back to tasks that already ran in prior
  iterations (e.g., `review`), producing spurious `CycleError`s on the next
  `build_dag` call.
- Options: (a) suppress inferred-edge logic for loop clones; (b) clear
  `inputs`/`outputs` on clones so no inferred edges are created.
- Decision: (b) — clear `inputs`, `outputs`, and `output_manifest` on clones.
- Reason: simpler invariant: cloned tasks have no artifact-level ordering; all
  ordering is expressed purely via the rewritten `depends_on` chain. No special
  case needed in `build_dag`; the inferred-edge warning path is untouched.
- Consequences: clone tasks have empty `inputs`/`outputs` in `RunState` (which
  is accurate — they share the body's path space and write to the same files).
  Downstream consumers that look up clone task paths via `state.tasks[id].inputs`
  will see `[]`; they should use `depends_on` chaining instead.

---

## 6. LLD highlights (full per-task LLD lives in each `TASK.md`)

### 6.1 Engine main-loop refactor (T-17av6o) — `_resolve_loop_dep` in `build_dag`

`build_dag` (not solely the engine) is responsible for resolving
`depends_on: [<loop_id>]` to the actual task node the DAG should use. The helper
`_resolve_loop_dep(loop_id, workflow)` lives in `dag.py` and is called inside
`build_dag` whenever a `dep` in a task's `depends_on` matches a known loop id:

```python
resolved_dep = _resolve_loop_dep(dep, workflow) if dep in loop_ids else dep
```

`_resolve_loop_dep` scans the live `workflow.tasks` list for the highest
`__iterN` suffix on `body[-1]`, returning `body[-1]__iter{max_iter}` (or
`body[-1]` if only iteration 1 exists). This means `build_dag` is always called
on the current snapshot of the workflow (after injection), and the loop-dep
resolution is automatically updated as new iterations are injected. The engine
calls `build_dag` + `recompute_order` after every injection event.

### 6.1 Engine main-loop pseudocode (T-17av6o)

```
FUNCTION run(workflow, reposets, agents, run_state=None):
  run_dir = runs/<run_id>
  attach_run_handler(run_id, run_dir/run.log)         # T-pd2vu2
  TRY:
    IF run_state and run_state.injected_tasks:
      merge run_state.injected_tasks INTO workflow.tasks   # resume expansion
    graph = build_dag(workflow); order = graph.topological_order()
    state = run_state or new_run(workflow); save(state)    # save() also writes status.json
    done = set(tid for tid,ts in state.tasks if ts.status terminal)
    cursor = 0
    WHILE cursor < len(order):
      tid = order[cursor]; cursor += 1
      IF tid in done: CONTINUE
      IF cancel(): state.status="cancelled"; BREAK
      task = workflow.task(tid)
      ... existing skip / inputs / execute-with-retries / outputs / output_manifest ...
      record ts.output_artifact_path = result.output_artifact_path   # T-f0xkdw
      done.add(tid) IF ts.status terminal-success
      IF ts.status not in (succeeded, skipped): state.status="failed"; BREAK

      # ---- dynamic expansion hooks ----
      IF task.emit_tasks AND ts.status == succeeded:
        new = read_task_manifest(store, task.task_manifest_path)
        inject(new, workflow, state, origin="injected")
        graph = build_dag(workflow); order = recompute_order(graph, done)
        cursor = first_index_not_done(order, done)
      loop = loop_for_gate_task(workflow, tid)
      IF loop AND ts.status == succeeded:
        IF should_continue(loop, store) AND iter(loop) < loop.max_iterations:
          clones = clone_body(loop, next_iter)
          inject(clones, workflow, state, origin="loop")
          state.loop_iterations[loop.id] = next_iter
          graph = build_dag(workflow); order = recompute_order(graph, done)
          cursor = first_index_not_done(order, done)
      save(state)
    ELSE: state.status = "succeeded" if still running
  FINALLY:
    detach_run_handler(run_id); save(state)
  RETURN state
```

### 6.2 Control-read helpers (artifacts.py — T-17av6o / T-sfdybw)

```
read_task_manifest(store, path) -> list[TaskSpec]:
   data = json.load(resolve(path))                 # {"tasks":[ {TaskSpec}, ... ]}
   raise ValueError on missing/invalid/wrong-shape
   return [TaskSpec(**t) for t in data["tasks"]]

read_gate(store, path, field) -> bool:
   data = json.load(resolve(path))                 # {"<field>": true/false, ...}
   raise ValueError if field missing or non-bool
   return bool(data[field])
```

### 6.3 Edge cases (must be covered by tests)
- Empty/malformed `task_manifest` (not a dict, no `tasks`, bad TaskSpec) → emitter task fails with structured error.
- Injected task id collides with an existing id → reject (NFR-6), emitter fails.
- Injected/loop graph introduces a cycle → `CycleError` naming the cycle; run fails.
- Gate file missing / `gate_field` absent / non-bool → loop controller fails the run.
- Loop reaches `max_iterations` while gate still says continue → loop stops cleanly, run proceeds.
- Resume after partial loop: `loop_iterations` prevents re-cloning already-materialized iterations.
- Executor produces no stdout/stderr → empty capture files still created; `output_artifact_path` set.
- `status.json` write must be atomic and never partially read by `ao status` (write-then-rename).
- Logging handler must be detached even if `run()` raises (no file-handle leak across runs).

---

## 7. Test strategy (T-38jqbk, T-5isej3)
- **Unit**: `JSONFormatter` field shape; `write_status` projection; `read_task_manifest`/`read_gate` happy + error paths; `clone_body` suffixing & dependency rewrite; cross-validation of new fields.
- **Integration** (FakeExecutor, fixed clock): full run produces `run.log` (valid JSON lines) + `status.json` consistent with `state.json`; `emit_tasks` run expands and completes; loop runs N iterations then stops on gate; loop hits `max_iterations`; resume after injection rebuilds graph and skips emitter; cycle-after-injection fails with `CycleError`.
- Coverage target ≥80% on touched modules; deterministic via fixed clock + `__iterN` suffix; no real subprocess (FakeExecutor) except one e2e smoke if `claude` is present (skipped otherwise).

---

## 8. Developer / operator experience
- Operator: `ao status <run_id>` answers "where is my run?" instantly from `status.json`; `run.log` is greppable JSON; per-task `stdout.txt`/`stderr.txt` make failures diagnosable without re-running.
- Author: dynamic growth (`emit_tasks`) and loops are declared in the same YAML/JSON spec; gate decisions live in agent-written control files, so authors express "review until clean" without touching engine code.

---

## 9. Resolved decisions (formerly Open questions)

**RESOLVED — exit code at `task.end`:** `run.log` does capture the exit code as
a structured field at every `task.end` event. Both the success and non-success
branches of `engine.py` emit `{"event": "task.end", "status": "...",
"exit_code": result.exit_code}`. `TaskResult.exit_code` is set by the executor
and carried through `_run_with_retries` to the event. The per-task `stderr.txt`
and `status.json` still exist; `exit_code` in `run.log` is additionally included
as a cheap, greppable signal.

**RESOLVED — `depends_on: [<loop_id>]` semantic:** The implemented semantic is
"depends on the **final materialized iteration's last task**" (not the gate task).
`build_dag` calls `_resolve_loop_dep(loop_id, workflow)` which returns
`body[-1]` (iteration 1) or `body[-1]__iter{N}` (iteration N). `body[-1]` is
the last ordered task in the loop body, not necessarily the gate. This is the
correct semantic: a downstream task should wait for the whole last iteration
(including any post-gate tasks) to complete, not just the gate verdict.

**STANDING ASSUMPTION — single-threaded engine:** The linear engine assumption
remains. No parallel task execution was introduced. Risk: loops/injection with
future parallelism need revisiting. Mitigation: order recomputation is pure and
idempotent (safe to run in a future parallel scheduler), but the `done: set`
and cursor state would need per-shard coordination.

## 10. Deviations from original design

No behavioral deviations. Minor implementation clarifications from what the HLD
sketched:
- `_clone_body` explicitly clears `inputs`, `outputs`, `output_manifest`,
  `emit_tasks`, and `task_manifest_path` on every clone (not mentioned in the
  original §4.5). This is a correctness detail, not a design change — see §4.5.
- `_resolve_loop_dep` lives in `dag.py` as a module-level function, called
  inside `build_dag`. The HLD described it as an "engine" concern in §4.5; the
  implementation correctly placed it at the DAG layer (§6.1).
- `TaskContext` gained two new fields (`task_manifest_path`, `gate_output_path`)
  compared to the original §3.6 table which only listed `output_dir`. These are
  the path-passthrough fields that let executors know where to write control
  files for Area 2 features. No contract breakage — all new fields default to
  `None`.
- `_clone_body` signature in `engine.py` takes a third argument `workflow:
  WorkflowSpec` (needed to look up base tasks). The original EPIC contract table
  showed `_clone_body(self, loop, iter_n)` without this argument.
