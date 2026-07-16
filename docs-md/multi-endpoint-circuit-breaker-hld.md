# HLD — Multi-endpoint workflows & circuit breaker

- Epic: [`E-rc7k2v-run-control-routing-breakers`](../meta/tickets/E-rc7k2v-run-control-routing-breakers/EPIC.md)
- Status: **Implemented** (LLD: [`lld-run-control-routing-breakers.md`](lld-run-control-routing-breakers.md) — all tickets landed and merged)
- Date: 2026-07-09
- Decision record: [`ADR-0002`](adr/ADR-0002-conditional-branching-multi-endpoint.md)
- LLD (implementable detail): [`lld-run-control-routing-breakers.md`](lld-run-control-routing-breakers.md)
- Related: core HLD [`hld-agent-orchestrator.md`](hld-agent-orchestrator.md) · dynamic injection [`guide-dynamic-task-injection.md`](guide-dynamic-task-injection.md) · budget HLD [`token-budgeting-hld.md`](token-budgeting-hld.md)

## 1. Problem

Two related gaps in the engine's run-completion semantics:

**A. Single implicit endpoint.** Today every task in `workflow.json` executes; "endpoints" are just DAG sinks, and a run succeeds only when *all* tasks succeed. The desired philosophy: one workflow file holds several routes (epic / task / bug / documentation, …) that share a common head (e.g. a single `prompt.md`), diverge based on the prompt's *content*, and terminate on **different endpoint tasks**. Untaken routes must not run and must not count as failures.

**B. No run-level circuit breaker.** The engine is fail-fast on *task* failure (first failed task → run failed), and has two special-cased run-level stops (budget `on_exhaustion`, quota max-wait). There is no general, spec-declared way to say "if condition X arises anywhere in the run, stop/fail the whole workflow".

## 2. Current state (facts the design builds on)

| Fact | Where |
|---|---|
| Sequential topo-order execution *(default `max_parallel=1`; opt-in parallel dispatch via ADR-0007)*; first task failure breaks the run | `engine.py` (`failed = True; break`) |
| Every task always dispatches; `skip_if_outputs_exist` is idempotency, not routing | `engine.py` / `models.TaskSpec` |
| Gate-verdict pattern already exists: engine reads a small JSON control file written by a task and acts on a boolean field | `LoopSpec.gate_output_path` / `gate_field` |
| Dynamic task injection: a succeeded `emit_tasks` task's manifest injects new tasks; persisted for resume | `engine._inject`, `runstate.injected_tasks` |
| Fixed-aggregator convention lets static tasks depend on unknown-N fan-out | memory `dynamic-fanout-fixed-aggregator-contract` |
| Existing run-level stops: budget exhaustion (`stop`/`wait`), quota episode max-wait, unsatisfiable estimate | `engine.py`, `budget.py`, `executors/claude_cli.py` |

Reading small **control files** (gate verdicts) is an established, sanctioned exception to NFR-1 (paths-only): the engine reads a bounded JSON verdict, never payload artifacts. Both features below reuse exactly this mechanism — no new NFR-1 surface.

## 3. Scope / requirements (draft)

### Functional
- **FR-B1 Routing gate**: a task can be declared a *router*: after it succeeds, the engine reads its verdict JSON (e.g. `{"routes": ["bug"]}`) and activates only the branches selected. Multi-select allowed (`["bug", "documentation"]`).
- **FR-B2 Branch declaration**: branches are declared statically in the spec (branch id → entry task ids), so `ao validate` can check them; membership of downstream tasks is derived from the DAG, not listed by hand.
- **FR-B3 Not-taken semantics**: tasks on unselected branches (and their exclusive descendants) end in a distinct terminal state `not_taken` (surfaced under the existing `skipped` status with a reason, or a new status — LLD decision). They never dispatch, never consume budget/attempts.
- **FR-B4 Run success**: a run succeeds when every *activated* task reaches `succeeded`/`skipped`; `not_taken` tasks do not block success. Multiple sinks may therefore be terminal per run.
- **FR-B5 Joins**: a task depending on tasks from several branches needs a declared `join` policy: `all` (default — becomes `not_taken` if any dependency is `not_taken`) or `any` (runs when at least one dependency succeeded and the rest are `not_taken`; missing declared inputs from not-taken producers are tolerated for `any`).
- **FR-CB1 Circuit breaker spec**: workflow-level `circuit_breakers: [...]`, each with a condition (from §6 catalog), threshold parameters, and an action.
- **FR-CB2 Actions**: `fail` (mark run failed now, resumable), `stop` (graceful: finish/kill in-flight task per flag, mark run `stopped`, resumable), `pause` (persist state, wait for operator / `ao resume`). Scope: `run` (default) or `branch` (trip kills one branch → its remaining tasks `cancelled`, run continues — non-MVP).
- **FR-CB3 Verdict breaker**: any task can be declared a breaker gate — if its verdict JSON contains e.g. `{"halt": true, "reason": "…"}`, the breaker trips (security gate, design-review "fundamentally broken", etc.).
- **FR-CB4 Observability**: route decisions and breaker trips are structured log events (`branch.route`, `breaker.trip`) and persisted in run state (which route(s), which breaker, when, why).
- **FR-CB5 Resume**: route decisions and tripped-breaker facts survive `ao resume`; resume does not re-run the router or re-trip a cleared condition.

