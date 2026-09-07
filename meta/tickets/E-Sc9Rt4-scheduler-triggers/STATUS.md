# STATUS

- ID: `E-Sc9Rt4-scheduler-triggers`
- Updated At: 2026-09-06
- State: Draft
- Owner: architect agent (design) → dev-epic agent (delivery)

## This update
- **Design complete, nothing implemented.** Architecture package delivered:
  `docs-md/scheduler-triggers-hld.md` (HLD + LLD, mermaid diagrams for the service loop, the
  fire path, overlap/catch-up, crash recovery and the webhook path, plus schema/config deltas,
  state-file formats, CLI/API surface and the test plan);
  `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md` (ten decisions D1-D10 with an
  alternatives table); `docs-md/ai-epics/E-Sc9Rt4-scheduler-triggers.md`; this ticket folder with
  14 task tickets.
- **No `src/` file was edited** and no commit was made — this is a design-only deliverable.
- **Decisions still needed from the user** are enumerated in HLD §16 (D-a … D-j). Each already
  carries a recommended default that the tickets are written against, so implementation is not
  blocked; overturning any one of them is a localized change. The three worth a deliberate
  answer before `T-Wh9Kv1` starts are D-b (webhook gets its own default-off listener rather than
  riding the hub), D-e (at-most-once on a crash mid-fire ⇒ a missed run, never a duplicate), and
  D-h (`ao run --run-id` is in scope here).

## Sprint plan

