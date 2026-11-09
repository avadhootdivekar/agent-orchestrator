# T-Sn0Spk RESULT — mid-flight reshape + kill spike

- By: claude-sonnet-5-5 · Role: developer (spike) · Date: 2026-11-09 · Branch: `ad/enhancements-20oct`
- Code under test: clone HEAD `4f0c6d9` (line refs below are `git show HEAD:` numbering; the working tree also carries another
  unit's uncommitted edits to `engine.py`/`cli.py`, which shift line numbers but do not touch the code exercised here).
- All engine runs used temp workspaces (`/tmp/ao-spike-*`, deleted afterwards). The only live data read was **read-only
  transcript files** under the workspace `.orchestrator/runs/*/*/attempt-*/transcript.jsonl` for (b); nothing under
  `workflows/**/runs` was read. Spike code is throwaway and **not merged into `src/`**.

## Verdict

**GO-WITH-CHANGES** for the soft-barrier design (FR-8, FR-12). The scheduler core tolerates reshaping with tasks in flight
(no double dispatch, running tasks untouched, deterministic order, 300-task bulk injection under 6 running workers), but
**six invariants must be added** (list in (a)). §4.4.4 fallback is **not** required; keep it only as the degraded mode if
T-Sn6Brr cannot land the persistence change (invariant I1).
Recommended `idle_kill_minutes` default: **15** (hard floor 12). Tick cost is a non-issue (0.35 ms vs 50 ms budget).

## Reproduce

```bash
cd agent-orchestrator          # product clone
S=output/E-Sn7tL2-sentinel-long-running-overseer/spike
uv run python $S/spike_a_inflight.py  > $S/spike_a_output.json 2> $S/spike_a_engine.log   # (a)
uv run python $S/spike_a2_inferred_edge.py                                                 # (a) addendum
uv run python $S/spike_c_tickcost.py                                                       # (c)
python3 -I $S/spike_b_gaps.py /usr/avadhoot/mounted/ao-runner-ao/.orchestrator/runs         # (b) gap histogram
uv run python $S/spike_b_kill.py                                                           # (b) killpg + growth probe
python3 -I $S/spike_b_real_claude.py                                                       # (b) one real haiku run (~cents)
$S/spike_d_sigterm.sh TERM                                                                 # (d)
```
Raw outputs sit next to each script (`*_output.json|txt`).

## (a) In-flight DAG rebuild safety

**Important prior fact:** the engine *already* rebuilds the graph with siblings in flight — `emit_tasks` injection happens in
`_settle_completed_task` (`engine.py:2581` `build_dag` → `_recompute_order` → `SettleResult("reshaped")` → `run()` re-derives
`graph/order/preds` at `engine.py:1017`) while other futures are running. So *additive* mid-flight rebuild is production-proven;
the spike's job was the new part: **edge edits / removals between pending tasks, and edits applied by a non-emitter (tick)**.

**Method.** `HookedOrch` subclasses `Orchestrator` and overrides `_ready_ids` (main thread, once per scheduler iteration —
the natural tick point, `engine.py:2860`). After the first completion (≥2 still in flight, 3 dispatched) it applies an edit set
via `apply_edits()` (validate on a deep copy → `build_dag` → commit) and swaps `run()`'s locals **in place** (`order[:]=`,
`preds.clear()/update()`, `graph.__init__(...)`), because `run()`'s locals can't be reassigned from a hook. Executor is a
thread-safe sleeper that records every dispatch. Checks per scenario: dispatch count per task, full-graph edge ordering
(declared **and inferred** edges), deterministic topo order, and *resume equality* (reload state + original static spec +
`prepare_resume` → same edge set as the live graph?).

| # | Scenario (6 slots, 3 running + pending chain) | Result |
|---|---|---|
| S1 | inject 2 tasks (dep on running) + add edge `q→p3` between pending tasks | OK: 0 double dispatch, 0 order violations; **resume graph differs** (edge `q→p3` lost) |
| S2 | remove edge `p1→p2` + remove pending task `q` | OK live; **resume differs** (edge and task `q` come back) |
| S3 | edit introducing a cycle | rejected by `build_dag` on the *copy* (`CycleError`); live graph untouched. (Applying it to the live workflow first would leave `workflow.tasks` poisoned.) |
| S4 | edit `depends_on` of / remove a *running* task | must be rejected by the checker (status≠pending); engine itself would accept silently |
| S5 | inject `late` whose **output** equals the **input** of a *running* task `slow3` | `build_dag` adds an **inferred** backward edge `late→slow3` although `slow3` already started: **ordering violation** |
| S6 | inject `dup` producing a path already produced by pending `p1` | accepted silently; `_build_output_to_task` is last-writer-wins (`dag.py:158`) so `producer_of("out/p1.txt")` flips `p1`→`dup` (affects join/`_apply_join`, `engine.py:3608`, and future inferred edges) |
| S7 | inject 300 tasks while 6 workers run | OK: 306/306 succeeded, `max_per_task==1`, 4.3 s wall, order deterministic |

