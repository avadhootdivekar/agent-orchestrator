# ADR-0024 — Sentinel: a run-long, engine-hosted overseer lane (main-thread mechanical tick + control inbox + soft-barrier checkpoints + bounded LLM generations)

- Status: **Accepted** — owner sign-off recorded 2026-11-09 (avadhoot); see
  `meta/tickets/E-Sn7tL2-sentinel-long-running-overseer/STATUS.md`.
- Date: 2026-11-09
- Deciders: `architect` (author); owner decisions D1–D4 + EPIC §10 (2026-11-09) are inputs, not re-opened here.
  Review requested: `reviewer`, `dev-critic`.
- Related: ADR-0003 (settings precedence), ADR-0004 (guardrail modes), ADR-0007 (wave/barrier scheduler),
  ADR-0016 (overseer cadence + budget governance), ADR-0017 (spawn provenance), ADR-0021 (dashboard auth),
  `docs-md/sentinel-hld.md` (the design this ADR ratifies), `docs-md/overseer-runner-hld.md` (NFR-X1),
  spike `output/E-Sn7tL2-sentinel-long-running-overseer/spike/RESULT.md` (GO-WITH-CHANGES).

## Context

Measured slot utilisation of the `overseer-runner` is 7–41 % at `max_parallel=6`; 50–80 % of a wave's span is spent
waiting for stragglers after the median unit finished (EPIC §1). The engine cannot (a) wake while all workers are busy
(DRAIN has no timeout), (b) detect or kill one hung task, (c) change a live run (`max_parallel`, powers), or (d)
reshape the graph other than while settling an emitter. A hung `claude` process runs to its 1800 s timeout and, on
timeout, orphans its grandchildren (spike (b)); `ao run` has no signal handling at all and leaves `state=running` plus
orphan children on SIGTERM (spike (d)). The owner wants a long-running overseer, mechanical where possible and LLM
where needed, that keeps slots busy, heals stuck work, edits the pending graph, and is fully controllable from CLI and
dashboard.

## Decision

Adopt option **F** of EPIC §5 as a single engine feature, opt-in, byte-identical when absent:

1. **D-1 Main-thread tick.** DRAIN becomes `wait(in_flight, timeout=tick_seconds, FIRST_COMPLETED)` *only when the
   sentinel is enabled*. `_sentinel_tick` runs on the engine main thread against in-memory state and an injectable
   clock, after `_reconcile_inputs`, before FILL. Single writer (NFR-4) is preserved; there is no second reader of
   `state.json`. State is saved **on mutation only**, never per loop iteration (spike (c): save = 10 ms @ 831 KB).
2. **D-2 One door: the control inbox.** All external actors (CLI `ao ctl`, dashboard API, LLM generations) write
   atomic JSON files (`ao.ctl/v1`) into `<run_dir>/control/inbox/`; the engine drains them before FILL through the
   shared mechanical checker. Invalid entries are quarantined with a rule id. The LLM host is therefore swappable.
3. **D-3 Per-task kill + new `killed` status (EPIC §10.2).** Executors take a `cancel_event`; the Claude CLI executor
   spawns with `start_new_session=True` and kills the process **group** `SIGTERM → grace (10 s) → SIGKILL`. This also
   fixes the existing timeout orphan leak. A mechanical recovery ladder (retry → escalate → fail) follows; kill
   retries do not consume `max_attempts`. Default `idle_kill_minutes = 15`, validation floor **12** (spike (b)).
4. **D-4 Soft barrier + pending-graph edits, restricted to one entry point.** Overseer checkpoints may be marked
   `barrier: soft`; graph reshape from the tick is allowed **only** via `_apply_pending_edits` (validate on a deep copy →
   commit → persist to `RunState.graph_edits` → return the `reshaped` triple), and the checker enforces invariants
   I1–I6 from the spike. Effective (declared + inferred) edges are the unit of editing, so edits trim `inputs` as well
   as `depends_on` (FR-10).
5. **D-5 Layered bandwidth (D1).** L1: checkpoint-approved backlog released into idle slots ($0). L2: an *early
   checkpoint* mechanically rewrites the pending checkpoint's predecessors to the settled subset and passes the rest as
   `carry_over[]`. Early checkpoints count toward `max_waves` (EPIC §10.3).
6. **D-6 LLM generations off-thread, bounded, file-only.** `sentinel-gNN` runs in a dedicated 1-worker lane outside
   `max_parallel`, on a fixed pack, writes only files (inbox proposals, report, handoff, memory-append), rotates by
   TTL / triggers / tokens, and is accounted as a task row in `run_cost_usd`. Mechanical layer is $0.
