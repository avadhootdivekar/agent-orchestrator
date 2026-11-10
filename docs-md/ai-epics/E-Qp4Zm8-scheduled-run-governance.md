# Epic: E-Qp4Zm8-scheduled-run-governance

## Metadata
- Epic ID: `E-Qp4Zm8-scheduled-run-governance`
- Title: Recurring `ao` runs with fixed prompts, per-period invocation quotas and cost caps
- Owner: architect agent (design) → dev-epic agent (delivery)
- Created: 2026-11-10
- Last Updated: 2026-11-10
- Status: Draft — design complete, implementation not started
- Origin ask (owner, verbatim): "I should be able to invoke the AO runs with some fixed prompts periodically, with some
  required constraints, ... invocations per specified period, like per day, per week or something like that. And the
  cost or something like that per iteration."
- Mirror: [`meta/tickets/E-Qp4Zm8-scheduled-run-governance/EPIC.md`](../../meta/tickets/E-Qp4Zm8-scheduled-run-governance/EPIC.md)
  (task tracking + sprint plan); design [`scheduled-run-governance-hld.md`](../scheduled-run-governance-hld.md);
  decisions [`ADR-0025`](../adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md)

## Goal

Let an operator declare a **recurring job** — a fixed prompt file + a template/workflow + a schedule + **mandatory
limits** — and have `ao service` run it unattended, inside the limits, explaining every skip. For example: "every
weekday 09:00 run `routed-runner` with `prompts/daily-triage.md`, at most 3 runs/day and 10/week, at most $2 per run
and $20 per week, pausing a run that reaches $2."

It is the governance layer on top of [E-Sc9Rt4](E-Sc9Rt4-scheduler-triggers.md), which is also design-only. It
re-implements none of E-Sc9Rt4's triggers, overlap, catch-up or at-most-once logic. It adds one gate step to
E-Sc9Rt4's evaluator, a governance block to its fire records, and a mid-run cost cap to the engine.

## Acceptance Criteria (testable)

1. A binding without any limit (here or in `defaults.limits`) is rejected at load and by `ao schedule add` (exit 2,
   `LIMITS_REQUIRED`). (D-2)
2. Quotas `{day|week|month: N}` are enforced in the binding's timezone with calendar alignment and a configurable week
   start. Each over-quota fire is skipped and recorded with its reason, the period, used/max and the reset time.
   Tests use fixed clocks across midnight, DST (incl. a midnight DST gap), week/month rollover and a service restart.
   (D-3, D-4)
3. `limits.cost.per_run_usd` becomes the run's `--cost-cap-usd`. The run stops dispatching and drains at the first
   dispatch or boundary where actual cost ≥ cap. It is resumable, and **stays capped on a plain `ao resume`**. (D-5)
4. Period cost caps are checked against actual spend + in-flight reservations before each fire. A fire that cannot
   afford its reservation is skipped. Each reservation is the run's cap, so the period cap also holds mid-run.
5. `on_cap: pause` (default) allows a bounded operator resume within `pause_ttl_seconds`, then expires. `on_cap:
   abort` refuses resume. The scheduler never auto-resumes.
6. Every fire records the prompt's path, sha256 and size. An optional pin skips the fire on mismatch.
7. `ao schedule usage` and the dashboard usage panel show runs/spend vs limits per window, paused runs, overshoot, and
   the governance event feed.
8. Usage numbers are identical before and after a daemon restart. A crash between reserve and launch never relaunches
   and never loses a quota slot. Two concurrent fire paths never exceed a quota.

## Design Decisions (summary; full text in ADR-0025)

| # | Decision |
|---|---|
| D1 | Limits on the E-Sc9Rt4 binding; D-2 enforced by the `ScheduleFile` validator; `until.max_runs` does not count. |
| D2 | Window membership by local-date label of `scheduled_for`; keys derived, never stored. |
| D3 | Usage derived from fire records + each run's `state.json` (+ summary meta); reservation in the atomic intent write; high-water mark outside the workspace; blind cost charged conservatively. |
| D4 | One gate step between overlap and concurrency; skips burn the key; authoritative re-check under a flock in the launcher. |
| D5 | Sticky `ao run/resume --cost-cap-usd` + non-latching `builtin.cost_cap` gate at dispatch and boundary. |
| D6 | `on_cap: pause \| abort`, default `pause`; neither kills in-flight tasks; overshoot ≈ in-flight work, measured and shown. |
| D7 | Prompt by path, hashed per fire, optional pin. |

