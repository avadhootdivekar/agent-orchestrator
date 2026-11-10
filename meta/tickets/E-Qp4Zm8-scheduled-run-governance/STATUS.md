# STATUS

- ID: `E-Qp4Zm8-scheduled-run-governance`
- Updated At: 2026-11-10
- State: Draft (design complete, implementation not started)
- Owner: architect agent (design) -> dev-epic agent (delivery)

## This update
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
