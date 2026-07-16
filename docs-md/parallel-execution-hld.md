# HLD + LLD — Opt-in parallel task execution (bounded thread pool, serialized core)

- Epic: [`E-IasNXu-parallel-execution`](../meta/tickets/E-IasNXu-parallel-execution/EPIC.md)
- Decision: [`ADR-0007`](adr/ADR-0007-parallel-task-execution.md)
- Related: ADR-0003 (settings precedence), ADR-0006 (per-agent vs run-level flags), [`hld-agent-orchestrator.md`](hld-agent-orchestrator.md)
- Date: 2026-07-15
- Status: Implemented (as-built reconciled 2026-07-15). This doc is the authoritative HLD/LLD; the epic ticket carries the condensed version. Reconciled against the merged `engine.py` / `cli.py` / `project_config.py` by **T-EJKD6f** — see §14 for as-built deviations from this design.

All engine line references below were verified against `src/agent_orchestrator/engine.py` on 2026-07-15.

---

## 1. Goal & scope

Run independent *ready* tasks concurrently, up to a bound `N = max_parallel`, without regressing
any existing engine guarantee. Default `N=1` is byte-identical to today's serial loop.

### In scope (MVP)
- `max_parallel` setting (CLI/env/config/default) plumbed exactly like `quota_max_wait_seconds`.
- A wave/barrier scheduler in `Orchestrator.run()` that dispatches ≤N independent ready tasks on a
  bounded `ThreadPoolExecutor`, with the entire engine core serialized on the main thread.
- Correct interaction of concurrency with budget, quota/429, self-heal, circuit-breakers, routing,
  `emit_tasks`, and loops (structural/routing tasks are serial barriers).
- Drain-based failure/cancel semantics; resumable `RunState`.

### Out of scope (Non-MVP — see §12)
- Per-workflow `defaults.max_parallel`; process/distributed executors; adaptive/auto `N`;
  per-agent concurrency caps; cross-task speculative execution; reordering the deterministic
  launch order for throughput.

---

## 2. Orchestration-landscape context (why threads + serialized core)

| Engine | Parallelism model | State ownership under concurrency |
|--------|-------------------|-----------------------------------|
| Airflow | Executor processes/workers (Local/Celery/K8s); scheduler distributes ready tasks | Central metadata DB; workers are stateless, DB is the single writer |
| Prefect | Task runners (thread/process/dask/ray); concurrent futures | Orchestration API/DB tracks state; runners report back |
| Dagster | Multiprocess/in-process executors; op-level concurrency | Run storage / event log is source of truth |
| Temporal | Workflow (deterministic, serialized) + activities (concurrent) | Workflow code is single-threaded & deterministic; activities are the parallel edge |
| Argo Workflows | Pods per step, DAG scheduler fans out ready nodes | K8s + workflow CR status |

**Positioning (ties directly to ADR-0007):**
- **Match** Airflow/Argo in "fan out independent ready DAG nodes up to a bound".
- **Mirror** Temporal's most valuable safety property — *the orchestration core is single-threaded
  and deterministic; only the side-effecting edge (our executor / their activity) runs concurrently*
  — which is exactly the serialized-core + worker-dispatch split (D3).
- **Beat** the heavyweight engines on **operational simplicity**: no broker, no worker fleet, no
  external DB. Concurrency is one integer (`max_parallel`) over the existing single-process engine;
  a wave is a `ThreadPoolExecutor`.
- **Avoid** the complexity we don't need: no process/IPC serialization (Dagster multiprocess), no
  async rewrite (Prefect runners), no distributed scheduler. The heavy compute is already in the
  child `claude` process, so in-process threads capture the win.

---

## 3. HLD

### 3.1 What changes vs today

Today (`engine.py:252`): `while cursor < len(order):` — pop one task id, run the full
pre-dispatch → dispatch → post-dispatch sequence inline, `save` after each, advance/rewind cursor.

New: the cursor walk is replaced by a **wave loop**. Each wave:
1. computes the **ready set** (deps settled, inputs present, not done/not_taken/running),
2. **launches** up to `N` ready *non-barrier* tasks on a shared `ThreadPoolExecutor` (only
   `_run_with_retries` runs on the worker),
