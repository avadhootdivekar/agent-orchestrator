# STATUS

- ID: `E-Qp4Zm8-scheduled-run-governance`
- Updated At: 2026-11-11
- State: In Progress (3 of 11 tasks Done; not closeable)
- Owner: architect agent (design) -> dev-epic agent (delivery)

## Reconciliation 2026-11-11 (w24-07, developer; docs only, branch `ad/enhancements-10nov`, HEAD `1d5c6f9`)
Every task STATUS/TASK carries a dated Reconciliation block; this table is the authoritative rollup and matches `EPIC.md`'s Task List. Older bullets below that say "uncommitted", "working tree only" or "NOT started" for `T-nI4DBf`, `T-rwj4nV`, `T-8knqVe`, `T-lTW88y` are superseded: all of that code is committed (`d0378d7`, `3e2870b`, `d7cb6a7`, `4b9d6a3`...).

| Task | State | Unmet ACs / open |
|---|---|---|
| `T-lJu9oZ` window math | Done | - |
| `T-tPlK1T` binding model | Done | - |
| `T-nI4DBf` pause/resume | Done | none (limitation B-2 recorded) |
| `T-Uz0Uol` usage ledger | Partial | production event-sink wiring (`cost_unknown`); exact 50 ms perf figure |
| `T-PpE5lf` pre-fire gate | Partial | period cost-cap polish (HLD §26.3 #9); L4 main, L5; AC5a engine-tick tier |
| `T-et7Edf` run cost cap | Partial | `fake_cost_script` (cosmetic); AC10 byte check. AC17 docstring + `ao status` xN and AC12 coverage LANDED (`e2971e4`, w26-04) |
| `T-rwj4nV` schedule CLI | Implemented, awaiting review | no dedicated review of `usage_view.py`/CLI |
| `T-8knqVe` dashboard usage | Partial | **UNFINISHED:** AC5 overshoot display (dashboard shows none; CLI only); AC6/AC7 feed-filter check + vitest; live-session/e2e of Resume. LANDED (w26-03, `4f6bbc1`): `resume-capped` route, paused list + Resume, M5 409 |
| `T-88wSLC` e2e suite | Partial | AC1 mapping; AC7 pre-epic count delta; AC8 full coverage; API cases (AC6 baseline + AC9b CLI landed w22-05) |
| `T-lTW88y` security gate | Partial | AC4 NFR-6 delta vs baseline; re-run after `T-8knqVe`; ADR-0025 acceptance; Info W-4, I-1, I-6, I-7, I-9 |
| `T-Hq3vXd` docs | Partial | HLD §26 re-pass, AC3 walkthrough, cross-links |

**Rollup: 11 tasks = 3 Done, 8 not Done (7 Partial: `T-Uz0Uol`, `T-PpE5lf`, `T-et7Edf`, `T-8knqVe`, `T-88wSLC`, `T-lTW88y`, `T-Hq3vXd`; 1 Implemented-awaiting-review: `T-rwj4nV`). Epic state: In Progress, NOT closeable.** Done: `T-lJu9oZ`, `T-tPlK1T`, `T-nI4DBf`.

## This update
- **2026-11-11 (reviewer/Opus, unit `w21-06-a2-test-quality-review`): mandatory test-quality review** of the governance
  tests (`tests/schedules/test_windows.py`, `test_governance_model.py`, `test_governance_e2e.py`,
  `test_cli_governance_e2e.py`; `tests/service/test_governance_gate.py`, `test_usage_ledger.py`, `test_capped_resume.py`,
  `test_governance_security_gate_ltw88y.py`; `tests/test_engine_cost_cap.py`, `test_e2e_cli_cost_cap.py`,
  `test_run_options_cost_cap.py`; `tests/ui/test_schedule_usage_api.py`; `ui/src/test/schedule-usage.test.tsx`).
  Overall: strong. Fixed clocks throughout, real files in tmp dirs, a DST/midnight-gap/24:00/Lord-Howe/historical sweep, a
  schema-vs-pydantic parity corpus, and real flock/thread races. Baseline 415 passed; after fixes 428 passed plus 1 strict
  xfail (includes concurrent units' additions); ruff clean; vitest `schedule-usage` 3/3. Findings:
  | ID | Sev | Finding (evidence) | Disposition |
  |---|---|---|---|
  | Q-1 | Medium | Retire hook `_finalize_governance_once` sets `cost_confidence = "actual" if observed > 0` and ignores `run_confidence()`. A partly blind run (one task with actuals, one blind) is persisted `actual` at its observed $0.40, but the ledger says `partial` and charges max(obs, reserved)=$2.00. Once `state.json` is gone the window under-charges. The gate tests' `SimpleNamespace` `_state()` has no `actuals_available`, so the mock hid this (probe on a real `RunState` confirmed it). | Fixed (`884c167`, w22-03 M1; verified w24-01): hook derives confidence from `run_confidence(state)`; xfail flipped to a passing test; gate `_state()` helper now builds a real `RunState`. Mutation check: test fails with the fix removed. |
  | Q-2 | Medium | `test_m3_check_and_reserve_uses_binding_timezone_and_week_start`: the sunday and monday cases shared one store, so the sunday case's own reservation refused the monday case even if `week_start` were ignored. | Fixed (separate stores; same instant). |
  | Q-3 | Medium | Usage API (`/api/schedules/{id}/usage`) was tested only on empty windows, so the real-ledger numbers (runs/actual/reserved/confidence) were never asserted at that tier. | Fixed: `test_usage_reflects_fire_records_through_the_real_ledger`. |
  | Q-4 | Medium | `test_dispatch_gate_covers_injected_and_redispatch` asserted `len(calls) >= 2`, and its name claimed injected/redispatch coverage that it does not exercise. | Fixed (renamed; exact count 3). **Closed (w23-06, `a26ff55`):** `test_q4_*` in `tests/test_engine_cost_cap.py` drive an injected task (held at cap / gated below cap / resumed) and a failed-task redispatch through the gate. |
  | Q-5 | Low | Wall-clock asserts in `test_tick_over_200_governed_schedules_stays_in_budget` (6x budget) and the 50k-record ledger test (500 ms) can flake on a loaded host; this host runs several pytest sessions at once. | Closed (w23-06, `a26ff55`): counter gates always run; the wall-clock asserts live in `@pytest.mark.perf` tests (`test_perf_*`, also check the counter first). Opt out with `-m "not perf"`. |
  | Q-6 | Low | Parallel-drain test did not assert that the failure was the cap. Adding it showed that a run records the trip once per settled sibling (0.6, then 0.9), which is by design (HLD L2, trailer "xN"). | Fixed (order-independent assertion). |
  | Q-7 | Low | Capped resume without `per_run`: refusal asserted as `in {bad_amount, no_headroom}`, but it is deterministically `no_headroom`. | Fixed (+ `remaining_usd == 6`). |
  | Q-8 | Low | CLI e2e sticky resume did not prove "nothing dispatched". | Fixed (no new spend; two `builtin.cost_cap` trips). |
  | Q-9 | Low | 50k-record perf test asserted only time (all records `suppressed`, so an empty result passed). | Fixed (s0 records `launched`; exact count asserted). |
  | Q-10 | Info | Wrong DST comment (`test_week_across_dst_sunday_start`); stale "usage/resume not committed" docstring (`test_governance_e2e.py`). | Fixed. |
  | Q-11 | Info | Window math is not property-tested on randomised zones and instants (the fixed 20-min sweep is good). CLI-tier boundary tests cover day only (week/month rollover are service-level). `ui` usage test is mocked fetch (field names match `ui/service.py`; `ScheduleUsageDoc` typing guards drift). | CLI-tier week/month rollover done (w23-06: `test_usage_week_and_month_roll_over_at_local_midnight`). CLOSED (w24-05): seeded property test `test_random_zone_instant_window_invariants` (13 zones, 2012-2035, 400 cases x 3 periods, fixed seed); drift guard `test_usage_row_keys_match_ui_type_ssot` ties real `schedule_usage` keys to `ui/src/types.ts`. UI vitest stays mocked-fetch (no Python backend in vitest; fixture typed `ScheduleUsageDoc`, so tsc + the guard cover drift). |
  Commits: `e8b8b18`, `6f08b67`. Five of the edits (windows, governance_e2e, e2e_cli_cost_cap, engine_cost_cap, usage API)
  landed inside the concurrent T-88wSLC commit `e71d73d`, which picked them up from the shared index.
- **2026-11-11 (reviewer, unit `w22-03-a2-security-gate-rereview`):** `T-lTW88y` gate re-run on the committed tip `5f51452`: **PASS** (no Critical, High or Warning open). The task stays Partial only on the AC4 pre-epic baseline delta and ADR-0025 acceptance. H-1/H-2/W-1 are confirmed by mutation. Every finding has a verdict: C-1, G-1, H-1, H-2, W-1, F-1..F-3 and I-4 are closed; W-2, W-3, I-2 and I-3 are accepted; W-4, I-1, I-6, I-7 and I-9 stay open as Info. New fixes with mutation-checked tests: N-1 (a cancel between retry attempts dropped spent cost, `884c167`), w01-07 M1 (the retire hook now charges a partly blind run like the ledger; the strict xfail is removed, `884c167`), and w01-07 M5 (`ao resume --cost-cap-usd` warns on a governed run, `5f51452`). Full pytest at `5f51452`: 12186 passed / 4 failed. The one governance-caused failure is the OpenAPI snapshot, fixed by `0f7dac0`; the other 3 pass in isolation. Governance suites: 2297 passed. Coverage of the governance modules is 96-100 %. `test_parallel_siblings_are_drained_not_killed` is not a race (400/400 under load).
- **2026-11-11 (developer, unit `w21-02-a2-security-fixes`):** `T-lTW88y` follow-up: **H-1** (retry loop re-checks the run cap), **H-2** (governed-run dashboard resume refuses `cost_cap_usd`; cap upper-bounded) and **W-1** (blind attempts counted per attempt) fixed with regression tests; committed `capped_resume.py` and the `usage`/`governance-events` routes audited (3 Low fixed: unbounded `add_usd`, drop-request free text, `usage?at=` 500). ADR-0025 corrections synced. Reviewer re-run of the gate on the final tip still owed.
- **2026-11-11 (reviewer, unit `w20-03-a2-security-gate`):** `T-lTW88y` final gate run on HEAD `a3d0195` and rechecked on `3e2870b`: **not passed** (C-1 has since been resolved by `3e2870b`; H-1/H-2 are open). Critical 1 (C-1: `--cost-cap-usd` wiring uncommitted, so every cost-reserving fire fails closed at HEAD), High 2 (H-1 retry loop ignores the cap; H-2 dashboard resume can pass an unbounded absolute cap), Warning 3, Info 4. One fix landed (G-1: `O_NOFOLLOW` prompt open, 3 tests). Pause/resume, routes and the cli wiring are uncommitted, so their checks are deferred. HEAD full pytest: 11930 passed / 6 failed (bench/install, unrelated); mypy and ruff check clean.
- **2026-11-11 (developer, unit `w20-01-a2-limits-usage-cli`):** `T-rwj4nV` implemented (AC1-11): `ao schedule` limit/prompt flags, `set-limits`, `pin-prompt`, `usage [--json] [--at]`, `resume`, list/history/next/validate additions; `schedules/usage_view.py` is the shared builder for T-8knqVe. 26 e2e tests. Gap: `resume` needs `service/capped_resume.py` (T-nI4DBf) committed; `--local` drain path untested here.
- **2026-11-11 (developer, unit `w20-08-a2-docs-adr-sync`, last work wave):** docs synced to the code (HLD §26 as built, ADR-0025 "Implementation outcome", guides). Task states: DONE/committed = `T-lJu9oZ`, `T-tPlK1T`, `T-Uz0Uol` (pending review/merge); PARTIAL = `T-PpE5lf`, `T-et7Edf`, `T-88wSLC`, `T-Hq3vXd`; code in working tree only, uncommitted/unreviewed = `T-nI4DBf` (pause/resume), `T-rwj4nV` (CLI), `T-8knqVe` (dashboard); NOT started = `T-lTW88y` (final gate). **Unfinished: pause/resume, `--cost-cap-usd` CLI wiring, period cost-cap polish, final gate.** ADR-0025 stays Proposed.
- **2026-11-11 (developer, unit `w15-04-a2-pre-fire-gate-review-fixes`):** `T-PpE5lf` review fixes H1 (blind/killed runs charged `max(observed, reserved)`), M1 (run_id on `intended`), M2 (idempotent, re-driven finalisation + start sweep), M3 (tz/DST/week-start/month fixed-clock tests) and Lows L1/L2/L3/L6 done, mutation-checked; L4 (main) and L5 left open with reasons in the task STATUS. Pending re-review/merge.
- **2026-11-11 (developer, unit `w17-03-a2-usage-ledger`):** `T-Uz0Uol` implemented (AC1-13): `service/usage_ledger.py` replaces the interim ledger behind `LedgerPort` (`FireRecordLedger` kept as alias); fire-store `update_governance`/`find_by_run_id`/`iter_schedule`. 52 new tests, 97% coverage, mutation-checked. Open: wire an event sink in engine/launcher. Pending review/merge.
- **2026-11-11 (developer, unit `w18-01-a2-capped-run-pause-resume`):** `T-nI4DBf` implemented (resume request + engine drain as single launch authority, expiry sweep, `launch_capped_resume`; 54 service tests + 7 CLI e2e). `T-et7Edf` CLI wiring finished; its warning/history/trailer items remain. Unfinished for downstream: HTTP route + Resume button + generic-resume 409 (T-8knqVe, use `is_governed_run`). Pending review/merge.
- **2026-11-11 (developer, unit `w14-02-a2-run-cost-cap-gate`):** `T-et7Edf` partially implemented: engine gate (dispatch + boundary, H1), sticky `RunState.cost_cap_usd`, `builtin.cost_cap`, AC13 `attempts_without_actuals`, registry option, FakeExecutor `cost_usd`. Unfinished: `cli.py` run/resume wiring + CLI e2e tier + `fake_cost_script` (cli.py owned by other units this wave), AC17 docstring (follow-up T-Hq3vXd). `T-Uz0Uol` may now depend on `attempts_without_actuals`.
- **2026-11-11 (developer, unit `w11-02-a2-pre-fire-gate`):** `T-PpE5lf` partially implemented (gate, reservation under flock, prompt pin, retire hook, fail-closed argv; 746 service+schedules tests green). Unfinished: real-renderer cap forwarding (needs `T-et7Edf`), production ledger (`T-Uz0Uol`; interim `FireRecordLedger` in place). Pending review/merge.
- **2026-11-11 (developer, unit `w08-01-a2-gov-binding-model`):** `T-tPlK1T` implemented (governance limits model, D-2, schema regen, `minimal_limits()` fixtures). Pending review/merge.
- **2026-11-11 (developer, unit `w07-01-a2-quota-window-math`):** `T-lJu9oZ` implemented — `schedules/windows.py` + 70 tests, 100 % cov, ruff/mypy clean. Pending review/merge.
- **Design-review edits applied (2026-11-10, run unit `w02-02-a2-apply-design-edits`).** The Opus review
  (`w01-07`: 2 High, 6 Medium, 7 Low) is folded into the HLD (§4, §6.4, §7, §8.1, §9.2/9.3/9.5, §10.2, §16.1, §17, §18.3, §20,
  §24), ADR-0025 (D4/D5/D6 corrections) and the tickets: H1 (boundary trips only with remaining work) and H2 (persisted
  `TaskRunState.actuals_available` + `attempts_without_actuals`) are ACs of `T-et7Edf` (AC5, AC13) and `T-Uz0Uol` (AC10, AC12).
  M1-M6, L1-L7 are in the tickets named in the table below; two are **rejected with reason** (see below). D-1..D-5 untouched.
  Docs/tickets only; no code.
- **Design complete, nothing implemented.** The architecture package is delivered:
  `docs-md/scheduled-run-governance-hld.md` (HLD + LLD, with mermaid block, sequence and state diagrams, schema deltas,
  window math, the ledger, both enforcement points, the E-Sc9Rt4 seam list in §17, the test plan and open decisions in
  §21); `docs-md/adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md` (D1-D7 + alternatives table);
  `docs-md/ai-epics/E-Qp4Zm8-scheduled-run-governance.md`; this folder's EPIC update and 11 task tickets.
- D-5 resolved: `on_cap: pause | abort`, default `pause` (ADR-0025 D6).
- No `src/`, `tests/`, `ui/` or `specs/` file was edited, and no commit was made.

- **2026-11-11 (developer, unit `w28-05-a2-adr-hld-sync`, docs only, branch `ad/enhancements-10nov`):** ADR-0025, HLD §14.2/§14.3/§26 and `run-options-and-limits-guide.md` re-checked against code and synced: capped-run pause/resume (done, B-2 still unfixed), read-only usage views (`readonly_fire_store`), run-cap trailer AC17 (landed `e2971e4`), retire-hook `run_confidence` (`884c167`) and cap_state=resumed crash recovery (`defa662`), `resume-capped` route + paused-runs list (landed `4f6bbc1`; the earlier "resume-capped not landed" caveat no longer applies). Stale claims removed: `--cost-cap-usd` "not committed" (run-options guide), AC17 "owed" (HLD §26.4). **Unfinished, marked in tickets/HLD §26.4:** dashboard usage panel overshoot display + feed-filter vitest + live Resume e2e (`T-8knqVe`); production event-sink for `cost_unknown` (`T-Uz0Uol`); period-only-cap polish (`T-PpE5lf`); B-2; `fake_cost_script`; `T-rwj4nV` review; `T-lTW88y` AC4 baseline delta + re-run after `T-8knqVe`; `T-Hq3vXd` AC3 walkthrough. Not touched (other units' dirty files): `scheduler-triggers-hld.md`, `guide-scheduled-runs.md`. Rollup unchanged: 3 Done, 8 not Done.

## Sprint plan

**Capacity math** (project standard: 2-week sprints of 5-day weeks, 40 % overhead, developers with <4 years'
experience):

```
team_size                 = 2
GrossHoursPerSprint       = team_size * 10 * 8            = 160 h
NetFocusHoursPerSprint    = 160 * 0.60                    =  96 h
CommitmentHoursPerSprint  = 96 * (0.70 .. 0.85)           =  67.2 .. 81.6 h  -> commit 72 h
1 person-day              = 8 h gross * 0.60              =   4.8 net focus hours
Person-days per sprint    = 72 / 4.8                      =  15 person-days
Total epic estimate       = 25 person-days                =  1.67 sprints -> plan 2
Capacity across 2 sprints = 30 person-days -> 83 % utilization (inside the 0.70-0.85 band)
```

Two sprints rather than three. The epic inherits E-Sc9Rt4's hard mechanisms (atomic records, single launch authority,
singleton lock) and adds mostly pure logic, so the extra buffer sprint E-Sc9Rt4 needed is not justified. The Sprint 2
cut-line, if needed, is `T-8knqVe`'s visual polish only. Its usage route and event feed are D-3 and cannot be cut.

**Sprint-order amendments (design review):** `T-et7Edf` is scheduled **after A3-D3 (T-Rk4Wn7) merges** and after the E-Sc9Rt4
core (it edits `engine.py` `_settle_completed_task` and the run loop together with D3). `T-lJu9oZ` starts immediately. `T-et7Edf`
now owns H2 and M6, so `T-Uz0Uol` depends on it. `T-tPlK1T` lands right after `T-Sd1Kq7` (or `T-Sd1Kq7` ships
`minimal_limits()`). Chain: `T-Sd1Kq7→T-tPlK1T`; `T-Fr2Nx8+T-lJu9oZ+T-tPlK1T+T-et7Edf→T-Uz0Uol`;
`T-Ev3Qm5+T-Lp4Wt6+T-Ri7Dz2+T-Uz0Uol→T-PpE5lf`; `T-Cl6Jn9+T-PpE5lf→T-nI4DBf ‖ T-rwj4nV→T-88wSLC→T-lTW88y`; `T-8knqVe` after
`T-Ap1Xs3`; `T-Hq3vXd` last. The diagrams below show the *original* graph; the added edges are `T-Rk4Wn7→T-et7Edf`, `T-et7Edf→T-Uz0Uol`.

**Start gate:** Sprint 1 starts when E-Sc9Rt4 Sprint 1 (MVP: T-Sd1Kq7, T-Fr2Nx8, T-Ev3Qm5, T-Ri7Dz2, T-Lp4Wt6, T-Sv5Hb3,
T-Cl6Jn9) has merged. `T-lJu9oZ` and `T-et7Edf` have no E-Sc9Rt4 dependency and may start earlier. `T-8knqVe` also
needs E-Sc9Rt4's `T-Ap1Xs3` (its Sprint 2).

| Sprint | Days | Tasks | Outcome |
|---|---|---|---|
| **1** | 12.5 / 15 | `T-lJu9oZ` (1.5) · `T-et7Edf` (3) · `T-tPlK1T` (2) · `T-Uz0Uol` (3) · `T-PpE5lf` (3) | Governed fires: limits validated, quotas + period caps gate every fire, every run carries a sticky mid-run cap. Usable through `ao schedule daemon --once` + `history`. |
| **2** | 12.5 / 15 | `T-nI4DBf` (2) · `T-rwj4nV` (2) · `T-8knqVe` (3) · `T-88wSLC` (2.5) · `T-lTW88y` (1.5) · `T-Hq3vXd` (1.5) | Pause/resume/expiry, full CLI, dashboard usage panel + feed, e2e tier, security/review gate, docs close. |

**Dependency order** ("must have landed and been read", E-Sc9Rt4 tasks in brackets):

```
T-lJu9oZ ───────────────┐
[T-Sd1Kq7] ─> T-tPlK1T ─┼─> T-Uz0Uol ─┐
[T-Fr2Nx8] ─────────────┘             │
T-et7Edf ─────────────────────────────┼─> T-PpE5lf ─┬─> T-nI4DBf ─┬─> T-8knqVe  (+ [T-Ap1Xs3])
[T-Ev3Qm5] [T-Lp4Wt6] [T-Ri7Dz2] ─────┘             │             │
                                                    └─> T-rwj4nV ─┤ (+ [T-Cl6Jn9] for both)
                                                                  └─> T-88wSLC ─> T-lTW88y ─> T-Hq3vXd
```

Parallelism: day 1 runs `T-lJu9oZ` ∥ `T-et7Edf` (∥ `T-tPlK1T` once T-Sd1Kq7 has merged). In Sprint 2, `T-rwj4nV` ∥
`T-nI4DBf`. **No two tasks of this epic create the same new file.** Edits to shared E-Sc9Rt4 files are sequential and
named per task.

## Evidence
- `docs-md/scheduled-run-governance-hld.md`: §4 prior-art audit with file/line citations; §17 seam table; §18 test
  plan; §21 decisions.
- `docs-md/adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md`.
- `docs-md/ai-epics/E-Qp4Zm8-scheduled-run-governance.md`.
- `meta/tickets/E-Qp4Zm8-scheduled-run-governance/T-*/{TASK,STATUS}.md`: 11 tickets. IDs were checked unique across
  `meta/`, `docs-md/` and `.claude/` (case-insensitive grep, 0 prior hits each).
- Code facts verified (not assumed): `budget.py` is token-only; `run_cost_usd` breaker latches (`breakers.py`
  `evaluate_breakers`); `map_action` maps every action to resumable `failed`; halt drains (`engine.py::_drain_remaining`);
  `--extend-breaker` only matches `wf.circuit_breakers` (`cli.py:2027`); `_render_options` drops unregistered keys;
  the sentinel lane cap derives only from `run_cost_usd` breakers (`engine.py:2924`); summary cost is outside
  `RunState.tasks`; `FakeExecutor` reports no cost; `ao run` has no `--run-id` yet; dashboard auth (ADR-0021) is wired
  in `ui/app.py`.

## Review disposition (w01-07 → w02-02)

| Finding | Disposition | Where |
|---|---|---|
| H1 boundary trip only with remaining work | applied | T-et7Edf AC5; HLD §9.2; ADR D5; T-88wSLC AC9a |
| H2 persist `actuals_available` | applied (flag + counter) | T-et7Edf AC13; T-Uz0Uol AC10; HLD §9.2/§10.2 |
| M1 sentinel kills under-count | applied | T-Uz0Uol AC12; HLD §9.3; T-Hq3vXd AC5 |
| M2 queue-drain second gate site + `queued_scheduled_for` | applied here; E-Sc9Rt4 part requested | T-PpE5lf AC15; HLD §8.1/§17 |
| M3 paused classification after resume | applied | T-PpE5lf AC9/AC16; HLD §6.4 |
| M4 seam with T-Rk4Wn7 D3 | applied here; T-Rk4Wn7 note requested | T-et7Edf AC16 + sequencing; HLD §9.2 |
| M5a sticky registry attr + `SOURCE_STATE`; M5b drop `cost_cap_history` | applied | T-et7Edf AC14 |
| M5c `RunState.cost_cap_source` + generic-resume 409 | **source field rejected** (cannot be forwarded through the allow-listed argv; the fire record is the authority); 409 applied via `find_by_run_id` | T-et7Edf AC14c; T-nI4DBf AC12; T-8knqVe AC10 |
| M6 fake `cost_usd` seam | applied | T-et7Edf AC15; T-88wSLC |
| L1 raw-value validation | applied | T-et7Edf AC1/AC2 |
| L2 `record_trip` docstring, trailer ×N | applied (deferrable to T-Hq3vXd if outside `breakers.py` boundary) | T-et7Edf AC17; HLD §16.1 |
| L3 `new_cap` from engine metric | applied | T-Uz0Uol AC13; T-nI4DBf AC1; HLD §9.5 |
| L4 sentinel lane / validate warning | applied | T-rwj4nV AC11; HLD §16.1 |
| L5 label authoritative; Santiago + Beirut | applied | T-lJu9oZ AC11; HLD §7 |
| L6 resume uses `LaunchRecord` inputs | applied | T-nI4DBf AC11; HLD §9.5 |
| L7 period-only caps documented; line refs refreshed | applied (HLD §4 `3232→3248` fixed now; rest in T-Hq3vXd AC5) | HLD §4/§16.1; T-Hq3vXd AC5 |
| §17 seams: single `intended` write; `RunArgs` excludes `cost_cap_usd` | applied | T-PpE5lf AC17; T-tPlK1T AC11; HLD §17 |
| dev-security half of the early gate | **still owed** | T-lTW88y AC5 |

**Cross-epic requests (not editable from this unit; for the owners of the E-Sc9Rt4 and E-Sn7tL2 tickets):**
1. E-Sc9Rt4 `T-Ev3Qm5`: store `queued_scheduled_for` next to `queued_fire_key`.
2. E-Sc9Rt4 `T-Sd1Kq7`: `RunArgs` explicitly excludes `cost_cap_usd`; ship `tests/schedules/_fixtures.py::minimal_limits()` (or land `T-tPlK1T` right after).
3. E-Sc9Rt4 `T-Ri7Dz2` AC4: `run_id` must be a registry `Option` (already flagged); apply before `T-et7Edf`.
4. E-Sn7tL2 `T-Rk4Wn7` (A3-D3): state that breaker/budget/cost-cap halts are exempt from the "no failed with pending tasks" invariant.

## Risks / Blockers
- **Blocked** (9 of 11 tasks) on E-Sc9Rt4 implementation. Not blocked on design: each HLD §21 question has a default.
- Carried hazards: mid-run overshoot ≈ in-flight work; blind executors; `state.json` tampering; D-2 vs E-Sc9Rt4
  fixtures; E-Sc9Rt4 T-Ri7Dz2 AC4 drift (registry-derived allow-list). The last one should be fixed in E-Sc9Rt4's
  ticket by its owner. It is flagged, not edited, here.

## Next actions
1. Owner reviews HLD §21 Q-1..Q-7 (Q-1 co-release and Q-3 skip-vs-reduced-cap have the most user-visible effect).
2. Early gate (reviewer + dev-security, before any code) on HLD/ADR-0025. This is step 1 of `T-lTW88y`. Fold findings
   into the HLD and an ADR-0025 "Early-gate corrections" section.
3. Dispatch `T-lJu9oZ` and `T-et7Edf` (no upstream). Dispatch the rest as E-Sc9Rt4 Sprint 1 merges.
4. Ask the E-Sc9Rt4 owner to amend T-Ri7Dz2 AC4: `run_id` must be a run-options registry `Option`.

## Comments

By: architect
Role: architect
Date: 2026-11-10
Comment: Design package complete. HLD sections 1-25 are present. Phase-4 consultations (manager/developer/reviewer/
tester/dev-security/dev-critic) were NOT run in this design-only pass. They are scheduled as T-lTW88y step 1 (early
gate), so this epic is not marked "ready for implementation" until that gate has run and E-Sc9Rt4 Sprint 1 has
merged.

- 2026-11-11 — By: developer (unit `w22-01-a2-capped-resume-shim`) · Role: developer · Date: 2026-11-11 · Comment: T-nI4DBf B-1 (launcher shims dropped `launch_capped_resume`) fixed; B-2 (pause settled only on next due tick) documented as a known limitation in HLD §26.5 with proposed fix, semantics unchanged.

- w27-04 (A1/transient-failures, 2026-11-11): tests/service run 6x on ad/enhancements-10nov (3 sequential, 1 reverse file order, 2 concurrent): 951/951, 951/951, 961 x4 passed, 0 failed. w26-05's 4 unattributed transient failures NOT reproduced; most likely shared-tree edits by concurrent units (test count rose 951->961 mid-session as test_until.py was edited by another unit). No fix made. Followup: if seen again, capture `-rf` output + `git status` at failure time.

- 2026-11-11 — By: reviewer (Opus, unit `w29-01-a2-governance-code-review`) · Role: reviewer · Date: 2026-11-11 · Comment: governance code review at `69dd4c0` → [`REVIEW.md`](REVIEW.md) (`214fc39`). 496/496 governance-suite tests pass. Open, unfixed: **G-1 High** dashboard `resume-capped` still uses a writable fire store (= T-Se4Bk5 R-2c, `ui/service.py:1506/1517`); **G-2 Medium** a resume's `reserved_usd` is not charged until the child marks state running (`usage_ledger.py:388`, probe-confirmed); **G-3 Medium** dashboard usage/paused bodies duplicate `usage_view.build_usage_doc` (root of T-8knqVe AC5 overshoot gap); Warnings G-4..G-7, Low G-8..G-11. Gives `T-rwj4nV` its dedicated review (no blocking CLI finding). No source edits.
- 2026-11-11 — By: developer (unit `w32-04-a2-gov-g8-max-add`) · Role: developer · Date: 2026-11-11 · Comment: G-8 fixed ([`REVIEW.md`](REVIEW.md)): dashboard `paused_runs.max_add_usd` is `null` without `per_run_usd`; `PausedRuns.tsx` shows "bounded by period headroom". POST re-check unchanged. pytest tests/ui + test_capped_resume 916 passed; vitest 923 passed; ruff, mypy src, tsc clean. Static UI bundle not rebuilt (human install step).
- 2026-11-11 — By: developer (unit `w32-03-a2-gov-g6-cost-metric`) · Role: developer · Date: 2026-11-11 · Comment: **G-6 fixed** — both cost-cap gates (`cost_cap_reached`, `_retry_hits_cost_cap`) now share `engine.settled_run_cost_usd` → `models.compute_run_usage_totals` (now accepts an iterable snapshot); agreement test added; 1334 cost/usage/engine/model tests pass, mypy src clean. G-1, G-2, G-3, G-4, G-5, G-7.. unchanged by this unit.
- 2026-11-11 — By: developer (unit `w32-01-a2-gov-g4-readonly-store`) · Role: developer · Date: 2026-11-11 · Comment: fixed review **G-4**: `ReadOnlyFireStore` refuses every mutator (`write`, `write_suppressed`, `update`, `_persist`, `_delete`, `prune`, `reconcile_orphans`) with `PermissionError`; one new test; reads unaffected. [`REVIEW.md`](REVIEW.md) updated.
- 2026-11-11 — By: developer (unit `w32-02-a2-gov-g5-g7-confidence-overshoot`) · Role: developer · Date: 2026-11-11 · Comment: fixed **G-5** (quota-only partly blind run persisted `partial`) and **G-7** (overshoot on the engine metric via new `FireGovernance.engine_cost_usd`; event + `paused_row`). 1452 tests in tests/service, tests/schedules, ui usage api, engine cost cap pass; ruff, mypy src clean. Followup: `usage_ledger.Charge.overshoot_usd` still on `observed` (not touched: owned by w31-02).
- 2026-11-11 — By: reviewer (Opus, unit `w34-01-a2-gov-rereview`) · Role: reviewer · Date: 2026-11-11 · Comment: re-review of G-1..G-8 fixes at `e1b1264` → [`REVIEW.md`](REVIEW.md) "Re-review". 547/547 governance-suite tests pass; each fix's test was mutation-checked (fails with the src hunk reverted). G-1/2/3/4/6/7/8 **fixed**; G-5 **partial** — open as **G-12 (Warning)**: a killed partly blind run on a reservation-less binding is still persisted `actual` (`schedule_engine.py:1579`, probe-confirmed). New Low G-13 (paused "spent" vs engine overshoot don't reconcile), G-14 (`Charge.overshoot_usd` on `observed`), G-15 (G-1 byte-equality test vacuous), G-16 (dashboard usage wasted I/O); Info G-17. No source edits.
- 2026-11-11 — By: developer (unit `w35-01-a2-gov-g12-killed-conf`) · Role: developer · Date: 2026-11-11 · Comment: fixed review **G-12**: a killed (cancelled) partly blind run on a reservation-less (quota-only) binding now persists the conservative confidence (`partial`) instead of `actual` (`schedule_engine.py` dropped `and not killed`; reservation branch still relabels). New test fails without fix; tests/service 972 pass; ruff, mypy clean. G-5 now fully fixed. [REVIEW.md](REVIEW.md)

- 2026-11-11 — By: developer (unit `w35-02-a2-gov-low-findings`) · Role: developer · Date: 2026-11-11 · Comment: fixed review **G-13..G-17** ([`REVIEW.md`](REVIEW.md) "Fix status"): paused "spent" = engine cost (CLI + dashboard); `Charge.overshoot_usd` on the engine metric; G-1 byte-equality test made discriminating (mutation-checked); dashboard usage uses new `usage_windows` (no wasted paused/events I/O); readonly store for pure lookups. tests/schedules+service+ui+cli 2332 passed, 2 skipped; vitest 923 passed; ruff, mypy clean on touched src. UI bundle not rebuilt.

## 2026-11-11 — A2 docs sync (unit `w37-01-a2-adr-hld-sync`, developer)

HLD (`docs-md/scheduled-run-governance-hld.md`) and ADR-0025 spot-checked against committed code at `ce8f24d` (`governance_gate`, `schedule_engine`, `capped_resume`, `schedule_launch`, `usage_ledger`, `fire_store` event level map, `engine.py`, `schedules/governance.py`). Docs only; no src/tests edits.

**Checked, matches code:** gate reason names (`quota_exhausted`, `cost_cap_reached`, `prompt_missing`, `prompt_changed`, `governance_error`); `skipped`/`reserved`/`launch_unsafe`/`error`/`run_cost_capped`/`pause_expired` event names and levels; `on_cap` default `pause`, `pause_ttl_seconds` default 72 h; engine cap trips `builtin.cost_cap` with `condition="run_cost_usd"`, `action="pause"`; `aborted` fires refuse resume; D-1..D-5 as stated in ADR Context (D-2 validator message exists in `schedules/governance.py`); ADR-0025 implementation-outcome table consistent with code.

**Drift fixed (HLD §5/§13.1/§25/§26.1):** `limits_changed` was documented as emitted but is never emitted (marked not implemented); `finalize_failed` added to the event table; `resumed` fields `requested_by` → `by` + `request_id`; `resume_refused` level INFO → WARNING and fields `detail`, `by`; `resume_recovered` gets `detail`; `cost_unknown` fields corrected (`reason`, no `charged_usd`); `run_cost_capped` gains `engine_cost_usd` and overshoot is the engine metric; `resume_recovered` added to the §26.1 emitted list. ADR-0025: no edits needed.

**Not checked:** HLD sections beyond event names/§26 (window math details, §9 retry-loop bound, dashboard/API payload shapes, CLI output formats); sticky-on-resume behaviour was read from docs/registry grep only, not exercised; `ao schedule` CLI text; usage_view body fields (w35-02 area, read-only per brief); no tests run.
