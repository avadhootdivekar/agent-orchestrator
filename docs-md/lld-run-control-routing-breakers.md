# LLD — Run-control: conditional routing, multi-endpoint success & circuit breakers

- Epic: [`E-rc7k2v-run-control-routing-breakers`](../meta/tickets/E-rc7k2v-run-control-routing-breakers/EPIC.md)
- HLD: [`multi-endpoint-circuit-breaker-hld.md`](multi-endpoint-circuit-breaker-hld.md) (§3 requirements, §4 routing, §5 breaker, §6 catalog)
- ADR: [`ADR-0002`](adr/ADR-0002-conditional-branching-multi-endpoint.md) (Accepted — first-class `branches`)
- Core docs: [`hld-agent-orchestrator.md`](hld-agent-orchestrator.md) · [`lld-agent-orchestrator.md`](lld-agent-orchestrator.md)
- Status: **LLD complete — ready for implementation.** Design + interfaces only; no `src/` change is made by this doc.
- Audience: a developer new to the repo should be able to implement each module from this doc alone.
- Date: 2026-07-09 · Owner: architect

> Grounding note: every line/behaviour cited below was read from the live engine on 2026-07-09
> (`engine.py`, `dag.py`, `models.py`, `artifacts.py`, `runstate.py`, `spec.py`, `cli.py`,
> `budget.py`, `specs/workflow.schema.json`). File-line references are as-read.

---

## 0. The three deferred decisions — resolved

The epic deferred three decisions to LLD. All three are resolved here; the rest of the doc builds on them.

### R1 (resolved) — `not_taken` is a **distinct terminal status**, backed by a persisted route decision
Add `"not_taken"` to the `TaskStatus` literal (a *new status*, not `skipped`+reason). The **source of truth**
for which tasks are not-taken is the persisted `RunState.route_decisions` (router_id → selected route ids);
the per-task `not_taken` status is *derived* from it and persisted for observability.

- **Why a new status, not `skipped`+reason**: `skipped` already means "idempotency — outputs already present"
  and is re-derived every run (`should_skip`), so it self-heals; `not_taken` is a *routing verdict* that never
  self-heals and must survive resume. Overloading `skipped` forces every reader of `skipped` to branch on a
  reason field; a distinct enum value is the honest model and reads cleanly in `ao status` and events (ADR-0002
  "new terminal state/reason `not_taken`"; FR-B3 "a *distinct* terminal state").
- **Run-success accounting**: success math extends the terminal-success set from `("succeeded","skipped")` to
  `("succeeded","skipped","not_taken")` **only at the finalisation gate**. Because `not_taken` tasks never
  dispatch and never set the `failed` flag, the existing `if not failed and status=="running": status="succeeded"`
  finaliser already yields success — the only new obligation is "never dispatch a `not_taken` task" (§5.3).
- **`ao status` output**: `status.json` snapshot + `ao status` table gain a `route` column and count `not_taken`
  distinctly (§10). Adding `not_taken` to the `write_status` counts dict is required (it currently hard-codes 7
  keys; `counts.get(...)+1` avoids a KeyError but the seed dict must include `not_taken` for a stable snapshot).
- **Resume replay**: `prepare_resume` must (a) NOT reset a `not_taken` task to pending, and (b) re-derive the
  not-taken set from persisted `route_decisions` (deterministic, NFR-2) rather than re-reading the verdict file
  (§9). The router is `succeeded` so it never re-runs (FR-CB5).
- **NFR-5 (backward-compat)**: widening a `Literal` is deserialization-safe (old `state.json` never contains
  `not_taken`). New RunState fields (`route_decisions`, `tripped_breakers`) and new TaskRunState fields
  (`route`, `not_taken_reason`) all get defaults.

### R2 (resolved) — cone computation runs on the **full `build_dag` graph**; `ao validate` rejects inferred cross-route coupling
Cones are computed over the graph `build_dag` actually returns (explicit `depends_on` **plus** inferred
path-matching edges — memory `build-dag-infers-edges-from-paths`), never over `depends_on` alone, so the
validator's cone equals the engine's runtime graph. Cross-route ambiguity is then made a **static, named
`ao validate` error**:

