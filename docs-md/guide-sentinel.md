# Guide: the Sentinel (run-long overseer lane)

> Operator guide to what is **shipped** (E-Sn7tL2, P1–P3 implemented). Design/rationale: `docs-md/sentinel-hld.md`
> (as-built sections are authoritative) and ADR-0024. Not yet shipped: the FR-23 live smoke, so the sentinel is
> **opt-in everywhere** today (see [Default enablement](#default-enablement)).

The sentinel is a lane inside the engine's main loop that runs for the whole run:

- **Mechanical lane** ($0, no LLM): a tick every `tick_seconds` (15) even while every worker is busy. It reads the control inbox,
  applies live overrides, measures slot utilisation, detects stalled tasks and kills/retries them, releases backlog units into idle
  slots and can fire an *early checkpoint*.
- **LLM lane** (opt-in, bounded spend): rotating "generations" — short `claude -p` sessions fed a deterministic pack — that may
  write notes/directives and, with `powers: act`, propose injections, graph edits and kills. Every proposal is just another inbox
  file, checked by the same mechanical checker and a power gate.

Disabled or absent `sentinel:` ⇒ behaviour, `workflow.json`, `state.json` and the run log are byte-identical to a sentinel-less
engine. A crash in either lane is contained: the mechanical tick fails safe (run continues as if disabled), an LLM-lane error pauses
only the LLM lane.

## Turning it on

Precedence: workflow `sentinel:` > CLI flag > env `AO_SENTINEL_*` > `.ao/config.yaml` `sentinel:` > built-in default; live overrides
(`ao ctl set`) sit on top for the rest of the run (source `live-override` in `state.effective_settings`).

| Surface | How |
|---|---|
| CLI (`ao run`, `ao resume`, `ao new --run`) | `--sentinel/--no-sentinel`, `--sentinel-llm/--no-sentinel-llm`, `--sentinel-powers advise\|act`, `--sentinel-budget-usd N`, `--max-parallel-ceiling N`, `--sentinel-set dotted.key=value` (repeatable, any leaf) |
| Env | `AO_SENTINEL_<DOTTED_UPPER>`, e.g. `AO_SENTINEL_ENABLED=true`, `AO_SENTINEL_RECOVERY_IDLE_KILL_MINUTES=20` (list leaves such as `recovery.escalate` are config/workflow/`--sentinel-set` only) |
| `.ao/config.yaml` | a top-level `sentinel:` block (commented sample in this repo's `.ao/config.yaml`) |
| Workflow JSON | a top-level `sentinel:` block |
| Builtin templates | `ao new <template> --param sentinel_enabled=true [--param sentinel_llm=true --param sentinel_powers=act --param sentinel_budget_pct=10]`; `routed-runner` also `--param sentinel_planner=true` |
| Dashboard | New Run / Resume / Template Launch forms have a schema-driven **Sentinel** group (`enabled`, `max_parallel_ceiling`, `llm.enabled`, `llm.powers`, `llm.budget_usd`, from `GET /api/schema/run-options`); tuning knobs stay on config/env/workflow |

A bad CLI/env value exits 2. `ao validate` checks a workflow's `sentinel:` block.

### Settings (defaults as shipped)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | master switch |
| `tick_seconds` | 15 | main-loop tick period |
| `max_parallel_ceiling` | null | upper bound for live `max_parallel` overrides (a `set` above it is quarantined) |
| `cancel_grace_seconds` | 30 | grace for `ao ctl cancel` |
| `inbox_batch_max` | 50 | entries consumed per tick |
| `max_injected_tasks` | null | cap on sentinel-origin injections; null ⇒ `max(20, ceil(3 × max_parallel × expected_hours))` |
| `recovery.enabled` | `true` | liveness/recovery lane (only active when `enabled`) |
| `recovery.idle_kill_minutes` | 15 (floor 12 unless `recovery.force`) | no growth of the attempt's `transcript.jsonl` ⇒ kill |
| `recovery.stuck_factor` / `stuck_min_minutes` | 3.0 / 20 | kill when running > factor × median of ≥3 settled same-agent tasks and ≥ min minutes |
| `recovery.cost_kill_pct` | null | kill when already-settled earlier-attempt cost ≥ pct × task cap |
| `recovery.max_recovery_kills_per_task` | 2 | beyond this a kill becomes a normal failure |
| `recovery.kill_grace_seconds` | 10 | TERM → grace → KILL of the process group |
| `recovery.systemic_kill_alarm` | 3 | this many kills in 3600 s ⇒ alarm: mechanical kills stop, LLM paused |
| `recovery.escalate` | `[{effort: high}]` | agent-field overrides applied on kill 2, 3, … |
| `recovery.include_barriers` | `false` | barrier/emitter tasks only alarm, never killed |
| `bandwidth.backlog_release` | `true` | L1: release published backlog units into idle slots |
| `bandwidth.early_checkpoint` | `true` | L2: fire a checkpoint early with carry-over |
| `bandwidth.early_ckpt_fraction` | 0.5 | settled fraction of the checkpoint's predecessors required |
| `bandwidth.early_ckpt_min_idle` / `early_ckpt_grace_seconds` | ceil(limit/2) / 120 | idle slots held this long before firing |
| `llm.enabled` / `llm.powers` | `false` / `act` | LLM lane / `advise` (note, directive, hold, cancel) or `act` (+ inject, edit_pending, kill) |
| `llm.allow_graph_edits`, `allow_kill` | `true` | per-power switches |
| `llm.max_llm_kills_per_hour`, `max_injections_per_hour`, `llm_kill_min_running_minutes` | 2, 6, 10 | LLM power limits |
| `llm.heartbeat_minutes`, `generation_ttl_minutes` | 30, 60 | generation cadence/lifetime |
| `llm.budget_usd` / `budget_pct` | null / 10 | cap on *new* generations (else pct of the lowest `run_cost_usd` breaker) |
| `llm.max_generations_per_hour`, `trigger_cooldown_minutes`, `max_triggers_per_generation`, `max_tokens_per_generation` | 3, 5, 20, 400000 | rotation limits |
| `llm.max_consecutive_rejections` | 5 | consecutive quarantined proposals ⇒ LLM auto-paused |
| `llm.planner`, `planner_idle_seconds` | `false`, 120 | routed-runner planner (needs `powers: act`) |

`idle_kill_minutes` was sized from 34 196 observed inter-output gaps (max healthy gap 600.4 s = Bash 10-minute cap; none > 15 min).

## `ao ctl` — live control

`ao ctl <status|set|pause-llm|resume-llm|note|cancel|kill|release> <run> [args] [--workspace W] [--reason R] [--wait] [--wait-timeout S] [--json]`.
`<run>` = run id or unique prefix. Each command writes one atomic `ao.ctl/v1` file into `<run>/control/inbox/`; the running engine
consumes it on its next tick. Without `--wait` the entry is only queued (a run with no live engine never consumes it).

```bash
ao ctl status 20261109-… --json            # ao.ctl.status/v1 snapshot (concurrency, utilisation, ladder, backlog, llm, inbox)
ao ctl set    <run> max_parallel=8 --wait   # live override (≤ ceiling); re-validated by the engine
ao ctl kill   <run> <task> --reason "hung on lock" [--no-retry] --wait
ao ctl release <run> [bk-id …]              # release held backlog now
ao ctl note   <run> "prioritise the auth units"
ao ctl pause-llm <run>  /  ao ctl resume-llm <run>   # resume clears the auto-pause reason too
ao ctl cancel <run>                         # graceful: drain in-flight, then stop
```

Exit codes: `0` queued/consumed · `1` bad run/IO · `2` client-side validation · `3` rejected by engine (`rule_id` printed) ·
`4` wait timeout · `5` `not_enabled_yet`. Rejected entries land in `inbox-rejected/<id>.reason.json`; consumed in `inbox-processed/`.
Overridable keys today: `max_parallel`.

## Dashboard panel

The run page has a **Sentinel** panel under *Limits & settings* (polls every 3 s; hidden on an old backend; collapsed while off):
enable state / last tick, concurrency with a live `max_parallel` form, slot-utilisation bar (busy / idle with work waiting / idle
nothing ready — cumulative slot-seconds, not a time series), running tasks with liveness age + **Kill** (reason required),
systemic-alarm banner, LLM lane (state, powers, generation, spend, pause reason, Pause/Resume), notes, recent actions, and (v2)
generation timeline with spend, proposals applied/quarantined with rule id, directives and their acks. Sentinel-origin graph nodes
get a shield badge and dashed border; their spawn edges are dashed.

API (thin over the same `control.py` writer; never mutates run state): `GET /api/runs/{id}/sentinel`, `GET …/sentinel/panel`
(`ao.sentinel.panel/v2`), `POST /api/runs/{id}/control` (202 `queued`; actor is server-set `dashboard:<user>`), `GET …/control/{entry_id}`
(verdict). Normal dashboard auth + same-origin/CSRF checks apply; a run not `running` ⇒ 409; LLM-lane kinds ⇒ 400.

## Recovery ladder (health)

Liveness = growth of the attempt's `transcript.jsonl`. Kill primitive: the Claude executor starts the child in its own session and
does `killpg` TERM → `kill_grace_seconds` → KILL (grandchildren never orphan). New task status **`killed`** (schedulable). Ladder:
kill 1 → retry with a "previous dispatch was killed" retry-context note · kill ≥ 2 → apply `recovery.escalate[k-2]` (e.g. higher turn
budget) · beyond `max_recovery_kills_per_task` (or `--no-retry`) → ordinary failure. A killed dispatch does not consume
`max_attempts`. `systemic_kill_alarm` kills in an hour trips the alarm. Non-Claude executors ignore the cancel token (best effort).
Process signals: **all** runs now handle SIGTERM/SIGINT (ADR-0024 D-10), not just sentinel ones.

## Soft barrier, pending edits, backlog, early checkpoint

- **Soft barrier** (`TaskSpec.barrier: "soft"`): an emitter that may inject while other tasks are in flight. Its manifest passes the
  shared checker (`graph_check.py`, rule ids `GE-*`); rejection fails the emitter like an `InjectionError`. Hard (default) emitters
  are untouched. See `guide-dynamic-task-injection.md`.
- **`inject` / `edit_pending`** inbox kinds (operator, tick, or LLM): add tasks; add/remove edges, cancel, skip or reprioritise
  *pending* tasks only. Persisted in `RunState.graph_edits`, replayed on resume. Never inside a route cone or loop.
- **Backlog (L1).** With the overseer-runner, a checkpoint may publish `backlog.json`; once `ckpt-check` passes the tool writes
  `control/backlog.json`. When slots are idle and nothing is ready, the tick injects entries by descending priority (id `s-NN-*`, origin
  `sentinel`), up to the idle slot count; expired (`expires_at_ckpt`) or checker-refused entries are never released. Fixture
  evidence: idle-with-work slot-seconds 3.5 → 0.01.
- **Early checkpoint (L2).** When a soft checkpoint has ≥ `early_ckpt_fraction` of predecessors settled, ≥ 1 still running, no backlog
  to release and slots stay idle for `early_ckpt_grace_seconds`, the engine drops the running stragglers from the checkpoint's deps and
  inputs (persisted) so it runs now; stragglers are *carry-over*, listed in the next digest, and are never re-scheduled. Counts toward
  `max_waves`.

## LLM generations, powers, directives

- A generation is one `claude -p` process in its own process group (its own worker, never a `max_parallel` slot) started on triggers
  (`stuck_task`, `unit_failed`, `human_note`, `heartbeat`, plus `idle_slots_no_backlog` for the planner) subject to cooldown and an
  hourly cap. Input: a deterministic ≤ 120 KiB pack (charter, digest, recent events, backlog, memory, previous handoff, contract).
  Output files only: `handoff.json` (`ao.sentinel.handoff/v1`), `report.md`, `memory-append.md`, proposals as inbox entries
  (`actor: sentinel-gNN`). Layout `<run>/sentinel/` (`memory.md` ≤ 16 KiB, hash-chained `ledger.jsonl`, per-`gNN` dirs).
- An ungraceful end (no valid handoff, non-zero exit, engine restart) is counted and the next generation restarts from digest + memory +
  last good handoff. Each generation is a `sentinel-gNN` task row, so `ao usage` and the `run_cost_usd` breaker count its cost.
  Exhausting the budget stops *new* generations; the mechanical tick continues.
- **Power gate**: `advise` ⇒ `note`, `directive`, `request_hold`, `request_cancel`; `act` adds `inject`, `edit_pending`, `kill`. `set`,
  `resume_llm`, `release_backlog`, `cancel`, `pause_llm` are operator-only. LLM kills need a ≥ 40-char rationale, a running non-barrier
  target, ≥ `llm_kill_min_running_minutes`, ≤ `max_llm_kills_per_hour`. Refusals are quarantined with `PWR-*` rule ids and count toward
  `max_consecutive_rejections`.
- **Directives** (`kind: directive`, text + severity info|warn|alarm) are stored in `control/directives.jsonl`. The overseer's next
  checkpoint digest lists unanswered ones in `digest.sentinel.directives[]`, and `ckpt-check` (rule OV-R12b) requires the verdict's
  `directive_responses[]` to answer each exactly once (`adopt|redirect|descope|reject`; reject needs ≥ 40 chars). Answers are mirrored to
  `control/directive-acks.jsonl` for the dashboard.

## Overseer-runner and routed-runner

- **overseer-runner**: `--param sentinel_enabled=true` renders the `sentinel:` block and the soft-barrier checkpoint contract
  (backlog, early checkpoints, carry-over, directives). See that template's README.
- **routed-runner**: `--param sentinel_enabled=true` gives the mechanical lane; `--param sentinel_planner=true sentinel_llm=true
  sentinel_powers=act` lets an LLM generation inject follow-up units when slots idle with nothing ready (origin `sentinel`).

## Default enablement

Decision (EPIC §10.1): after FR-23 live-smoke evidence the mechanical lane becomes on-by-default in both builtin templates; the LLM
lane always stays opt-in. **The flip has not been made**: no live-smoke evidence exists yet (T-SnXAcc). Until then pass
`--param sentinel_enabled=true`.

## Known limits

- Liveness needs an `activity_path`; executors without one only get alarms. Live attempt spend is only known when it ends, so
  `cost_kill_pct` sees settled earlier attempts only.
- Generation token cap is effective only for drivers reporting progress; TTL and `--max-budget-usd` bound the Claude CLI driver.
- Triggers `new_signal` and `stage_change` are not implemented. Edited-edge (dependency add/remove) styling in the graph is not done.
- The LLM lane is tested against a fake `claude` and scripted drivers, not a live model (T-SnXAcc).
