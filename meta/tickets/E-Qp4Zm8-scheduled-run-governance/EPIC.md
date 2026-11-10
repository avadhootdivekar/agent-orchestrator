# EPIC: E-Qp4Zm8-scheduled-run-governance

## Metadata
- Epic ID: `E-Qp4Zm8-scheduled-run-governance`
- Title: Recurring `ao` runs with fixed prompts, per-period invocation caps and cost limits
- Owner: architect agent (design) -> dev-epic agent (delivery)
- Created: 2026-11-10
- Last Updated: 2026-11-10 (design-review edits applied, w02-02)
- Status: Draft (design complete; implementation not started; blocked on E-Sc9Rt4 for 9 of 11 tasks)
- Origin ask: user request (2026-11-10), verbatim: "I should be able to invoke the AO runs with some fixed prompts
  periodically, with some required constraints, ... invocations per specified period, like per day, per week or
  something like that. And the cost or something like that per iteration."

## Summary
- Goal: Let an operator define a **recurring job** = (fixed prompt file + template/workflow + schedule + mandatory
  limits) and have `ao` run it unattended. Example: "every weekday 09:00 run `routed-runner` with
  `prompts/daily-triage.md`, at most 3 runs/day and 10/week, <= $2 per run and <= $20/week; pause a run that reaches
  $2; explain every skip."
- Relationship to `E-Sc9Rt4-scheduler-triggers` (design-only, Draft, nothing implemented): that epic builds the
  firing mechanism. This epic is the governance layer on top. It extends E-Sc9Rt4's `ScheduleBinding`,
  `FireIntentRecord`, `evaluate()` order (one inserted step), `ScheduledLauncher`, `ao schedule` CLI and Schedules
  panel. The exact seam list is in HLD §17. It re-implements no trigger, overlap, catch-up or at-most-once logic.
- Scope In: `limits` on bindings/defaults (JSON Schema + pydantic, D-2 validator); pure calendar-window math (tz, DST,
  week start); usage ledger derived from fire records + each run's `state.json`; pre-fire gate + atomic reservation;
  fail-closed launch; engine `--cost-cap-usd` with a sticky, re-armed `builtin.cost_cap` gate; pause/abort lifecycle
  with operator resume + TTL expiry; prompt hashing/pinning; `ao schedule` limit flags + `usage` + `resume`; dashboard
  usage panel + governance event feed; tests at all three tiers; HLD, ADR-0025, docs.
- Scope Out: new trigger kinds / webhook / file-watch (E-Sc9Rt4); notifications (D-3); billing integrations;
  token-denominated caps (FR-7); workspace-wide shared spend pools (FR-9); killing in-flight tasks on cap; per-dispatch
  `--max-budget-usd` (FR-8, blocked on a result-cache-key decision, ADR-0019 D7); any change to `budget.py` /
  `BudgetCounters` semantics.

## Requirements

### MVP
- FR-1 (fixed prompt): a binding's prompt is `prompt_file` (by path). Each fire reads it once (<= 256 KiB, UTF-8),
  passes exactly those bytes to the run, and records `{path, sha256, bytes}` on the fire. An optional `prompt_sha256`
  pin skips the fire on mismatch (`prompt_changed`). A missing or invalid file skips it (`prompt_missing`).
  `ao schedule add --prompt TEXT` writes `.ao/schedule-prompts/<id>.md`. Verification: unit (`read_prompt`) +
  integration (sha in fire record) + CLI e2e. Tasks: T-tPlK1T, T-PpE5lf, T-rwj4nV.
- FR-2 (quotas): `limits.quotas: [{period: day|week|month, max_runs}]`, calendar-aligned in the binding timezone
  (D-4), `week_start` monday|sunday. A fire counts if its status is `intended|launched|orphaned`. Window membership is
  decided by `scheduled_for`. An over-quota fire is suppressed (key burned) and recorded (`quota_exhausted`, period,
  used, max, resets_at). Verification: fixed-clock boundary tests (midnight, DST incl. midnight gap, week/month
  rollover, restart mid-period). Tasks: T-lJu9oZ, T-Uz0Uol, T-PpE5lf.
