# EPIC: E-IasNXu-parallel-execution

## Metadata
- Epic ID: `E-IasNXu-parallel-execution`
- Title: Opt-in parallel task execution (bounded thread pool, serialized core)
- Owner: architect agent (design) → developer/tester agents (delivery)
- Created: 2026-07-15
- Last Updated: 2026-07-15
- Status: Done (5/5 tasks complete — `T-JXiI9j`, `T-j8YLGd`, `T-VSfAUN`, `T-TNleFt`, `T-EJKD6f` all Done)

## Summary
- Goal: Run independent *ready* tasks concurrently up to a bound `N = max_parallel` (default **1**),
  making a wide DAG finish faster while the engine idles less on the I/O-bound `claude` subprocess —
  **without regressing any existing guarantee**. `N=1` is byte-identical to today's serial loop.
- Scope In: `max_parallel` setting (CLI/env/config/default, ADR-0003 invocation chain); a
  wave/barrier scheduler in `Orchestrator.run()` dispatching ≤N ready tasks on a bounded
  `ThreadPoolExecutor` with the **entire engine core serialized on the main thread** (only
  `_run_with_retries` → `executor.execute` runs on workers); correct interaction with budget,
  quota/429, self-heal, circuit-breakers, routing, `emit_tasks`, and loops (structural/routing tasks
  are serial barriers); drain-based failure/cancel semantics; resumable `RunState`.
- Scope Out (Non-MVP): per-workflow `defaults.max_parallel`; process/remote executors; adaptive/auto
  `N`; per-agent concurrency caps; speculative/branch-parallel execution; throughput launch
  reordering. See design doc §12.

Deep design (HLD + LLD + pseudocode + diagrams + edge cases):
[`docs-md/parallel-execution-hld.md`](../../../docs-md/parallel-execution-hld.md).
Decision record: [`docs-md/adr/ADR-0007-parallel-task-execution.md`](../../../docs-md/adr/ADR-0007-parallel-task-execution.md).

## Requirements (MVP) with traceability

| ID | Requirement | Task(s) | Verify |
|----|-------------|---------|--------|
| FR-1 | `max_parallel` resolved via CLI `--max-parallel` > env `AO_MAX_PARALLEL` > `.ao/config.yaml: max_parallel` > default `1`; validated `>= 1` | T-JXiI9j | unit + CliRunner (3 independent sources); `--max-parallel 0` errors |
| FR-2 | `max_parallel` plumbed to `Orchestrator(max_parallel=…)` in **both** `run` and `resume`; `ProjectConfig.max_parallel` field; `ao init` template line | T-JXiI9j | unit; scaffolded template contains the line |
| FR-3 | Wave scheduler dispatches ≤N independent ready tasks on a `ThreadPoolExecutor`; only `_run_with_retries` runs on workers; main thread is sole `RunState` writer/`save` caller | T-j8YLGd | integration: ≥2 tasks overlap in flight (gated executor) |
| FR-4 | Deterministic launch order = existing sorted-Kahn `order`; ready-set = deps settled (succeeded/skipped/not_taken) ∧ inputs present ∧ not done/not_taken/running | T-j8YLGd | unit on `_ready_ids` / `_is_barrier` |
| FR-5 | Structural/routing tasks (`emit_tasks`, loop-gate incl `__iter`, router `router_task_id`) run as **serial barriers** — solo, nothing else in flight, side effects atomic | T-j8YLGd, T-VSfAUN | integration: barrier never overlaps a sibling |
| FR-6 | Budget gate+charge on main thread **before** dispatch; reconcile on completion; ≤N charged estimates outstanding; window-roll "wait" drains instead of deadlocking | T-VSfAUN | integration: (N+1)th blocked while N run; no double-charge |
| FR-7 | Quota / 429 / self-heal requeue under concurrency = reverse estimate + wait + return task to `pending`; in-flight siblings **drain** (not cancelled) | T-VSfAUN | integration: requeue with siblings in flight |
| FR-8 | Failure / breaker-halt / `cancel_fn` while K in flight → stop admitting, **drain** in-flight (workers not killed), finalize status, remain resumable | T-VSfAUN | integration: halt+cancel mid-wave; `ao resume` continues |
| NFR-1 | **`N=1` byte-identical** to pre-epic serial loop (final `RunState` + `run.log` event sequence) | T-TNleFt | regression gate: existing engine tests pass unedited at default N |
| NFR-2 | Executors safe under concurrent `execute()`; `ClaudeCliExecutor` per-`ctx` `output_dir` verified; `N>1` tests use a thread-safe/gated executor | T-TNleFt | unit + audit |
| NFR-3 | No `*.schema.json` change (max_parallel is invocation-scoped, not a spec field — ADR-0007 D5) | T-JXiI9j | `ao validate` unaffected; grep shows no schema edit |
| NFR-4 | `ThreadPoolExecutor` never leaks threads (shutdown in `finally`, incl. on exception) | T-j8YLGd, T-VSfAUN | test: worker exception → clean shutdown |

