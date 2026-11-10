# STATUS

- ID: `E-Qp4Zm8-scheduled-run-governance`
- Updated At: 2026-11-11
- State: In Progress — partially implemented, NOT closeable (see below)
- Owner: architect agent (design) -> dev-epic agent (delivery)

## This update
- **2026-11-11 (developer, unit `w20-08-a2-docs-adr-sync`, last work wave):** docs synced to the code (HLD §26 as built, ADR-0025 "Implementation outcome", guides). Task states: DONE/committed = `T-lJu9oZ`, `T-tPlK1T`, `T-Uz0Uol` (pending review/merge); PARTIAL = `T-PpE5lf`, `T-et7Edf`, `T-88wSLC`, `T-Hq3vXd`; code in working tree only, uncommitted/unreviewed = `T-nI4DBf` (pause/resume), `T-rwj4nV` (CLI), `T-8knqVe` (dashboard); NOT started = `T-lTW88y` (final gate). **Unfinished: pause/resume, `--cost-cap-usd` CLI wiring, period cost-cap polish, final gate.** ADR-0025 stays Proposed.
- **2026-11-11 (developer, unit `w15-04-a2-pre-fire-gate-review-fixes`):** `T-PpE5lf` review fixes H1 (blind/killed runs charged `max(observed, reserved)`), M1 (run_id on `intended`), M2 (idempotent, re-driven finalisation + start sweep), M3 (tz/DST/week-start/month fixed-clock tests) and Lows L1/L2/L3/L6 done, mutation-checked; L4 (main) and L5 left open with reasons in the task STATUS. Pending re-review/merge.
- **2026-11-11 (developer, unit `w17-03-a2-usage-ledger`):** `T-Uz0Uol` implemented (AC1-13): `service/usage_ledger.py` replaces the interim ledger behind `LedgerPort` (`FireRecordLedger` kept as alias); fire-store `update_governance`/`find_by_run_id`/`iter_schedule`. 52 new tests, 97% coverage, mutation-checked. Open: wire an event sink in engine/launcher. Pending review/merge.
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
