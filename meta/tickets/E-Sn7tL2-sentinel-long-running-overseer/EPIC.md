# EPIC: E-Sn7tL2-sentinel-long-running-overseer

## Metadata
- Epic ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Title: Sentinel — a run-long overseer that keeps every slot busy, heals stuck work, and edits the pending graph
- Owner: avadhoot
- Created: 2026-11-09
- Last Updated: 2026-11-09 (Rev 4 — implemented P0–P3; W11 §11 reasons refreshed; live smoke pending owner budget)
- Status: Implemented except live smoke + default flip (owner budget). 11 tasks Done, T-SnXAcc In Progress, T-SnXDoc Partial.
- Related: `E-YAAGhk-overseer-runner-template` (per-wave checkpoint overseer), ADR-0003 (settings precedence),
  ADR-0007 (wave/barrier scheduler), ADR-0016 (overseer cadence + budget governance), ADR-0017 (spawn provenance),
  ADR-0021 (dashboard auth), `docs-md/overseer-runner-hld.md` (NFR-X1 deferred preemptive checkpoints),
  `docs-md/guide-dynamic-task-injection.md`, `src/agent_orchestrator/monitoring.py` (AgentMonitor, not reused).
- **Co-delivery:** `E-aYG5vn-dashboard-run-limits-and-effort-naming` (dashboard limits panels, thinking-effort /
  turn-budget naming, schema-driven launch forms). Same components; implement in the same run — see §8.

> **Fresh session:** read `CLAUDE.md` + `AGENTS.md`, then this file §0 → §4, then
> `docs-md/overseer-runner-hld.md` (§0, §7, §8.2–8.4, §26) and `docs-md/guide-dynamic-task-injection.md`.
> Start with the P0 spike (`T-Sn0Spk`) and the ADR (`T-Sn1Adr`). Decisions in §0 are **owner-approved — do not
> re-litigate**; anything not covered there or in §10 must be raised, not assumed.

---

## 0. Owner requirement (primary) and decisions

**Requirement, in the owner's words (2026-11-09, condensed):** a long-running overseer running in parallel with the
workflow — mechanical where possible, LLM where needed — that (1) does runtime **health checks and recovery**,
(2) keeps the **full bandwidth utilised** by allocating new work as soon as slots free up instead of letting a wave
take as long as its slowest task (like an interactive Claude session spawning subagents on demand), (3) can
**correct dependencies and inject new tasks**, and (4) has all its **settings exposed in the CLI and dashboard**.

| ID | Decision (owner, 2026-11-09) | Consequence in this epic |
|----|------------------------------|--------------------------|
| D1 | **Bandwidth: layered.** L1 = release checkpoint-approved backlog into idle slots ($0, mechanical). L2 = when no backlog is left, *plan early* while the tail of the wave is still running. | §4.4; soft-barrier checkpoints + early-checkpoint trigger + sentinel planning generation |
| D2 | **Recovery: mechanical kill+retry ladder AND LLM may kill** (complementary). | §4.3; engine per-task kill primitive, liveness probe, ladder; LLM `kill` proposals rate-limited + rationale |
| D3 | **Graph edits on pending tasks ship with bandwidth, behind a setting that defaults on** for the LLM sentinel: add tasks, add/remove edges between pending tasks, cancel/skip/reprioritise pending tasks. Same mechanical checker as checkpoints. | §4.5; former "tier 3" folded into the `act` power |
| D4 | **Engine-wide + live controls.** Any workflow can opt in (overseer-runner and routed-runner); settings via `.ao/config.yaml`, env, CLI flags, New Run / Template Launch forms; live control via `ao ctl …` and a dashboard Sentinel panel (pause, powers, `max_parallel`, kill, note). | §4.7, §4.8 |

## 1. Evidence: the problem is real and large

Measured from `state.json` of the last 10 `overseer-runner` runs in `ao-runner-ao/.orchestrator/runs/` (P = 6):

| Run | Tasks | Wall | Slot utilisation | Wall time with exactly 1 task running | Wave tail* |
|---|---|---|---|---|---|
| o-q715f4 (game review) | 59 | 11.2h | 22% | 75% | 80% |
| o-frajcr (offline RPG) | 145 | 8.8h | 18% | 45% | 50% |
| o-yohvjn (protocol website) | 94 | 10.7h | 7% | 28% | — |
| o-1seol3 (minibots energy) | 46 | 1.9h | 22% | 78% | 72% |
| o-1gk30c / o-cs92jr / o-zubnbm | 27–43 | 0.3–2h | 23–41% | 60–68% | — |

\* share of each wave's span spent *after the median unit finished*, i.e. waiting for stragglers.

Conclusions: (a) wave sizes are small (3–8 units typical) **and** tails dominate, so both "more independent work"
(L1) and "plan the next wave while the tail runs" (L2) matter; (b) this satisfies the HLD's own revisit trigger for
NFR-X1 ("revisit if field runs show wave durations are highly variable"); (c) the primary KPI of this epic is
**slot utilisation / idle-slot-minutes**, which the engine does not record today (FR-20).

## 2. Findings from the code (verified at HEAD of `ad/enhancements-20oct`, 2026-11-09)

1. **The main loop cannot wake up while all workers are busy.** DRAIN is `next(as_completed(list(in_flight)))`
   (`engine.py:999`) with no timeout. Any in-engine tick/inbox drain first needs this to become
   `wait(in_flight, timeout=tick_seconds, return_when=FIRST_COMPLETED)`. (Closes former spike question 2.)
2. **Main thread owns state** (`engine.py:880` comment): RunState mutation, save, budget, breakers, injection are
   serialized there; only `_run_with_retries` runs on pool workers. ⇒ tick logic can run *on the main thread* against
   in-memory state; no second reader of `state.json` is needed.
3. **Barriers are the wave mechanism.** `_is_barrier` (`engine.py:2814`): any `emit_tasks` task (overseer
   checkpoints), loop gates, routers, and non-isolated tasks while integration is active. FILL breaks when a barrier
   is next and anything is in flight (`engine.py:943`). Checkpoints `depends_on` every wave unit, so they wait for the
   slowest one, then run solo.