## Requirements (Non-MVP)
- NM-1: `workflow.defaults.max_parallel` (per-workflow author intent; requires schema change +
  precedence merge). NM-2: process/remote executors. NM-3: adaptive `N`. NM-4: per-agent
  concurrency. Tracked in design doc §12; not implemented this epic.

## HLD / LLD (condensed — full version in the design doc)

Cursor walk (`engine.py:252` `while cursor < len(order):`) → **wave loop**: compute ready set →
launch ≤N ready non-barrier tasks on a `ThreadPoolExecutor` (only `_run_with_retries` on the worker)
→ drain completions one-at-a-time and run the **unchanged** post-dispatch settlement on the main
thread → barriers run solo. The post-dispatch body (`engine.py` ~L565–L1063) is extracted verbatim
into `_settle_completed_task(...)` returning `SETTLED | REQUEUE | HALT | RESHAPED`; that verbatim
extraction is what makes `N=1` byte-identical.

```
main thread (sole RunState writer)                         ThreadPoolExecutor(max_workers=N)
 ┌─────────────────────────────────────────────┐           ┌────────────────────────────────┐
 │ WAVE: ready=_ready_ids(order,preds,state)    │  submit   │ _run_with_retries(task,…)      │
 │  fill ≤N (skip/join/budget-gate+charge)──────┼──────────►│  → executor.execute(ctx)       │
 │  barrier? launch solo, no siblings           │           │  reads paths + run_id ONLY     │
 │  DRAIN first completion → _settle_completed  │◄──────────┤  returns TaskResult            │
 │   (budget reconcile, quota/429/heal, router, │  result   └────────────────────────────────┘
 │    breaker, emit inject, loop clone) UNCHANGED│
 │   signal: SETTLED|REQUEUE|HALT|RESHAPED; save │   HALT/cancel ⇒ stop admitting, drain in-flight,
 └─────────────────────────────────────────────┘   finalize (resumable). Workers never killed.
```

Thread boundary: only read-only inputs to `_run_with_retries` cross to workers (`task`, `workflow`
[read-only during a wave — only solo barriers mutate it], `agents`, `repo_paths`, `state.run_id`,
resolved paths). Main thread owns every `RunState` mutation, `save`, budget/breaker/router/inject/
loop/quota/429/self-heal decision. Full audit table in design doc §10.

## Interface / config-schema changes
- `ProjectConfig.max_parallel: int | None = None`.
- `cli.py::_resolve_run_settings` returns one extra `int`; `run`+`resume` gain `--max-parallel`
  (env `AO_MAX_PARALLEL`); both pass `max_parallel=` into `Orchestrator(...)`.
- `Orchestrator.__init__(..., max_parallel: int = 1)`; `run()` scheduler rewrite + new private
  helpers (`_is_barrier`, `_predecessors`, `_ready_ids`, `_prepare_and_maybe_dispatch`,
  `_settle_completed_task`, `_drain_remaining`).
- `ao init` `_INIT_TEMPLATE` gains a commented `max_parallel:` line.
- **No JSON-schema change** (ADR-0007 D5). No trigger/event change.

## Sprint plan & capacity