### Non-functional
- **NFR-1 preserved**: engine reads only bounded verdict/control JSON, never artifacts.
- **Deterministic**: same verdict files → same activation set and same topo order.
- **Validated**: `ao validate` rejects unknown branch entry tasks, unreachable endpoints, breakers referencing unknown tasks, and `any`-joins whose inputs cannot be satisfied on any single route.
- **Injected tasks participate**: a task injected on an activated branch inherits that branch; breakers count injected-task failures too.

## 4. Design sketch — routing & multiple endpoints

Chosen direction (see ADR-0002 for alternatives): **first-class gate-verdict branching**, mirroring `LoopSpec`'s shape:

```jsonc
{
  "tasks": [
    {"id": "classify-prompt", "agent": "triage", "instruction": "…/00-triage.md",
     "inputs": ["runs/x/prompt.md"], "outputs": ["outputs/route.json"]},
    {"id": "bug-repro",  "…": "…"},
    {"id": "bug-fix",    "…": "…"},
    {"id": "epic-design","…": "…"},
    {"id": "doc-update", "…": "…"}
  ],
  "branches": {
    "router_task_id": "classify-prompt",
    "verdict_path": "outputs/route.json",   // {"routes": ["bug"]}
    "routes": {
      "bug":           {"entry": ["bug-repro"]},
      "epic":          {"entry": ["epic-design"]},
      "documentation": {"entry": ["doc-update"]}
    },
    "default_route": null                    // null → unknown verdict = validation-style run failure
  }
}
```

Mechanics:
1. Pre-run, `build_dag` computes each route's **cone**: entry tasks + transitive descendants reachable *only* via that route (a task reachable from ≥2 routes or from outside any route is *shared* and follows join policy).
2. Router succeeds → engine reads verdict (same bounded-JSON reader as loop gates) → marks every task in unselected cones `not_taken` → normal loop proceeds; scheduler skips `not_taken` tasks without dispatch.
3. Run finalization treats `not_taken` like `skipped` for success computation (FR-B4).
4. Nested routers (a router inside a branch) compose naturally — each router only ever *deactivates* cones downstream of itself.
5. `emit_tasks` remains the escape hatch when even the route shapes aren't known statically; both mechanisms coexist (a route's cone may contain an emitter).

## 5. Design sketch — circuit breaker

```jsonc
"circuit_breakers": [
  {"id": "too-many-failures", "condition": "task_failures", "threshold": 3,
   "window": "run", "action": "stop"},
  {"id": "runaway-fanout", "condition": "injected_task_count", "threshold": 60, "action": "fail"},
  {"id": "security-gate", "condition": "verdict", "task_id": "security-review",
   "verdict_path": "outputs/security-verdict.json", "field": "halt", "action": "fail"},
  {"id": "run-deadline", "condition": "run_wall_clock_seconds", "threshold": 28800, "action": "stop"}
]
```

- Evaluated at **task boundaries** (after each task settles, before the next dispatch) — cheap and sufficient for a sequential engine; a future parallel engine evaluates at the same points per worker plus on a timer for time conditions.
  **Update (2026-07-15, ADR-0007):** opt-in parallel *dispatch* shipped, but breaker evaluation did
  **not** move to per-worker — it stayed exactly here, centralized on the main thread, one
  `_settle_completed_task` call at a time, regardless of `max_parallel`. Only task dispatch runs
  concurrently; the settle/evaluate path (and every `RunState` mutation) remains fully serialized by
  design (ADR-0007 D3). The one real consequence: at `max_parallel > 1`, completion order across
  independent tasks is thread-timing-dependent, so *which* task's settle trips a count-based breaker
  (`task_failures`/`consecutive_failures`) can vary run-to-run — accepted and documented as R4 in
  `E-IasNXu-parallel-execution/EPIC.md`; `max_parallel=1` remains fully deterministic. No timer-based
  check was added for time conditions (`run_wall_clock_seconds` remains task-boundary-only).