4. **Injection is settle-bound** (`engine._inject` ≈L4685, called only while settling an emitter); graph rebuild
   (`_recompute_order`, `_predecessors`) has only ever run with nothing in flight. Safety with tasks in flight is
   **unverified** (spike).
5. **No liveness, no per-task kill.** `executors/claude_cli.py:634-651` does `proc.wait(timeout=ctx.timeout_seconds)`
   (default 1800s) then `kill()` → `timed_out`. Nothing detects a process that stops producing output; nothing can kill
   one running task from outside. Dashboard cancel SIGTERMs the whole run's process group; `cancel_fn` is never wired by
   the CLI (`engine.py:371` `_NO_CANCEL`), so SIGTERM likely kills `ao run` without a graceful unwind (unconfirmed —
   spike checks it).
6. **Existing recovery to build on:** retries (`_run_with_retries` ≈L4307, `RetryPolicy`), quota/429 waits
   (≈L1936-2060), self-heal (`_consult_task_failure_heal`, off by default), breakers (`breakers.py:387-396`:
   task_failures, consecutive_failures, wall/active seconds, verdict, injected_task_count, stop_file, task/run cost).
   Loop/stall detectors live only in `overseer_tool.py` (template side), not the engine.
7. **Nothing can change a live run from outside.** `max_parallel` is fixed at pool creation (`engine.py:673`, `:887`);
   no control files, no `ao inject/hold`, no service API. Only workaround: cancel + `ao resume --max-parallel N`.
8. **Settings plumbing is hand-wired in several places** — see §4.7. The dashboard has no config editor; run options go
   through a hard-coded allow-list (`ui/processes.py:651-667`) that silently drops unknown keys; `TemplateLaunch.tsx:54`
   posts `options: {}` so template launches carry no run options at all; `ao new --run` passes no run settings.
9. **AgentMonitor is the wrong base** (`monitoring.py`): out-of-band calls, cost not counted in `run_cost_usd`, no
   injection. Borrow its "never raise; safe default" posture only.
10. **Resume model:** `prepare_resume` merges `injected_tasks`; succeeded tasks with outputs are skipped. Every
    sentinel mutation must be persisted in the same `RunState` save as its inbox-consume record (G5 pattern).

---

## 3. Requirements

Phases: **P1** engine foundations (mechanical, $0) · **P2** bandwidth · **P3** LLM sentinel · **P4** later.

### 3.1 Functional