7. **D-7 Authority split.** Checkpoint wins on charter, wave contents and close-out; sentinel owns health, recovery,
   live overrides and bandwidth; sentinel changes are visible to the next checkpoint and revisitable by it. The engine
   (breakers, unit gate, hard cap) remains the only hard containment (NFR-10).
8. **D-8 Settings.** One `SentinelSpec` (pydantic) for workflow block, `.ao/config.yaml`, env, CLI and dashboard form,
   registered in the E-aYG5vn run-options registry; precedence per ADR-0003 with **live overrides on top** recorded as
   source `live-override` in `state.effective_settings`. Mechanical default flips to on in the builtin templates only
   after live-smoke evidence (EPIC §10.1); LLM stays opt-in.
9. **D-9 Separate injection cap.** Sentinel/backlog/ctl injections have their own counter and breaker
   (`sentinel_injected_task_count`), leaving the template's `injected_task_count` math unchanged.
10. **D-10 Signals.** `ao run` gains SIGTERM/SIGINT handling wired to `cancel_fn`: first signal = stop dispatching +
    kill ladder on groups; second/timeout = SIGKILL; state reconciled (never left `running`). Applies to all runs, not
    only sentinel runs (T-Sn3Tck / T-Sn4Rec).
11. **D-11 No database.** In-memory state + `state.json` + ledger + inbox files suffice for P1–P3; SQLite only as a
    rebuildable index when a revisit trigger fires (HLD §14).

## Options considered (summary; detail in EPIC §5)

| Option | Verdict |
|---|---|
| A. One long-lived `claude` session as a workflow task | Rejected: 1800 s timeout, `claude -p` exits per turn, blocks the DAG, drift/cost unbounded |
| B. Sidecar under `ao service` editing state | Rejected: second writer; remains a possible *host* later via the inbox contract |
| C. Extend `AgentMonitor` | Rejected: out-of-band, cost uncounted, no injection |
| D. Lane thread re-reading `state.json` | Rejected: races; superseded by main-thread tick |
| E. Larger waves only | Rejected: tails dominate even at 15–20 units |
| **F. Chosen** | Fixes bandwidth/health at $0 first; judgement where it pays; single writer; resumable |
| Fallback (EPIC §4.4.4: leaf-only injection, hard-barrier early ckpt) | Kept **only** as degraded mode if invariant I1 (persisted `graph_edits`) cannot land; FR-10/FR-12 must not ship without I1 |

## Reasons

- Mechanical-first keeps cost at $0 and behaviour deterministic/testable with fixed clocks; the LLM is only used for
  judgement and never touches state directly.
- Spike evidence: tick 0.35 ms and full reshape 1.9 ms @1k tasks (NFR-8 target 50 ms); additive mid-flight rebuild is
  already production-proven via `emit_tasks`; edge edits and tick-applied edits are safe given I1–I6.
- A single inbox contract gives one validation path, one audit trail and one idempotency story for four writers.

## Consequences

- **Engine:** DRAIN timeout (enabled-only); `_sentinel_tick`, `_apply_pending_edits`, `_kill_task`; `RunState` gains
  `graph_edits`, `sentinel` block, `metrics`, per-task `recovery`; new task status `killed` (touches summaries, UI
  status maps, breakers' failure counting — `killed` is not a failure until the ladder exhausts); executor ABC gains
  `cancel_event` and `activity_path`; `_inject` gains an `origin="sentinel"` variant without `parent_task_id`.
- **Cost:** `RunState.save` bounded to mutation points; LLM spend capped (default 10 % of run cap).
- **Risk accepted:** false-positive kills (mitigated by 15 min default, 12 min floor, per-task kill cap, systemic-kill
  alarm); early-checkpoint overhead (≤1 in flight, counted in `max_waves`); reshape edge cases (checker rules I1–I6,
  no edits in route cones/loop clones).
- **Compatibility:** absent/disabled ⇒ byte-identical workflow.json/state.json/run log (golden test, NFR-1).
  Exception that is intentional and independent of the sentinel: process-group spawn + signal handling (D-3, D-10)
  change kill behaviour of existing runs for the better; they are called out in the changelog and tested separately
  from the golden byte-identity set (they do not alter any written artifact when no kill occurs).
- **Follow-ons:** P4 items (FR-24..28) deferred (HLD §16).

## Owner sign-off items (resolved 2026-11-09)

- Confirm D-10 (signal handling for *all* runs, not opt-in) — it is the only behaviour change outside the opt-in.
- Confirm the `idle_kill_minutes` floor of 12 with an explicit `force` escape (spike-derived).
- Owner sign-off **recorded 2026-11-09** (avadhoot): ADR + HLD accepted; D-10 and the floor-12 `idle_kill_minutes` (with explicit `force` escape) confirmed.