Addendum (`spike_a2`): with `ck.depends_on=[u1,u2]` and `ck.inputs=[u1.json,u2.json]`, trimming **only** `depends_on` leaves the
edge `u2→ck` in place (inferred from `inputs`); trimming both removes it. This matters directly for **FR-10** (early
checkpoint trims `depends_on`; overseer checkpoint inputs list every unit breadcrumb).

### Invariants that break → minimal engine/design change

| ID | Breaks | Minimal fix |
|---|---|---|
| I1 | Edge adds/removals and task removals are **not persisted**: `RunState.injected_tasks` stores only added specs and `prepare_resume` (`runstate.py:460`) re-attaches those onto the *static* spec. A resumed run silently reverts the edits (S1/S2). | Add `RunState.graph_edits: list[GraphEdit]` (`op, task_id, depends_on/inputs, source, at`), append in the same step as the mutation, and replay in `prepare_resume` after the injected-task merge (idempotent, ordered). Snapshot (`record_spec_session`) stays static-only. |
| I2 | **Inferred edges** from `inputs`↔`outputs` make `depends_on`-only edits ineffective (addendum) and let an injected output create a backward edge into a running/finished task (S5). | Edit op must be defined on the *effective* edge set: to remove an edge the checker requires trimming the matching `inputs` entry too (FR-12 rule "removing an edge whose predecessor produces a declared input" already points here — **FR-10 must trim `inputs` as well**). Checker rejects any injected/edited task whose post-edit effective edges include an edge **into** a non-pending task (compare `build_dag` edges before/after). |
| I3 | Duplicate producer silently re-points `producer_of` (S6). | Checker rejects a new output path already declared by any other task (spec load never allowed this implicitly; mid-flight must not either). |
| I4 | Nothing in the engine stops edits to running/finished tasks (S4). | Checker (FR-11/12) + engine assertion in the apply step: only `state.tasks[tid].status == "pending"` may be edited/removed. |
| I5 | Cycle detection fires only inside `build_dag`/`topological_order` after mutation (S3). | Apply step = validate on a deep copy (`build_dag` + topo), then commit; never mutate `workflow.tasks` before validation. |
| I6 | `run()` owns `graph/order/preds` as locals (only replaced on `RESHAPED`), and `cones/membership` are computed once (`engine.py:819`). Edits cannot be applied from outside `run()`; edits inside a routed cone invalidate membership. | New `_apply_pending_edits(...)` call in `run()` returning the same `reshaped` triple (reuse the `SettleResult("reshaped")` handling at `:1017`); checker rejects edits touching tasks in any `membership` cone (FR-12 "inside a hard-barrier cone" already says this — extend to *any* branch cone) and loop-clone ids. Removed pending tasks must also be dropped from `state.tasks` (else a stale `pending` row; harmless to completion but misleading in the summary). |

Also confirmed safe (no change needed): worker threads only read `workflow` through already-dispatched `TaskSpec`s; appending to
`workflow.tasks` while 6 workers run caused no error in S7. `_ready_ids` is O(|order|) and recomputed each iteration, so a swapped
`order/preds` takes effect on the very next fill. `_inject` currently requires a `parent_task_id` with a `dispatch_cycle`
(`engine.py:4685`) — sentinel-origin injection (FR-19) needs an `origin="sentinel"` variant without a parent.

**Recommendation: soft-barrier (FR-8/FR-12) — GO-WITH-CHANGES.** Land I1–I6 as part of T-Sn6Brr. §4.4.4 fallback (leaf-only
injection, hard-barrier early checkpoint) is only the degraded mode if I1 slips; without I1 an engine restart/resume would undo
early-checkpoint rewiring, so do **not** ship FR-10/FR-12 without it.

## (b) Kill primitive and liveness calibration