**Capacity math** (project standard: 2-week sprints of 5-day weeks, 40 % overhead, developers
with <4 years' experience):

```
team_size                 = 2
GrossHoursPerSprint       = team_size * 10 * 8            = 160 h
NetFocusHoursPerSprint    = 160 * 0.60                    =  96 h
CommitmentHoursPerSprint  = 96 * (0.70 .. 0.85)           =  67.2 .. 81.6 h  -> commit 72 h
1 person-day              = 8 h gross * 0.60              =   4.8 net focus hours
Person-days per sprint    = 72 / 4.8                      =  15 person-days
Total epic estimate       = 32 person-days                =  2.13 sprints -> plan 3
Capacity across 3 sprints = 45 person-days -> 71 % utilization (inside the 0.70-0.85 band)
```

Three sprints rather than two: the epic is 32 person-days against a 30-day two-sprint capacity
with **zero** slack, and a <4-years team on a daemon with crash-recovery semantics will generate
spillover. Sprint 3 is deliberately light (4 of 15 days) so it absorbs that spillover instead of
becoming a crunch, and so the security/review gate is never the thing that gets cut.

| Sprint | Days | Tasks | Outcome |
|---|---|---|---|
| **1** | 15 / 15 | `T-Sd1Kq7` (2) · `T-Fr2Nx8` (2) · `T-Ev3Qm5` (3) · `T-Ri7Dz2` (1) · `T-Lp4Wt6` (2) · `T-Sv5Hb3` (2) · `T-Cl6Jn9` (3) | **MVP shippable**: cron + interval schedules fire from `ao service run`, at-most-once, with the full `ao schedule` CLI and hub status. |
| **2** | 13 / 15 (+2 buffer) | `T-Fw8Gp4` (2) · `T-Wh9Kv1` (3) · `T-Un0Lm6` (2) · `T-Ap1Xs3` (3) · `T-Te3Qw8` (3) | **MVP-2**: file-watch and webhook triggers, `until` loops, the dashboard panel, and the full integration/e2e tier. |
| **3** | 4 / 15 | `T-Se4Bk5` (2) · `T-Dc6Zr2` (2) | Security + architecture gate, NFR-6 re-verification, docs/ROADMAP reconciliation, epic close. |

**Dependency order** (each arrow is "must have landed and been read, not just designed"):

```
T-Sd1Kq7 ──┬─> T-Ev3Qm5 ──┬─> T-Sv5Hb3 ──> T-Cl6Jn9 ──┬─> T-Te3Qw8 ──> T-Se4Bk5 ──> T-Dc6Zr2
           │              │                            │
T-Fr2Nx8 ──┘   T-Ri7Dz2 ──┴─> T-Lp4Wt6 ────────────────┤
                                                       ├─> T-Fw8Gp4
                                                       ├─> T-Wh9Kv1
                                                       ├─> T-Un0Lm6
                                                       └─> T-Ap1Xs3
```

`T-Sd1Kq7` and `T-Fr2Nx8` are independent of each other and can run in parallel on day 1;
`T-Ri7Dz2` is independent of everything except `T-Lp4Wt6`. `T-Fw8Gp4` / `T-Wh9Kv1` / `T-Un0Lm6` /
`T-Ap1Xs3` are mutually independent once `T-Cl6Jn9` has landed. **No two tasks create or edit the
same new file** — the ownership boundary is stated explicitly in each `TASK.md`.

## Evidence
- `docs-md/scheduler-triggers-hld.md` — HLD/LLD, §15 landscape survey (Airflow, Prefect,
  Dagster, Temporal, Argo Events/CronWorkflows, systemd timers, GitHub Actions/Step Functions)
  with the vocabulary each contributed, §16 decisions needed, §18 test plan, §19 non-goals.
- `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md` — D1-D10 + alternatives table +
  consequences summary.
- `docs-md/ai-epics/E-Sc9Rt4-scheduler-triggers.md` — epic narrative page.
- `meta/tickets/E-Sc9Rt4-scheduler-triggers/T-*/{TASK,STATUS}.md` — 14 task tickets.
- Prior-art claims in the HLD were verified against the shipped code, not assumed:
  `scheduler.py` is orphaned (no importer in `src/`); `ProcessSupervisor.launch_run` already has
  an argv allow-list (`ALLOWED_OPTIONS` / `ALLOWED_BOOL_OPTIONS`); `Supervisor._decide_and_act`
  already launches headlessly; `service/cli.py::run` owns the loop and the signal handlers while
  `Supervisor` is deliberately signal-free; `build_status_provider` is the designated status
  enrichment point; `croniter` is already a core dependency.

## Risks / Blockers
- Not blocked. The HLD §16 decisions all have recommended defaults the tickets are written
  against.
- Carried into implementation: the shared-process blast radius (ADR-0014 D1), the untrusted
  `.ao/schedules.yaml` surface (NFR-3), and the DST fall-back double-fire, which is a documented
  limitation rather than a defect — `timezone: UTC` is the mitigation and a test pins the
  behavior so a future change is deliberate.
- Coordination: the concurrent per-task-isolation epic owns `ADR-0013` and
  `docs-md/task-isolation-hld.md`. Neither was created or edited here; both are referenced by
  name only.

## Next actions
1. User reviews HLD §16 D-a … D-j and confirms or overturns the recommended defaults (the three
   flagged above are the ones with real consequences).
2. Run an early gate (`reviewer` + `architect`, in parallel, before any implementation lands) on
   the HLD/ADR/task breakdown — the pattern E-GIytcL used to catch three blocking correctness
   defects before code existed. Fold findings into the HLD, an ADR-0014 "Early-gate corrections"
   section, and the affected tasks' acceptance criteria.
3. Start Sprint 1 by dispatching `T-Sd1Kq7-schedule-binding-model` and
   `T-Fr2Nx8-fire-store-and-state` in parallel.

## Comments

By: architect
Role: architect
Date: 2026-09-06
Comment: Design package complete; sections 1-25 of the architecture deliverable are present
across the HLD, ADR-0014, this ticket folder and the ai-epics page. Not marked "ready for
implementation" until the early gate in Next action #2 has run — E-GIytcL's history shows that
gate catching blocking defects (singleton lock, idempotency re-check, orphan reclamation) that
no amount of design review by the author alone had surfaced. The analogous candidates here are
the fire-store at-most-once semantics, the tick-budget interaction with child monitoring, and
the webhook's middleware mounting.
