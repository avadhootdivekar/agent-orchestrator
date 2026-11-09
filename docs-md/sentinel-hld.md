# Sentinel — High-Level Design

- Status: **Proposed — awaiting owner sign-off** (with ADR-0024). Author: architect, 2026-11-09.
- Epic: `meta/tickets/E-Sn7tL2-sentinel-long-running-overseer/` · ADR: `docs-md/adr/ADR-0024-sentinel-overseer-lane.md`
- Evidence: `output/E-Sn7tL2-sentinel-long-running-overseer/spike/RESULT.md` (T-Sn0Spk, GO-WITH-CHANGES)
- Seed: EPIC §4 (this HLD supersedes it where they differ; differences are the spike changes in §3.1).

## 0. Purpose and scope

A run-long overseer inside the engine: mechanical tick (liveness, recovery, bandwidth, live overrides) at $0, plus
optional bounded LLM generations for judgement. Opt-in via `sentinel:`; absent/disabled ⇒ byte-identical behaviour.

In scope: P1 (foundations), P2 (bandwidth), P3 (LLM). Out of scope: P4 (see §16 Deferred), the per-wave checkpoint's
own logic (unchanged apart from digest/instruction additions), the live-install flow.

## 1. Requirements traceability (every FR/NFR of EPIC §3 → HLD section)

| Req | Ph | Summary | HLD § |
|---|---|---|---|
| FR-1 | P1 | Opt-in layered settings; absent ⇒ byte-identical | 11, 12 (golden) |
| FR-2 | P1 | Main-thread tick, DRAIN timeout | 3, 3.1 |
| FR-3 | P1 | Control inbox `ao.ctl/v1`, quarantine | 4, App. A.1 |
| FR-4 | P1 | Liveness probe | 5.1 |
| FR-5 | P1 | Per-task kill, `killed` status, process groups | 5.2, 5.3 |
| FR-6 | P1 | Recovery ladder | 5.4 |
| FR-7 | P1 | Live overrides (`max_parallel` soft limit, powers…) | 4.4, 11.3 |
| FR-8 | P2 | Soft-barrier emitters | 6.1, 6.2 |
| FR-9 | P2 | L1 backlog release | 6.3, App. A.3 |
| FR-10 | P2 | L2 early checkpoint (trims `depends_on` **and** `inputs`) | 6.4 |
| FR-11 | P2 | Shared mechanical checker | 7 |
| FR-12 | P2 | Pending-graph edits + extra rules | 6.2, 7.2 |
| FR-13 | P2 | Spawn origin `sentinel` | 6.5 |
| FR-14 | P3 | LLM generations, pack, handoff, memory | 8, App. A.2 |
| FR-15 | P3 | LLM powers advise/act, LLM kill limits | 8.4, 9 |
| FR-16 | P3 | Checkpoint ↔ sentinel contract, directive ack | 9 |
| FR-17 | P3 | Cost accounting (`sentinel-gNN` rows, budget) | 8.5 |
| FR-18 | P1–3 | Fail-safe + resume | 10 |
| FR-19 | P2 | Separate injection cap | 6.6 |
| FR-20 | P1 | Utilisation metrics | 5.5 |
| FR-21 | P1–3 | Observability events | 13 |
| FR-22 | P1–3 | CLI + dashboard surface | 11, 12 |
| FR-23 | P3 | Live smoke | 15 |
| FR-24–28 | P4 | Deferred | 16 |
| NFR-1 | | Byte-identical when off | 3.2, 15 |
| NFR-2 | | Determinism (pure fns + injectable clock) | 3, 15 |
| NFR-3 | | Idempotency (ids, consume records) | 4.2, 10 |
| NFR-4 | | Single writer | 2, 3 |
| NFR-5 | | Path safety | 4.3 |
| NFR-6 | | Bounded IO | 4.3, 8.2, 13 |
| NFR-7 | | Cost | 8.5 |
| NFR-8 | | Long-run stability; tick <50 ms @1k; save-on-mutation | 3.3 |
| NFR-9 | | Legibility | 13 |
| NFR-10 | | Tamper-evident only; engine-side containment | 9.2 |
| NFR-11 | | Self-hosting safety | 15 |
| NFR-12 | | Dashboard auth/CSRF/actor | 12 |
| NFR-13 | | KPI ≥50 % idle-slot reduction (reported) | 15 |

## 2. Architecture