Team profile: developers with <4 yrs experience; 2-week sprints (5-day weeks); 40% overhead.
Assumed `team_size = 2` (one developer + one tester, matching this repo's epic staffing).

```
GrossHoursPerSprint      = 2 * 10 * 8            = 160
NetFocusHoursPerSprint   = 160 * 0.60            = 96      (12 focus-days)
CommitmentHoursPerSprint = 96 * (0.70..0.85)     = 67.2 .. 81.6
```

Task sizing (person-days): T-JXiI9j 1.0, T-j8YLGd 3.0, T-VSfAUN 2.5, T-TNleFt 2.5, T-EJKD6f 1.0 =
**10.0 person-days ≈ 80 gross hours** → fits the top of one sprint's commitment band (67–82h).

Recommendation: **one 2-week sprint**, justified — total commitment fits the band. Risk: the
critical path is largely serial (T-JXiI9j → T-j8YLGd → T-VSfAUN → T-TNleFt → T-EJKD6f) and T-j8YLGd
is the tentpole. Mitigation: the tester builds T-TNleFt's gated-executor harness + the `N=1`
byte-identical gate **in parallel with** T-j8YLGd to de-risk. Contingency: if T-j8YLGd slips,
T-TNleFt/T-EJKD6f roll into a short second sprint (design unchanged).

## Task List (ordered; each ≤3 days)
- [x] `T-JXiI9j-config-cli-env-plumbing` (1.0d) — `max_parallel` config/CLI/env/ctor plumbing; engine still serial. Deps: none. **Done 2026-07-15** — see `T-JXiI9j-config-cli-env-plumbing/STATUS.md`.
- [x] `T-j8YLGd-wave-barrier-scheduler` (3.0d) — wave/barrier scheduler + thread-pool dispatch; settle-body extraction; `N=1` byte-identical. Deps: T-JXiI9j. **Done 2026-07-15** — see `T-j8YLGd-wave-barrier-scheduler/STATUS.md`.
- [x] `T-VSfAUN-concurrency-failure-semantics` (2.5d) — budget/quota/429/self-heal/breaker/cancel correctness under concurrency (drain policy). Deps: T-j8YLGd. **Done 2026-07-15** — see `T-VSfAUN-concurrency-failure-semantics/STATUS.md`.
- [x] `T-TNleFt-tests-parallel-matrix` (2.5d) — gated-executor harness; `N=1` regression gate; deterministic `N>1` proof; interaction integration tests; CliRunner e2e. Deps: T-j8YLGd, T-VSfAUN. **Done 2026-07-15** — see `T-TNleFt-tests-parallel-matrix/STATUS.md`.
- [x] `T-EJKD6f-docs-adr-reconcile` (1.0d) — reconcile design doc to as-built, finalize ADR-0007, README, HLD index pointer, learnings (post-implementation docs-refresh). Deps: all. **Done 2026-07-15** — see `T-EJKD6f-docs-adr-reconcile/STATUS.md`.

## Risks and Dependencies
- **R1 (highest): `N=1` regression.** The settle-body extraction must be verbatim; any reordering
  breaks byte-identical parity. Mitigation: NFR-1 gate + run existing engine tests unedited at N=1.
- **R2: executor thread-safety.** `FakeExecutor` counters aren't thread-safe; `N>1` tests must not
  share it. `ClaudeCliExecutor` must be confirmed per-`ctx`-isolated (NFR-2).
- **R3: budget deadlock under "wait".** Waiting on a rolled window while capacity is held by
  in-flight siblings — solved by the BLOCKED-then-drain rule (design doc §7.1); needs explicit test.
- **R4: breaker count nondeterminism at `N>1`** (which task trips `task_failures` depends on
  completion order). Accepted + documented; `N=1` deterministic; barrier/routing correctness intact.
- Reuses (does not modify): `dag.py` (`build_dag`/`Graph.adjacency`/`topological_order`),
  `budget.py`, `breakers.py`, `monitoring.py`, `runstate.py`, `_run_with_retries`,
  `_settle`/router/inject/loop helpers — behavior preserved, only call sites move.

**Final status (2026-07-15, epic complete):** R1 closed (`N=1` regression gate held through every
task, re-asserted at each handoff, unedited to the end). R2 closed (gated/thread-safe test doubles
used throughout `N>1` tests; `ClaudeCliExecutor` per-`ctx` isolation unchanged). R3 closed (T-VSfAUN's
`BLOCKED`-drain-then-re-gate policy, proven via `TestNoBudgetDeadlockOnRollingWindow`). R4 accepted
by design, not a defect — `N=1` stays fully deterministic. See `T-EJKD6f-docs-adr-reconcile/STATUS.md`
for the final documentation reconciliation and the full evidence trail.

## Links
- Design doc (HLD+LLD): [`docs-md/parallel-execution-hld.md`](../../../docs-md/parallel-execution-hld.md)
- ADR: [`docs-md/adr/ADR-0007-parallel-task-execution.md`](../../../docs-md/adr/ADR-0007-parallel-task-execution.md)
- HLD index pointer: [`docs-md/hld-agent-orchestrator.md`](../../../docs-md/hld-agent-orchestrator.md) (feature-docs list)
- Output artifacts (if any): `output/E-IasNXu-parallel-execution/` (none yet — evidence lives in tickets + test suite)
