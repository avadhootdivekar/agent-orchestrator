# ADR-0025 — Scheduled-run governance: mandatory per-job limits, calendar-window quotas, a derived usage ledger, and a sticky re-armed mid-run cost cap that pauses by default

- Status: **Proposed — partially implemented (2026-11-11)**. D1, D2, D3, D4, D7 and the engine half of D5 are committed on `ad/enhancements-10nov`; D6 (pause/resume) and the CLI half of D5 are unfinished or uncommitted, so this ADR is **not yet Accepted**. See "Implementation outcome". Builds on ADR-0014 (now mostly shipped, also not closed).
- Date: 2026-11-10
- Deciders: Avadhoot Divekar (owner — D-1..D-5 locked before design), Claude (architect role)
- Related: **ADR-0014** (scheduler; at-most-once fires, drop-directory launch authority) · **ADR-RC-004** in
  `lld-run-control-routing-breakers.md` (all breaker actions ⇒ resumable `failed`) · **ADR-0016** D2/D9 (breaker
  latching; re-armed containment; overshoot ≈ in-flight work) · **ADR-0004** (`hard`/`recommend`) · **ADR-0007** D7
  (drain, don't kill) · **ADR-0019** D7 (argv-hashed result-cache key) · **ADR-0021** (dashboard auth) · design
  [`scheduled-run-governance-hld.md`](../scheduled-run-governance-hld.md) · epic
  [`E-Qp4Zm8`](../../meta/tickets/E-Qp4Zm8-scheduled-run-governance/EPIC.md)

---

## Context

The owner wants recurring `ao` runs with a fixed prompt, a number of invocations per day/week, and a cost per
iteration. E-Sc9Rt4 (ADR-0014) designs the firing mechanism and puts cost out of scope. Five owner decisions are locked:
standalone follow-up epic (D-1); every recurring job declares at least one limit (D-2); a cap hit at fire time skips and
records, with no notifications (D-3); windows are calendar-aligned in the schedule's timezone (D-4); the per-run cost
cap applies mid-run, pause or abort to be chosen by the architect (D-5).

Facts verified in the code that shape the decisions:

1. `budget.py` meters **tokens** only; there is no USD budget in the engine.
2. USD cost exists per task (`TaskResult.cost_usd` from the Claude CLI's `total_cost_usd`) and only becomes visible
   when a task **settles**.
3. `run_cost_usd` is a registered breaker, but breakers **latch** per id forever; `--extend-breaker` only works for ids
   declared in the workflow. After a trip, plain `ao resume` has no cost wall (ADR-0016 D2 already notes this).
4. Every breaker action lands on resumable `status="failed"`; halt drains in-flight workers instead of killing them.
5. `ui/processes.py::_render_options` silently drops any option not in the run-options registry.
6. A killed `claude -p` emits no `result` event, so its cost is never recorded.
7. `FakeExecutor` reports no cost.

---

## D1 — Limits live on the E-Sc9Rt4 binding; at least one is mandatory, enforced by the pydantic file validator

**Decision.** `ScheduleBinding`/`ScheduleDefaults` gain `limits: {quotas[], cost{per_run_usd, on_cap,
pause_ttl_seconds, periods[]}}`. A binding inherits `defaults.limits` wholesale when it has none (no deep merge). The
`ScheduleFile` validator rejects any binding whose effective limits declare nothing (D-2). `until.max_runs` does not
count (lifetime, not per-period). `run_args` may not carry `cost_cap_usd`.

**Why on the binding.** ADR-0014 D2 made `.ao/schedules.yaml` the single place that says *what fires and how*; limits
are part of "how". A second governance file would be two sources of truth for one job.

**Consequence.** D-2 makes `limits` effectively required, which changes every E-Sc9Rt4 test fixture. Handled by a
shared fixture helper in the same PR, and by recommending E-Sc9Rt4 and this epic be co-released.

## D2 — Calendar windows are derived from stored instants by local-date label

**Decision.** A fire belongs to the day/week/month window containing its `scheduled_for`, computed in the binding's
timezone by local calendar date (week start configurable, default Monday). Window keys are never stored; the ledger
stores instants and derives keys on demand.

**Why.** Label-based membership makes DST days (23 h/25 h) and a midnight DST gap exact without special cases. Deriving
keys means a timezone or week-start change re-buckets history deterministically, with no migration. Using
`scheduled_for` (not jittered or actual launch time) aligns with ADR-0014 D5's logical fire identity.

**Consequence.** E-Sc9Rt4's DST fall-back double fire lands both instants in one day window, so a day quota contains it.

## D3 — Usage is derived from fire records and each run's own `state.json`; nothing is a running counter

**Decision.** The only persisted governance state is a `governance` sub-object on each `FireIntentRecord` (reservation,
cap passed, prompt hash, cap state, resumes, a monotonic high-water mark of observed cost). Spend is read from the run's
`state.json` (`compute_run_usage_totals`) plus its summary metadata. Window totals are computed on demand. A fire counts
once (by `fire_key`). The reservation is written in the same atomic `intended` write as the fire intent, before launch.
A run with no reported cost is charged `max(observed, reservation)`.

**Why.** It follows the codebase's "derive, don't duplicate" rule (`compute_run_usage_totals`, monitor counters), so
restarts, crashes and clock rewinds reproduce identical totals. The high-water mark is held in the service state
directory, outside the workspace, so a run whose agents rewrite `state.json` cannot lower its own charge.

**Consequence.** A run's entire cost is attributed to its fire's window, even if it spends across a boundary or is
resumed later. This is accepted for explainability (one fire ⇒ one window ⇒ one reservation).

## D4 — One pre-fire gate step, inserted between overlap and concurrency; governance skips burn the key

**Decision.** E-Sc9Rt4's normative `evaluate()` order gains one step:
`... already_fired → overlap → GOVERNANCE → concurrency → FIRE`. Quota and cost skips write a `suppressed` record
(burned key) and a `schedule.governance.skipped` event. The launcher repeats the gate authoritatively under a flock
and writes the reservation in the intent record (replacing, not adding to, E-Sc9Rt4's own `intended` write). There is a second
gate call site: the `overlap: queue` drain return, which needs `queued_scheduled_for` stored by E-Sc9Rt4 (design review M2). `run-now --force` bypasses overlap only, never governance.

**Why.** After `already_fired` and overlap so the true cause is recorded; before concurrency so a quota-exhausted fire
is reported deterministically rather than flapping. Burning the key matches D-3's "skip", since quotas cannot reset
within a grace window. The flock closes the same-tick and no-daemon CLI races.

## D5 — The mid-run cap is a sticky `--cost-cap-usd` enforced by a non-latching built-in gate in the engine

**Decision.** `ao run`/`ao resume` gain `--cost-cap-usd` (a run-options registry entry; CLI-only; dashboard-forwardable).
The value is persisted in `RunState.cost_cap_usd`. On resume, an absent flag keeps the persisted cap; a given flag
replaces it. A pure helper checks `compute_run_usage_totals(state).cost_usd >= cost_cap_usd` at every dispatch and
every task boundary. A hit calls `record_trip(BUILTIN_COST_CAP="builtin.cost_cap", condition="run_cost_usd",
action="pause")`, sets `failed`, and halts through the existing drain path. The helper never reads `tripped_breakers`,
so it re-arms on every invocation. It never builds a `CircuitBreakerSpec`, so a monitor can never extend it. The
sentinel LLM lane's cost cap also takes it into account. When unset, behavior is byte-identical, with no extra
`clock()` call.

**Why not inject a `run_cost_usd` breaker.** It latches, so resume would be uncapped, and the scheduler cannot extend
it through `--extend-breaker`. **Why not `budget.py`.** It meters tokens, and prices per token vary by model and cache
tier, while the CLI already reports true USD. **Why in the engine at all.** D-5 requires mid-run enforcement, and the
engine is the only component that sees each settle.

**Early-gate corrections (Opus design review, 2026-11-10; the decision itself is unchanged).**
(1) The boundary check trips only while non-terminal work remains: a run that finishes over its cap ends `succeeded` with the
overshoot recorded, instead of being marked `failed`. (2) `TaskRunState` persists `actuals_available` and
`attempts_without_actuals`, so the ledger can tell a blind run from a $0 run (and a killed attempt from a clean one).
(3) "Sticky" is a registry attribute (`Option.sticky_on_resume`, provenance `SOURCE_STATE`); cap changes use the existing
`effective_settings_history`. (4) The FakeExecutor gets a `cost_usd` seam so CLI-level tests can script cost. (5) The value is
validated before `resolve_option`, which treats falsy values as unset. (6) The gate coexists with A3-D3's run-end invariant,
which exempts breaker/budget/cost-cap halts.

**Security and review gate (T-lTW88y, 2026-11-11).** Counts at `a3d0195`: Critical 1, High 2, Warning 3, Info 4 (+ G-1 Low,
fixed). After the follow-up unit w21-02: **C-1** resolved (`3e2870b`); **G-1** fixed (`O_NOFOLLOW`); **H-1** fixed (the retry
loop re-checks the run cap between attempts; §9.3 bound corrected); **H-2** fixed (generic dashboard resume refuses
`cost_cap_usd` for governed runs with 409, and `--cost-cap-usd` / `options.cost_cap_usd` is bounded to `MAX_COST_CAP_USD`);
**W-1** fixed (blind attempts counted per attempt via `TaskResult.blind_attempts`); **W-2** Info. Open: W-3 (state.json
high-water mark can under-report if the run rewrites it), W-4, I-1..I-4. The committed `capped_resume.py` and the
`usage` / `governance-events` routes were audited (checklist b, g; drop-request schema): three Low findings fixed (F-1
unbounded `add_usd` without any cost limit, now capped at `MAX_USD`; F-2 unbounded `request_id`/`requested_by` in a drop
request; F-3 `usage?at=9999-…` returned 500). Details: the T-lTW88y `STATUS.md`.
Re-review on the committed tip (w22-03): H-1/H-2/W-1 confirmed by mutation; **N-1** (a cancel between retry attempts
dropped the spent cost) fixed; w01-07 **M1** (retire hook now charges a partly blind run `max(observed, reserved)` like the
ledger) and **M5** (`ao resume --cost-cap-usd` warns on a schedule-governed run) completed; W-3, I-2, I-3 accepted
(HLD §13.3); I-4 closed (export artefact). Open, all Info: W-4, I-1, I-6 (blind attempts invisible to the in-task retry
check), I-7, I-8, I-9.

**Consequence.** The cap is general: it is useful without schedules. The launcher must verify that the flag reached the
argv, because `_render_options` silently drops unknown keys. It fails closed otherwise.

## D6 — `on_cap: pause | abort`, default `pause`; neither kills in-flight tasks

**Decision.** Both values use the identical engine effect: stop dispatching, drain, leave the run resumable `failed`.
`pause` lets an operator resume within `pause_ttl_seconds` (default 72 h) through `ao schedule resume` or the dashboard,
adding a bounded amount (default `per_run_usd`) checked against the fire's window. The engine performs the resume as
the single launch authority. After the TTL the run is `expired`. `abort` marks the fire final and refuses governance
resume. Neither releases the sticky cap, so a manual `ao resume` without a higher `--cost-cap-usd` halts at $0. A governed run's cap cannot
be raised through the generic dashboard resume (409; use `resume-capped`); the CLI stays the explicit escape hatch with a
WARNING. Cap-state classification looks only at a `builtin.cost_cap` trip later than the latest governed resume. The
scheduler never auto-resumes a capped run.

**Why pause is the default.** It is what breaker `action: pause` already means (ADR-RC-004), with no new status
literal. It preserves paid, unfinished work, consistent with ADR-0014 D3's rejection of `replace`. A paused run holds no
process and no reservation, so pause never costs more than abort unless a human chooses to spend.

**Why not kill in-flight tasks ("hard abort").** A killed CLI never reports its cost, so the ledger would under-count.
ADR-0007 D7's drain keeps the working tree and `state.json` consistent. The cap is only *detected* at a settle anyway,
so killing buys little.

**Consequence (stated hazard).** Existing sentinel recovery kills already lose spend, so such runs are `partial` and charged
conservatively. Overshoot ≤ the cost of tasks in flight when the cap is crossed (≈ `max_parallel` ×
per-task cost), plus summaries (≤ 2 % plus the final summary). It is measured per fire (`overshoot_usd`) and displayed.
A tight bound is `max_parallel: 1`. Per-dispatch `--max-budget-usd` would bound it further, but it is deferred because
it varies the argv that ADR-0019 D7 hashes into the result-cache key.

## D7 — Fixed prompts are by path, hashed per fire, optionally pinned

**Decision.** The prompt is the binding's `prompt_file`, read once per fire (≤ 256 KiB, UTF-8). The bytes are passed
verbatim through `launch_run(prompt=...)`, and `{path, sha256, bytes}` is recorded on the fire. An optional
`prompt_sha256` pin skips mismatching fires (`prompt_changed`). `ao schedule add --prompt TEXT` writes the text to
`.ao/schedule-prompts/<id>.md` instead of inlining it.

**Why.** "Payloads by path" is a design principle. A content hash per fire is the versioning that matters (what actually
ran), and it needs no prompt store.

---

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Separate governance file (`.ao/governance.yaml`) | Two sources of truth for one job; ADR-0014 D2 already designates the binding file. (D1) |
| Allow an explicit "unlimited" opt-out | D-2 forbids it (locked). (D1) |
| Rolling windows (last 24 h / 7 d) | D-4 locks calendar alignment; rolling also makes "resets at" unanswerable. (D2) |
| Store window keys / per-window counters | Must be migrated on tz/week-start change, can drift from fire records, and double-counts on crash/replay. (D2, D3) |
| Separate cost ledger file updated incrementally | A mutable running total double-counts on resume/replay, the bug class `RunUsageTotals` was designed to avoid. (D3) |
| Attribute cost by task `ended_at` (spend time) | Splits one fire's quota and cost across windows, lets a resumed run consume a later window it never reserved, and still lumps a multi-day task into one window. (D3) |
| Retry a cost-skipped fire within grace (like concurrency) | Makes skips time-dependent and hard to explain, for a rare small gain. (D4) |
| Inject a `run_cost_usd` `CircuitBreakerSpec` | Latches, so resume is uncapped; not extendable for undeclared ids. (D5) |
| Map USD to `budget.py` tokens | Inaccurate across models/cache tiers; the CLI already reports USD. (D5) |
| Scheduler polls `state.json` and cancels over-cap runs from outside | Sees cost no sooner than the engine. SIGTERM/SIGKILL loses in-flight cost records and dirties the tree. Adds a second enforcement authority. (D5, D6) |
| `abort` = kill in-flight tasks | Under-counts cost (killed CLI reports none) and violates ADR-0007 D7. (D6) |
| Auto-resume paused runs at the next window | Defeats the cap; spend beyond the declared limit must be a human act. (D6) |
| Per-dispatch `--max-budget-usd` in MVP | Changes the argv hashed into the result-cache key (ADR-0019 D7), so every dispatch would miss the cache. Needs a cache-key decision first. (D6) |
| Inline prompt text in `schedules.yaml` | Violates payloads-by-path. The CLI writes a file instead. (D7) |
| Notifications on cap | D-3 (locked): dashboard + log only. |

## Consequences summary

- **Gained.** Every recurring job is bounded by construction. Per-job quotas are exact across DST/timezone changes and
  restarts. Period cost caps hold mid-run because each fire's reservation becomes its run cap. A general
  `ao run --cost-cap-usd` closes ADR-0016 D2's latch gap for USD caps. Every skip and cap is explained on the CLI and
  the dashboard.
- **Given up.** Exact per-spend-time attribution, a tight in-flight cost bound, notifications, and any governance
  bypass.
- **Carried hazards (surfaced, not hidden).** Overshoot ≈ in-flight work. Blind executors are charged their
  reservation. A run's agents can tamper with their own `state.json` mid-run: the in-run gate trusts it, the ledger's
  high-water mark limits the damage. E-Sc9Rt4's `ALLOWED_OPTIONS` assumption is stale: options must be registry entries.


## Implementation outcome (2026-11-11)

Source of truth for details: `scheduled-run-governance-hld.md` §26 (as built, deviations, unfinished list).

| Decision | Outcome |
|---|---|
| D1 limits on the binding, one mandatory | **Landed** (`schedules/governance.py`, `T-tPlK1T`). |
| D2 calendar windows by local-date label | **Landed** (`schedules/windows.py`, `T-lJu9oZ`); Santiago/Beirut/DST fixtures. |
| D3 derived usage ledger | **Landed** (`service/usage_ledger.py`, `T-Uz0Uol`). Deviation: lazy per-workspace index; `schedule.governance.cost_unknown` only emitted when an event sink is injected (not wired in production yet). |
| D4 one pre-fire gate, governance skips burn the key | **Landed** (`service/governance_gate.py`, `T-PpE5lf`) with review corrections: blind/killed runs charged `max(observed, reserved)`; idempotent re-driven finalisation. |
| D5 sticky `--cost-cap-usd`, non-latching `builtin.cost_cap` | **Engine landed** (`RunState.cost_cap_usd`, dispatch + boundary gate, boundary trip only with remaining work). **CLI flag wiring is not committed**; `COST_CAP_OPTION` is not yet in `run_options.OPTIONS` (`T-et7Edf`, partial). Overshoot ≈ in-flight work stays as documented; a run that finishes everything while over its cap is `succeeded`. The `cost_cap_source` field from the design review was rejected (cannot cross the allow-listed argv). |
| D6 `on_cap: pause | abort`, default `pause` | **Model landed** (`LimitsSpec`, `pause_ttl_seconds`). The pause/resume/expiry machinery (`service/capped_resume.py`) exists in the working tree but is **uncommitted and unreviewed** (`T-nI4DBf`). |
| D7 fixed prompts by path, hashed, optionally pinned | **Landed** in the gate (`prompt_sha256`, `MAX_PROMPT_BYTES`); the `ao schedule pin-prompt` CLI is uncommitted. |

Alternatives table above is unchanged. Move to **Accepted** only when `T-nI4DBf`, `T-et7Edf` CLI wiring and `T-lTW88y`
(security/review gate, still to run) are done.