```
 ao run (engine process, main thread owns RunState)
   loop: cancel? → reconcile_inputs → SENTINEL TICK
            ├ liveness + metrics                      (5.1, 5.5)
            ├ drain control/inbox → checker → apply   (4, 7)
            ├ mechanical: recovery ladder, backlog release, early-ckpt, needs_llm triggers
            └ apply via _inject / _kill_task / _apply_pending_edits / override
         → FILL (soft limit = live max_parallel) → wait(in_flight, timeout=tick_seconds)
   worker pool (size = max_parallel_ceiling)     lane executor (1 worker, outside max_parallel)
     task attempts                                 sentinel-gNN: bounded claude -p → inbox files
 writers of control/inbox:  ao ctl │ dashboard POST /api/runs/{id}/control │ sentinel-gNN
```

Components: `SentinelSpec` (models), `SentinelState` (runstate), `sentinel.py` (tick, triggers, ladder, metrics —
pure functions), `checker.py` (shared mutation checker), `control.py` (inbox read/validate/quarantine/archive),
`graph_edits.py` (`_apply_pending_edits` + replay), executor `cancel_event`/`activity_path`, `SentinelDriver` ABC +
`ClaudeCliSentinelDriver`, CLI `ao ctl`, dashboard API + panel. Module/file names are indicative; tasks own the final
layout.

## 3. Main-thread tick

- DRAIN: `wait(in_flight, timeout=tick_seconds, return_when=FIRST_COMPLETED)`; settle each completed future as today.
  Disabled ⇒ the original un-timed `as_completed` path (NFR-1).
- `_sentinel_tick(state, workflow, ctx, now)`: pure function of in-memory state + files + injected clock. Steps in
  fixed order: (1) liveness refresh, (2) metrics accrue, (3) drain inbox (id order), (4) mechanical triggers
  (ladder → backlog → early ckpt → needs_llm), (5) apply accepted mutations, (6) **one** `RunState.save` iff anything
  mutated (inbox consume records are in that save — G5 pattern, NFR-3).
- Any exception ⇒ `sentinel.error`, lane disabled for the run, run continues as today (FR-18).
- Pool is created at `max_parallel_ceiling` (default = `max_parallel`); FILL honours the live soft limit.

### 3.1 Spike changes folded in (normative)

| Spike item | Design consequence |
|---|---|
| I1 edits not persisted | `RunState.graph_edits: list[GraphEdit]` appended in the same step as the mutation; replayed in `prepare_resume` after injected-task merge (ordered, idempotent). Snapshot (`record_spec_session`) stays static-only. |
| I2 inferred edges | Edit ops are defined over the **effective** edge set (declared + inferred from `inputs`↔`outputs`). Removing an edge requires trimming the matching `inputs` entry; checker rejects any edit whose post-edit effective edges add an edge **into** a non-pending task (before/after `build_dag` comparison). |
| I3 duplicate producer | Reject a new/edited output path already declared by another task. |
| I4 running/finished tasks | Only `state.tasks[tid].status == "pending"` may be edited/removed (checker + engine assertion). |
| I5 cycle detection after mutation | Validate on a **deep copy** (`build_dag` + topological order), then commit; never mutate `workflow.tasks` first. |
| I6 `run()` owns graph/order/preds; cones computed once | `_apply_pending_edits(...)` returns the same `reshaped` triple handled at the `SettleResult("reshaped")` site; reject edits touching any route cone/`membership` or loop-clone ids; removed pending tasks are dropped from `state.tasks`. |
| FR-10 | Early checkpoint trims `inputs` **and** `depends_on`, persisted via `graph_edits`. |
| FR-5/6 | `start_new_session=True`; `killpg` TERM → grace → KILL (also fixes timeout orphans); `idle_kill_minutes` default **15**, floor **12** (healthy max gap 600.4 s = Bash 10-min cap; 0 / 34 196 gaps > 15 min). |
| NFR-8 | Tick 0.35 ms, full reshape 1.9 ms @1k tasks; `RunState.save` 10 ms @831 KB ⇒ **save on mutation only**. |
| SIGTERM (d) | `ao run` has no handler: SIGTERM leaves `running` + orphans. New: §3.4. |
| `_inject` parent | Requires `parent_task_id` with `dispatch_cycle`; add `origin="sentinel"` variant (§6.5). |

### 3.2 Disabled-path guarantee
Every new code path is guarded by `workflow.sentinel.enabled`; new `RunState` fields serialise only when non-default
(`exclude_defaults`-style) so state.json is byte-identical. Golden test covers workflow.json/state.json/run log.
Process-group spawn and signal handling (§3.4) are the sole always-on changes; they write no new artifacts.

### 3.3 Performance contract
Tick < 50 ms @1k tasks (measured 0.35 ms). No per-iteration save. Inbox scan is `os.scandir` of one dir, capped at
`inbox_batch_max` (default 50) entries per tick, oldest first.