- FR-3 (per-run cost cap, mid-run, D-5): `limits.cost.per_run_usd` becomes `ao run --cost-cap-usd`. The engine checks
  actual run cost (`compute_run_usage_totals`) at every dispatch and every task boundary. On cap it records
  `TrippedBreaker(id="builtin.cost_cap")`, sets resumable `failed`, and drains. The cap is persisted in `RunState`
  and **sticky on resume**, and the gate re-arms (it does not latch). The boundary check trips only while non-terminal work
  remains (a run that finishes over its cap ends `succeeded`, overshoot recorded). *Correction:* `budget.py` is token-only and is
  **not** the mechanism. Verification: engine unit + `ao run --cost-cap-usd` e2e + latch-regression test.
  Task: T-et7Edf.
- FR-4 (period cost cap): `limits.cost.periods: [{period, max_usd}]`. Before each fire, `remaining = max_usd −
  actual − in-flight reservations`. The fire is skipped (`cost_cap_reached`) if `remaining < per_run_usd`, or
  `< MIN_RESERVATION_USD` when there is no per-run cap. The reservation becomes the run's cap, so the period cap holds
  mid-run. Verification: fixed-clock + scripted-cost integration tests. Tasks: T-Uz0Uol, T-PpE5lf.
- FR-5 (observability, D-3): `ao schedule usage [--json]`, `list`/`history` columns, a dashboard usage panel (meters,
  paused runs, overshoot), and a governance event feed built from `schedule.governance.*` events. No notifications.
  Tasks: T-rwj4nV, T-8knqVe.
- FR-6 (pause lifecycle): `on_cap: pause` (default) | `abort`. A paused run is resumable only by an explicit operator
  action (`ao schedule resume`, dashboard) within `pause_ttl_seconds` (default 72 h). The top-up defaults to
  `per_run_usd`, is bounded, and is checked against the fire's original windows. The engine executes the resume as
  the single launch authority. After the TTL the run is `expired`. `abort` refuses resume. The scheduler never
  auto-resumes. Task: T-nI4DBf.
- NFR-1 (crash-safety / no double count): usage is derived from fire records + run `state.json` (+ summary meta) and
  never from a running counter. The reservation is written in the atomic `intended` record before launch. One
  `fire_key` = one contribution. The cost high-water mark is monotonic and stored outside the workspace. Restart
  yields identical totals. Concurrent fire paths are serialized by a flock. Tasks: T-Uz0Uol, T-PpE5lf, T-88wSLC.
- NFR-2 (safe by default): D-2 validator (no opt-out). A run with no reported cost is charged `max(observed,
  reservation)`. A cap that does not reach the argv fails the launch closed. A gate error suppresses the fire
  (`governance_error`). Tasks: T-tPlK1T, T-Uz0Uol, T-PpE5lf.
- NFR-3 (determinism): window math and gating are pure, and every test uses a fixed clock. Task: all.
- NFR-4 (backward compat): no `.ao/schedules.yaml` ⇒ zero governance events. `ao run` without `--cost-cap-usd` is
  byte-identical (no extra `clock()` call). New `RunState` fields are defaulted. Tasks: T-et7Edf, T-88wSLC.
- NFR-5 (bounded overhead): the gate is O(fires in the widest open window) with an mtime cache and stays inside
  E-Sc9Rt4's 0.5 s tick budget. Task: T-Uz0Uol.
- NFR-6 (quality gate): pytest delta reported; ruff + format clean; mypy no regression; >= 80 % coverage on new
  modules. Task: T-lTW88y re-verifies.

### Non-MVP
- FR-7: token-denominated caps; per-model cost attribution.
- FR-8: per-dispatch `--max-budget-usd` allowance (tightens overshoot). Needs a result-cache-key exclusion decision
  first (ADR-0019 D7).
- FR-9: workspace-wide / service-wide shared spend pools across schedules.
- FR-10: notifications on skip/cap (excluded by D-3 for MVP).