**Today's kill (`executors/claude_cli.py:634-649`):** `Popen` without `start_new_session`; timeout path does `proc.kill()` on the
direct child only. K1 (stub `claude` that spawns a grandchild like a Bash tool): after the task settles as `timed_out`, the
grandchild is **still alive** (orphan). 

**Cooperative kill via process group (K2/K3):** `start_new_session=True` ⇒ pgid==pid; `os.killpg(pgid, SIGTERM)` killed the
direct child (rc −15) **and** the grandchild in ~1 ms. A SIGTERM-ignoring tree needed escalation: `SIGKILL` to the group after a
grace period, dead in 3.2 s with a 3 s grace, no survivors. ⇒ Design for T-Sn4Rec: executor takes `cancel_event`, polls
`proc.wait(timeout=1)` slices, on cancel `killpg(SIGTERM)` → grace (default 10 s) → `killpg(SIGKILL)`; set the new session for
every spawn (also fixes the timeout orphan leak independent of the sentinel). Settle as `killed` without burning `max_attempts`.

**Liveness signal (K4 + real run).** Probe = one `os.stat()` of `transcript.jsonl` (no read/lock). One real `claude -p
--output-format stream-json --verbose` haiku run executing a ~30 s silent Bash wait: file size changed at t = 0, 0.5, 3.0, 6.0,
**33.0**, 38, 40 s ⇒ **27 s of zero growth while the tool ran** (one `tool_progress` at ~6 s, then nothing). So growth is a valid
*positive* signal (any growth ⇒ alive) but **absence of growth is only meaningful above the longest legitimate tool call**; there
is no heartbeat during tool execution. Optional second signal for T-Sn4Rec: descendant-process liveness/CPU of the pgid
(`/proc`), to separate "long tool" from "wedged".

**Silent-gap distribution of healthy tasks** (`spike_b_gaps.py`; 675 healthy attempts / 682 scanned, 34 196 gaps between
timestamped assistant/user events; conservative since untimestamped events like `thinking_tokens` also grow the file):

| | n | p50 | p90 | p95 | p99 | p99.9 | max |
|---|---|---|---|---|---|---|---|
| all gaps (s) | 34196 | 0.9 | 6.9 | 14.5 | 78.2 | 580 | 600.4 |
| model gaps (s) | 21610 | 1.8 | 8.0 | 14.1 | 41.7 | 122 | 566 |
| tool gaps (s) | 12586 | 0.0 | 3.1 | 17.0 | 120.1 | 590 | 600.4 |
| per-task max gap (s) | 675 | 31.6 | 120.5 | 347.8 | 590.3 | 600.3 | 600.4 |

Histogram (all gaps): 0–5 s 29458 · 5–15 s 3078 · 15–30 s 840 · 30–60 s 387 · 60–120 s 233 · 120–300 s 136 · 300–600 s 60 ·
600–1200 s 4 · >1200 s **0**. Gaps >2 min: 200; >5 min: 64; >10 min: 4; >15 min: **0**. The 600 s ceiling is the Bash tool's
10-minute cap (the workflow instructions' `timeout 590` wait loops land at 590–600 s, p99.9). Bash dominates tool gaps (9507
calls, p99 163 s, max 600.4 s); Read/Write/Edit/WebSearch/WebFetch ≤ 73 s.

**Recommendation:** `sentinel.recovery.idle_kill_minutes` default **15** (EPIC §4 already says 15 — confirmed): above the observed
healthy maximum (10.007 min) with 5 min margin, 0/34 196 healthy gaps would have been killed. **Validation floor 12**
(must exceed the 10-min Bash ceiling + margin; reject lower unless an explicit `force`). Because 64 healthy tasks had a >5 min
gap, anything ≤ 10 would produce false kills. Caveat: sample is sonnet/haiku/opus runs in this workspace only; recheck after the
live smoke (T-SnXAcc) and keep the "systemic-kill alarm" from the EPIC.

## (c) Tick cost on 1k tasks (`spike_c_tickcost.py`)

Synthetic DAG: 1000 tasks (25 layers × 40, 2 deps each), mid-run state (480 succeeded, 6 running, rest pending); 1000 iterations
for the tick, 200 for rebuilds/IO.