### 3.4 Signals
`ao run` installs SIGTERM/SIGINT handlers setting a cancel flag read by `cancel_fn` (today never wired). First signal:
stop dispatching, `killpg` TERM on in-flight groups, settle remaining as `cancelled`, set `state.status=cancelled`,
save, exit. Second signal or `cancel_grace_seconds` (default 30) elapsed: SIGKILL groups. Replaces the "drain waits for
25-min tasks" behaviour. `ao ctl cancel` uses the same path via inbox. SIGINT under a pty must be tested (spike gap).

## 4. Control inbox (`ao.ctl/v1`)

### 4.1 Layout
`<run_dir>/control/{inbox,inbox-processed,inbox-rejected}/`. Writers create `<id>.json.tmp` then atomically rename.
Engine moves consumed files to `inbox-processed/` (rotated: keep last 200) after the state save that records the id in
`state.sentinel.consumed_ids` (bounded ring of last 1000 ids).

### 4.2 Entry kinds and idempotency
`set`, `pause_llm`, `resume_llm`, `note`, `kill`, `release_backlog`, `inject`, `edit_pending`, `directive`,
`request_hold`, `request_cancel`. Schema App. A.1. `id` is writer-generated (`<actor>-<ts>-<rand>`); an id already in
`consumed_ids` is dropped silently (idempotent replay, crash between save and file move). Entries are processed in
lexicographic `id` order within a tick for determinism.

### 4.3 Validation and quarantine
Order: size ≤ 1 MiB → JSON parse → schema/`kind` → path safety (resolve + `is_relative_to(workspace_root)`, reject
escaping symlinks) → shared checker (§7). Failure ⇒ move to `inbox-rejected/` with sidecar `<id>.reason.json`
`{rule_id, detail}`, emit `sentinel_rejected`/`ctl.rejected`. Per-source consecutive LLM rejections ≥ 5 ⇒ pause LLM +
directive for next checkpoint (FR-18).

### 4.4 Live overrides
`set` writes `state.sentinel.overrides{dotted_key: {value, actor, at}}`, validated against `SentinelSpec` and the
run-options registry (E-aYG5vn). Applied values appear in `state.effective_settings` with source `live-override`.
Overridable: `max_parallel` (≤ ceiling), `llm.powers`, `llm.allow_*`, recovery thresholds (respecting floors),
sentinel budget, `tick_seconds`. Not overridable: `enabled`, ceiling, driver. Overrides persist and are restored on
resume.

## 5. Health and recovery

### 5.1 Liveness (FR-4)
Executor exposes `activity_path`; tick `os.stat`s it (size+mtime; no read/lock). `last_activity_at` = last observed
growth. Growth is a positive signal; **absence is meaningful only above the longest legitimate tool call**
(spike: 27 s silence during a real Bash wait; no heartbeat during tool runs). Optional P1 second signal: descendant
CPU/liveness of the pgid via `/proc` to separate "long tool" from "wedged" (kept optional; see Deferred if cut).
Exposed in state, digest, `ao ctl status`, dashboard.

### 5.2 Kill primitive (FR-5)
`TaskContext.cancel_event`; Claude CLI executor polls `proc.wait(timeout=1)`, on cancel `killpg(SIGTERM)` → grace
(`kill_grace_seconds`, default 10) → `killpg(SIGKILL)`. Engine `_kill_task(tid, reason, source)` settles as `killed`
touching no other task.

### 5.3 `killed` status (EPIC §10.2)
New terminal-for-attempt status `killed{reason, source ∈ ladder|ctl|dashboard|llm}`. Not counted as a failure for
`task_failures`/`consecutive_failures` breakers until the ladder is exhausted (then normal `failed`). Does not
consume `max_attempts`; counts against `max_recovery_kills_per_task` (default 2). All status consumers (summary, UI
maps, graph colours, `ao status`) must handle it.

### 5.4 Recovery ladder (state machine)
Per task, `state.tasks[tid].recovery{kills, last_reason, escalation_step}`.

```
running ──trigger──▶ KILL ──▶ killed
 triggers: idle ≥ idle_kill_minutes │ running ≥ stuck_factor×median(same-kind settled; ≥3 samples; floor stuck_min_minutes)
           │ cost ≥ cost_kill_pct×task cap │ manual/LLM kill
killed ─▶ kills < max_recovery_kills_per_task ?
            yes ─▶ step 0: retry same (+ retry-context note "killed: <reason>")
                   step n≥1: retry with escalate[n-1] ({effort|model})
            no  ─▶ failed (normal failure path ⇒ checkpoint/sentinel replans)
 --no-retry (ctl) ─▶ failed immediately
```
Barrier/emitter tasks: alarm-only unless `recovery.include_barriers`. Systemic alarm: ≥ N consecutive kills across
tasks (default 3) ⇒ stop mechanical kills, pause LLM, raise directive (env/quota problem). `idle_kill_minutes`
validated ≥ 12 unless `force`. LLM never selects the retry shape; it may `inject` a replacement through the checker.

