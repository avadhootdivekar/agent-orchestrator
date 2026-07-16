# ADR-0007 — Opt-in parallel task execution (bounded thread pool, serialized core)

- Status: **Accepted — Implemented** (design approved 2026-07-15; user pre-approved the mechanism —
  this ADR records it; epic `E-IasNXu-parallel-execution` shipped 2026-07-15, all 5 tasks done,
  857 passed / 3 skipped, 93% coverage — see "Implementation notes" below)
- Date: 2026-07-15
- Deciders: Avadhoot Divekar (user), Claude (architect role)
- Related: Epic [`E-IasNXu-parallel-execution`](../../meta/tickets/E-IasNXu-parallel-execution/EPIC.md) · design doc [`parallel-execution-hld.md`](../parallel-execution-hld.md) · ADR-0003 (settings precedence — `max_parallel` rides the invocation chain) · ADR-0006 (per-agent config vs run-level flags — why `max_parallel` is run-level, not per-agent) · ADR-0001 (orchestration approach) · `src/agent_orchestrator/engine.py` (`Orchestrator.run`, `_run_with_retries`)

## Context

The engine is strictly serial today. `Orchestrator.run()` walks one deterministic Kahn
topological order (`dag.py::Graph.topological_order`, sorted for determinism) with a single
`while cursor < len(order):` cursor, dispatching **one** task at a time and calling
`save(state)` after each step. No `threading`, `asyncio`, or `concurrent.futures` appears
anywhere in `src/` (confirmed 2026-07-15).

The dominant wall-clock cost of a run is the `claude` subprocess launched inside
`ClaudeCliExecutor.execute()` — it is I/O-bound (it waits on a child process over pipes) and
releases the GIL while blocked. On a wide DAG (many independent tasks with no dependency edge)
the engine idles almost entirely, running one agent when it could run several.

We want to run independent *ready* tasks concurrently, up to a bound `N`, **without** regressing
any existing guarantee: budget gate/charge/reconcile, circuit-breakers, routing, dynamic task
injection (`emit_tasks`), loops, quota/429 handling, self-heal, and `RunState`
mutation+persistence are all interleaved into today's serial loop and all assume a single writer.

## Decision

Add **opt-in parallel execution via a bounded thread pool with a fully serialized engine core.**

### D1 — Opt-in, default serial (`max_parallel`, default 1)

A new run setting `max_parallel: int` (default **1**). At `N=1` the engine takes the byte-identical
serial path — same dispatch order, same per-step `save`, same events. `N>1` opts into waves.
This is an explicit, testable regression gate, not a "should be equivalent" claim (see D6).

### D2 — Threads, not processes, not an async rewrite

- **Threads (`concurrent.futures.ThreadPoolExecutor`).** The parallel work is the `claude`
  subprocess: I/O-bound, GIL-releasing, so Python threads deliver real wall-clock parallelism at
  near-zero cost. Threads share the process, so `RunState`, the artifact store, and the executor
  instance are all directly reachable — no serialization boundary.
- **Processes — rejected.** Would require pickling `TaskContext`/`RunState`/executors across an
  IPC boundary, a cross-process story for artifact paths, and would shatter the single-writer
  `RunState` model. No benefit: the heavy compute already lives in the child `claude` process,
  not in Python.
- **asyncio — rejected.** The engine and both executors (`ClaudeCliExecutor`, `FakeExecutor`)
  are synchronous and subprocess-based; going async is a full rewrite of the engine and executor
  contract (`Executor.execute(ctx) -> TaskResult`) for no parallelism gain over threads on a
  subprocess-bound workload.

### D3 — Serialized core + worker dispatch (single-writer `RunState`)

**Only** the executor dispatch runs on worker threads: `_run_with_retries(...) ->
self._executor.execute(ctx)` (+ its retry `_sleeper` backoff). `_run_with_retries` is already a
pure reader — it reads `task`/`workflow`/`agents`/`repo_paths` and only `state.run_id` off
`RunState`, mutates nothing, and returns a `TaskResult` (confirmed `engine.py`).

**Everything else stays on the main thread**, exactly as today: budget gate/charge/reconcile,
breaker evaluation, the router-success hook, task injection + DAG rebuild, loop-gate
clone/inject, quota/429/self-heal requeue, `RunState` mutation, and every `save(state)`. As each
future completes, the main thread post-processes that one task to completion before touching the
next. The main thread is the **sole mutator of `RunState` and sole caller of `save`** → no locks,
no shared-state races, and every existing invariant is preserved by construction.