3. **drains** completed futures one at a time on the main thread, running the *unchanged*
   post-dispatch settlement for each,
4. **barriers** (`emit_tasks` / loop-gate / router tasks) run **solo** — the wave admits nothing
   else while a barrier is scheduled, so its DAG/route side effects are atomic w.r.t. scheduling.

At `N=1` the wave holds exactly one task and drains it before the next → identical to today.

### 3.2 Wave / barrier scheduler (diagram)

```
                     ┌───────────────────────── main thread (single writer of RunState) ─────────────────────────┐
                     │                                                                                            │
  build_dag ───────► │  preds = invert(graph.adjacency())                                                        │
  (once + on         │                                                                                            │
   every reshape)    │  WAVE LOOP  (repeat until no runnable work):                                               │
                     │   1. ready = [t in order : preds(t) all settled, not done/not_taken/running/in_flight]    │
                     │   2. if ready[0] is a BARRIER and in_flight != {} : ─────────► DRAIN one, goto 1           │
                     │   3. for t in ready (in `order`), while len(in_flight) < N:                                │
                     │        - if barrier and in_flight != {}: stop filling (barrier waits for empty)            │
                     │        - main-thread pre-checks: not_taken? done? should_skip? apply_join? inputs?         │
                     │        - BUDGET GATE + CHARGE (main thread)   ── may block → see §7                        │
                     │        - ts.status="running"; ts.started_at=…; save(state)                                 │
                     │        - submit _run_with_retries(t) ──────────────┐                                       │
                     │        - if t is a barrier: break (run it solo)     │                                      │
                     │   4. if in_flight == {} and ready == []: END        │                                      │
                     │   5. fut = wait_first_completed(in_flight) ◄────────┼───────────┐                          │
                     │   6. result = fut.result()                          │           │  TaskResult              │
                     │   7. SETTLE(t, result)  (budget reconcile, quota/429/heal,       │  (no state writes on    │
                     │        settle usage, outputs, ROUTER hook, BREAKER eval,         │   the worker)           │
                     │        emit_tasks inject, loop-gate clone) ── all UNCHANGED       │                        │
                     │        → returns SETTLED | REQUEUE | HALT | RESHAPED             │                          │
                     │   8. act on signal (requeue→pending; halt→drain+stop; reshaped→rebuild DAG); save          │
                     │                                                     │           │                          │
                     └─────────────────────────────────────────────────────┼───────────┼──────────────────────────┘
                                                                            ▼           │
                          ┌───────────── ThreadPoolExecutor(max_workers=N) ──┴───────────┴─────────┐
                          │  worker: _run_with_retries(task, …) → self._executor.execute(ctx)      │
                          │          (reads paths + state.run_id ONLY; mutates nothing)            │
                          └───────────────────────────────────────────────────────────────────────┘
```

### 3.3 Thread boundary (what crosses)

```
 MAIN THREAD ONLY (never touched by workers)          WORKER THREAD (per submitted task)
 ────────────────────────────────────────────         ─────────────────────────────────────────
 RunState mutation + save()                            _run_with_retries(task, workflow, agents,
 budget gate / charge / reconcile                          repo_paths, state, dynamic_input_paths,
 evaluate_breakers + consult                               task_manifest_path, gate_output_path)
 _on_router_success / route cones                        ├─ reads: task/workflow/agents/repo_paths
 _inject / build_dag / _recompute_order                  │         state.run_id (str) ONLY
 loop clone/inject                                       ├─ self._store.resolve/…  (read-only, pure)
 quota / 429 / self-heal requeue decisions               ├─ self._cancel_fn()      (read-only flag)
 logging_setup attach/detach run handler                 ├─ self._sleeper(backoff) (retry backoff)
 ready-set + wave scheduling                             └─ self._executor.execute(ctx) → TaskResult
                                                         Returns: TaskResult (immutable handoff)
```

Invariants:
- **Sole writer:** only the main thread mutates `RunState` and calls `save`.
- **Immutable inbound:** `task`, the `agents` dict, `repo_paths`, and resolved paths are read-only
  for the duration of a wave. Only barrier tasks mutate `workflow`/graph, and they run solo.