### 5.5 Metrics (FR-20)
`state.metrics{slot_seconds_busy, slot_seconds_idle_with_ready, slot_seconds_idle_no_ready, utilisation_pct}`
accrued per tick by Δt × counts (using the injectable clock). Surfaced in `ao status`, summary, dashboard. KPI.

## 6. Bandwidth

### 6.1 Soft barrier (FR-8)
`TaskSpec.barrier: "hard"|"soft"` (default hard; `overseer-runner` checkpoints set soft when sentinel is enabled).
A soft emitter may start with non-predecessor tasks in flight if its manifest is additive w.r.t. in-flight tasks.
Routers, loop gates and integration barriers stay hard. FILL's break-on-barrier honours the flag.

### 6.2 `_apply_pending_edits`
Single entry for all post-start graph reshape (edges, removals, cancel/skip, injected tasks from non-emitters):
`validate on copy (checker §7.2 + build_dag + topo) → commit workflow → append graph_edits → drop removed from
state.tasks → recompute order/preds → return reshaped triple`. Atomic: any rule failure rejects the whole entry.
Priority affects FILL order only among ready tasks; stable ADR-0007 D6 tie-break afterwards. Each edit is ledgered
with before/after effective edge lists.

### 6.3 L1 backlog (FR-9)
Checkpoint writes `outputs/backlog.json` (App. A.3). Tick: if `running < limit` and nothing ready, release entries
FIFO by priority as `s-NN-<slug>` units through the checker (`origin=sentinel`, `parent=sentinel-tick`). Entries expire at
the next checkpoint unless re-confirmed (`expires_at_ckpt`). `ao ctl release` forces release. $0.

### 6.4 L2 early checkpoint (FR-10)
Trigger (all): backlog empty; `idle_slots ≥ early_ckpt_min_idle` (default ⌈P/2⌉) for ≥ `early_ckpt_grace_seconds`
(120); ≥ `early_ckpt_fraction` (0.5) of the pending soft checkpoint's predecessors settled; no early checkpoint already
in flight. Action (one `edit_pending`): drop unsettled predecessors from the checkpoint's `depends_on` **and** matching
`inputs` entries; persist in `graph_edits`; pass them as `carry_over[]` in the digest. The checkpoint must not
re-schedule carry-over work (checker rejects). Counts toward `max_waves` (EPIC §10.3). Sentinel/carry-over units
never block the next checkpoint; its digest lists them.

### 6.5 Spawn origin (FR-13)
`SPAWN_ORIGIN_SENTINEL` (ADR-0017); `parent_task_id ∈ {sentinel-tick, sentinel-gNN, ctl:<user>}`; `_inject` variant
without `dispatch_cycle`. UI graph shows sentinel-origin tasks, edited edges, killed attempts distinctly.

### 6.6 Injection cap (FR-19)
`state.sentinel.injected_count`; breaker `sentinel_injected_task_count` default `max(20, 3×max_parallel×expected_hours)`.
Template `injected_task_count` math untouched.

### 6.7 Fallback
If I1 cannot land, degrade to EPIC §4.4.4 (leaf-only injection, hard-barrier early checkpoint). Not expected.

## 7. Shared mechanical checker (FR-11, FR-12)

### 7.1 Common rules (any source)
Kind map; brief exists and cites ≥1 charter ask (overseer workflows; skipped where no charter, e.g. routed-runner);
attempt caps/rename-dodge (OV-R13/R13c); `ov-unit-gate` pre-hook; id patterns; allowed `effort`; budget-stage rules
(none new scope in converge+, only stabilize/verify/document in stabilize, nothing in closeout); rate limits
(`max_injections_per_hour`, `max_llm_kills_per_hour`); sentinel injection cap; `allow_graph_edits`/`allow_kill`.

### 7.2 Graph-edit rules (FR-12 + spike)
Reject: cycle (on copy); touching non-pending task; edge into a non-pending task via inferred edges (I2/I4);
duplicate output producer (I3); removing an edge whose predecessor produces a declared input of the successor unless
that input is also removed; orphaning a route sink; edits inside any route cone/`membership` or loop clone (I6);
re-scheduling carry-over work. Each rule has a stable id (`CK-*`) used in quarantine sidecars.

## 8. LLM generations (P3)