### D4 — Structural / routing tasks are serial barriers

A task is a **barrier** (must run alone — nothing else in flight when it starts, and no new task
launched until it fully settles) when it is any of:

- `emit_tasks == True` (reshapes the DAG by injecting tasks + rebuilding order),
- a **loop gate** task id (`_loop_for_gate(workflow, tid) is not None`, including `__iter` clones),
- a **router** task id (`router_task_id` of any `RouterSpec`; its success hook marks whole route
  cones `not_taken`).

Barriers mutate the topology or the route/branch bookkeeping that the ready-set and budget gate
read; running them concurrently with siblings would race the graph. Making them solo keeps their
side effects atomic with respect to scheduling. Non-barrier waves resume immediately after a
barrier settles. This is why the general-case MVP is correct without a rewrite of the routing,
injection, loop, or breaker subsystems.

### D5 — `max_parallel` is invocation-scoped, not a workflow-spec field

`max_parallel` guards the run's concurrency (the machine's wallet/pressure), not a task's
behavior — it is the same category as quota/budget. Per **ADR-0003 §3** those settings ride the
invocation chain only. Therefore:

```
CLI --max-parallel  >  env AO_MAX_PARALLEL  >  .ao/config.yaml: max_parallel  >  built-in default (1)
```

resolved in the existing `_resolve_run_settings()` (cli.py), stored on `ProjectConfig`, and passed
to the `Orchestrator(...)` constructor — **identical plumbing to `quota_max_wait_seconds`.**

Consequences of D5:
- **No `workflow.schema.json` / `agents.schema.json` change.** `max_parallel` is not a spec field;
  it never enters a workflow/agents file (quota settings likewise are absent from those schemas).
  This sidesteps the "new spec field ⇒ update the JSON schema" gotcha entirely.