- A trip is recorded in run state (`tripped_breakers: [{id, at, detail}]`) and emitted as a `breaker.trip` event; action then executes.
- Existing hard-coded stops (budget exhaustion, quota max-wait, unsatisfiable estimate) are **re-framed as built-in breakers** over time so there is one trip/record/act path — no behavior change, one mechanism.
- Reset/half-open semantics apply only to *transient* conditions (429 bursts, quota): those already have episode logic; declarative breakers in MVP are latch-only (trip once, act).

## 6. Circuit-break condition catalog (requested outline)

| Group | Condition | Parameters | Notes |
|---|---|---|---|
| Failure-based | `task_failures` | threshold, window (`run`/branch/last-N-tasks) | total failed tasks incl. injected |
| | `consecutive_failures` | threshold | catches systemic breakage (bad branch, broken env) |
| | `failure_ratio` | ratio, min-sample | for large fan-outs |
| | `same_task_exhausted` | — | task failed all `max_attempts` (today = fail-fast; breaker makes it policy) |
| Cost/budget | `total_tokens` | threshold | exists as `BudgetSpec.total_tokens` → becomes built-in breaker |
| | `token_rate` | rate window | exists as `BudgetSpec.rate` |
| | `run_wall_clock_seconds` | threshold | whole-run deadline (task timeout exists; run deadline doesn't) |
| | `projected_cost_exceeds` | threshold | estimator says remaining work can't fit budget (exists as "unsatisfiable estimate") |
| | `task_cost_usd` **(implemented, E-9h3m7k)** | threshold (USD) | any single task's cumulative ACTUAL cost (all retry attempts) ≥ threshold — distinct from `projected_cost_exceeds`, which is a pre-flight estimate |
| | `run_cost_usd` **(implemented, E-9h3m7k)** | threshold (USD) | run-wide cumulative ACTUAL cost (sum across all tasks) ≥ threshold |
| Provider health | `quota_exhaustion_wait_exceeded` | max wait | exists (`_quota_exhausted_since` episode timer) |
| | `provider_429_count` | threshold, window | repeated rate-limits despite backoff |
| | `executor_spawn_failures` | threshold | CLI missing, auth broken — fail fast, don't burn retries |
| Verdict/content gates | `verdict` | task id, verdict path, field | security gate, design-review "halt", QA gate |
| | `output_validation_failures` | threshold | declared outputs missing/empty N times |
| Progress/liveness | `loop_max_iterations_no_converge` | — | loop ended by cap, not by gate verdict |
| | `no_artifact_progress_seconds` | threshold | nothing new written run-wide for T (stuck agent) |
| Structural/runaway | `injected_task_count` | cap | runaway fan-out |
| | `injection_depth` | cap | emitter-emits-emitter recursion guard |
| | `dag_size` | cap | total live tasks |
| External/manual | `stop_file` | path | operator drops a file / `ao stop --run-id` writes it |
| | `os_signal` | — | SIGINT/SIGTERM → graceful stop (partially exists as cancellation) |
| Environment safety | `workspace_disk_usage` | cap | agents can generate GBs |
| | `git_workspace_dirty` | task ids | repo must be clean at declared checkpoints |

MVP recommendation: `task_failures`, `consecutive_failures`, `run_wall_clock_seconds`, `verdict`, `injected_task_count`, `stop_file` — plus re-framing the existing budget/quota stops. `task_cost_usd`/`run_cost_usd` landed later (E-9h3m7k) as actual-cost breakers. The rest are declared in the schema but can land incrementally.

## 7. Out of scope (this draft)
- Parallel scheduling (breaker evaluation points are designed to survive it).
- Branch-scoped breaker action (`scope: branch`) — non-MVP.
- UI/status visualisation of routes; `ao status` gains a `route`/`not_taken` column only.
- LLD: exact schema names, `skipped`-vs-new-status decision, join edge cases, tests — separate LLD/task-breakdown phase.

## 8. Proposed epic shape
One epic — *"Run-control: conditional routing, multi-endpoint success, circuit breakers"* — both features change the same code (run-completion semantics, engine task loop, run state, schema, `ao validate`) and land naturally as: schema+models → routing/not-taken → breaker framework + MVP conditions → re-frame existing stops → docs/tests.
