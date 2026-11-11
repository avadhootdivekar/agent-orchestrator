# STATUS

- ID: `E-Sc9Rt4-scheduler-triggers`
- Updated At: 2026-11-10
- State: Draft
- Owner: architect agent (design) → dev-epic agent (delivery)

## This update
- **2026-11-11 (developer, unit `w20-07-a1-docs-refresh`):** `T-Dc6Zr2` docs reconciliation done (partial: epic not closed). HLD §21 deviations / §22 status / §23 constants sweep, ADR-0014 late-gate corrections, LLD, README, ROADMAP, ai-epics traceability, operator guide `docs-md/guide-scheduled-runs.md`. **Task rollup:** 6 Done (`T-Sd1Kq7`, `T-Fr2Nx8`, `T-Ev3Qm5`, `T-Lp4Wt6`, `T-Sv5Hb3`, `T-Ri7Dz2`); 7 partial/in review (`T-Cl6Jn9`, `T-Fw8Gp4`, `T-Wh9Kv1`, `T-Un0Lm6`, `T-Ap1Xs3`, `T-Te3Qw8`, `T-Dc6Zr2`); 1 not started (`T-Se4Bk5`). Suite 1233 passed / 1 failed (stale `test_hld_event_table_is_complete`); `mypy src` clean. Unfinished product items: `ao schedule add --watch`/`--from-workflow`, ruamel comment-preserving `save`, security gate. State below stays Draft until the gate runs.
- **2026-11-11 (developer, unit `w17-02-a1-until-termination`):** `T-Un0Lm6` implemented — `until_condition`/`until_satisfied` + `evaluate` call site, `ao schedule add --until-*/--max-runs`, `list` RUNS column; `tests/service/test_until.py` (40 tests, mutation-checked). AC13 and the coverage report remain open; see the task STATUS.
- **2026-11-11 (developer, unit `w16-01-a1-file-watch-rereview-fixes`):** `T-Fw8Gp4` re-review (w15-01) N1 (symlinked pattern prefix), N2 (queued file-watch fire lost on restart), N3 (future-mtime starvation) and the small Lows fixed, mutation-checked. Pending merge.
- **Design complete, nothing implemented.** Architecture package delivered:
  `docs-md/scheduler-triggers-hld.md` (HLD + LLD, mermaid diagrams for the service loop, the
  fire path, overlap/catch-up, crash recovery and the webhook path, plus schema/config deltas,
  state-file formats, CLI/API surface and the test plan);
  `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md` (ten decisions D1-D10 with an
  alternatives table); `docs-md/ai-epics/E-Sc9Rt4-scheduler-triggers.md`; this ticket folder with
  14 task tickets.
- **No `src/` file was edited** — this is a design-only deliverable (original 2026-09-06 note; see the 2026-11-10 update above).
- **Decisions still needed from the user** are enumerated in HLD §16 (D-a … D-j). Each already
  carries a recommended default that the tickets are written against, so implementation is not
  blocked; overturning any one of them is a localized change. The three worth a deliberate
  answer before `T-Wh9Kv1` starts are D-b (webhook gets its own default-off listener rather than
  riding the hub), D-e (at-most-once on a crash mid-fire ⇒ a missed run, never a duplicate), and
  D-h (`ao run --run-id` is in scope here).

## Update 2026-11-10 — T-Ri7Dz2 implemented (first code in this epic)
- `ao run --run-id` + `Orchestrator.run(run_id=)` + `RunStateStore.reserve_run_dir`/`new_run(run_id=)`
  (atomic `os.mkdir` reservation; duplicate -> exit 4 "use `ao resume`"; invalid -> exit 2); shared
  `RUN_ID_RE`/`validate_run_id` hoisted into `runstate.py` and used by `feedback.py`.
- `run_id` registry `Option` (no env/config/ui_group); `ALLOWED_OPTIONS` unchanged;
  `launch_run(run_id=)` kwarg skips discovery; `LaunchRecord.run_id_supplied` +
  `derive_launch_status(run_dir_exists=)` -> supplied id with no run dir is `failed_to_start`.
- Tests: `tests/test_run_id_flag.py` (new), run-options help pin, regenerated schema snapshot.

## Update 2026-11-10 — Opus design review applied (docs/tickets only; still no code)
- Findings F1-F24 from overseer unit `w01-06` were applied to the HLD (§3.3/§5.1.1/§5.2/§6.1/§6.3/
  §6.4/§6.6/§6.7/§6.9/§7.1/§7.2/§8/§9.2/§13.3/§16), all 14 `TASK.md`s, `EPIC.md` (disposition table
  + owner decisions) and ADR-0014 ("Late-gate corrections (2026-11-10)"). None rejected.