| ID | Ph | Requirement | Verification |
|----|----|-------------|--------------|
| FR-1 | P1 | **Opt-in, layered settings.** `sentinel:` block on `WorkflowSpec` + `sentinel:` defaults in `.ao/config.yaml` + env `AO_SENTINEL_*` + CLI flags on `ao run`/`ao resume`/`ao new --run`, resolved per ADR-0003 (§4.7). Absent/disabled ⇒ engine behaviour **byte-identical** to today. | schema/validate tests; golden byte-identity test |
| FR-2 | P1 | **Tick on the main loop.** DRAIN waits with `timeout=tick_seconds` (default 15s); on each wake-up (timeout or completion) the engine runs `_sentinel_tick` on the main thread: refresh liveness, update utilisation metrics, evaluate mechanical triggers, drain the control inbox, apply live overrides. Pure function of in-memory state + injectable clock; no LLM, no network. | unit tests with fixed clock; loop test proving wake-up with all workers busy |
| FR-3 | P1 | **Control inbox.** `<run_dir>/control/inbox/*.json` (atomic rename, 1 MiB cap, schema `ao.ctl/v1`, id-keyed, consumed once). Kinds: `set` (live override), `pause_llm`/`resume_llm`, `note`, `kill`, `release_backlog`, `inject`, `edit_pending`, `directive`, `request_hold`, `request_cancel`. Drained on the main thread **before FILL**; invalid entries → `control/inbox-rejected/` + rule id + ledger/run-log event. Writers: `ao ctl`, dashboard API, LLM generations. | engine unit tests; quarantine test; idempotent replay test |
| FR-4 | P1 | **Liveness probe.** Per running task the engine records `last_activity_at` (stdout/stream-json file size/mtime growth, sampled at tick) and `running_seconds`. Exposed in state, digest, `ao ctl status`, dashboard. | executor + engine tests with a fake process that stalls |
| FR-5 | P1 | **Per-task kill primitive.** Executors get a cooperative cancel (`TaskContext.cancel_event`; Claude CLI polls `proc.wait` in short slices, kills the process group on cancel). Engine `_kill_task(tid, reason, source)` → settles the task as `killed` (new status) with reason, never touching other tasks. Used by recovery, `ao ctl kill`, dashboard, LLM. | executor test (real subprocess sleep), engine test, CLI e2e |
| FR-6 | P1 | **Mechanical recovery ladder.** Triggers: `idle_kill_minutes` without output (default 15); `stuck_factor` × median duration of settled same-kind tasks (default 3×, floor `stuck_min_minutes` 20, needs ≥3 samples); task cost ≥ `cost_kill_pct` of task cap. Ladder per task: retry same → retry with escalation (`effort`/`model` step from `recovery.escalate`) → mark failed (normal failure path ⇒ checkpoint/sentinel replans). Kill-retries carry a retry-context note ("killed: no output for 15m") and count against `max_recovery_kills_per_task` (default 2), **not** `max_attempts`. Protected: barrier tasks get alarm-only unless `recovery.include_barriers: true`. | ladder unit tests; e2e with stalled fake executor |
| FR-7 | P1 | **Live overrides.** `ao ctl set` / dashboard can change, mid-run: `max_parallel` (soft limit ≤ `max_parallel_ceiling`; pool is created at the ceiling), sentinel powers, `pause_llm`, recovery thresholds, sentinel budget. Overrides persist in `state.sentinel.overrides` (survive resume) and are ledgered. | engine tests; resume test |
| FR-8 | P2 | **Soft-barrier emitters.** An `emit_tasks` task marked `barrier: soft` (overseer checkpoints) may start while non-predecessor tasks are in flight, provided its manifest is additive w.r.t. in-flight tasks (no edges *into* running tasks, no re-declaring them). Graph rebuild runs with tasks in flight. Routers/loop gates/integration barriers stay hard. | gated on P0 spike; engine tests incl. in-flight rebuild |
| FR-9 | P2 | **L1 backlog release.** Checkpoints may emit `outputs/backlog.json` (`ao.overseer.backlog/v1`): pre-approved units independent of the current wave (`id, priority, brief_path, kind, ask_ids, work_item, rationale, expires_at_ckpt`). When `running < max_parallel` and nothing is ready, the tick releases entries FIFO by priority as `s-NN-<slug>` units. No LLM. Entries expire at the next checkpoint unless re-confirmed. | e2e: idle-slot fill during wave tail |
| FR-10 | P2 | **L2 early checkpoint (mechanical trigger).** When the backlog is empty, `idle_slots ≥ early_ckpt_min_idle` (default ⌈P/2⌉) for ≥ `early_ckpt_grace_seconds` (default 120), and ≥ `early_ckpt_fraction` (default 0.5) of the pending checkpoint's predecessors have settled, the tick rewrites that checkpoint's `depends_on` to the settled predecessors only (a pending-edge edit, FR-12) and passes the unsettled ones as `carry_over[]` in its digest. The (soft-barrier) checkpoint then plans the next wave while the tail runs. At most one early checkpoint in flight. | e2e with heavy-tailed scripted durations; digest test |
| FR-11 | P2 | **Shared mechanical checker** for every mutation regardless of source (backlog, early ckpt, `ao ctl`, LLM): kind map, brief exists + cites ≥1 charter ask (overseer workflows), attempt caps / rename-dodge (OV-R13/R13c), `ov-unit-gate` pre-hook, id patterns, allowed `effort`, budget-stage rules (no new scope in converge+, only stabilize/verify/document in stabilize, nothing in closeout), rate limits, cap on sentinel-injected tasks (separate counter, FR-19). For workflows without a charter (routed-runner) the charter rules are skipped, the structural rules still apply. | checker unit tests incl. stage matrix |
| FR-12 | P2 | **Pending-graph edits** (`edit_pending`): add/remove edges between **pending** tasks, cancel/skip pending tasks (→ `skipped` with reason), reprioritise. Checker additionally rejects: cycles; touching running/finished tasks; removing an edge whose predecessor produces a declared `inputs` artifact of the successor (unless the input is also removed); orphaning a route sink; edits inside a hard-barrier cone. Gated by `sentinel.allow_graph_edits` (default **true** when LLM `act` power is on, per D3). | checker tests; engine test with in-flight rebuild |
| FR-13 | P2 | **Spawn origin `sentinel`** (`SPAWN_ORIGIN_SENTINEL`, ADR-0017) with `parent_task_id` = `sentinel-tick` / `sentinel-gNN` / `ctl:<user>`; UI graph shows them distinctly. | engine + ui/graph tests |
| FR-14 | P3 | **LLM sentinel generations.** On an `needs_llm` trigger or heartbeat, start a bounded generation `sentinel-gNN` via `SentinelDriver` in a dedicated 1-worker lane executor (**outside** `max_parallel`). Fixed input pack (charter if any, digest incl. slots/liveness/carry-over, last K=40 ledger/run events, backlog, `memory.md` tool-compacted ≤16 KiB, previous `handoff.json`, `sentinel-contract.md`; pack ≤120 KiB). Writes only files: inbox proposals, `report.md`, `handoff.json` (`ao.sentinel.handoff/v1`), `memory-append.md`. Ends at the first of TTL / max triggers / max tokens / explicit handoff; ungraceful exit ⇒ `sentinel_ungraceful` + next gen starts from digest+memory. | pack determinism tests; rotation e2e with scripted sessions; fault injection |
| FR-15 | P3 | **LLM powers.** `advise`: directives, alarms, notes, `request_hold/cancel`. `act` (adds): `inject`, `edit_pending` (if `allow_graph_edits`), `kill` (if `allow_kill`, default true per D2). LLM kills: rationale ≥ 40 chars, task running ≥ `llm_kill_min_running_minutes` (10), ≤ `max_llm_kills_per_hour` (2), never barrier/emitter tasks; the killed task then follows the FR-6 ladder. For workflows without checkpoints (routed-runner), the `act` generation is the L2 planner. | checker tests per power |
| FR-16 | P3 | **Checkpoint ↔ sentinel contract.** Checkpoint digest gains `sentinel{generation, directives[], injected_since_last_ckpt[], kills[], edits[], carry_over[], alarms[]}`; checkpoint must acknowledge each open directive (`OV-R12b`: adopt/redirect/descope/reject-with-rationale). Sentinel units count toward wave signals + attempt caps. Authority: checkpoint wins on charter, wave contents, close-out; sentinel wins on nothing structural that a later checkpoint revisits. | checker + e2e |
| FR-17 | P3 | **Cost accounting.** Each generation is a task row `sentinel-gNN` in `RunState.tasks` (counted in `run_cost_usd`, `usage_totals`). `sentinel.budget_usd` / `budget_pct` (default 10% of the run cap where one exists) stops *new generations* when reached; ticks keep running at $0. | engine + usage tests |
| FR-18 | P1–P3 | **Fail-safe + resume.** Any sentinel exception ⇒ `sentinel.error` logged, lane disabled for the run, run continues as today. `N` consecutive rejected LLM proposals (default 5) ⇒ LLM paused + directive to next checkpoint. `ao resume` restores overrides, generation counter, memory, backlog, inbox cursor; no duplicate injection/kill. | fault-injection + resume e2e |
| FR-19 | P2 | **Separate injection cap.** Sentinel/backlog/ctl injections count in `sentinel_injected_count` with their own breaker `sentinel_injected_task_count` (default `max(20, 3×max_parallel×expected_hours)`), leaving the template's `injected_task_count` math (CFG-3) unchanged. | breaker tests |
| FR-20 | P1 | **Utilisation metrics.** `state.metrics{slot_seconds_busy, slot_seconds_idle_with_ready, slot_seconds_idle_no_ready, utilisation_pct}` updated each tick; in `ao status`, run summary, dashboard. This is the epic's KPI. | metrics unit tests |
| FR-21 | P1–P3 | **Observability.** Run-log events `sentinel.*` / `ctl.*`; ledger events (overseer workflows, tool-owned hash chain) `sentinel_trigger`, `sentinel_applied`, `sentinel_rejected`, `sentinel_kill`, `sentinel_edit`, `sentinel_generation_start/end`, `sentinel_ungraceful`, `ctl_override`. NFR-9 legibility. | ledger + log tests |
| FR-22 | P1–P3 | **CLI + dashboard surface** (§4.7, §4.8): settings at launch, live controls, Sentinel panel, auth-protected control endpoint. | CliRunner e2e; UI tests; API auth/CSRF tests |
| FR-23 | P3 | **Live smoke run** with real Claude CLI on a toy multi-ask prompt: ≥3 generation rotations, scripted stalled task (killed + retried), idle-slot scenario (backlog + early ckpt), one LLM pending-edit. Evidence (ledger, handoffs, utilisation before/after, cost split work/overseer/sentinel) under `output/E-Sn7tL2-sentinel-long-running-overseer/`. | evidence review |