### 8.1 Lifecycle
Trigger (`needs_llm`) or heartbeat ⇒ start `sentinel-gNN` via `SentinelDriver` (`start/poll/stop`;
`ClaudeCliSentinelDriver` first) in the lane executor. Triggers with cooldown + per-hour caps: `stuck_task` (after
ladder exhausted), `new_signal(high|medium)`, `stage_change`, `unit_failed|blocked`, `human_note`,
`idle_slots_no_backlog` (routed-runner L2), `heartbeat`. Ends at first of TTL / max triggers / max tokens / explicit
handoff. Ungraceful exit ⇒ `sentinel_ungraceful`; next generation starts from digest + memory.

### 8.2 Input pack (deterministic, ≤120 KiB)
Charter (if any), digest (slots, liveness, carry-over, utilisation), last K=40 ledger/run events, backlog,
`memory.md` (tool-compacted ≤16 KiB), previous `handoff.json`, `sentinel-contract.md`. Same state ⇒ byte-identical pack.

### 8.3 Outputs (files only)
Inbox proposals, `report.md`, `handoff.json` (App. A.2), `memory-append.md`. Memory compaction is tool-owned.

### 8.4 Powers (FR-15)
`advise`: directives, alarms, notes, `request_hold/cancel`. `act` adds `inject`, `edit_pending`
(`allow_graph_edits`, default true), `kill` (`allow_kill`, default true; rationale ≥ 40 chars, task running ≥
`llm_kill_min_running_minutes` 10, ≤ 2/hour, never barrier/emitter). Killed task then follows §5.4. For
routed-runner (no checkpoints) the `act` generation is the L2 planner.

### 8.5 Cost (FR-17, NFR-7)
Each generation is a `state.tasks` row `sentinel-gNN` (counted in `run_cost_usd`, `usage_totals`).
`budget_usd`/`budget_pct` (default 10 % of run cap) stops **new generations**; ticks continue at $0. Target ≤ 3
generations/hour steady state; report combined overseer+sentinel overhead.

## 9. Authority and checkpoint contract (FR-16)

### 9.1 Split
Checkpoint: charter, wave contents, alignment, close-out. Sentinel: health, recovery, overrides, bandwidth,
ad-hoc injection/edits that a later checkpoint may revisit. Engine: hard containment.

### 9.2 Contract
Checkpoint digest gains `sentinel{generation, directives[], injected_since_last_ckpt[], kills[], edits[],
carry_over[], alarms[]}`. Checkpoint must acknowledge each open directive (`OV-R12b`: adopt / redirect / descope /
reject-with-rationale); enforced by ckpt-check. Sentinel units count toward wave signals + attempt caps.
Tamper-evident ledger (tool-owned hash chain), not tamper-proof (NFR-10).

## 10. Fail-safe, crash and resume (FR-18)
- Sentinel exception ⇒ lane disabled, run continues.
- Every mutation + its inbox consume record share one `RunState` save; replay of an already-consumed id is a no-op.
- `ao resume` restores overrides, generation counter, memory pointer, backlog, inbox cursor/consumed ring and replays
  `graph_edits`; no duplicate injection/kill/edit. Crash points tested: before save, after save before file move.
- SIGTERM/SIGINT: §3.4. Stale `running` from an unclean death is reconciled by `ao resume`/status (follow-up task
  decides whether the service also reconciles).

## 11. Settings

### 11.1 Schema
`SentinelSpec` as in EPIC §4.7 with spike adjustments: `recovery.idle_kill_minutes` default 15 (min 12 unless
`force`), added `recovery.kill_grace_seconds` (10), `recovery.systemic_kill_alarm` (3), `inbox_batch_max` (50),
`cancel_grace_seconds` (30). Same shape in workflow `sentinel:` and `.ao/config.yaml` `sentinel:`; registered in the
E-aYG5vn run-options registry (single source for CLI flags, env `AO_SENTINEL_*`, UI schema, allow-list).

### 11.2 Precedence (ADR-0003 policy)
workflow `sentinel:` > invocation (CLI flag > env `AO_SENTINEL_*` > `.ao/config.yaml`) > built-in default;
**live overrides** on top for the rest of the run (source `live-override` in `state.effective_settings`, shared with
T-zP28k9; same pattern as `breaker_overrides`).

### 11.3 Surfaces
`ao run`/`resume`/`new --run` share `_resolve_sentinel_settings`; flags `--sentinel/--no-sentinel`,
`--sentinel-llm/--no-sentinel-llm`, `--sentinel-powers`, `--sentinel-budget-usd`, `--max-parallel-ceiling`,
`--sentinel-set key=value`; template params; `ao validate`; `ao config show`; dashboard forms from
`GET /api/schema/sentinel` (or the registry schema endpoint). Default enablement: off until FR-23 evidence, then
mechanical on in both builtin templates (T-SnXDoc), LLM opt-in.