- **Epic task count is 14**, not 16 (the overseer charter's A1.1 wording was wrong).
- **Owner decisions recorded** (reviewer's recommended defaults): F5 `WorkspaceEntry.schedules`
  **opt-in** (default `false`); F3 `RunArgs` = curated `SCHEDULE_RUN_ARG_NAMES` (no `sentinel.llm.*`,
  no `sentinel.max_parallel_ceiling`).
- **T-Ri7Dz2 AC4 replaced:** explicit `launch_run(run_id=)` kwarg + registry `Option`, allow-list
  unchanged. HLD §3.3 allow-list drift corrected (live set 14 value + 3 bool, registry-derived).
- Implementation order now (review §4): `T-Ri7Dz2` → `T-Sd1Kq7`‖`T-Fr2Nx8` → `T-Ev3Qm5` → `T-Lp4Wt6` →
  `T-Sv5Hb3` → `T-Cl6Jn9` (A1 usable bar) → MVP-2 serial on the engine file `T-Un0Lm6` → `T-Fw8Gp4` →
  `T-Wh9Kv1` (`T-Ap1Xs3` parallel) → `T-Te3Qw8` → `T-Se4Bk5` → `T-Dc6Zr2`. Task ACs changed but task
  estimates/state (all Draft) are unchanged.
- No `src/` or test file was edited by this update.

- **2026-11-10:** `T-Sd1Kq7` implemented (Done) — see its STATUS.md; downstream tasks unblocked.

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

## Update 2026-11-11 — T-Ev3Qm5 implemented
- `service/schedule_engine.py` + 59 tests landed (see `T-Ev3Qm5-schedule-engine-core/STATUS.md`); `T-Lp4Wt6` / `T-Sv5Hb3` unblocked.
  Launcher must share the engine's state/fire/event stores and per-workspace ProcessSupervisor.

## Update 2026-11-11 — T-Lp4Wt6 implemented
- `service/schedule_launch.py` (`ScheduledLauncher`, `scheduled_run_id`, `render_instance_id`, `LaunchResolutionError`) + 33 tests landed (see `T-Lp4Wt6-scheduled-launcher/STATUS.md`). `T-Sv5Hb3` / `T-Cl6Jn9` unblocked.
  By: developer · Role: developer · Date: 2026-11-11

## Update 2026-11-11 — T-Sv5Hb3 implemented
- Registry opt-in/policy block, `ao service run` engine wiring (kill switches, outer tick backstop, shutdown order), status merge under `"schedules"`, `ao service add/set/list/status/install` deltas, webhook flags + guard (`T-Wh9Kv1` removes it). See `T-Sv5Hb3-service-integration/STATUS.md`. `T-Cl6Jn9` / `T-Wh9Kv1` unblocked.
  By: developer · Role: developer · Date: 2026-11-11

- 2026-11-11: `T-Wh9Kv1` core landed (`service/webhook.py`, cli guard replaced, 95% cov); `ao schedule add` webhook flags + daemon `--once` e2e wait on `T-Cl6Jn9`.

## Update 2026-11-11 — T-Cl6Jn9 implemented (partial)
- `ao schedule` (list/next/history/validate/add/remove/enable/disable/run-now/daemon) landed in `schedules/cli.py`; `cli.py` diff = import + `add_typer` + `ao validate` hook; 31 CliRunner e2e tests, 90% coverage; `mypy src` 0 errors. Event-kind flags and `next`'s `observe` branch wait on `T-Fw8Gp4`/`T-Wh9Kv1`. See `T-Cl6Jn9-schedule-cli/STATUS.md`.
  By: developer · Role: developer · Date: 2026-11-11

- 2026-11-11: `T-Wh9Kv1` security-review fixes landed (H1 replay bypass, M1 dropped deliveries, M2 slow-client bounds, M3 webhook host allow-list, Lows L1-L7 except L4 caching). Breaking: `X-AO-Signature` senders must sign `"{ts}.{body}"` and send `X-AO-Timestamp`. See `T-Wh9Kv1-webhook-ingress/STATUS.md`.
  By: developer · Role: developer · Date: 2026-11-11

## Update 2026-11-11 — T-Cl6Jn9 review fixes (w17-01)
- The 2 High findings (daemon / `run-now --local` bypassed the kill switch, global cap, webhook source and usage ledger; `run-now` fired non-opted-in workspaces) are fixed with mutation-checked tests; most Mediums/Lows fixed. Followups: W3 (`store.save` materializes defaults), W4 (public `ScheduleEngine.fire`), S4, and extracting one shared engine factory in `service/cli.py`. See `T-Cl6Jn9-schedule-cli/STATUS.md`.

## Update 2026-11-11 — schedule follow-ups (w18-03)
- `T-Un0Lm6` AC13 test landed (only AC14 coverage report open). `T-Cl6Jn9` W4 fixed (public `ScheduleEngine.fire`), W3 partly fixed (`save` no longer materializes defaults; comment loss remains, needs ruamel), kill-switch flake not reproducible, S4 awaits owner decision.
- 2026-11-11: `T-Wh9Kv1` re-review fixes landed (N1 scheme pin, N2 header deadline, N3 per-schedule nonces, N4-N6) + `ao schedule add --webhook` flags and daemon `--once` e2e (AC15/AC15b). Persistent nonces handed to `T-Se4Bk5`. See `T-Wh9Kv1-webhook-ingress/STATUS.md`.

## Update 2026-11-11 — test-quality review (w21-05)
- `T-Se4Bk5` mandatory Opus test-quality review: findings TQ-1..TQ-14 fixed in `b5fe677` (test-only, mutation-checked); open follow-ups FU-1..FU-11, of which 3 are High: no cron/DST engine coverage, a vacuous integration opt-in test needing a monotonic seam, and a nonce-eviction test that doesn't test eviction. See `T-Se4Bk5-security-and-review-gate/TEST-QUALITY.md`. Epic state unchanged.
  By: reviewer · Role: reviewer · Date: 2026-11-11