### 3.2 Later (P4)
FR-24 non-Claude drivers on the same contract · FR-25 optional SQLite *index* (never truth) if tick cost / UI history
justifies it (§7) · FR-26 adaptive thresholds from observed durations · FR-27 output-pattern health checks (repeated
identical tool errors, permission-prompt loops) · FR-28 dynamic `max_parallel` autoscaling against quota/budget burn.

### 3.3 Non-functional

| ID | NFR |
|----|-----|
| NFR-1 | Backward compat: no `sentinel:` / disabled ⇒ byte-identical behaviour for every existing workflow (golden test). The DRAIN timeout change (FR-2) is itself behaviour-neutral when disabled (no wake-up without a tick to run). |
| NFR-2 | Determinism: tick, triggers, ladder, checker, pack assembly are pure functions of state + files + injectable clock. |
| NFR-3 | Idempotency: replaying the same inbox/state produces no duplicate injections, kills, edits or ledger events. |
| NFR-4 | Single writer: all RunState mutation stays on the engine main thread; the LLM lane, CLI and dashboard only write inbox files. |
| NFR-5 | Path safety: everything confined under `workspace_root` (resolve + `is_relative_to`, reject escaping symlinks). |
| NFR-6 | Bounded IO: 1 MiB per JSON; memory 16 KiB; pack 120 KiB; inbox archives rotated. |
| NFR-7 | Cost: mechanical layer $0; LLM spend ≤ sentinel cap; target ≤3 generations/hour steady state; report combined overseer+sentinel overhead (checkpoints already 15–17%, HLD NFR-8). |
| NFR-8 | Long-run stability: 12h run ⇒ bounded growth of state, ledger, memory, archives; tick < 50 ms at 1k tasks on the main thread (measure in P1). |
| NFR-9 | Legibility: every sentinel/ctl action reconstructable from ledger/run log + generation dirs. |
| NFR-10 | Tamper-evident, not tamper-proof; hard containment stays engine-side (breakers, unit gate, backstop). |
| NFR-11 | Self-hosting safety: clone-only, temp workspaces, PR to `main`; never `install.sh`/restart `ao.service` from a run. |
| NFR-12 | Security: dashboard control endpoint requires ADR-0021 auth + origin/CSRF checks; `kill`/`set` actions audited with the actor. |
| NFR-13 | KPI target (reported, not gated): on the heavy-tail e2e fixture, idle-slot-seconds-with-work-available drop ≥50% vs disabled; live smoke reports utilisation vs the §1 baseline. |

---

## 4. Design

### 4.1 Overview

```
 ┌──────────────────────────────── ao run (engine process) ────────────────────────────────────┐
 │ main thread loop:                                                                             │
 │   cancel? → reconcile_inputs → SENTINEL TICK ─┬─ liveness + metrics                           │
 │                                               ├─ drain control/inbox (ctl, dashboard, LLM)    │
 │                                               ├─ mechanical: recovery ladder, backlog release,│
 │                                               │   early-ckpt trigger, needs_llm triggers      │
 │                                               └─ apply via shared checker → _inject/_kill/edit│
 │   → FILL (soft limit = live max_parallel) → wait(in_flight, timeout=tick_seconds)            │
 │                                                                                               │
 │ worker pool (ceiling)        lane executor (1 worker, NOT counted in max_parallel)            │
 │   tasks ──────────────        sentinel-gNN: bounded `claude -p` on a fixed pack → inbox files │
 └───────────────────────────────────────────────────────────────────────────────────────────────┘
   writers of control/inbox:  ao ctl …   │  dashboard POST /api/runs/{id}/control  │  sentinel-gNN
```

Change vs Rev 1: the deterministic tick now runs **on the engine main thread** against in-memory state (no lane
thread re-reading `state.json`); only LLM generations run off-thread. This removes a whole class of races, makes
tick cost trivially cheap, and reuses the single-writer invariant. The inbox remains the one door for every
external actor, so the LLM host is swappable (sidecar later if needed).

| | Per-wave checkpoint (existing) | Sentinel — mechanical (P1–P2) | Sentinel — LLM (P3) |
|---|---|---|---|
| Lifetime | one task per wave | every tick, whole run | rotating bounded generations |
| Owns | charter, wave contents, alignment, close-out | health, recovery ladder, live overrides, backlog release, early-ckpt trigger | directives, ad-hoc injection, pending-graph edits, judgement kills, L2 planning where no checkpoints exist |
| Cost | LLM per wave | $0 | capped (default 10% of run cap) |