| Operation | p50 | p95 | max |
|---|---|---|---|
| `_ready_ids` | 0.311 ms | 0.317 | 0.41 |
| `_pending_ids` | 0.040 ms | 0.041 | 0.08 |
| **tick core (`_ready_ids`+`_pending_ids`)** | **0.353 ms** | 0.359 | 0.37 |
| `build_dag` | 1.08 ms | 1.11 | 6.8 |
| `topological_order` / `_recompute_order` | 0.62 ms | 0.63 | 0.65 |
| `_predecessors` | 0.16 ms | 0.17 | 6.9 |
| **full reshape (build_dag+topo+preds)** | **1.90 ms** | 1.99 | 8.7 |
| `RunState.save` (831 KB state.json + status) | **10.3 ms** | 10.4 | 11.2 |
| `RunState.load` | 3.8 ms | 4.0 | 10.7 |

Tick (0.35 ms) and a full reshape (1.9 ms) are ~140×/26× under the 50 ms target (NFR-8). The dominant cost is `RunState.save`
(10 ms; `model_dump_json(indent=2)` is only 2.1 ms, the other ~8 ms is the atomic write + `write_status` second file) is fine at one save per settle, but the tick must **not save every
loop iteration** — save only on mutation (edit/kill/override) or at the existing settle points. State size, not graph size, is what scales NFR-8.

## (d) SIGTERM to `ao run` today (`spike_d_sigterm.sh`)

No `signal` handling anywhere on the run path (`grep -rn "signal.signal" src` hits only `service/cli.py` — the daemon) and `cancel_fn`
is never passed by `cli.py` (constructor default `_NO_CANCEL`, `engine.py:371`). Measured with the clone's `ao run` + stub `claude`
(a→c, b; `max_parallel` 1):

- SIGTERM to `ao`: process gone in **1.5 ms** — default Python action, **no unwind**: no `run.end`/cancel log line, no `finally`,
  no `pool.shutdown`, no state save.
- `state.json` left `status: running`, task `a: running` (stale; `ao resume` is needed and the run will look live forever in
  the dashboard until something reconciles it).
- **Orphans:** the stub `claude` (and its grandchild) kept running after `ao` died (4 processes), still in the dead run's
  workspace — a real `claude` would keep spending until it finishes.
- SIGINT: inconclusive here — bash starts `&` jobs with SIGINT ignored, so the test process ignored it (alive after 10 s). Python's
  default `KeyboardInterrupt` path is expected, but was not measured; T-Sn3Tck should test it under a pty.

Implications for design: wire `SIGTERM`/`SIGINT` to a flag read by `cancel_fn` (the engine already implements the cancel path:
`run.cancelled`, `state.status="cancelled"`, `_drain_remaining` — but note "drain" waits for in-flight tasks, so with 25 min
tasks it would block; pair it with the kill ladder: first signal = stop dispatching + SIGTERM groups, second/timeout = SIGKILL),
and set `start_new_session=True` so children can be killed as a group. This is in scope for T-Sn3Tck/T-Sn4Rec, not this spike.

## Spec changes recommended

- **FR-8:** add: *graph reshape from the tick is allowed only through `_apply_pending_edits` (validate on copy → commit → persist
  to `RunState.graph_edits` → return reshaped triple).* Additive injection is already safe in-flight today.
- **FR-10:** early checkpoint must trim **both** `depends_on` **and** `inputs` (inferred edges, spike a2) and carry the edit in
  `graph_edits` so a resume keeps it; digest `carry_over[]` already covers the lost inputs.
- **FR-12:** add checker rules: reject output paths already produced by another task (I3); reject any edit whose effective edge set
  adds an edge into a non-pending task (I2/I4); cycle check on a copy (I5); reject edits inside any route cone or loop clone (I6);
  removed pending tasks are dropped from `state.tasks`. Edit ops are defined over effective (declared + inferred) edges.
- **FR-5/FR-6:** `start_new_session=True` + `killpg` TERM→grace→KILL; `idle_kill_minutes` default 15, validation floor 12.
- **NFR-8:** tick measured 0.35 ms @1k tasks; `RunState.save` 10 ms @831 KB is the cost to bound (save-on-mutation only).
- **New (T-Sn3Tck):** SIGTERM/SIGINT currently leave `running` state and orphan `claude` children (d).

## Honest limitations

- (a) uses an in-place-swap hook, not a real `run()` change: it proves scheduler tolerance and enumerates invariants; the real
  `_apply_pending_edits` still needs engine tests (T-Sn6Brr). Isolation/worktree tasks, routers, and loops were not exercised
  (rules I6 reject edits there instead).
- (b) kill tested on a stand-in tree, liveness on one real 30 s Bash call; gap histogram is from this workspace's history only.
- (d) SIGINT not conclusively measured.