## Task List
- [x] `T-lJu9oZ-quota-window-math` — pure day/week/month windows in tz; DST, midnight gap, week start (FR-2, NFR-3) — 1.5 d
- [ ] `T-et7Edf-run-cost-cap-gate` — `ao run/resume --cost-cap-usd`, sticky `RunState.cost_cap_usd` (`Option.sticky_on_resume`), re-armed `builtin.cost_cap` gate (boundary trips only with remaining work), persisted `TaskRunState.actuals_available`, fake `cost_usd` seam, sentinel cap feed (FR-3, NFR-4) — 3 d; **after A3-D3 (T-Rk4Wn7)**
- [ ] `T-tPlK1T-governance-binding-model` — `LimitsSpec` & co., binding/defaults deltas, D-2 validator, JSON Schema, E-Sc9Rt4 fixture helper (FR-1, FR-2, FR-4, NFR-2) — 2 d
- [ ] `T-Uz0Uol-usage-ledger` — `FireGovernance` record, `charge()`, window usage, high-water mark, cost confidence, index + mtime cache (FR-2, FR-4, NFR-1, NFR-5) — 3 d
- [ ] `T-PpE5lf-pre-fire-gate-and-reservation` — gate step in `evaluate()`, `check_and_reserve` under flock, prompt snapshot, fail-closed argv check, retire hook (FR-1..FR-4, NFR-1, NFR-2) — 3 d
- [ ] `T-nI4DBf-capped-run-pause-resume` — cap_state machine, resume drop-request + engine drain, TTL sweep, abort semantics (FR-6) — 2 d
- [ ] `T-rwj4nV-schedule-cli-limits-usage` — `ao schedule add/set-limits/pin-prompt/usage/resume`, list/history columns (FR-1, FR-5, FR-6) — 2 d
- [ ] `T-8knqVe-dashboard-usage-panel` — 3 routes + `ScheduleUsage.tsx` + governance feed (FR-5, FR-6) — 3 d
- [ ] `T-88wSLC-governance-e2e-suite` — integration + `CliRunner` e2e across the HLD §18.2/§18.3 scenarios (all) — 2.5 d
- [ ] `T-lTW88y-security-and-review-gate` — early gate on HLD, then dev-security + reviewer pass + NFR-6 re-verification (NFR-1, NFR-2, NFR-6) — 1.5 d
- [ ] `T-Hq3vXd-docs-refresh` — reconcile docs-md + ADR-0025 + E-Sc9Rt4 HLD cross-links + ROADMAP with what was built (all) — 1.5 d

**Total: 25 person-days, 2 sprints at 83 % utilization — capacity math and dependency graph in `STATUS.md`.**

## Owner decisions (2026-11-10, locked — do not re-litigate)
- D-1 (was O-1): **Standalone follow-up epic**, not folded into E-Sc9Rt4. It is sequenced after E-Sc9Rt4's
  engine-core and binding-model work and builds on it.
- D-2 (was O-2): **Every recurring job MUST declare at least one limit** (quota and/or cost cap). A job with
  no limit is rejected at validation. (Whether an explicit opt-out exists is NOT approved — default: none.)
- D-3 (was O-3): On a quota/cost-cap hit at fire time, **skip the fire and record it on the dashboard**
  (plus structured log). No notifications in MVP.
- D-4 (was O-4): Quota windows are **calendar-aligned in the schedule's own timezone**.
- D-5 (was O-5): The per-run cost cap applies **mid-run**: when a run reaches its cap it is **paused or
  aborted**, not merely prevented from re-firing. The owner left pause-vs-abort to the design: architect
  must recommend one (or a configurable `on_cap: pause|abort`) with justification against existing
  run-control/budget/breaker semantics, and define how a paused-by-cap run is resumed or expires.
  - **Architect resolution (ADR-0025 D6):** configurable `on_cap: pause | abort`, default **`pause`**. Both stop
    dispatching and drain in-flight tasks (no kill) onto resumable `failed`. `pause` allows a bounded operator resume
    within `pause_ttl_seconds` (default 72 h), then `expired`. `abort` is final for governance. The cap is sticky,
    so a plain `ao resume` halts at $0.

## Review edits not applied here (owned by other epics; requested in STATUS)
- E-Sc9Rt4 T-Ev3Qm5 stores `queued_scheduled_for`; T-Sd1Kq7 `RunArgs` excludes `cost_cap_usd` (+ `minimal_limits()`); T-Ri7Dz2 AC4
  registry fix; E-Sn7tL2 T-Rk4Wn7 exempts breaker/budget/cost-cap halts from the "no failed with pending" invariant.