### 4.2 Main-loop changes (engine)
- DRAIN: `wait(in_flight, timeout=tick_seconds, return_when=FIRST_COMPLETED)`; settle each completed future as today.
  Disabled ⇒ no timeout (byte-identical).
- `_sentinel_tick(state, workflow, ctx, now)` after `_reconcile_inputs`, before FILL. Any exception ⇒ log
  `sentinel.error`, disable the lane for the run, continue (FR-18).
- Pool created at `max_parallel_ceiling` (default = configured `max_parallel`, so no change unless the user raises
  the ceiling); FILL uses the live soft limit.
- All mutations + inbox consume records are saved in one `RunState` save before breaker evaluation (G5).

### 4.3 Health & recovery (D2)
- Liveness: executor exposes `activity_path` (stdout / stream-json file); tick samples size+mtime →
  `last_activity_at`. Claude CLI with stream-json emits events continuously during tool use, so silence for
  15 min is a strong hang signal (spike validates on real logs).
- Kill: cooperative `cancel_event` → executor kills the process group → outcome `killed{reason, source}` → settle
  path routes to the ladder.
- Ladder state per task in `state.tasks[tid].recovery{kills, last_reason, escalation_step}`. Escalation steps come
  from `sentinel.recovery.escalate` (default `[{effort: high}, {model: <next stronger in agents config>}]`).
- LLM kill = an inbox `kill` with rationale; after the kill the same ladder applies (the LLM never chooses the retry
  shape directly — it may additionally `inject` a replacement unit, through the checker).
- Run-level health alarms (no LLM): no task settled for `stall_minutes`; consecutive kills across tasks ≥ N
  (systemic: env/quota problem → pause LLM, raise directive, do **not** keep killing); quota waits surfaced.

### 4.4 Bandwidth (D1)
1. **L1 backlog** (FR-9): checkpoint pre-approves independent units; tick releases when slots idle and nothing ready.
2. **L2 early planning:** overseer workflows → early checkpoint (FR-10) on a soft barrier (FR-8): the checkpoint
   plans the next wave from the settled units and sees the stragglers as `carry_over[]`; it must not re-schedule them.
   Workflows without checkpoints (routed-runner) → the LLM `act` generation proposes units (FR-15).
3. Interaction: sentinel-injected and carry-over units never block the next checkpoint (they are not its
   predecessors); the next checkpoint's digest lists them so it can depend on them explicitly if needed.
4. Fallback if the spike says in-flight rebuild is unsafe: L1 only, released units restricted to *new leaves with
   no new edges*, and the early checkpoint waits for a hard barrier (still removes the slowest-unit dependency).

### 4.5 Pending-graph edits (D3)
`edit_pending{add_edges[], remove_edges[], cancel[], skip[], priority{}}` applied atomically or rejected as a whole.
Checker rules in FR-12. Priority affects FILL order only among ready tasks (stable tie-break after priority, keeping
ADR-0007 D6 determinism). Every edit is ledgered with before/after edge lists.

### 4.6 LLM generations (P3, unchanged in substance from Rev 1)
Fixed pack, tool-owned memory compaction, handoff with watch-list and do-not-repeat notes, rotation by TTL / triggers
/ tokens, `SentinelDriver` ABC (`start/poll/stop`) with `ClaudeCliSentinelDriver` first. Triggers with cooldowns and
per-hour caps: `stuck_task` (after the mechanical ladder is exhausted), `new_signal(high|medium)`, `stage_change`,
`unit_failed|blocked`, `human_note`, `idle_slots_no_backlog` (routed-runner L2), `heartbeat`.

### 4.7 Settings (D4) — one schema, every surface

`SentinelSpec` (pydantic, `models.py`) — same shape in workflow `sentinel:` and `.ao/config.yaml` `sentinel:`:

```yaml
sentinel:
  enabled: false                 # master switch (mechanical layer)
  tick_seconds: 15
  max_parallel_ceiling: null     # null = max_parallel; raise to allow live increases
  recovery:
    enabled: true
    idle_kill_minutes: 15
    stuck_factor: 3.0
    stuck_min_minutes: 20
    cost_kill_pct: null
    max_recovery_kills_per_task: 2
    escalate: [{effort: high}]
    include_barriers: false
  bandwidth:
    backlog_release: true
    early_checkpoint: true
    early_ckpt_fraction: 0.5
    early_ckpt_min_idle: null    # null = ceil(max_parallel/2)
    early_ckpt_grace_seconds: 120
  llm:
    enabled: false
    powers: act                  # advise | act
    allow_graph_edits: true      # D3
    allow_kill: true             # D2
    max_llm_kills_per_hour: 2
    max_injections_per_hour: 6
    heartbeat_minutes: 30
    generation_ttl_minutes: 60
    budget_usd: null
    budget_pct: 10
    driver: claude-cli
    agent: null                  # agents-config id; default = workflow's strongest reviewer/architect agent
  max_injected_tasks: null       # FR-19 default formula
```

Precedence (ADR-0003, unchanged policy): task-n/a · workflow `sentinel:` > invocation (CLI flag > env
`AO_SENTINEL_*` > `.ao/config.yaml`) > built-in default; **live overrides** (`ao ctl set`) sit on top for the rest of
the run and are recorded in state (same pattern as `breaker_overrides`). The ADR must state this explicitly.

Plumbing checklist (§2 finding 8 — every item is a task deliverable):
- `models.py` `SentinelSpec`; `WorkflowSpec.sentinel`; `ProjectConfig.sentinel` + commented sample in `project_config.py`.
- `cli.py`: one shared resolver `_resolve_sentinel_settings` used by **both** `run` and `resume` (do not duplicate the
  existing run/resume wiring pattern again); flags `--sentinel/--no-sentinel`, `--sentinel-llm/--no-sentinel-llm`,
  `--sentinel-powers`, `--sentinel-budget-usd`, `--max-parallel-ceiling`, `--sentinel-set key=value` (repeatable,
  dotted keys, validated against `SentinelSpec`).