- **Immutable outbound:** a worker returns a fresh `TaskResult`; the main thread reads it.
- **Executor thread-safety:** `self._executor.execute(ctx)` is called concurrently → the executor
  must hold no cross-call mutable state that races (see §10, §11).

---

## 4. LLD — Module M1: config / CLI / env resolution (Task T-JXiI9j)

**Purpose:** resolve `max_parallel` via the exact ADR-0003 chain and hand it to the engine.
**Inputs:** CLI `--max-parallel`, env `AO_MAX_PARALLEL`, `.ao/config.yaml: max_parallel`.
**Outputs:** a validated `int >= 1` passed to `Orchestrator(max_parallel=…)`.
**Dependencies:** `cli.py::_resolve_run_settings` / `_int_env`, `project_config.py::ProjectConfig`.

```
# project_config.py — add to ProjectConfig (mirrors quota_* fields at lines 112-116)
max_parallel: int | None = None
"""Max independent ready tasks dispatched concurrently (1 = serial). Run-scoped
(ADR-0003 §3 / ADR-0007): invocation chain only, never a workflow-spec field."""

# cli.py::_resolve_run_settings — add one resolved value, same _int_env + `or` chain used
# by quota (lines 289-300). Return arity grows by one; BOTH run and resume unpack it.
resolved_max_parallel = int(
    max_parallel_cli
    or _int_env("AO_MAX_PARALLEL")
    or (cfg.max_parallel if cfg else None)
    or DEFAULT_MAX_PARALLEL          # = 1, new module constant
)
if resolved_max_parallel < 1:
    typer.echo("ERROR: --max-parallel must be >= 1", err=True); raise typer.Exit(1)
# NOTE: the `or` chain treats 0 as unset (falls through to default); the explicit
# `< 1` guard rejects a negative CLI/env/config value loudly instead of silently.

# cli.py::run and ::resume — new option (mirror --self-heal / quota options), passed into
# _resolve_run_settings, then into BOTH Orchestrator(...) constructions (run ~L656, resume ~L904):
max_parallel: int | None = typer.Option(
    None, "--max-parallel",
    help="Max independent ready tasks to run at once (default 1 = serial). "
         "Env: AO_MAX_PARALLEL. Config: max_parallel.")
...
orch = Orchestrator(..., max_parallel=eff_max_parallel)
```