## Critical findings from the code survey (recorded so they are not rediscovered)

- `budget.py` meters tokens only. The draft's "per-run cap via `budget.py`" was not implementable as stated.
- A `run_cost_usd` breaker **latches**, so after a trip a plain `ao resume` is uncapped. ADR-0016 D2 had already
  observed this for the overseer. The new built-in gate re-arms instead.
- USD cost is only known when a task settles, so a mid-run cap cannot be exact. Killing in-flight tasks would make the
  ledger under-count, because a killed CLI never reports its cost.
- `_render_options` silently drops options missing from the run-options registry. This affects E-Sc9Rt4's
  `run_id` plan (T-Ri7Dz2 AC4) as well as this epic's `cost_cap_usd`. Hence the fail-closed argv check.
- The fake executor reports no cost, so cost-only limits would be no-ops without the conservative charge rule.
- Dashboard auth now exists (ADR-0021). The new routes inherit deny-by-default when auth is configured.

## Decomposition

| Task | Est. | Depends on (E-Sc9Rt4 in **bold**) |
|---|---|---|
| `T-lJu9oZ-quota-window-math` | 1.5 d | — |
| `T-et7Edf-run-cost-cap-gate` | 3 d | — |
| `T-tPlK1T-governance-binding-model` | 2 d | **T-Sd1Kq7** |
| `T-Uz0Uol-usage-ledger` | 3 d | **T-Fr2Nx8**, T-lJu9oZ, T-tPlK1T |
| `T-PpE5lf-pre-fire-gate-and-reservation` | 3 d | **T-Ev3Qm5**, **T-Lp4Wt6**, **T-Ri7Dz2**, T-et7Edf, T-Uz0Uol |
| `T-nI4DBf-capped-run-pause-resume` | 2 d | T-PpE5lf, **T-Cl6Jn9** |
| `T-rwj4nV-schedule-cli-limits-usage` | 2 d | **T-Cl6Jn9**, T-Uz0Uol, T-PpE5lf |
| `T-8knqVe-dashboard-usage-panel` | 3 d | **T-Ap1Xs3**, T-Uz0Uol, T-nI4DBf |
| `T-88wSLC-governance-e2e-suite` | 2.5 d | T-PpE5lf, T-nI4DBf, T-rwj4nV |
| `T-lTW88y-security-and-review-gate` | 1.5 d | T-88wSLC (+ early gate on the HLD before Sprint 1) |
| `T-Hq3vXd-docs-refresh` | 1.5 d | all |

25 person-days, 2 sprints at 83 % utilization (capacity math in `STATUS.md`).

## Evidence Log

- 2026-11-10 — architect: design package written. HLD
  (`docs-md/scheduled-run-governance-hld.md`), ADR-0025, this page, the EPIC/STATUS update and 11 task folders. No
  `src/`, `tests/`, `ui/` or `specs/` file was edited, and no commit was made. Code claims were verified against
  `budget.py`, `breakers.py`, `engine.py` (breaker call site, drain, sentinel lane cap), `models.py` (`RunState`,
  `TaskRunState`, `compute_run_usage_totals`, built-in ids), `run_options.py`, `ui/processes.py`, `control.py`,
  `sentinel_tick.py`, `sentinel_driver.py`, `summarizer.py`, `executors/claude_cli.py`, `executors/fake.py`,
  `scheduler.py`, `service/cli.py`, `ui/app.py`, and the E-Sc9Rt4 HLD/ADR/tickets.

## Risks & Blockers

- **Blocked on E-Sc9Rt4** for 9 of 11 tasks. T-lJu9oZ and T-et7Edf can start now.
- Overshoot ≈ in-flight work (D6). This is measured, displayed and documented, but it is not eliminated.
- D-2 changes every E-Sc9Rt4 fixture. A co-release is recommended (HLD §21 Q-1).

## Next actions

1. Owner: confirm or overturn HLD §21 Q-1..Q-7. Each has a default the tickets already assume.
2. Run the early gate (reviewer + dev-security on the HLD/ADR) before Sprint 1 code lands. It is step 1 of
   T-lTW88y.
3. Start T-lJu9oZ and T-et7Edf in parallel. The rest waits for E-Sc9Rt4 Sprint 1 to merge.