- `ao new … --run`: pass run options through `_invoke_run_in_process` (today it passes none).
- Templates: `overseer-runner` and `routed-runner` params `sentinel_enabled`, `sentinel_llm`, `sentinel_powers`,
  `sentinel_budget_pct` rendered into `workflow.json` `sentinel:`; template READMEs; `overseer-runner` checkpoint
  instruction gains backlog + directive-ack + carry-over sections and its contract gains the schemas.
- Dashboard: replace the hard-coded `ALLOWED_OPTIONS` entries for sentinel with a schema-driven section generated
  from `SentinelSpec` JSON schema (served by a new `GET /api/schema/sentinel`); NewRun and resume forms render it;
  `TemplateLaunch.tsx` sends `options` (fixes `options: {}`).
- `ao validate` validates the block; `ao config show` (or equivalent) prints effective sentinel settings with source.

### 4.8 Live control surface (D4)

CLI (`ao ctl`, every subcommand writes one inbox entry or reads state; works for any running run):

| Command | Effect |
|---|---|
| `ao ctl status <run>` | slots (running/limit/ceiling), utilisation, liveness per running task, ladder state, backlog, LLM generation + spend, pending inbox, recent actions |
| `ao ctl set <run> key=value…` | live override (`max_parallel`, `sentinel.llm.powers`, `sentinel.recovery.idle_kill_minutes`, …) |
| `ao ctl pause-llm / resume-llm <run>` | stop/allow new generations (ticks continue) |
| `ao ctl kill <run> <task> --reason …` | manual kill → ladder (`--no-retry` marks failed) |
| `ao ctl note <run> "…"` | human note → next generation / next checkpoint digest |
| `ao ctl release <run> [backlog-id…]` | force backlog release |
| `ao ctl cancel <run>` | graceful run cancel via inbox (also fixes the unwired `cancel_fn`, §2.5) |

Dashboard: **Sentinel panel** on the run page — enable state, powers, utilisation gauge + idle-slot timeline,
running tasks with liveness age and a Kill button (confirm + reason), ladder state, backlog list, generation
timeline with spend, quarantined proposals, notes box, controls for pause/resume LLM, powers, `max_parallel`.
Backed by `GET /api/runs/{id}/sentinel` and `POST /api/runs/{id}/control` (auth + CSRF, actor recorded). Graph view:
sentinel-origin tasks, removed/added edges, killed attempts marked.

---

## 5. Alternatives considered

| Option | Verdict |
|---|---|
| A. One long-lived `claude` session as a workflow task | ✗ 1800s timeout, `claude -p` exits per turn, would block the DAG as an emitter, compaction drift at 6–12h, cost unbounded. |
| B. Sidecar process under `ao service` editing state | ✗ second writer on run state. Remains a possible *host* for LLM generations later; the inbox contract makes that swap cheap. |
| C. Extend `AgentMonitor` | ✗ shallow, no injection, cost uncounted. |
| D. Lane thread that re-reads `state.json` (Rev 1) | ✗ superseded: main-thread tick on in-memory state is simpler, cheaper and race-free once DRAIN has a timeout. |
| E. Only bigger waves (raise `wave_size`) | ✗ insufficient: tails dominate even in 15–20-unit waves (o-frajcr, 50% tail). |
| **F. Main-thread mechanical tick + inbox + soft-barrier checkpoints + bounded LLM generations (chosen)** | ✓ Fixes bandwidth and health at $0 first, adds judgement where it pays, preserves single-writer and resumability. |

## 6. Phasing

| Phase | Scope | Value on its own |
|---|---|---|
| **P0 spike** | (a) graph rebuild + `_ready_ids` with tasks in flight (soft barrier); (b) cooperative kill of a Claude CLI process group + liveness signal on real stream-json logs; (c) per-tick cost on a 1k-task state; (d) confirm SIGTERM behaviour of `ao run`. Go / go-with-changes / no-go recorded in STATUS.md. | de-risks P1/P2 |
| **P1 foundations (mechanical, $0)** | FR-1–7, FR-18, FR-20–22 (settings, tick, inbox, `ao ctl`, liveness, kill, ladder, live `max_parallel`, metrics, Sentinel panel v1). | hung tasks recovered; live control; utilisation visible |
| **P2 bandwidth** | FR-8–13, FR-19 (soft barrier, backlog, early checkpoint, pending edits via ctl/mechanical, sentinel origin, shared checker); overseer-runner checkpoint instruction + contract updates. | the idle-slot fix (§1) |
| **P3 LLM sentinel** | FR-14–17, FR-23 (generations, powers incl. LLM kill + edits, directives/ack, cost row, routed-runner L2, live smoke). | judgement between waves; routed-runner benefits |
| **P4** | FR-24–28. | breadth |

## 7. Do we need a DB?
Not for P1–P3: in-memory state on the main thread + `state.json`, hash-chained ledger, inbox files and generation
dirs cover it. Revisit (SQLite as rebuildable index, never truth) only if: multiple processes must write one queue
concurrently, tick cost grows with state size beyond NFR-8, or the dashboard needs cross-run sentinel history.

## 8. Task breakdown (task folders created 2026-11-09; each has TASK.md + STATUS.md)