## 12. CLI `ao ctl` and dashboard control API

| Command | Entry kind |
|---|---|
| `ao ctl status <run>` | read-only |
| `ao ctl set <run> k=v…` | `set` |
| `ao ctl pause-llm / resume-llm` | `pause_llm` / `resume_llm` |
| `ao ctl kill <run> <task> --reason [--no-retry]` | `kill` |
| `ao ctl note <run> "…"` | `note` |
| `ao ctl release <run> [id…]` | `release_backlog` |
| `ao ctl cancel <run>` | `request_cancel` |

Dashboard: `GET /api/runs/{id}/sentinel`, `POST /api/runs/{id}/control` (body = one `ao.ctl/v1` entry minus id).
Requires ADR-0021 auth + Origin/CSRF check; actor recorded; kill requires reason; endpoint writes the inbox file only
(never mutates state). Panel: enable state, powers, utilisation gauge + idle timeline, running tasks with liveness age
+ Kill, ladder, backlog, generations with spend, quarantined proposals, notes, controls. Panel v1 in P1, v2 in P3.

## 13. Observability (FR-21, NFR-9, NFR-6)
Run-log: `sentinel.*`, `ctl.*`. Ledger (overseer workflows): `sentinel_trigger`, `sentinel_applied`,
`sentinel_rejected`, `sentinel_kill`, `sentinel_edit`, `sentinel_generation_start/end`, `sentinel_ungraceful`,
`ctl_override`. Bounded growth: archives rotated, memory ≤16 KiB, consumed ring 1000, 1 MiB per JSON.

## 14. Do we need a DB? — No for P1–P3
Revisit (SQLite as rebuildable **index**, never truth) only if: multiple processes must write one queue
concurrently; tick cost scales with state size beyond NFR-8; or the dashboard needs cross-run sentinel history.

## 15. Test strategy and acceptance
- Unit (fixed clock): tick, triggers, ladder, checker matrix (every `CK-*` rule + stage matrix), pack determinism,
  metrics, inbox idempotency/quarantine, `graph_edits` replay.
- Engine/integration (FakeExecutor): tick-while-all-busy; soft barrier with tasks in flight; in-flight rebuild;
  I1 resume-keeps-edits; kill → retry → escalate → fail; SIGTERM/SIGINT (pty); lane exception ⇒ as-disabled;
  crash at each mutation point + resume.
- Real subprocess: killpg TERM→KILL with grandchild; no survivors.
- E2E (CliRunner): `ao ctl` round-trips; golden byte-identity (NFR-1); heavy-tail fixture (NFR-13: ≥50 % idle-slot
  reduction, reported); LLM rotation ≥3 with scripted drivers.
- Dashboard: API auth/CSRF/actor tests; UI `npm test` + build.
- Live smoke (FR-23) only after owner confirms budget (proposal run cap $15); evidence under
  `output/E-Sn7tL2-sentinel-long-running-overseer/smoke/`. All engine tests use temp workspaces (NFR-11).
- Gates: `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .`.

## 16. Deferred (not in P1–P3)
FR-24 non-Claude drivers on the same contract · FR-25 SQLite index · FR-26 adaptive thresholds · FR-27 output-pattern
health checks · FR-28 `max_parallel` autoscaling · `/proc` descendant-CPU liveness signal if cut from P1 · service-side
reconciliation of stale `running` runs · sidecar LLM host.

## 17. Decisions (EPIC §10 — resolved by owner 2026-11-09)
1. Default enablement: after FR-23 evidence, mechanical on by default in both builtin templates; LLM opt-in.
2. New task status `killed`.
3. Early checkpoints count toward `max_waves`.
4. Live-control namespace `ao ctl`.
Plus D1–D4 (EPIC §0) as in ADR-0024.

## 18. Assumption log
| Assumption | Risk if wrong | Mitigation |
|---|---|---|
| Healthy silent gaps ≤ 10 min (Bash cap) in other workspaces | false kills | 15 min default, floor 12, kill cap, systemic alarm, recheck after smoke |
| In-flight rebuild safe given I1–I6 (spike used in-place swap hook, not real `run()`) | reshape bug | T-Sn6Brr engine tests; fallback §6.7 |
| Isolation/worktree tasks, routers, loops behave | unexercised in spike | checker rejects edits there |
| Stream-json activity file exists for all Claude executions | liveness blind | executor without `activity_path` ⇒ liveness disabled for it, alarm only |
| SIGINT default path works | unmeasured | pty test in T-Sn3Tck |