## Decisions needed (open; defaults already assumed by the tickets — HLD §21)
- Q-1 Co-release E-Sc9Rt4 with this epic (D-2 would invalidate limit-less bindings installed earlier) — default: yes.
- Q-2 `until.max_runs` does not satisfy D-2 — default: correct, it does not.
- Q-3 Remaining period budget < `per_run_usd` ⇒ skip (not a reduced cap) — default: skip.
- Q-4 `pause_ttl_seconds` default — 72 h.
- Q-5 Unknown cost charged `max(observed, reservation)` — default: yes.
- Q-6 Cost attributed to the fire's window (not spend time) — default: yes.
- Q-7 A natural fire never resumes a paused run — default: correct for MVP.

## Risks and Dependencies
- **Hard dependency on E-Sc9Rt4** (unimplemented): T-Sd1Kq7, T-Fr2Nx8, T-Ev3Qm5, T-Lp4Wt6, T-Ri7Dz2, T-Cl6Jn9 (Sprint 1
  of that epic) and T-Ap1Xs3 (its Sprint 2). T-lJu9oZ has no E-Sc9Rt4 dependency. **T-et7Edf has no E-Sc9Rt4 *code* dependency but
  is sequenced after A3-D3 (T-Rk4Wn7) and after the T-Ri7Dz2 registry fix** (design review, amendment 1).
- **Sprint-order amendments (Opus design review `w01-07`, applied by `w02-02`):** (1) `T-et7Edf` after A3-D3 and after E-Sc9Rt4 core,
  owning H2 (`actuals_available`) and M6 (fake `cost_usd`), so `T-Uz0Uol` depends on it; (2) `T-tPlK1T` right after `T-Sd1Kq7`, or
  `T-Sd1Kq7` ships `minimal_limits()`; (3) chain `T-Sd1Kq7→T-tPlK1T`; `T-Fr2Nx8+T-lJu9oZ+T-tPlK1T+T-et7Edf→T-Uz0Uol`;
  `T-Ev3Qm5(+queued_scheduled_for)+T-Lp4Wt6+T-Ri7Dz2+T-Uz0Uol→T-PpE5lf`; `T-Cl6Jn9+T-PpE5lf→T-nI4DBf ‖ T-rwj4nV→T-88wSLC→T-lTW88y`;
  `T-8knqVe` after `T-Ap1Xs3`; `T-Hq3vXd` last. Full text: HLD §20.
- **Mid-run cap accuracy lag (D-5 hazard):** USD cost is visible only when a task settles. Overshoot ≈ cost of in-flight
  tasks (≈ `max_parallel` × per-task cost) + summaries (≤ 2 % + final). It is measured per fire and displayed. Killing
  in-flight tasks would *under*-count cost, so it is rejected.
- **Breaker latching:** a `run_cost_usd` breaker latches, so resume would be uncapped. Hence the sticky, re-armed
  built-in gate (T-et7Edf).
- **E-Sc9Rt4 drift:** `ALLOWED_OPTIONS` is now registry-derived (`run_options.allowed_options()`). E-Sc9Rt4 T-Ri7Dz2
  AC4 ("add one entry") must register `run_id` as an `Option`. This epic's `cost_cap_usd` follows the same rule plus
  a fail-closed argv check.
- **D-2 vs E-Sc9Rt4 fixtures:** every E-Sc9Rt4 test binding needs a limit. T-tPlK1T ships the helper and updates them.
- **Blind executors** (fake, future non-Claude) report no cost. Mitigated by the conservative charge rule and a
  validate warning.
- **State tampering:** a run's agents can rewrite `state.json`. The high-water mark outside the workspace limits the
  damage (accepted residual, ADR-0016 D9).
- **Engine edits:** T-et7Edf touches `engine.py`/`models.py`/`cli.py`/`run_options.py`, confined to named sites.

## Links
- Design doc: [`docs-md/scheduled-run-governance-hld.md`](../../../docs-md/scheduled-run-governance-hld.md)
- ADR: [`docs-md/adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md`](../../../docs-md/adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md)
- Epic narrative: [`docs-md/ai-epics/E-Qp4Zm8-scheduled-run-governance.md`](../../../docs-md/ai-epics/E-Qp4Zm8-scheduled-run-governance.md)
- Builds on: [`meta/tickets/E-Sc9Rt4-scheduler-triggers/`](../E-Sc9Rt4-scheduler-triggers/EPIC.md), `docs-md/scheduler-triggers-hld.md`, ADR-0014
- Sprint plan: `STATUS.md` (this folder)
- Output artifacts (if any): `output/E-Qp4Zm8-scheduled-run-governance/` (none yet)