| # | Task | Phase | Reqs | Depends on |
|---|------|-------|------|------------|
| 1 | `T-Sn0Spk-midflight-reshape-kill-spike` | P0 | de-risk FR-5/8/12, NFR-8 | — |
| 2 | `T-Sn1Adr-sentinel-adr-and-hld` | P0 | design authority | Sn0Spk |
| 3 | `T-Sn2Set-sentinel-settings-plumbing` | P1 | FR-1, FR-22 (settings) | **E-aYG5vn T-zP28k9**, Sn1Adr |
| 4 | `T-Sn3Tck-main-loop-tick-inbox-overrides` | P1 | FR-2, FR-3, FR-7, FR-18, FR-20, FR-21 | Sn2Set |
| 5 | `T-Sn3Ctl-ao-ctl-cli` | P1 | FR-3 (writers), FR-7, FR-22 (CLI) | Sn3Tck |
| 6 | `T-Sn4Rec-liveness-kill-recovery-ladder` | P1 | FR-4, FR-5, FR-6 | Sn3Tck, Sn0Spk |
| 7 | `T-Sn5Ui1-sentinel-panel-and-control-api` | P1 | FR-7, FR-20, FR-22, NFR-12 | Sn3Ctl, Sn4Rec, **E-aYG5vn T-pMpo4O, T-xNFBww** |
| 8 | `T-Sn6Brr-soft-barrier-and-pending-edits` | P2 | FR-8, FR-11, FR-12, FR-13 | Sn0Spk GO, Sn3Tck |
| 9 | `T-Sn7Bkl-backlog-and-early-checkpoint` | P2 | FR-9, FR-10, FR-19 | Sn6Brr |
| 10 | `T-Sn8Gen-llm-generations-pack-handoff` | P3 | FR-14, FR-17, FR-18 | Sn3Tck, Sn2Set |
| 11 | `T-Sn9Pow-llm-powers-directives-routed` | P3 | FR-15, FR-16, FR-22 (panel v2) | Sn8Gen, Sn6Brr, Sn4Rec, Sn5Ui1 |
| 12 | `T-SnXAcc-e2e-and-live-smoke` | P3 close | FR-23, NFR-7/8/13, §11 | Sn7Bkl, Sn9Pow, **E-aYG5vn T-mQs7CQ** |
| 13 | `T-SnXDoc-docs-readme-learnings` | each phase close | docs gates; default flip §10.1 | phase completion |

Critical path: Sn0Spk → Sn1Adr → Sn2Set → Sn3Tck → Sn6Brr → Sn7Bkl → SnXAcc. Parallel lanes once Sn3Tck lands:
{Sn3Ctl → Sn5Ui1}, {Sn4Rec}, {Sn8Gen → Sn9Pow}. E-aYG5vn's T-zP28k9 runs in parallel with Sn0Spk/Sn1Adr.
Combined run order and parallelism: see `HANDOFF.md` in this folder.

**Co-delivery with `E-aYG5vn`** (avoid reworking the same components twice):
- `T-zP28k9-run-options-registry-and-settings-snapshot` comes **before `Sn2Set`** (it can run in parallel with
  Sn0Spk/Sn1Adr). It builds the single run-options registry (CLI flags/env/config/UI schema/allow-list) and
  `state.effective_settings` with sources; `Sn2Set` then adds `SentinelSpec` to that registry, and FR-7 live
  overrides are recorded as source `live-override` in the same snapshot.
- `T-f0d7gm` (schema-driven launch forms, naming) and `T-xNFBww` (limits panels) are designed together with `Sn5Ui1`
  (Sentinel panel), as one set of run-page sections.

## 9. Risks

| Risk | Mitigation |
|---|---|
| In-flight graph rebuild unsafe | P0 spike; fallback §4.4.4 (leaf-only injection, hard-barrier early ckpt) |
| False-positive kills (long silent thinking, big builds) | conservative defaults (15 min idle, 3× median, ≥3 samples, floor 20 min); kills don't burn `max_attempts`; per-task kill cap; systemic-kill alarm stops killing; spike calibrates on real logs |
| Early checkpoints double cost or plan duplicates of stragglers | at most one early ckpt in flight; `carry_over[]` in digest + checker rejects re-scheduling carry-over work; fraction/grace thresholds; report combined overhead |
| Sentinel ↔ checkpoint thrash | authority split; directives acknowledged; shared attempt caps / loop detectors; rate limits |
| LLM cost creep | $0 mechanical layer first; LLM off by default; hard cap; triggers with cooldowns |
| Live `max_parallel` > quota | ceiling defaults to configured value; quota waits already pause dispatch; FR-28 later |
| Dashboard control endpoint abuse | ADR-0021 auth + CSRF; actor audit; kill requires reason |
| Settings drift between surfaces | single `SentinelSpec` schema drives CLI validation and dashboard form |
| Self-hosting hazard | NFR-11 |

## 10. Decisions resolved (owner, 2026-11-09)
1. Default enablement: after FR-23 evidence, **mechanical layer on by default in both builtin templates; LLM stays opt-in** (done in T-SnXDoc).
2. **New task status `killed`** (not `timed_out` + reason).
3. **Early checkpoints count toward `max_waves`.**
4. Live-control namespace: **`ao ctl`**.

No open design decisions remain. New ones discovered during implementation are raised to the owner (fail-fast
rule), not assumed.

## 11. Final checklist (definition of done — every box ticked with evidence before closing)

**Gates**
- [x] P0 spike result recorded (go / go-with-changes / no-go) in STATUS.md. — STATUS.md: GO-WITH-CHANGES (T-Sn0Spk).
- [x] ADR-0024 + `docs-md/sentinel-hld.md` approved by owner (STATUS.md entry). — STATUS.md owner sign-off 2026-11-09.
- [ ] Full `pytest -q`, `ruff check .`, `ruff format --check .`, `mypy .` green; UI `npm test` + `npm run build` green.
- [x] NFR-1 golden test: no `sentinel:` / disabled ⇒ byte-identical workflow.json/state.json/run log for existing workflows. — `test_sentinel_e2e_matrix.py::test_disabled_sentinel_equals_absent_state_golden` (state.json scrubbed + file set; run-log byte-identity not separately asserted).