## 19. Risks
See EPIC §9; additions: `killed` status ripple through UI/summaries; hot-file contention in `engine.py` (sequence
tasks, HANDOFF §3).

## 20. Ownership / handoffs
Tasks per EPIC §8: Sn2Set settings → Sn3Tck tick/inbox → {Sn3Ctl, Sn4Rec, Sn8Gen} → Sn5Ui1/Sn6Brr → Sn7Bkl/Sn9Pow →
SnXAcc/SnXDoc. SnXDoc reconciles this HLD ("Deviations") with the implementation.

---

## Appendix A — Draft JSON schemas (draft 2020-12; indicative, tasks finalise)

### A.1 `ao.ctl/v1` (control inbox entry)
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "ao.ctl/v1",
  "type": "object",
  "required": ["schema", "id", "kind", "actor", "created_at"],
  "additionalProperties": false,
  "properties": {
    "schema": {"const": "ao.ctl/v1"},
    "id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9:_.-]{5,95}$"},
    "kind": {"enum": ["set", "pause_llm", "resume_llm", "note", "kill", "release_backlog", "inject",
                      "edit_pending", "directive", "request_hold", "request_cancel"]},
    "actor": {"type": "string", "description": "cli:<user> | dashboard:<user> | sentinel-gNN"},
    "created_at": {"type": "string", "format": "date-time"},
    "rationale": {"type": "string", "maxLength": 4000},
    "payload": {"type": "object"}
  },
  "allOf": [
    {"if": {"properties": {"kind": {"const": "set"}}},
     "then": {"properties": {"payload": {"type": "object", "required": ["values"],
       "properties": {"values": {"type": "object", "minProperties": 1}, "force": {"type": "boolean"}}}}}},
    {"if": {"properties": {"kind": {"const": "kill"}}},
     "then": {"required": ["rationale"], "properties": {"payload": {"type": "object", "required": ["task_id"],
       "properties": {"task_id": {"type": "string"}, "retry": {"type": "boolean", "default": true}}}}}},
    {"if": {"properties": {"kind": {"const": "note"}}},
     "then": {"properties": {"payload": {"type": "object", "required": ["text"],
       "properties": {"text": {"type": "string", "maxLength": 4000}}}}}},
    {"if": {"properties": {"kind": {"const": "release_backlog"}}},
     "then": {"properties": {"payload": {"type": "object",
       "properties": {"ids": {"type": "array", "items": {"type": "string"}}}}}}},
    {"if": {"properties": {"kind": {"const": "inject"}}},
     "then": {"properties": {"payload": {"type": "object", "required": ["tasks"],
       "properties": {"tasks": {"type": "array", "minItems": 1, "items": {"type": "object"}}}}}}},
    {"if": {"properties": {"kind": {"const": "edit_pending"}}},
     "then": {"properties": {"payload": {"type": "object", "additionalProperties": false, "properties": {
       "add_edges": {"type": "array", "items": {"$ref": "#/$defs/edge"}},
       "remove_edges": {"type": "array", "items": {"$ref": "#/$defs/edge"}},
       "trim_inputs": {"type": "array", "items": {"type": "object", "required": ["task_id", "inputs"],
         "properties": {"task_id": {"type": "string"}, "inputs": {"type": "array", "items": {"type": "string"}}}}},
       "cancel": {"type": "array", "items": {"type": "string"}},
       "skip": {"type": "array", "items": {"type": "string"}},
       "priority": {"type": "object", "additionalProperties": {"type": "integer"}}}}}}},
    {"if": {"properties": {"kind": {"const": "directive"}}},
     "then": {"required": ["rationale"], "properties": {"payload": {"type": "object", "required": ["text"],
       "properties": {"text": {"type": "string"}, "severity": {"enum": ["info", "warn", "alarm"]}}}}}}
  ],
  "$defs": {"edge": {"type": "object", "required": ["from", "to"],
    "properties": {"from": {"type": "string"}, "to": {"type": "string"}}}}
}
```
Quarantine sidecar `<id>.reason.json`: `{"rule_id": "CK-…", "detail": "…", "at": "<ts>"}`.

### A.2 `ao.sentinel.handoff/v1`
```json
{
  "$id": "ao.sentinel.handoff/v1",
  "type": "object",
  "required": ["schema", "generation", "ended_reason", "summary", "watch_list", "do_not_repeat", "open_directives"],
  "additionalProperties": false,
  "properties": {
    "schema": {"const": "ao.sentinel.handoff/v1"},
    "generation": {"type": "integer", "minimum": 1},
    "ended_reason": {"enum": ["ttl", "max_triggers", "max_tokens", "explicit_handoff", "budget"]},
    "summary": {"type": "string", "maxLength": 4000},
    "watch_list": {"type": "array", "items": {"type": "object", "required": ["task_id", "concern"],
      "properties": {"task_id": {"type": "string"}, "concern": {"type": "string"}, "check_after": {"type": "string", "format": "date-time"}}}},
    "do_not_repeat": {"type": "array", "items": {"type": "string"}},
    "open_directives": {"type": "array", "items": {"type": "string"}},
    "proposals_submitted": {"type": "array", "items": {"type": "string", "description": "ao.ctl ids"}},
    "next_suggested_triggers": {"type": "array", "items": {"type": "string"}}
  }
}
```

### A.3 `ao.overseer.backlog/v1`
```json
{
  "$id": "ao.overseer.backlog/v1",
  "type": "object",
  "required": ["schema", "checkpoint_id", "entries"],
  "additionalProperties": false,
  "properties": {
    "schema": {"const": "ao.overseer.backlog/v1"},
    "checkpoint_id": {"type": "string"},
    "entries": {"type": "array", "items": {
      "type": "object",
      "required": ["id", "priority", "brief_path", "kind", "ask_ids", "work_item", "rationale", "expires_at_ckpt"],
      "additionalProperties": false,
      "properties": {
        "id": {"type": "string", "pattern": "^[a-z0-9][a-z0-9-]{2,60}$"},
        "priority": {"type": "integer", "minimum": 0},
        "brief_path": {"type": "string"},
        "kind": {"enum": ["research", "design", "implement", "fix", "document", "test", "review", "verify"]},
        "ask_ids": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "work_item": {"type": "string"},
        "rationale": {"type": "string"},
        "expires_at_ckpt": {"type": "integer", "minimum": 1}
      }}}
  }
}
```

### A.4 `state.sentinel` (RunState block; serialised only when sentinel enabled)
```json
{
  "$id": "ao.runstate.sentinel/v1",
  "type": "object",
  "additionalProperties": false,
  "properties": {
    "enabled": {"type": "boolean"},
    "disabled_reason": {"type": ["string", "null"]},
    "overrides": {"type": "object", "additionalProperties": {
      "type": "object", "required": ["value", "actor", "at"],
      "properties": {"value": {}, "actor": {"type": "string"}, "at": {"type": "string", "format": "date-time"}}}},
    "consumed_ids": {"type": "array", "maxItems": 1000, "items": {"type": "string"}},
    "llm": {"type": "object", "properties": {
      "paused": {"type": "boolean"},
      "generation": {"type": "integer"},
      "active_generation_task": {"type": ["string", "null"]},
      "consecutive_rejections": {"type": "integer"},
      "spend_usd": {"type": "number"},
      "kills_last_hour": {"type": "array", "items": {"type": "string", "format": "date-time"}},
      "injections_last_hour": {"type": "array", "items": {"type": "string", "format": "date-time"}}}},
    "injected_count": {"type": "integer"},
    "backlog": {"type": "array", "items": {"type": "object"}},
    "early_checkpoint": {"type": ["object", "null"], "properties": {
      "checkpoint_id": {"type": "string"}, "carry_over": {"type": "array", "items": {"type": "string"}},
      "at": {"type": "string", "format": "date-time"}}},
    "systemic_kill_streak": {"type": "integer"},
    "metrics": {"type": "object", "properties": {
      "slot_seconds_busy": {"type": "number"}, "slot_seconds_idle_with_ready": {"type": "number"},
      "slot_seconds_idle_no_ready": {"type": "number"}, "utilisation_pct": {"type": "number"}}}
  }
}
```
Sibling top-level `RunState.graph_edits` (not inside `sentinel`, so non-sentinel `ao ctl`-less flows stay clean):
```json
{"type": "array", "items": {"type": "object", "required": ["op", "source", "at"],
  "properties": {
    "op": {"enum": ["add_edge", "remove_edge", "trim_inputs", "remove_task", "skip_task", "set_priority"]},
    "task_id": {"type": "string"}, "depends_on": {"type": "array", "items": {"type": "string"}},
    "inputs": {"type": "array", "items": {"type": "string"}},
    "source": {"type": "string", "description": "ctl:<user> | sentinel-tick | sentinel-gNN | early-ckpt"},
    "at": {"type": "string", "format": "date-time"}}}}
```
Per-task addition: `state.tasks[tid].recovery{kills:int, last_reason:str, escalation_step:int}`; per-task
`activity{last_activity_at, running_seconds}` (liveness).