**Edge cases:** empty env (`AO_MAX_PARALLEL=""`) → falsy → falls through (learning #23 pattern);
`0` → also falsy → falls through to `DEFAULT_MAX_PARALLEL` (**no error** — identical treatment to
`--quota-max-wait 0` / `--max-attempts 0` on this same `or`-chain); only a **negative** value
survives the chain (negative ints are truthy in Python) and hits the `< 1` guard → explicit
`Exit(1)`; absent everywhere → `1` (byte-identical). As-built (T-TNleFt e2e-confirmed): `ao run
--max-parallel 0` exits **0** (runs serially); `--max-parallel -1` exits **1** with "must be >= 1".
**No `*.schema.json` change** — `max_parallel` is not a spec field (ADR-0007 D5).

**Subtasks:** (1) ProjectConfig field + docstring; (2) `DEFAULT_MAX_PARALLEL` constant; (3) extend
`_resolve_run_settings` + validation; (4) `--max-parallel` option in run+resume; (5) pass to both
constructors; (6) `ao init` `_INIT_TEMPLATE` line; (7) unit tests for precedence + validation.

---

## 5. LLD — Module M2/M3: Orchestrator ctor, ready-set & barrier predicate (Task T-j8YLGd)

```
# engine.py::Orchestrator.__init__ — add (default preserves serial):
max_parallel: int = 1
...
self._max_parallel = max(1, int(max_parallel))   # defensive clamp

# Barrier predicate — the precise "runs alone" rule (ADR-0007 D4).
FUNCTION _is_barrier(task, workflow) -> bool:
    IF task.emit_tasks: RETURN True
    IF self._loop_for_gate(workflow, task.id) is not None: RETURN True   # incl. __iter clones
    IF self._router_for_task(workflow, task.id) is not None: RETURN True # router_task_id
    RETURN False

# Predecessor map — invert graph.adjacency() (successors) once per DAG build.
FUNCTION _predecessors(graph) -> dict[str, set[str]]:
    preds = {n: set() for n in graph.adjacency()}
    FOR node, succs IN graph.adjacency().items():
        FOR s IN succs: preds[s].add(node)
    RETURN preds
    # (Optional: add Graph.predecessors() to dag.py instead; either is acceptable.)

# Ready set — the wave's candidate list, in deterministic `order`.
FUNCTION _ready_ids(order, preds, state, done, in_flight) -> list[str]:
    SETTLED = {"succeeded", "skipped", "not_taken"}
    ready = []
    FOR tid IN order:                                  # deterministic tie-break = sorted-Kahn order
        IF tid IN done OR tid IN in_flight: CONTINUE
        ts = state.tasks.get(tid)
        IF ts is not None AND ts.status IN ("succeeded","skipped","not_taken","running"): CONTINUE
        IF ALL(state.tasks.get(p) is not None AND state.tasks[p].status IN SETTLED
               FOR p IN preds[tid]):
            ready.append(tid)
    RETURN ready
```

**Edge cases:** a task with a `not_taken` predecessor is still "settled-dep" (join logic downstream
decides skip vs not_taken at launch — unchanged from today's `apply_join`); duplicate ids are
already rejected at inject (`_inject`), so ready ids are unique; empty ready + empty in_flight = run
complete.

---

## 6. LLD — Module M4/M5: wave loop + extracted settle (Task T-j8YLGd)

The current post-dispatch body (`engine.py` ~L565–L1063) is **extracted verbatim** into
`_settle_completed_task(...)` returning a control signal. This extraction is what makes `N=1`
byte-identical: same statements, same order, same `save` calls — only the *call site* moves from
inline to "called as each future drains".

```
ENUM Settle = SETTLED | REQUEUE | HALT | RESHAPED

FUNCTION run(workflow, reposets, agents, run_state=None) -> RunState:
    ... (unchanged setup: build_dag, order, cones/membership, done, attach logger) ...
    ctx   = _RunContext(repo_paths, agents, cones, membership, run_log, done)  # §14 item 2
    preds = _predecessors(graph)
    pool  = ThreadPoolExecutor(max_workers=self._max_parallel)
    in_flight: dict[Future, str] = {}     # future -> tid
    failed = False
    TRY:
      WHILE True:
        IF self._cancel_fn(): failed = _begin_cancel(state); BREAK      # §7 drain
        # ---- FILL: launch ready non-barrier tasks up to N ----
        ready = _ready_ids(order, preds, state, ctx.done, set(in_flight.values()))
        IF ready == [] AND in_flight == {}: BREAK                        # done
        FOR tid IN ready:
            IF len(in_flight) >= self._max_parallel: BREAK
            barrier = _is_barrier(workflow.task(tid), workflow)
            IF barrier AND in_flight != {}: BREAK        # barrier must run solo → drain first
            prep = _prepare_and_maybe_dispatch(tid, workflow, graph, state, ctx,
                                                in_flight_nonempty=bool(in_flight))
            IF prep.signal == "skipped": CONTINUE          # not_taken/done/should_skip/join → save, continue
            IF prep.signal == "halt":   failed = True; BREAK   # missing inputs / budget stop / unsatisfiable
            IF prep.signal == "blocked": BREAK              # budget wait & nothing else to free → see §7
            in_flight[submit(pool, _run_with_retries, prep.task, ...)] = tid
            IF barrier: BREAK                              # launched solo; go drain it
        IF failed: BREAK
        IF in_flight == {}: CONTINUE                       # nothing launched (all blocked) → re-evaluate
        # ---- DRAIN: one completion at a time, settle on main thread ----
        done_fut = next(as_completed(in_flight))           # wait for the first to finish
        tid = in_flight.pop(done_fut)
        result = done_fut.result()
        settle = _settle_completed_task(tid, result, workflow, state, ctx)  # UNCHANGED body; returns SettleResult
        IF settle.signal == "halt":     failed = True; _drain_remaining(in_flight, workflow, state, ctx); BREAK
        IF settle.signal == "requeue":  state.tasks[tid].status = "pending"  # eligible next wave (§7)
        IF settle.signal == "reshaped": graph = settle.graph; order = settle.order   # already rebuilt
                                        preds = _predecessors(graph)                  # INSIDE settle — no 2nd
                                                                                       # build_dag call here
        # "settled": nothing extra
      IF NOT failed AND state.status == "running": state.status = "succeeded"
      save(state); RETURN state
    FINALLY:
      pool.shutdown(wait=True)      # never leak threads; drain even on exception
      detach_run_handler(state.run_id)
```

**Signal types as implemented** (T-j8YLGd/T-VSfAUN): the pseudocode's symbolic `SKIPPED`/`HALT`/
`BLOCKED`/`REQUEUE`/`RESHAPED` are, in the merged code, lowercase string `Literal`s —
`DispatchSignal = Literal["skipped", "dispatch", "halt", "blocked"]` and
`SettleSignal = Literal["settled", "requeue", "halt", "reshaped"]` — each wrapped in a small
`@dataclass` return object (`DispatchPrep{signal, task, dynamic_input_paths, task_manifest_path,
gate_output_path}` / `SettleResult{signal, graph, order}`) rather than a bare enum value, so the
caller reads `.signal` plus, on `"dispatch"`/`"reshaped"`, the payload the old inline code computed
inline. See §14 item 3 for why.

**Key correctness points**
- `_settle_completed_task` is today's code path (budget reconcile, quota/429/self-heal, usage
  settle, output verify, router hook, breaker eval, emit inject, loop clone) with the `cursor -= 1`
  requeues replaced by returning `REQUEUE`, and the `break`s replaced by returning `HALT`, and the
  inject/loop rebuilds replaced by returning `RESHAPED`. **No logic inside changes.**
- **Barrier ⇒ solo:** a barrier is only ever launched when `in_flight == {}`, and after launching a
  barrier the fill loop `break`s, so no sibling joins it. Its `RESHAPED`/router side effects run
  with nothing else in flight → atomic w.r.t. the ready-set (D4).
- **`RESHAPED` only from barriers** (emit/loop are barrier tasks), so a DAG rebuild never races an
  in-flight sibling.

---

## 7. LLD — Module M6/M7: budget, requeue, failure, cancel under concurrency (Task T-VSfAUN)

### 7.1 Budget gate/charge before dispatch; reconcile on completion
- **Gate + charge happen on the main thread in `_prepare_and_maybe_dispatch`, before `submit`.**
  So at most `N` *charged* estimates are ever outstanding — one per in-flight task. No double-charge:
  the existing resume double-charge guard (reverse stale estimate, `engine.py:368`) is preserved.
- **Reconcile happens in `_settle_completed_task`** (existing block, `engine.py:729-750`): replace
  the task's estimate with actuals. Commutative across tasks → final `consumed_tokens` is
  order-independent (ADR-0007 D6).
- **Budget "wait" (window roll) while tasks are in flight — do NOT block the engine.** In
  `_prepare_and_maybe_dispatch`, if the gate says *block/wait*:
  - if `in_flight != {}` → return **BLOCKED** (stop filling this wave); the loop drains a completion
    (which reconciles actuals and may free the window), then re-evaluates the gate next wave.
  - if `in_flight == {}` → sleep to `next_available_epoch` exactly as today (`engine.py:471-501`),
    then retry. This preserves "wait" semantics without deadlocking on capacity held by in-flight
    siblings.
  - **unsatisfiable / on_exhaustion=="stop"** → `record_trip` + **HALT** (drain in-flight, then fail),
    identical events to today.

### 7.2 Quota / 429 / self-heal requeue (today: `cursor -= 1; continue`)
Each currently reverses its estimate, waits (`_sleeper`), resets `ts.status="pending"`, and rewinds
the cursor. Under waves, `_settle_completed_task` performs the same reverse+wait+`pending` and
returns **REQUEUE**. The task re-enters the ready set on a later wave (its deps are already settled).
**In-flight siblings are drained, not cancelled** — they are independent and already charged. The
per-episode quota timer (`_quota_exhausted_since`) stays main-thread state; a success anywhere
resets it (unchanged). If the quota max-wait is exceeded → `record_trip` + HALT.

### 7.3 Failure / breaker / cancellation (ADR-0007 D7)
- **Task fails** (non-requeued) → `ts.status` settles `failed` → the existing
  `if ts.status not in (...succeeded,skipped): failed=True` path → **HALT**.
- **Breaker trips (halt)** in `_settle_completed_task`'s breaker block → **HALT**.
- **`cancel_fn` fires** → stop admitting, set `state.status="cancelled"`, **drain** in-flight.
- On any HALT/cancel: `_drain_remaining(in_flight)` waits for each running worker, post-processes its
  result on the main thread (so its artifacts + `RunState` are consistent), persists, then finalizes.
  Workers are **not** killed (avoids half-written artifacts → keeps `ao resume` correct).
- Final status = today's rules; run is resumable because every drained result was persisted by the
  single-writer main thread.

```
FUNCTION _drain_remaining(in_flight):
    FOR fut IN as_completed(list(in_flight)):
        tid = in_flight.pop(fut)
        result = fut.result()                    # worker already running; let it finish
        _settle_completed_task(tid, result, …)   # persists usage/actuals; ignore further HALT (already halting)
    # pool.shutdown(wait=True) in finally is the backstop
```

---

## 8. Interface / contract changes (summary)

| Surface | Change |
|--------|--------|
| `ProjectConfig` | `+ max_parallel: int | None = None` |
| `cli.py::_resolve_run_settings` | returns one extra `int` (max_parallel); `run` + `resume` unpack + pass it |
| `cli.py` run/resume | `+ --max-parallel` option; env `AO_MAX_PARALLEL` |
| `Orchestrator.__init__` | `+ max_parallel: int = 1` (stored as `self._max_parallel`) |
| `Orchestrator.run` | cursor walk → wave/barrier scheduler; post-dispatch body extracted to `_settle_completed_task` |
| new helpers | `_is_barrier`, `_predecessors`, `_ready_ids`, `_prepare_and_maybe_dispatch`, `_settle_completed_task`, `_drain_remaining` |
| `_INIT_TEMPLATE` (`project_config.py`) | commented `max_parallel:` line under runtime settings |
| Schemas | **none** — `max_parallel` is not a spec field (ADR-0007 D5) |
| Trigger/event schema | **none** — no new triggers; cron/event unchanged |

`Executor` ABC is unchanged (`execute(ctx) -> TaskResult`); the new requirement is that
implementations are safe under concurrent `execute()` calls (§10).

---

## 9. Determinism & test strategy (Task T-TNleFt)

- **`N=1` byte-identical regression gate (blocking AC).** Run a representative workflow (diamond +
  emit_tasks + loop + router + budget) under the pre-epic engine and under `N=1`; assert identical
  final `RunState` (task statuses, order of dispatch, budget counters, tripped breakers) and identical
  `run.log` event sequence. Practically: keep/adapt existing engine tests unchanged — they must all
  pass at the default `N=1` with zero edits.
- **Deterministic `N>1` parallelism proof.** A purpose-built **gated executor** (records dispatch,
  blocks on a per-task latch, releases in a test-controlled order) proves ≥2 independent tasks are
  simultaneously in flight (assert overlap: task B `execute()` entered before task A released) and
  that completion-order does not change the final state.
- **Executor thread-safety:** `FakeExecutor` is not thread-safe (mutable counters). The harness uses
  the gated executor (or a thread-safe fake) for `N>1`; a separate unit test verifies
  `ClaudeCliExecutor` uses per-`ctx` `output_dir`s with no shared mutable state.
- **Coverage matrix (integration, `N>1`, gated executor):** wide-DAG fan-out; barrier isolation
  (emit_tasks/loop/router never overlap a sibling); budget gate blocks the (N+1)th while N run;
  quota/429/self-heal requeue with siblings in flight; breaker halt mid-wave drains in-flight; cancel
  mid-wave drains + resumable.
- **CliRunner e2e:** `--max-parallel 4`, `AO_MAX_PARALLEL=4`, and config `max_parallel: 4` each take
  effect; `--max-parallel 0` errors; run via `CliRunner`, not the engine API (learning #12).

---

## 10. Thread-safety boundary (audit table)

| Component | Concurrent access | Safe? | Note |
|-----------|-------------------|-------|------|
| `RunState` + `save` | main thread only | ✅ | single writer by construction |
| `_run_with_retries` | one per worker | ✅ | reads only `state.run_id`; mutates nothing |
| `ArtifactStore.resolve/exists` | workers + main | ✅ | pure path ops / `os.stat`; stateless |
| `self._sleeper`, `self._cancel_fn` | workers + main | ✅ | `time.sleep` / read-only flag |
| `ClaudeCliExecutor.execute` | workers | ✅ (verify) | per-`ctx` `output_dir`; fresh subprocess; **AC to confirm** |
| `FakeExecutor.execute` | workers | ❌ | mutable counters → test harness must not share it under `N>1` |
| `logging` handlers | workers + main | ✅ | stdlib handler lock; only ordering interleaves |
| `workflow` / `graph` | workers read | ✅ | mutated only by solo barriers on main thread |

---

## 11. Edge cases (must be covered)

- Empty workflow / single task → wave loop runs once, `N` irrelevant.
- All tasks independent (`N >= |tasks|`) → one wave launches all, drains all.
- `N` larger than ready count → launch only what's ready; refill as deps settle.
- Barrier as the very first ready task → launched solo immediately (in_flight already empty).
- Two barriers ready together (e.g. two routers) → strictly sequential (each waits for empty).
- Budget window rolls mid-wave → BLOCKED path; drain frees window; no deadlock.
- Quota/429 on one task while siblings run → requeue that task, drain siblings, resume later.
- Breaker `task_failures`/`consecutive_failures` count under concurrency → evaluated per drain on the
  main thread; may trip on a different task depending on completion order (inherent; `N=1`
  deterministic). Barrier/routing correctness unaffected.
- Cancel during budget wait / during drain → `state.status="cancelled"`, resumable.
- Exception inside a worker (`_run_with_retries` raising, not returning a failed `TaskResult`) →
  `fut.result()` re-raises on the main thread; treat as HALT after draining; `pool.shutdown` in
  `finally` guarantees no thread leak.
- Duplicate/injected ids → already rejected by `_inject`; ready-set sees unique ids.

---

## 12. Non-MVP / follow-ons
Per-workflow `defaults.max_parallel` (author intent; needs schema change); process/remote executors;
adaptive `N` from load/budget; per-agent concurrency; speculative/branch-parallel routing;
throughput-oriented launch reordering (would break the deterministic tie-break).

---

## 13. Execution readiness
- Junior-implementable: yes — every module has pseudocode, explicit signals, and the settle body is a
  verbatim extraction. ✅
- AI-agent-executable without ambiguity: yes — interfaces, signals, and edge cases enumerated. ✅
- Interfaces/schemas fully defined: yes (§8); no schema change by design. ✅
- Failure scenarios handled: yes (§7, §11). ✅

---

## 14. As-built deviations from design (T-EJKD6f, 2026-07-15)

Reconciled against the merged `src/agent_orchestrator/engine.py` (T-j8YLGd, T-VSfAUN),
`cli.py`/`project_config.py` (T-JXiI9j), and the full test suite (T-TNleFt: 857 passed / 3
skipped, 93% coverage). The design holds; the deviations below are implementation-level
refinements, not rationale changes.

1. **`--max-parallel 0` is not an error — only negative is.** Corrected directly in §4's edge-case
   list above (the original draft said "`0`/negative → explicit error", contradicting its own
   pseudocode a few lines above it). As-built: the `or`-chain treats `0` exactly like
   `--quota-max-wait 0` / `--max-attempts 0` (falls through to the default, silently); only a
   negative value survives the chain and hits the `< 1` guard. `ao init`, the CLI `--help` text, and
   the README are all consistent with this (none claim "0 errors").
2. **`_RunContext` dataclass** (`engine.py`, alongside `DispatchPrep`/`SettleResult`): the extraction
   threads per-run state — `repo_paths`, `agents`, `cones`, `membership`, `run_log`, and the two
   genuinely mutable pieces (`done`, `quota_exhausted_since`) — through a single `_RunContext ctx`
   object passed BY REFERENCE into `_prepare_and_maybe_dispatch` / `_settle_completed_task` /
   `_drain_remaining`, instead of each helper closing over loop-local variables the way the original
   inline cursor loop did. §6's pseudocode now shows `ctx` explicitly; the original draft didn't name
   this type.
3. **Signal types** — see the callout immediately after §6's pseudocode block: `DispatchSignal` /
   `SettleSignal` are lowercase-string `Literal`s wrapped in `DispatchPrep` / `SettleResult`
   dataclasses, not bare enum values.
4. **`REQUEUE` normalizes `state.tasks[tid].status = "pending"` uniformly, at the `run()` call site**
   — not only for the requeue sites that already set it inline. The quota-exhaustion and self-heal
   requeue sites already set `ts.status = "pending"` before returning `REQUEUE` (inherited verbatim
   from the old inline code); the **provider-429-wait** site never did, because the old cursor model
   revisited the same task id positionally regardless of its status. The new ready-set model
   (`_ready_ids`) excludes `status == "running"` from re-admission, so without this uniform
   normalization a 429-requeued task would never be re-offered and the run would silently stall.
   `run()`'s pseudocode above already reflected this (`IF settle.signal == "requeue":
   state.tasks[tid].status = "pending"` at the caller) — called out here because it's easy to miss
   that this line is load-bearing for exactly one of the three requeue sites.
5. **`RESHAPED` sites differ in what they hand back.** Both DAG-rebuild sites live inside
   `_settle_completed_task` (unchanged from the design), but:
   - **`emit_tasks`** keeps `self._recompute_order(graph, ctx.done)` — its log line
     (`"Injected %d tasks; order recomputed (%d remaining)"`) genuinely reads the `(order, cursor)`
     pair `_recompute_order` returns.
   - **loop-gate** (incl. `__iter` clones) calls `graph.topological_order()` **directly** instead —
     its log line never read the `cursor` half, and `cursor` isn't part of the ready-set interface
     anywhere, so keeping it would be a dead value (and an avoidable `ruff` F841).
   Both sites hand the already-built `(graph, order)` back to `run()` via `SettleResult` — `run()`
   itself never calls `build_dag` a second time on a `RESHAPED` signal (fixed in §6's pseudocode
   above; the original draft incorrectly showed `run()` rebuilding the DAG itself).
6. **`_estimate` is re-derived in `_settle_completed_task`**, not carried over from a task-block-scoped
   local. The old inline loop declared `_estimate` once per task iteration and both the budget gate
   and the reconcile block below it could see it. Once prepare/settle became separate methods, that
   local no longer spans both — the reconcile branch instead reads
   `state.budget_counters.charged_estimate.get(tid, 0)` **before** calling
   `self._budget_manager.reconcile(...)` (which pops that key). This holds the identical value
   `charge_estimate()` stored pre-dispatch, so the `"actual=%d estimate_delta=%d"` log line's numbers
   are unchanged from pre-epic behavior.
7. **Flagged, not fixed here (docs-only ticket): two in-code docstrings are stale.**
   `Orchestrator.__init__`'s `max_parallel` parameter docstring and `ProjectConfig.max_parallel`'s
   field docstring both still say "`run()` does **not** consume this value yet... every run is fully
   serial regardless of what is passed here" / "not yet consumed by the engine -- `Orchestrator.run()`
   stays fully serial until T-j8YLGd's wave/barrier scheduler lands." Both statements were accurate
   at T-JXiI9j's handoff (plumbing-only) but are now false: T-j8YLGd's `ThreadPoolExecutor(max_workers
   =self._max_parallel)` and the wave/barrier scheduler landed after `__init__` was written and,
   per T-j8YLGd's own extraction methodology, `__init__` was deliberately left byte-for-byte
   untouched (verified against `engine.py:204-211` and `project_config.py:118-122` on 2026-07-15).
   This needs a small, code-only follow-up (two docstrings, no behavior change) — out of scope for
   this docs-reconciliation ticket per its own charter (no production-code edits); reported to the
   epic owner instead of silently fixed here.