> A task reachable from ≥2 distinct routes is legal **only** if that multi-route reachability comes from
> *declared* `depends_on` edges **and** the task carries an explicit `join`. If any coupling edge is an
> *inferred* (input-path == another cone's output-path) edge that is **not** in `depends_on`, `ao validate`
> fails with the two task ids and the shared artifact path.

This converts "inferred edges make cone exclusivity ambiguous" into a checkable rejection (§4.4). Full rules in §4.

### R3 (resolved) — existing stops are re-framed through trip→record→act while **preserving every externally-observed byte**
The three existing terminal stops (budget-exhaustion `stop`, unsatisfiable-estimate, quota-max-wait) are routed
through the new breaker pipeline as **built-in breakers** with fixed ids, but:

- final `RunState.status` and CLI exit code are **unchanged**;
- the **existing event names are still emitted** (`budget.exhausted`, `quota.max_wait_exceeded`) — the breaker
  pipeline emits `breaker.trip` and appends to `tripped_breakers` *in addition*, never *instead*;
- transient wait/retry loops (429 wait, quota poll, budget wait) are **not** trips — only the terminal give-up
  moments are.

Parity is proven by **characterization (pinning) tests written before the refactor** that capture current
status/exit-code/event-sequence, then re-assert them after (§8). This is the checkable mitigation for R3.

---

## 1. Files touched (map of change)

| File | Change |
|---|---|
| `specs/workflow.schema.json` | add `branches`, `circuit_breakers` (top-level) + `join` on task; new `$defs`: `router`, `route`, `circuitBreaker` |
| `models.py` | new `RouteSpec`, `RouterSpec`, `CircuitBreakerSpec`, `TrippedBreaker`; `TaskSpec.join`; `WorkflowSpec.branches`/`circuit_breakers`; `TaskStatus += "not_taken"`; `TaskRunState.route`/`not_taken_reason`; `RunState.route_decisions`/`tripped_breakers`; breaker id constants |
| `artifacts.py` | generalise the bounded-JSON reader: `read_control` + typed helpers; `MAX_CONTROL_FILE_BYTES` size bound; `read_gate` becomes a thin alias |
| `dag.py` | `compute_cones(workflow, graph)` + reach helper (uses the graph `build_dag` returns) |
| `spec.py` (`cross_validate`) | routing + breaker static checks (§4.4) |
| `engine.py` | routing activation on router success; not_taken skip + join at dispatch; breaker registry + evaluation at task boundaries; re-frame the three built-in stops; `branch.route`/`breaker.trip` events |
| `runstate.py` | `prepare_resume` preserves/re-derives `not_taken`; `write_status` adds `not_taken` count + `route` |
| `cli.py` | `ao status` route/not_taken column (via status.json) |
| `errors.py` | `ControlFileError` base; `GateError` subclasses it; `RouteError`, `BreakerTripped` (recorded, not necessarily raised) |

**No new runtime dependency.** All reads stay path-only or bounded-control-JSON (NFR-1 preserved).

---

## 2. Data-model changes

### 2.1 `workflow.schema.json` (diff)

Add to top-level `properties` (after `budget`):

```jsonc
"branches": { "type": "array", "default": [], "items": { "$ref": "#/$defs/router" } },
"circuit_breakers": { "type": "array", "default": [], "items": { "$ref": "#/$defs/circuitBreaker" } }
```

Add to `$defs/task/properties`:

```jsonc
"join": { "enum": ["all", "any"], "default": "all",
          "description": "Convergence policy when a task depends on tasks from >1 route." }
```

New `$defs`:

```jsonc
"route": {
  "type": "object", "additionalProperties": false, "required": ["entry"],
  "properties": {
    "entry": { "type": "array", "minItems": 1, "items": { "type": "string" },
               "description": "Task ids that head this route; cone = their exclusive descendants." }
  }
},
"router": {
  "type": "object", "additionalProperties": false,
  "required": ["id", "router_task_id", "verdict_path", "routes"],
  "properties": {
    "id":             { "type": "string", "pattern": "^[a-z0-9][a-z0-9-_]*$" },
    "router_task_id": { "type": "string", "description": "Task whose success triggers the route read." },
    "verdict_path":   { "type": "string", "description": "PATH to {\"routes\":[...]} written by the router task." },
    "verdict_field":  { "type": "string", "default": "routes" },
    "routes":         { "type": "object", "minProperties": 2, "additionalProperties": { "$ref": "#/$defs/route" } },
    "default_route":  { "type": ["string", "null"], "default": null,
                        "description": "Route to select when the verdict is empty/unknown; null => unknown verdict fails the run." }
  }
},
"circuitBreaker": {
  "type": "object", "additionalProperties": false, "required": ["id", "condition", "action"],
  "properties": {
    "id":        { "type": "string", "pattern": "^[a-z0-9][a-z0-9-_]*$" },
    "condition": { "enum": ["task_failures","consecutive_failures","run_wall_clock_seconds",
                            "verdict","injected_task_count","stop_file",
                            "failure_ratio","same_task_exhausted","projected_cost_exceeds",
                            "provider_429_count","executor_spawn_failures","output_validation_failures",
                            "loop_max_iterations_no_converge","no_artifact_progress_seconds",
                            "injection_depth","dag_size","os_signal","workspace_disk_usage",
                            "git_workspace_dirty","token_rate"] },
    "action":    { "enum": ["fail","stop","pause"] },
    "scope":     { "enum": ["run"], "default": "run", "description": "'branch' reserved (non-MVP)." },
    "threshold": { "type": "integer", "minimum": 1 },
    "window":    { "enum": ["run"], "default": "run", "description": "'branch'/'last_n' reserved (non-MVP)." },
    "task_id":      { "type": "string", "description": "verdict condition: task whose verdict is read." },
    "verdict_path": { "type": "string", "description": "verdict condition: PATH to the verdict JSON." },
    "field":        { "type": "string", "default": "halt", "description": "verdict condition: boolean field." },
    "path":         { "type": "string", "description": "stop_file condition: PATH polled for existence." }
  },
  "allOf": [
    { "if": { "properties": { "condition": { "enum": ["task_failures","consecutive_failures",
              "run_wall_clock_seconds","injected_task_count"] } } },
      "then": { "required": ["threshold"] } },
    { "if": { "properties": { "condition": { "const": "verdict" } } },
      "then": { "required": ["task_id","verdict_path"] } },
    { "if": { "properties": { "condition": { "const": "stop_file" } } },
      "then": { "required": ["path"] } }
  ]
}
```

> The full §6 catalog is enumerated in the `condition` enum so authored specs referencing a future condition
> validate against schema today; only the six MVP conditions are wired in the engine (§7). A non-MVP condition
> reaching the engine registry raises a clean `SpecValidationError("condition not implemented in this release")`
> at `cross_validate` time (§4.4), never a silent no-op.

### 2.2 Pydantic models (`models.py`)

```python
# --- routing ---
class RouteSpec(BaseModel):
    entry: list[str]

class RouterSpec(BaseModel):
    id: str
    router_task_id: str
    verdict_path: str
    verdict_field: str = "routes"
    routes: dict[str, RouteSpec]
    default_route: str | None = None

# --- breakers ---
BreakerCondition = Literal[
    "task_failures", "consecutive_failures", "run_wall_clock_seconds",
    "verdict", "injected_task_count", "stop_file",
    # ...non-MVP names accepted by schema, rejected by registry until implemented...
]

class CircuitBreakerSpec(BaseModel):
    id: str
    condition: str                       # validated against BreakerCondition + registry
    action: Literal["fail", "stop", "pause"]
    scope: Literal["run"] = "run"
    window: Literal["run"] = "run"
    threshold: int | None = None
    task_id: str | None = None
    verdict_path: str | None = None
    field: str = "halt"
    path: str | None = None

class TrippedBreaker(BaseModel):
    id: str
    condition: str
    action: str
    at: str                              # ISO-8601 UTC
    detail: dict = {}                    # e.g. {"failures": 3, "threshold": 3}

# MVP built-in (re-framed) breaker ids — named, not magic literals (§8)
BUILTIN_BUDGET_EXHAUSTED = "builtin.budget_exhausted"
BUILTIN_BUDGET_UNSATISFIABLE = "builtin.budget_unsatisfiable"
BUILTIN_QUOTA_MAX_WAIT = "builtin.quota_max_wait"
MAX_CONTROL_FILE_BYTES = 65536           # bounded-control read guard (NFR-1 hardening)
```

Edits to existing models (all additions have defaults — NFR-5):

```python
class TaskSpec(BaseModel):
    ...
    join: Literal["all", "any"] = "all"

class WorkflowSpec(BaseModel):
    ...
    branches: list[RouterSpec] = []
    circuit_breakers: list[CircuitBreakerSpec] = []

TaskStatus = Literal[
    "pending","running","succeeded","failed","skipped","cancelled","timed_out","not_taken"  # + not_taken
]

class TaskRunState(BaseModel):
    ...
    route: str | None = None             # "<router_id>:<route_id>" this task belongs to (observability)
    not_taken_reason: str | None = None  # e.g. "router=classify route=bug not selected"

class RunState(BaseModel):
    ...
    route_decisions: dict[str, list[str]] = {}     # router_id -> selected route ids (source of truth)
    tripped_breakers: list[TrippedBreaker] = []
```

`run_wall_clock_seconds` reuses the existing `RunState.started_at` (ISO string set at `new_run`) — **no new
clock field**; elapsed = `injected_clock().timestamp() - parse(started_at).timestamp()`. Semantics: measured
from *original* run start, so a run resumed much later trips immediately (correct "deadline" semantic; documented).

---

## 3. Shared bounded-JSON control-file reader (`artifacts.py`)

Today three verdict-shaped reads exist or are needed: loop gate (`read_gate` → bool), router
(`{"routes":[...]}` → list), verdict breaker (`{"halt":bool,"reason":str}`). MVP collapses them onto **one
audited read primitive** so NFR-1 has a single surface (ADR-0002 consequence, epic FR-B1/FR-CB3/NFR-1).

### 3.1 Interface

```python
class ControlFileError(OrchestratorError): ...     # new base
class GateError(ControlFileError): ...             # existing name now subclasses it (back-compat for engine)

def read_control(store: ArtifactStore, path: str) -> dict:
    """Read one bounded JSON *object* control file. The ONLY sanctioned content read besides
    read_manifest/read_task_manifest. Enforces MAX_CONTROL_FILE_BYTES via store.size() BEFORE reading
    (closes today's unbounded-read gap in read_gate/read_manifest). Raises ControlFileError on:
    missing file, size > cap, invalid JSON, or non-object root."""

def read_bool_field(store, path, field: str) -> bool:      # loop gate + verdict breaker
def read_routes(store, path, field: str = "routes") -> list[str]:   # router
```

### 3.2 Pseudocode

```
FUNCTION read_control(store, path) -> dict:
  resolved = store.resolve(path)                         # raises ArtifactPathError on traversal
  IF not store.exists(path): RAISE ControlFileError("control file not found: "+path)
  IF store.size(path) > MAX_CONTROL_FILE_BYTES:
      RAISE ControlFileError("control file exceeds "+MAX_CONTROL_FILE_BYTES+" bytes: "+path)
  TRY: data = json.loads(read_text(resolved))
  EXCEPT JSONDecodeError as e: RAISE ControlFileError("not valid JSON: "+path)
  IF not isinstance(data, dict): RAISE ControlFileError("root must be object: "+path)
  RETURN data

FUNCTION read_bool_field(store, path, field) -> bool:
  data = read_control(store, path)
  IF field not in data: RAISE ControlFileError("missing field "+field)
  v = data[field]
  IF not isinstance(v, bool): RAISE ControlFileError("field "+field+" must be bool")
  RETURN v

FUNCTION read_routes(store, path, field="routes") -> list[str]:
  data = read_control(store, path)
  IF field not in data: RAISE ControlFileError("missing field "+field)
  v = data[field]
  IF not (isinstance(v, list) and all(isinstance(x,str) and x!="" for x in v)):
      RAISE ControlFileError("field "+field+" must be a list of non-empty strings")
  RETURN v
```

`read_gate(store, path, field="continue")` is kept as `return read_bool_field(store, path, field)` so the loop
code and the memory `loop-iterate-event-only-on-continue` remain valid; existing `except GateError` handlers in
`engine.py` still catch (because `GateError <: ControlFileError`, but engine catches the concrete `GateError` it
raises for loops; router/verdict paths catch `ControlFileError`).

---

## 4. Route cone computation & validation (`dag.py`, `spec.py`)

### 4.1 Definitions
- **Graph** = the adjacency `build_dag(workflow)` returns (declared **and** inferred edges).
- **`reach(entries)`** = forward transitive closure over the graph starting from `entries` (entries included).
- **`route_reach[router][route]`** = `reach(route.entry)`.
- **exclusive cone** of `(router, route)` = tasks reachable from that route's entries **and from no other route
  of the same router**. `|routers reaching t within this router| == 1`.
- **shared/convergence task** = reachable from ≥2 routes (of the same router) or from a route + an always-on
  (non-route) predecessor. Governed by `join`, never placed in an exclusive cone.

### 4.2 Algorithm (`compute_cones`)

```
FUNCTION compute_cones(workflow, graph) -> (cones, membership):
  cones = {}          # router_id -> {route_id -> set[task_id]}    (exclusive cones)
  membership = {}     # task_id  -> set[(router_id, route_id)]     (all reaching routes)
  FOR router IN workflow.branches:
    reach_by_route = {}
    FOR route_id, route IN router.routes:
      r = forward_closure(graph, route.entry)                     # BFS/DFS over adjacency
      reach_by_route[route_id] = r
      FOR t IN r: membership.setdefault(t, set()).add((router.id, route_id))
    cones[router.id] = {}
    FOR route_id, r IN reach_by_route:
      # a task is exclusive to this route iff no OTHER route of THIS router also reaches it
      exclusive = { t FOR t IN r
                    IF count(rid FOR (rid) IN reach_by_route IF t IN reach_by_route[rid]) == 1 }
      # entries themselves stay in the cone even if some are also reachable elsewhere (validation rejects that)
      cones[router.id][route_id] = exclusive
  RETURN cones, membership
```

`forward_closure` is a plain iterative BFS over `graph._adj` (expose a small read accessor on `Graph`, or return
`adj` from `build_dag`). Deterministic; no clock; pure.

### 4.3 Runtime use
- Pre-run: `compute_cones` is computed once after the initial `build_dag` and cached on the engine per run.
- On injection (emit/loop rebuild `build_dag`), cones are **not** recomputed for injected tasks — injected tasks
  inherit their emitter's `route` (§5.5, R4). Static cones are stable across injection because injected ids are
  new and never appear in a pre-computed cone.

### 4.4 `ao validate` rules (extend `cross_validate`; NFR-3, R2)
Run **after** `build_dag` succeeds (need the graph incl. inferred edges) — add a post-DAG validation pass invoked
from `cross_validate` (or a sibling `validate_run_control(workflow, graph)` called right after in the CLI path).

1. **Unknown router task**: `router.router_task_id` must be an existing task id.
2. **Unknown entry**: every `route.entry` id must be an existing task id.
3. **Entry downstream of router**: every entry must be reachable from `router_task_id` in the graph (else it can
   never be deactivated). Reject naming entry + router.
4. **Route-entry disjointness**: for a router, no entry of route A may be in `reach(route B)`; selecting B would
   force A active → ambiguous. Reject with both entry ids and the coupling path.
5. **Inferred cross-route coupling (the R2 rule)**: for any task `t` with `|membership[t]|` spanning ≥2 routes
   of one router, if the *reason* `t` is reachable from >1 route includes an **inferred** edge (producer→t where
   `t.inputs ∋ producer.outputs` and `producer ∉ t.depends_on`), FAIL with `t`, the producer, and the shared
   path. Legitimate convergence must be *declared* (`depends_on` + `join`).
6. **Convergence needs `join`**: a task in ≥2 routes' reach via *declared* edges must not rely on the default
   silently — require it to be intentional: allow default `all` but WARN if it converges >1 route with no
   explicit `join` set (helps authors; not a hard fail).
7. **`any`-join satisfiability**: for a `join: any` task, at least one declared route must be able to activate at
   least one of its producers on a single selection; reject an `any`-join every producer of which sits in a cone
   that can never co-activate with the join's own activation path.
8. **Unreachable endpoint**: every route must contain ≥1 sink (a task with no successors) in its exclusive cone.
9. **Nested router rejection (MVP boundary)**: a `router_task_id` must not lie inside another router's exclusive
   cone (mirrors the existing "no nested loops" rule in `cross_validate`). Reject with both router ids.
10. **Breaker refs**: each `verdict` breaker's `task_id` must exist; each breaker's `condition` must be in the
    MVP-implemented set (else "condition not implemented in this release"); ids unique across breakers; `action`
    `pause` allowed but documented as "persist + stop; operator resumes" (§6.4).
11. **Reserved-suffix / id-pattern**: router/route/breaker ids follow `^[a-z0-9][a-z0-9-_]*$`; entries/body must
    not contain the reserved `__iter` marker (reuse existing guard).

---

## 5. Routing execution & run-success (`engine.py`)

### 5.1 Where routing hooks into the loop
The engine's main `while cursor < len(order)` loop (engine.py:176) settles each task, then runs the
**dynamic-expansion hooks** (emit at :638, loop gate at :677). Routing adds a **third post-success hook** and a
**pre-dispatch skip/join check**.

### 5.2 Router-success hook (after a router task reaches `succeeded`, alongside 2a/2b)

```
FUNCTION on_router_success(router, state, store, cones, run_log):
  selected = TRY read_routes(store, router.verdict_path, router.verdict_field)
             EXCEPT ControlFileError as e:
                 route_fail(state, router, reason="verdict unreadable: "+str(e)); RETURN FAILED
  known = set(router.routes.keys())
  selected = [r for r in selected if r in known]           # drop unknowns
  IF selected is empty:
      IF router.default_route is not None: selected = [router.default_route]
      ELSE: route_fail(state, router, reason="empty/unknown verdict, no default_route"); RETURN FAILED
  state.route_decisions[router.id] = selected              # SOURCE OF TRUTH (persist)
  not_taken_ids = []
  FOR route_id NOT IN selected:                            # every unselected route of THIS router
      FOR t IN cones[router.id][route_id]:
          ts = state.tasks.setdefault(t, TaskRunState())
          IF ts.status in ("succeeded","skipped","failed"): CONTINUE   # never override settled
          ts.status = "not_taken"
          ts.route = router.id+":"+route_id
          ts.not_taken_reason = "router="+router.router_task_id+" route="+route_id+" not selected"
          not_taken_ids.append(t)
  FOR route_id IN selected:                                # tag activated tasks for the route column
      FOR t IN cones[router.id][route_id]:
          state.tasks[t].route = router.id+":"+route_id
  run_log.info("branch.route", extra={"event":"branch.route","router_id":router.id,
               "router_task_id":router.router_task_id,"selected":selected,
               "not_taken_count":len(not_taken_ids)})
  save(state); RETURN OK
```

`route_fail` sets `state.status="failed"`, `failed=True`, emits `branch.route` with an `error` field, saves.
It is a *validation-style* run failure (FR-B3/FR-B4, ADR default_route=null semantics), distinct from a breaker.

### 5.3 Not-taken skip in the loop (top of the while body, next to `if tid in done: continue`)

```
tid = order[cursor]; cursor += 1
ts0 = state.tasks.get(tid)
IF ts0 and ts0.status == "not_taken":                      # never dispatch, never bill (FR-B3)
    CONTINUE
IF tid in done: CONTINUE
```

### 5.4 Join handling (pre-dispatch, before the missing-input check)
A task is a **convergence task** if `|membership[tid]|` spans ≥2 routes OR it mixes route + always-on producers.
Evaluate just before dispatch:

```
FUNCTION apply_join(task, state, membership) -> Optional[skip_reason]:
  dep_states = [state.tasks.get(d) for d in effective_deps(task)]     # depends_on + inferred producers
  not_taken_deps = [d for d in effective_deps(task)
                    if state.tasks.get(d) and state.tasks[d].status == "not_taken"]
  IF task.join == "all":
      IF not_taken_deps:                                  # any not-taken dep ⇒ task not-taken (propagate)
          state.tasks[task.id].status = "not_taken"
          state.tasks[task.id].not_taken_reason = "join=all; dep(s) not_taken: "+not_taken_deps
          RETURN "not_taken"
      RETURN None                                         # topo order already guarantees deps done
  ELSE:  # join == "any"
      live_deps = [d for d in effective_deps(task) if d not in not_taken_deps]
      IF not live_deps:                                   # every producer not-taken ⇒ task not-taken
          state.tasks[task.id].status = "not_taken"; RETURN "not_taken"
      RETURN None                                         # runs; §5.4a relaxes its input check
```

**5.4a — missing-input relaxation for `any` joins**: the engine's input existence check
(`missing = [inp for inp in task.inputs if not store.exists(inp)]`, engine.py:209) would wrongly fail an
`any`-join task because a not-taken producer never wrote its declared output. For `join == "any"` only, exclude
inputs whose *sole* producer is a not-taken task:

```
optional_inputs = { inp FOR inp IN task.inputs
                    IF producer_of(inp) is not None and state.tasks[producer_of(inp)].status == "not_taken" }
missing = [inp for inp in task.inputs if inp not in optional_inputs and not store.exists(inp)]
```
`producer_of(inp)` = the `output_to_task` map already built in `build_dag` (expose it, or rebuild locally).
For `join == "all"` the existing check is unchanged (an all-join with a not-taken dep is already not_taken and
never reaches the input check).

### 5.5 Injected tasks inherit their branch (R4)
When an activated task injects tasks (emit/loop), set the injected `TaskRunState.route = emitter.route`. Because
a not-taken emitter never runs (never dispatched, §5.3), it never injects — so injected tasks are **always on an
activated branch by construction**. Breaker counting (`task_failures`, `consecutive_failures`,
`injected_task_count`) already iterates all of `state.tasks` / `state.injected_tasks`, so injected failures are
counted with no extra work (§7). This resolves R4.

### 5.6 Run success (FR-B4)
Unchanged finaliser (`if not failed and state.status=="running": state.status="succeeded"`), because
`not_taken` tasks never set `failed`. The **`done`** set stays "succeeded/skipped only"; `not_taken` is handled
by the §5.3 skip, not by `done`. Multiple sinks may be terminal — natural consequence.

---

## 6. Circuit-breaker framework (`engine.py`)

### 6.1 Evaluation point
**At task boundaries** — after a task settles and `save(state)` runs, before the next dispatch (HLD §5). Concretely,
after the outcome-handling block (engine.py ~:628, after `self._runstate.save(state)`), call
`self._evaluate_breakers(...)`. Designed to survive a future parallel engine (same points per worker + a timer
for time conditions); MVP is sequential.

### 6.2 Registry (pluggable, ABC — mirrors `Executor`/`BudgetManager` DI style)

```python
class BreakerContext(BaseModel):        # paths/ids/counters only — NFR-1
    state: RunState
    clock_epoch: float
    store: ArtifactStore                # for verdict/stop_file reads (bounded control only)

class Breaker(ABC):
    condition: str
    @abstractmethod
    def evaluate(self, spec: CircuitBreakerSpec, ctx: BreakerContext) -> TripResult | None: ...
    # returns TripResult(detail={...}) when tripped, else None. Pure w.r.t. injected clock.

BREAKER_REGISTRY: dict[str, Breaker]     # condition name -> instance; the six MVP conditions registered
```

### 6.3 Evaluation loop (trip → record → act)

```
FUNCTION evaluate_breakers(workflow, state, clock, store, run_log) -> Optional[Action]:
  triggered = []
  FOR spec IN workflow.circuit_breakers + builtin_specs_active_this_boundary:  # declared first, deterministic
      breaker = BREAKER_REGISTRY[spec.condition]
      trip = breaker.evaluate(spec, BreakerContext(state, clock().timestamp(), store))
      IF trip is not None:
          IF spec.id in {tb.id for tb in state.tripped_breakers}: CONTINUE   # latch: record once
          rec = TrippedBreaker(id=spec.id, condition=spec.condition, action=spec.action,
                               at=iso(clock()), detail=trip.detail)
          state.tripped_breakers.append(rec)
          run_log.warning("breaker.trip", extra={"event":"breaker.trip","breaker_id":spec.id,
                          "condition":spec.condition,"action":spec.action,"detail":trip.detail})
          triggered.append(spec)
  save(state)
  IF triggered is empty: RETURN None
  # first tripped breaker in evaluation order owns the action (deterministic, documented)
  RETURN map_action(triggered[0].action)
```

### 6.4 Actions (FR-CB2, scope=run)
- **`fail`** → `state.status="failed"`, `failed=True`, break. Resumable (`prepare_resume` gives failure-count
  breakers a clean slate, §9).
- **`stop`** → graceful: honour the in-flight-task cancel flag if a task is running; `state.status="failed"`
  for MVP parity with existing stops (the current budget/quota stops set `"failed"`; a dedicated `"stopped"`
  RunState value is **non-MVP** to avoid changing the `RunState.status` literal and the CLI exit-code contract).
  Recorded as action `stop` in `tripped_breakers` for audit even though status is `failed`.
- **`pause`** → persist state, `state.status="failed"` (resumable), stop the loop; operator runs `ao resume`.
  MVP note: "pause" == "stop that expects a resume"; no half-open/auto-resume (latch-only, HLD §5).

> Deliberate MVP simplification: `stop`/`pause` both land on `RunState.status="failed"` (resumable). Adding
> `"stopped"`/`"paused"` to the persisted `RunState.status` literal and threading new exit codes is out of MVP
> scope (Core-simple; the audit distinction lives in `tripped_breakers[].action`). Recorded as ADR-RC-004.

---

## 7. The six MVP breaker conditions — exact trip logic

All use the injected clock (never `time.time()` directly) and read only counters/statuses/bounded control files.

| # | condition | trip predicate | detail |
|---|---|---|---|
| 1 | `task_failures` | `count(ts for ts in state.tasks.values() if ts.status in {"failed","timed_out"}) >= threshold` | `{failures, threshold}` |
| 2 | `consecutive_failures` | trailing run of settled tasks (in completion order) whose status ∈ {failed,timed_out} `>= threshold` | `{streak, threshold}` |
| 3 | `run_wall_clock_seconds` | `clock_epoch - parse(state.started_at).timestamp() >= threshold` | `{elapsed, threshold}` |
| 4 | `verdict` | task `spec.task_id` is settled `succeeded` **and** `read_bool_field(store, spec.verdict_path, spec.field) == True` | `{task_id, field}` |
| 5 | `injected_task_count` | `len(state.injected_tasks) >= threshold` | `{count, threshold}` |
| 6 | `stop_file` | `store.exists(spec.path)` | `{path}` |

Notes:
- **consecutive_failures** needs completion order. Track a per-run ordered list of settled task ids (or derive
  from `ended_at` timestamps). Simplest: maintain `state`-local settle order in memory during a run; on resume,
  reconstruct from `ended_at` sort. Detail records the streak.
- **verdict** uses the *shared reader* `read_bool_field` (§3) — same audited path as loop gates. Reads only when
  `spec.task_id` just settled succeeded, so it evaluates at most once per verdict file (latch handles resume).
- **run_wall_clock_seconds** is checked at task boundaries only (MVP sequential). A single long task can overrun
  the deadline; documented limitation (a future timer-based check closes it; the eval point is chosen to survive
  that addition). Uses `state.started_at` (§2.2) so resume measures from original start.
- **stop_file** = operator external kill switch; `ao stop --run-id` (future CLI verb) or an operator `touch`
  writes the file. Existence-only (`store.exists`), never content — NFR-1 clean.
- **injected_task_count** is the E2 enabler consumed by epic `E-gd8m4x` (runaway fan-out cap). `injection_depth`
  is non-MVP (schema-declared, not registered).

---

## 8. Re-framing the existing stops (R3) — parity plan + characterization tests

### 8.1 The three stops today (as-read from engine.py)
| Stop | Current site | Current terminal effect | Current event |
|---|---|---|---|
| Budget exhaustion (`on_exhaustion=stop`) | :312-324 & 429 branch :531-540 | `state.status="failed"`, break | `budget.exhausted` |
| Unsatisfiable estimate | :292-306 | `state.status="failed"`, break | `budget.exhausted` |
| Quota max-wait exceeded | :421-437 | `state.status="failed"`, break | `quota.max_wait_exceeded` |

### 8.2 Re-frame (additive, not replacing)
At each terminal site, before setting `failed`, call `self._trip_builtin(state, id, condition, action="fail", detail, clock, run_log)`:

```
FUNCTION trip_builtin(state, id, condition, action, detail, clock, run_log):
  state.tripped_breakers.append(TrippedBreaker(id=id, condition=condition, action=action,
                                               at=iso(clock()), detail=detail))
  run_log.warning("breaker.trip", extra={"event":"breaker.trip","breaker_id":id,
                  "condition":condition,"action":action,"detail":detail})
```

Then the existing `run_log.*("budget.exhausted"/"quota.max_wait_exceeded", ...)` and
`state.status="failed"; failed=True; break` lines are **kept exactly as-is**. Net observable delta: a
`tripped_breakers` entry + a `breaker.trip` event are *added*; status, exit code, and the pre-existing event are
*identical*.

- budget-exhaustion → `trip_builtin(BUILTIN_BUDGET_EXHAUSTED, "projected_cost_exceeds"/"total_tokens", ...)`
- unsatisfiable → `trip_builtin(BUILTIN_BUDGET_UNSATISFIABLE, "projected_cost_exceeds", ...)`
- quota max-wait → `trip_builtin(BUILTIN_QUOTA_MAX_WAIT, "quota_exhaustion_wait_exceeded", ...)`

Transient waits (429 wait, quota poll, budget wait) are untouched — not trips.

### 8.3 Characterization tests (write BEFORE the refactor — this is the checkable R3 mitigation)
Pin the *current* observable behaviour, then re-assert after the re-frame. Use `FakeExecutor` + injected clock +
fixed budget (deterministic, per CLAUDE.md testing rules). Both an engine-API test **and** a CliRunner E2E test
(memory `engine-api-tests-dont-cover-cli`).

```
test_budget_exhaustion_stop_parity:
  GIVEN budget.total_tokens below the first task's estimate, on_exhaustion="stop", fixed clock
  WHEN run
  THEN state.status == "failed"  AND cli exit == 1
   AND events contain "budget.exhausted"
   AND (after re-frame) tripped_breakers contains id "builtin.budget_exhausted"
       AND events contain "breaker.trip" with breaker_id "builtin.budget_exhausted"

test_unsatisfiable_estimate_parity:
  GIVEN single task estimate > total_tokens
  THEN status=="failed", exit==1, event "budget.exhausted" present
   AND (after) tripped_breakers has "builtin.budget_unsatisfiable"

test_quota_max_wait_parity:
  GIVEN FakeExecutor returns claude_quota_exhausted=True, clock advanced past quota_max_wait_seconds
  THEN status=="failed", event "quota.max_wait_exceeded" present
   AND (after) tripped_breakers has "builtin.quota_max_wait"
```

The "before" run (assertions minus the `tripped_breakers`/`breaker.trip` lines) is committed first as the golden
pin; the refactor must not change the pinned lines. This makes drift a failing test, not a claim.

---

## 9. Resume-replay semantics (`runstate.py`, FR-CB5 / R1)

`prepare_resume` changes:

```
# (existing) re-attach injected_tasks to workflow.tasks, then per task:
FOR task IN workflow.tasks:
  ts = state.tasks.get(task.id, TaskRunState())
  IF ts.status == "succeeded" and outputs_present(task): KEEP        # existing
  ELIF ts.status == "not_taken": KEEP                                # NEW — never reset a routing verdict
  ELIF ts.status != "pending": state.tasks[task.id] = TaskRunState(status="pending")

# (NEW) deterministically re-derive not_taken from persisted route_decisions (NFR-2) — do NOT re-read verdicts:
graph = build_dag(workflow); cones,_ = compute_cones(workflow, graph)
FOR router_id, selected IN state.route_decisions.items():
  router = find_router(workflow, router_id)
  FOR route_id NOT IN selected:
    FOR t IN cones[router_id][route_id]:
      IF state.tasks[t].status not in ("succeeded","skipped","failed"):
        state.tasks[t].status = "not_taken"; set route/not_taken_reason
```

- **Router never re-runs**: it is `succeeded`, kept as-is (FR-CB5). The verdict file is never re-read; the
  recorded decision replays.
- **Tripped breakers**: `state.tripped_breakers` persists (audit). On resume the engine re-evaluates breakers
  fresh, but `evaluate_breakers`'s latch (§6.3) is keyed on breaker id for the lifetime of `state.tripped_breakers`
  — an id already recorded there is **never** re-evaluated, regardless of whether its condition remains true. This
  means the corrected behavior (as implemented, not as originally drafted here) is:
  - `task_failures`/`consecutive_failures` start clean after resume — not because of the latch, but because
    `prepare_resume` resets the failed task to pending, so the underlying count is genuinely back to 0 (FR-CB5).
  - `stop_file`/`injected_task_count` re-trip on resume **only if their id was never evaluated before the run
    stopped** (e.g. a crash between injection and the next boundary's evaluation, or a different breaker/task
    failure halted the run first). If the SAME breaker id is what caused the original stop, it is already latched
    and will **not** re-halt the resumed run even if the file is still present / the cap is still exceeded — the
    operator must clear the condition (or accept the run will proceed past it). This corrects an earlier draft of
    this paragraph, which incorrectly claimed `injected_task_count` "re-trips if the persisted injected set still
    exceeds the cap" unconditionally; T-t4m8x1's implementation and tests confirmed the latch prevents that. See
    the operator-facing gotcha in §11 (Resume).
  - The historical `tripped_breakers` records do **not** by themselves halt a resumed run — only a fresh,
    not-yet-latched evaluation can.
- **Latch scope**: the "record once" latch (§6.3) is keyed on breaker id within one run session; on a fresh
  resume session the in-memory `triggered` set is empty but `state.tripped_breakers` guards against duplicate
  records for a still-latched declarative breaker.

---

## 10. Structured events & `ao status` (FR-CB4, HLD §7)

### 10.1 New events (JSON log, same handler as `run.start`/`task.end`)
```
branch.route  : {event, router_id, router_task_id, selected:[...], not_taken_count, at, [error]}
breaker.trip  : {event, breaker_id, condition, action, detail:{...}, at}
```
Existing events (`budget.exhausted`, `quota.max_wait_exceeded`, `loop.iterate`, `task.*`, `run.*`) are unchanged
(R3 parity). `loop.iterate` still only fires on continue (memory `loop-iterate-event-only-on-continue`).

### 10.2 `status.json` + `ao status`
- `write_status`: seed the `counts` dict with `"not_taken": 0`; add per-task `route` and `not_taken_reason` to
  each task entry; add top-level `route_decisions` and `tripped_breakers` summaries.
- `ao status` (`_print_status_snapshot` / `_print_state`): add a `Route` column; render `not_taken` rows so an
  operator sees which endpoints were selected vs excluded, and a trailer line listing any tripped breakers.

```
Task                          Status          Route            Attempts
bug-repro                     not_taken       classify:bug     0
epic-design                   succeeded       classify:epic    1
...
Tripped breakers: builtin.quota_max_wait (quota_exhaustion_wait_exceeded, action=fail)
```

---

## 11. Edge cases (per module — mandatory)

**Routing / cones**
- Router verdict selects an unknown route id → dropped; if none valid → `default_route` or run-fail (§5.2).
- Router verdict selects *all* routes → nothing is not_taken (full fan-out, legal).
- Empty verdict list + `default_route=null` → validation-style run fail (ADR-0002).
- A shared/convergence task settled `succeeded` before its route was deactivated → never overridden to
  `not_taken` (§5.2 guard).
- `join: any` task whose live producer also fails → task fails normally (join only governs not_taken, not
  failure).
- Nested router (router inside a cone) → rejected by `ao validate` rule 9 (MVP).
- Duplicate route entry across routers (independent routers) → allowed; each router's cones computed
  independently; a task in two routers' cones is not_taken only if *either* router deactivates it (union).
- Malformed/oversized verdict file (> `MAX_CONTROL_FILE_BYTES`) → `ControlFileError` → run-fail (bounded).

**Breakers**
- Two breakers trip at the same boundary → both recorded; first-in-eval-order action executed (§6.3).
- `verdict` breaker whose task failed (never succeeded) → not evaluated (predicate requires settled succeeded).
- `stop_file` path escaping workspace → `store.exists` returns False (ArtifactPathError swallowed) → never trips;
  `ao validate` should also reject a traversal path.
- `run_wall_clock_seconds` with a single task longer than threshold → trips at the *next* boundary (documented).
- Re-framed built-ins must not double-count with a *declared* `total_tokens`/`quota` breaker of the same
  semantics → for MVP the built-ins have reserved `builtin.*` ids and declared breakers use author ids; both may
  record (audit) but each latches once.

**Resume**
- Old `state.json` without `route_decisions`/`tripped_breakers` → loads (defaults) (NFR-5).
- Resume after a `fail` breaker → clean slate for failure-count breakers (the underlying count is genuinely 0
  after `prepare_resume` resets the failed task to pending); structural breakers (`injected_task_count`,
  `stop_file`) re-trip on resume **only if their id was never evaluated before the stop** — corrected from an
  earlier draft that claimed unconditional re-tripping (see §9).
- **Breaker latch gotcha (operator)**: a breaker id that already recorded a trip *before* a run stopped will
  **not** re-halt a resumed run, even if its condition remains true. Only a condition that was never evaluated
  before the stop can re-trip correctly on resume. Reason: the latch is keyed on `state.tripped_breakers[id]`,
  which persists. This is intentional per §9 design ("historical `tripped_breakers` records do not by themselves
  halt a resumed run"); operators must clear/remove the condition (e.g. delete a `stop_file`, increase an
  `injected_task_count` cap) to allow resume past a once-tripped breaker.
- Injected task on a route that was later deactivated (impossible by construction, §5.5) → not reachable; assert
  in tests.

**Duplicate/injection interactions** (memories)
- Injected ids globally unique (`injected-task-ids-globally-unique`) — unchanged.
- Cloned loop bodies clear inputs/outputs (`build-dag-infers-edges-from-paths`) — cone computation therefore sees
  clones as edge-free islands chained by `depends_on`; they belong to the emitter's route via inherited `route`.
- Emitter inside a cone must set `skip_if_outputs_exist:false` (`skipped-emit-task-never-injects`) — documented in
  the nested-emission verification task.

---

## 12. ADR log (this LLD)

```
ADR-RC-001: not_taken as a distinct terminal status (not skipped+reason)
Context: routing needs a terminal state for untaken routes that survives resume and reads clean in status.
Options: (a) new status not_taken; (b) reuse skipped + reason field.
Decision: (a) new status, backed by persisted route_decisions as source of truth.
Reason: skipped self-heals and means idempotency; overloading forces reason-branching everywhere; a new enum
  value is deserialization-safe (Literal widening) and clearer in events/status.
Consequences: success math extends terminal-success set at finaliser; write_status/print gain not_taken+route;
  prepare_resume must preserve + re-derive not_taken.
```
```
ADR-RC-002: cones computed on the full build_dag graph; inferred cross-route coupling is a validation error
Context: build_dag infers edges from matching paths; cone exclusivity is ambiguous if computed on depends_on only.
Options: (a) compute cones on depends_on; (b) compute on the full graph + reject inferred cross-route edges.
Decision: (b).
Reason: validator must equal runtime graph; the duplicate-path bug (memory) is caught statically with a named
  error instead of a silent wrong cone.
Consequences: cross_validate runs a post-DAG pass; convergence must be declared (depends_on + join).
```
```
ADR-RC-003: existing stops re-framed additively through trip->record->act, pinned by characterization tests
Context: three hard-coded stops must become one mechanism without behaviour drift.
Options: (a) replace stop sites with breaker action; (b) keep stop sites, add trip_builtin + keep old events.
Decision: (b) additive.
Reason: preserves status/exit/event contract for existing log consumers and tests; drift becomes a failing pin.
Consequences: built-in breaker ids reserved; tripped_breakers/breaker.trip added alongside old events.
```
```
ADR-RC-004: stop/pause actions land on RunState.status="failed" (resumable) for MVP
Context: FR-CB2 lists fail/stop/pause; RunState.status literal is {running,succeeded,failed,cancelled}.
Options: (a) add "stopped"/"paused" statuses + new exit codes; (b) map stop/pause to failed(resumable), record
  the real action in tripped_breakers[].action.
Decision: (b) for MVP.
Reason: avoids changing the persisted status literal + CLI exit-code contract mid-epic; audit distinction lives
  in tripped_breakers. Dedicated statuses are a clean, additive non-MVP follow-on.
Consequences: operators read tripped_breakers[].action for stop-vs-pause; documented in ao status trailer.
```
```
ADR-RC-005: one bounded-control reader (read_control) with a size cap; read_gate becomes an alias
Context: loop/router/verdict all read small JSON; NFR-1 wants one audited surface; today's readers are unbounded.
Decision: introduce read_control + typed helpers with MAX_CONTROL_FILE_BYTES; ControlFileError base; GateError
  subclasses it.
Reason: single NFR-1 surface (ADR-0002 consequence) + closes an unbounded-read gap.
Consequences: verdict/router catch ControlFileError; loop keeps GateError; size cap is a named constant.
```

## Assumption log
```
ASSUMPTION: MVP engine is sequential (no parallel workers).  Risk: run_wall_clock_seconds granularity is
  task-boundary, not real-time.  Mitigation: eval point chosen to survive a future timer; documented limitation.
ASSUMPTION: team_size = 2 developers (<4 yrs) for sprint capacity (not stated in epic).  Risk: sprint count off.
  Mitigation: capacity math shown (§14) so re-planning with the real number is trivial.
ASSUMPTION: nested routers are out of MVP; validate rejects them.  Risk: a real workflow needs one.  Mitigation:
  schema (branches: list) already leaves room; lifting the rejection is additive.
ASSUMPTION: no author declares a breaker whose semantics duplicate a built-in (both latch once).  Risk: double
  audit record.  Mitigation: reserved builtin.* ids; documented.
```

---

## 13. Test strategy & acceptance matrix

Layered per CLAUDE.md (unit + integration + CliRunner E2E; fixed clock/seeds; both engine-API and CLI —
memory `engine-api-tests-dont-cover-cli`). Coverage target ≥80% on new modules.

| Req | Test (level) | Given / When / Then |
|---|---|---|
| FR-B1 | integration | router writes `{"routes":["bug"]}` → only bug cone runs, others `not_taken` |
| FR-B3 | unit | not_taken tasks never dispatched, `attempts==0`, no budget charge |
| FR-B4 | integration | multi-endpoint run succeeds with 2 routes selected, 2 not_taken; `state.status=="succeeded"` |
| FR-B5 | unit | `join:all` → not_taken if any dep not_taken; `join:any` → runs on ≥1 live dep, tolerates missing optional inputs |
| R2 | unit | two routes sharing an output path via *inferred* edge → `ao validate` fails naming both + path |
| NFR-3 | unit | unknown entry / unreachable endpoint / nested router / unknown breaker task_id → validate errors |
| FR-CB1/2/4 | integration | each of fail/stop/pause records `tripped_breakers` + `breaker.trip`; status per action |
| FR-CB3 | integration | verdict breaker `{"halt":true}` on `security-review` → run fails, trip recorded |
| §7 #1-6 | unit (fixed clock) | each MVP condition trips exactly at threshold, not before |
| R3 | characterization (engine+CLI) | §8.3 parity tests: status/exit/old-event pinned, new records added |
| FR-CB5 | integration | resume replays route_decisions (router not re-run); cleared stop_file not re-tripped; still-over injected cap re-trips |
| NFR-5 | unit | old `state.json` (no new fields) loads with defaults |
| NFR-2 | unit | same verdict files → identical activation set + topo order across two runs |
| E1 (granular) | integration | injected `emit_tasks:true` task injects on success; `injection_depth`=2 observed |

---

## 14. Task sequencing & capacity (sprint planning)

**Capacity math** (2-week sprint, 5-day weeks, 8h/day, 40% overhead; team <4 yrs). ASSUMPTION team_size=2:
- `GrossHoursPerSprint = 2 * 10 * 8 = 160`
- `NetFocusHoursPerSprint = 160 * 0.60 = 96`
- `CommitmentHoursPerSprint = 96 * (0.70..0.85) = 67.2 .. 81.6` → **commit ≈ 72 h/sprint (≈ 9 focus-days)**

12 tasks, each ≤ 3 days; total ≈ 25 dev-days ⇒ **2 sprints** (with headroom for the docs/e2e task).

**Dependency waves** (task ids created under the epic):
```
Wave 1 (schema + primitives, parallelisable):
  T-b7q2m4 schema+models      (blocks all)
  T-k9r3n8 bounded reader     (dep: b7q2m4 for field constants; can overlap)
  T-h5b2q7 nested-emission verification (no new dep; scheduled early to UNBLOCK E-gd8m4x)
Wave 2 (routing + breaker cores):
  T-c4w6p1 cone computation   (dep: b7q2m4)
  T-x8v4d3 breaker framework  (dep: b7q2m4)
Wave 3 (execution):
  T-m2h5t7 routing execution + run-success (dep: c4w6p1, k9r3n8)
  T-q5n7k2 six MVP conditions (dep: x8v4d3, k9r3n8)
  T-w6p2c8 ao validate checks (dep: c4w6p1)
Wave 4 (integration + parity + resume + observability):
  T-r3j9b6 re-frame stops     (dep: x8v4d3; characterization pins written first)
  T-t4m8x1 resume replay      (dep: m2h5t7, x8v4d3)
  T-n9k3r5 observability/status (dep: m2h5t7, x8v4d3)
Wave 5 (close-out):
  T-d8w4v2 tests + docs refresh (dep: all)
```
Sprint 1 ≈ Waves 1-2 + start Wave 3; Sprint 2 ≈ finish Wave 3 + Waves 4-5.

---

## 15. Execution-readiness gate

- Can a junior implement each module without guessing? **Yes** — pseudocode + exact hook sites (engine line
  refs), schema diffs, and typed interfaces are given.
- Can an AI agent execute without ambiguity? **Yes** — every decision (R1/R2/R3) is resolved with a rule, not an
  option.
- Are all interfaces/schemas defined? **Yes** — §2 (schema + models), §3 (reader), §6.2 (breaker ABC).
- Are failure scenarios handled? **Yes** — §11 edge cases per module; parity pins for R3.

Residual open questions (non-blocking):
- `OPEN_QUESTION`: should `stop_file` be workspace-relative only, or allow an operator-config absolute path? MVP:
  workspace-relative (NFR-1 traversal guard). Revisit if operators need an out-of-tree kill switch.
- `OPEN_QUESTION`: dedicated `RunState.status` values `stopped`/`paused` (ADR-RC-004) — schedule as a small
  non-MVP follow-on once a consumer needs the distinction beyond `tripped_breakers[].action`.
```

---

## 16. Addendum (E-3JTmVu, 2026-07-14) — `run_active_seconds` + breaker resume-extension

Follow-on epic [`E-3JTmVu-breaker-resume-extend`](../meta/tickets/E-3JTmVu-breaker-resume-extend/EPIC.md)
(not a reopening of this LLD/epic) closes two gaps this document and `T-t4m8x1`'s STATUS.md flagged but
left for a later owner. Both pieces are additive — every decision above (R1/R2/R3, the six MVP
conditions, the framework's latch semantics) is unchanged.

### 16.1 `run_active_seconds` — a pause/resume-immune alternative to `run_wall_clock_seconds`

§7's `run_wall_clock_seconds` is, by design, a **deadline** semantic: elapsed is measured from
`RunState.started_at` (the run's *original* creation time, never touched by `prepare_resume`), so a run
paused for hours/days and resumed counts the entire gap as elapsed and can trip on the very next task
boundary. That is intentional for workflows that genuinely want a wall-clock SLA regardless of operator
pauses.

`run_active_seconds` is the complementary **"how much real work happened"** semantic: it sums
`(TaskRunState.ended_at - TaskRunState.started_at)` over every SETTLED task in `state.tasks`. Because
each task's timestamps are its own, independent window, any gap between one task's `ended_at` and the
next task's `started_at` — including an operator pause-then-resume — contributes nothing. In-task waits
(429/quota/budget retry sleeps) DO count, since those happen inside a task's own execution window and
represent the engine genuinely busy/blocked on that task, not an operator-initiated stop. Implemented as
`RunActiveSecondsBreaker` in `breakers.py`, registered under `run_active_seconds` — the ninth breaker
condition (see the module's own docstring header for the up-to-date count). Both conditions coexist in
the schema and registry; a workflow author picks whichever fits (`run_wall_clock_seconds` for a hard
deadline, `run_active_seconds` for a "don't runaway while actually executing" cap).

Like `_consecutive_failure_streak` (§7), the summation is a pure reconstruction from persisted
`state.tasks[*].started_at/ended_at` — no new bookkeeping field, so it is resume-safe by construction and
requires zero changes to `prepare_resume`.

**Engine fix required to make "in-task waits DO count" actually true** (`engine.py`, one guarded line):
`TaskRunState.started_at` has exactly one writer in `engine.py` — a review of this epic's diff found that
writer unconditionally overwrote `started_at` on every dispatch, including the quota-exhaustion and
429/budget-wait redispatch loops (`cursor -= 1; continue`, then straight back through the same write
site). That silently reset `started_at` to the POST-wait time, dropping the wait itself from
`run_active_seconds`'s sum — directly contradicting the paragraph above. Fixed by guarding the write to
fire only when `ts.started_at is None` (i.e. the task's first-ever dispatch); `prepare_resume` already
hands a fresh `TaskRunState()` (started_at unset) to any task it resets to pending for `ao resume`, so a
resumed dispatch still gets its own fresh `started_at` — only the SAME-process wait-and-redispatch loops
are affected. Verified safe: `started_at` has no other reader in the codebase besides
`RunActiveSecondsBreaker`, and the full regression suite (including `tests/test_engine_budget.py`,
`tests/test_stop_reframe_parity.py`, `tests/test_resume_replay.py`) stays green, unmodified. See
`tests/test_run_active_seconds_breaker.py::TestRunActiveSecondsCountsQuotaWaitTime` for the integration
test that pins this.

### 16.2 Scoped breaker-threshold extension + un-latch (`ao resume --extend-breaker`)

§6.3's per-`spec.id` latch (`state.tripped_breakers`) is intentionally permanent for the life of a run —
an id, once recorded, is never re-evaluated. `T-t4m8x1` (Wave 4) explicitly flagged this as a gap for
resumed runs: a breaker that tripped before the run stopped stays latched forever, even if an operator
believes the underlying condition should no longer block progress (e.g. they've reviewed the situation and
want to raise the cap for this run only).

The fix is deliberately **scoped**, not a blanket "un-latch everything on resume": a blanket unlatch would
regress the existing, intentional "a resumed run doesn't immediately re-trip on a still-true condition"
behaviour for every OTHER breaker in the workflow (§9) — an operator who extends breaker A should not
silently reset breaker B's latch too.

Mechanism:
- `RunState.breaker_overrides: dict[str, float]` (breaker id → absolute overridden threshold, in the
  condition's own native unit — seconds, USD, or a raw count) — defaulted `{}` for NFR-5, same pattern as
  `tripped_breakers`/`route_decisions`.
- `evaluate_breakers` resolves the effective threshold **once, centrally**:
  `state.breaker_overrides.get(spec.id, spec.threshold)`, evaluating a `spec.model_copy(update=
  {"threshold": effective_threshold})` when an override is present. No individual `Breaker` subclass
  needs to know overrides exist — this is what makes the mechanism uniform across all nine conditions
  (and any future one) rather than a wall-clock-only special case.
- `apply_breaker_extension(state, spec, *, extend_by_seconds, extend_by_same, clock, run_log) -> float`
  (standalone, same calling convention as `record_trip`) computes the new effective threshold (current
  effective + either an explicit delta or the ORIGINAL `spec.threshold` again for "extend by the same
  amount set at startup"), writes it into `breaker_overrides`, removes the matching `TrippedBreaker`
  record(s) for that id (the un-latch), and logs a structured `breaker.extend` event mirroring
  `record_trip`'s `breaker.trip` logging shape. Rejects a non-positive `extend_by_seconds` (would silently
  shrink or no-op the threshold, bypassing the schema's own `exclusiveMinimum: 0` invariant).
- `ao resume --extend-breaker <id> [--extend-by-seconds <f> | --extend-by-same]` validates the id exists
  in `wf.circuit_breakers`, applies the extension to the loaded/prepared `RunState` before `orch.run()`,
  and prints an old→new confirmation line.

**Persistence fix from review**: the extension is applied to the same `RunState` instance later passed
into `orch.run()`, so `orch.run()`'s own save path *would* persist it in the common case — but `resume`
still runs other CLI-only validation (e.g. `--on-exhaustion`) AFTER the extension block and BEFORE
`orch.run()`, and any of those can `typer.Exit(1)` first. Relying solely on `orch.run()`'s later save would
silently discard an already-logged, already-confirmed extension whenever an unrelated flag was also bad.
Fixed by an explicit `rs_store.save(existing)` immediately after `apply_breaker_extension` returns — the
extension is durable the moment it's applied and confirmed, independent of anything checked afterward.

Explicitly out of scope (do not build without a fresh epic): live/in-process threshold mutation for an
already-running `ao run`/`ao resume` invocation (no `WorkflowSpec` hot-reload, no stop-file-style live
poll for thresholds) — this mechanism only ever applies at `ao resume` time, before the run resumes.