- **Not a per-agent knob** (ADR-0006): concurrency is a whole-run property, and a per-agent value
  would be meaningless (which agent's `N` bounds a mixed-agent wave?). A single run-level fill-in
  default is the correct granularity.

### D6 — Determinism strategy

- **Launch order is deterministic**: within each wave, candidates are taken in the existing
  sorted-Kahn `order`, so which tasks launch (and in what order) is reproducible.
- **`N=1` is byte-identical** to the pre-epic serial loop (explicit regression gate).
- **`N>1` completion order is inherently nondeterministic** (thread timing). Correctness is
  therefore designed to be **order-independent**: each drained result mutates only its own
  `TaskRunState` plus commutative shared counters (budget `consumed_tokens` is per-task
  reverse-estimate + add-actual; final value is order-independent). Tests force a deterministic
  completion order with a **gated/latched executor** rather than relying on timing.

### D7 — Failure / breaker / cancellation: drain, don't kill

When a task fails, a breaker trips (halt), the budget becomes unsatisfiable/stops, or `cancel_fn`
fires while `K` tasks are in flight, the engine **stops admitting new tasks, drains the in-flight
set** (lets running workers finish and post-processes each result on the main thread), then
finalizes. Worker `claude` subprocesses are **not** hard-killed mid-flight (they may be writing
artifacts; a half-written artifact would break idempotent resume). The final `state.status`
follows today's rules (`failed`/`cancelled`), and the run stays **resumable** — because the
single-writer main thread persisted every drained result before stopping. Requeue signals
(quota/429/self-heal) return the affected task to `pending` and let its siblings drain; the task
becomes eligible again on a later wave after its wait.

## Alternatives considered

- **Per-task locks around `RunState`** — rejected. Locking is unnecessary once the core is
  single-threaded (D3); it would add contention and deadlock surface for zero benefit.
- **Parallelize everything, reconcile with a merge/actor model** — rejected as massive scope for a
  general-case MVP; the serialized-core design gets the wall-clock win where it actually is (the
  subprocess) with a minimal, auditable diff.
- **`max_parallel` as `workflow.defaults.max_parallel`** (author-intent, per-workflow) — deferred
  to Non-MVP. It would require a `workflow.schema.json` change and a precedence merge with the
  invocation value; ADR-0003 §3 keeps run-pressure settings invocation-scoped for MVP.
- **Hard-kill in-flight workers on halt/cancel for a faster stop** — rejected (D7): risks
  half-written artifacts and non-resumable state; draining is safer and bounded by task timeouts.

## Consequences

- **Executors must be thread-safe for concurrent `execute()` calls.** `ClaudeCliExecutor` writes
  only to per-task/attempt `output_dir`s and builds a fresh subprocess per call → safe (to be
  re-verified as an acceptance check). `FakeExecutor` carries mutable per-call counters
  (`_gate_invocations`, `_rate_limited_once`, `_quota_exhausted_remaining`) and is **not**
  thread-safe as written — the `N>1` test harness must use a purpose-built gated/thread-safe
  executor (or those counters must be guarded).
- **Log lines from workers interleave under `N>1`.** Python's `logging` handlers are internally
  locked, so concurrent `.info()` is safe; only ordering interleaves, which is acceptable and
  absent at `N=1`.
- The "do we also add a CLI flag?" question is answered per ADR-0006: yes here, because concurrency
  is a legitimate whole-run knob and `--max-parallel` is a precedence-correct fill-in default, not a
  silent per-agent clobber.
- Future extensions (per-workflow `defaults.max_parallel`, process executors, adaptive `N`) are
  unblocked but explicitly out of MVP scope.

## Implementation notes (2026-07-15, T-EJKD6f)

Epic `E-IasNXu-parallel-execution` (5 tasks: T-JXiI9j, T-j8YLGd, T-VSfAUN, T-TNleFt, T-EJKD6f) shipped
2026-07-15. The decision above stands unchanged; this addendum records how the build matched it.
Full as-built detail (pseudocode-level) lives in
[`parallel-execution-hld.md`](../parallel-execution-hld.md) §14 — summarized here:

- **D1/D6 (default-serial, `N=1` byte-identical) — held exactly.** `tests/test_engine*.py` (the
  pre-epic engine suite) passes **unedited** at the default `max_parallel=1`, gating every task in
  the epic. `--max-parallel 0` is treated as unset (falls through to the default, no error) —
  identical to how `--quota-max-wait 0` / `--max-attempts 0` already behaved on the same
  `or`-chain pattern; only a negative value is rejected (`Exit(1)`).
- **D3 (serialized core + worker dispatch) — held exactly.** Only `_run_with_retries` runs on a
  `ThreadPoolExecutor` worker; every `RunState` mutation, `save`, budget/breaker/router/inject/loop
  decision stays on the main thread, via a `_prepare_and_maybe_dispatch(...) -> DispatchPrep` /
  `_settle_completed_task(...) -> SettleResult` split of the old inline per-task body (verbatim
  extraction, proven by the `N=1` gate above).
- **D4 (structural/routing tasks are serial barriers) — held exactly**, implemented as
  `_is_barrier()` reusing the existing `_loop_for_gate`/`_router_for_task` lookups rather than
  hand-rolled id matching.
- **D7 (drain, don't kill) — held, and the one open item from the original design (the budget-wait
  "BLOCKED" path, §7.1) is now fully closed.** `_prepare_and_maybe_dispatch` takes an
  `in_flight_nonempty` parameter: a budget-blocked task with siblings already in flight returns
  `BLOCKED` immediately with **no sleep** (avoids the deadlock risk called out in EPIC.md R3 — never
  sleep while a sibling holds the capacity that might free the window); with nothing in flight
  (always true at `N=1`) it sleeps inline exactly as the pre-epic engine did. `TestBudgetCap
  UnderConcurrency` / `TestNoBudgetDeadlockOnRollingWindow` (T-VSfAUN) prove this under real
  concurrency; breaker-halt and cancel drain paths are proven the same way (T-VSfAUN AC-4/AC-5).
- **R4 (breaker count nondeterminism at `N>1`) accepted as designed** — `N=1` stays deterministic;
  which task trips a count-based breaker (`task_failures`/`consecutive_failures`) at `N>1` can vary
  with completion order, matching D6's "order-independent correctness, not order-independent
  breaker attribution" framing. Not treated as a defect.
- **Cross-references verified 2026-07-15:** ADR-0003 §3 (invocation-chain precedence) and ADR-0006
  (run-level vs per-agent flag) both still hold as cited in D5 — `max_parallel` shipped exactly as a
  run-level, invocation-scoped setting with no schema change.