**E2E scenarios (CliRunner + FakeExecutor)**
- [x] Tick runs while all workers busy; metrics correct. — `T-SnXAcc*/MATRIX.md` (full `pytest -q` 2026-11-09: matrix + sentinel suites pass).
- [x] Stalled task → kill → retry → escalate → fail; systemic alarm stops kills. — `T-SnXAcc*/MATRIX.md` (full `pytest -q` 2026-11-09: matrix + sentinel suites pass).
- [x] `ao ctl` set / pause-llm / resume-llm / note / kill / release / cancel round-trip, incl. live `max_parallel`. — `T-SnXAcc*/MATRIX.md` (full `pytest -q` 2026-11-09: matrix + sentinel suites pass).
- [x] Dashboard control API: auth + CSRF enforced; actions audited with actor. — `T-SnXAcc*/MATRIX.md` (full `pytest -q` 2026-11-09: matrix + sentinel suites pass).
- [x] Heavy-tail fixture: idle-slot-seconds-with-work-available reduced ≥50% vs disabled (NFR-13). — `tests/test_sentinel_backlog.py::test_heavy_tail_*`: 3.5 s -> 0.01 s (>99%), HLD §6.9.
- [x] Backlog release + expiry; early checkpoint with carry-over, never re-schedules stragglers. — `T-SnXAcc*/MATRIX.md` (full `pytest -q` 2026-11-09: matrix + sentinel suites pass).
- [x] Pending-edit accept/reject matrix (all FR-12 rules) + stage gating (FR-11). — `T-SnXAcc*/MATRIX.md` (full `pytest -q` 2026-11-09: matrix + sentinel suites pass).
- [x] Soft-barrier emitter with tasks in flight (or documented fallback). — `T-SnXAcc*/MATRIX.md` (full `pytest -q` 2026-11-09: matrix + sentinel suites pass).
- [x] LLM generation rotation (≥3), ungraceful exit, budget cap, consecutive-rejection auto-pause. — `T-SnXAcc*/MATRIX.md` (full `pytest -q` 2026-11-09: matrix + sentinel suites pass).
- [x] LLM powers: advise vs act; LLM kill limits; directive acknowledgement enforced by ckpt-check. — `T-SnXAcc*/MATRIX.md` (full `pytest -q` 2026-11-09: matrix + sentinel suites pass).
- [x] routed-runner opt-in: LLM L2 planner injects a sentinel-origin unit. — `T-SnXAcc*/MATRIX.md` (full `pytest -q` 2026-11-09: matrix + sentinel suites pass).
- [x] Crash + `ao resume` at each mutation point: no duplicate or lost injection/kill/edit; overrides restored. — `T-SnXAcc*/MATRIX.md` (full `pytest -q` 2026-11-09: matrix + sentinel suites pass).
- [x] Lane exception ⇒ run continues as if disabled. — `T-SnXAcc*/MATRIX.md` (full `pytest -q` 2026-11-09: matrix + sentinel suites pass).

**Evidence & docs**
- [ ] FR-23 live smoke under `output/E-Sn7tL2-sentinel-long-running-overseer/smoke/`: utilisation vs §1 baseline, cost split, combined overhead.
- [x] HLD "Deviations" reconciled; template READMEs, dynamic-injection guide, CLI reference, `.ao/config.yaml` sample updated. — HLD §21, `docs-md/guide-sentinel.md`, both template READMEs, injection guide, README, `.ao/config.yaml` sample (T-SnXDoc 2026-11-09).
- [ ] Mechanical-sentinel default flipped on in both builtin templates (after evidence).
- [ ] Settings visible in all surfaces: `ao validate`, `ao run/resume/new --run` flags, `ao ctl status`, New Run / Resume / Template Launch forms, run page (via E-aYG5vn limits panel).
- [ ] All task TASK.md/STATUS.md + this EPIC/STATUS rollup consistent; learnings added only where new.
- [ ] Co-delivered epic `E-aYG5vn` checklist complete (shared foundation).
- [ ] PR from the branch to `main` opened; no `install.sh` / `ao.service` restart from runs (owner promotes).

**Unticked at T-SnXDoc (2026-11-09; superseded by the W11 note below):** full gates / NFR-1 golden / E2E scenarios — owned by T-SnXAcc, whose
`tests/test_sentinel_e2e_matrix.py` is in progress and its golden test was failing on the shared tree during the doc run; FR-23 live smoke and
the **default flip** — no evidence exists, so the flip was deliberately not made; settings-surfaces item — `ao config show` was never built
(HLD §21) and run-page limits panel is unverified; ticket-consistency / co-delivered epic / PR items — open until T-SnXAcc closes.

**Unticked at W11 (2026-11-09, w11-02), with reasons — supersedes the T-SnXDoc note.** E2E scenario boxes and the NFR-1 golden were ticked in W11 on
the evidence of `T-SnXAcc*/MATRIX.md` and a full `pytest -q` run. Still open: **gates box** — full pytest is 11010 passed / 2 failed
(`tests/bench/test_dev_core_suite.py` + `test_dev_medium_suite.py::test_fake_subject_full_suite_run_produces_valid_run_json_and_summary`, reproducible in
isolation, bench-harness, not sentinel code; not investigated by this docs unit), `mypy .` red by construction (1097 errors in tests/ + `output/` scratch
scripts; `mypy src` clean — owner picks the gate), ruff + UI npm test (901 pass)/build clean; **FR-23 live smoke** and **default flip** (owner budget —
`T-SnXAcc*/HANDOFF.md`); **settings visible in all surfaces** (`ao config show` / `GET /api/schema/sentinel` never built, HLD §21 — owner accepts as-built
surfaces or requests them); **ticket consistency** (T-SnXAcc In Progress / T-SnXDoc Partial until smoke); **co-delivered epic** (E-aYG5vn: 5 Done, gated
T-9ymKja Blocked); **PR to `main`** (human step after the smoke). Never from a run: `install.sh` / `ao.service` restart.

## 12. Links
- Design doc: `docs-md/sentinel-hld.md` + `docs-md/adr/ADR-0024-sentinel-overseer-lane.md` (written by `T-Sn1Adr`, Proposed — awaiting owner sign-off; HLD supersedes §4 where they differ).
- Related: `docs-md/overseer-runner-hld.md`, ADR-0003, ADR-0007, ADR-0016, ADR-0017, ADR-0021,
  `docs-md/guide-dynamic-task-injection.md`.
- Output: `output/E-Sn7tL2-sentinel-long-running-overseer/` (when evidence exists).
