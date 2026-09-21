# TASK: T-Ev3Qm5-schedule-engine-core

## Metadata
- Task ID: `T-Ev3Qm5-schedule-engine-core`
- Epic ID: `E-Sc9Rt4-scheduler-triggers`
- Owner: developer agent
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 3 days

## Requirements Mapping
- Requirement IDs: FR-2, FR-4, FR-5, FR-6, NFR-2 (see `../EPIC.md`)

## Description
The decision engine: a signal-free `ScheduleEngine` that, given a clock, decides which schedules
should fire and why. It owns *policy* — overlap, catch-up, jitter, concurrency, quarantine — and
owns none of the *mechanism*: it never spawns a process (that is `T-Lp4Wt6`), never registers a
signal handler (that is `T-Sv5Hb3`), and never touches a workspace file other than through
`T-Sd1Kq7`'s store.

Read HLD §6.1-§6.5, §6.9, §7.2 and §14, and ADR-0014 D1/D3/D4 before starting. Read the **merged
code** of `T-Sd1Kq7` and `T-Fr2Nx8` — the engine is written against their landed signatures, not
against the HLD's sketch of them.

The structural rule this task must preserve: `evaluate()` is **pure with respect to wall-clock
time**. All time enters through `now`. That is what makes the whole epic testable with fixed
clocks and what lets `T-Te3Qw8` replay any scenario deterministically.

Files you own (create/edit freely):
- `src/agent_orchestrator/service/schedule_engine.py` (new)
- `tests/service/test_schedule_engine.py` (new)

Do NOT touch: `service/fire_store.py`, `service/statefile.py`, `schedules/`, `scheduler.py`
(all read-only imports); `service/supervisor.py`, `service/cli.py`, `service/hub.py` (owned by
`T-Sv5Hb3`); `service/schedule_launch.py` (owned by `T-Lp4Wt6` — depend on the
`ScheduledLauncher` *protocol* you declare here, and let `T-Lp4Wt6` implement it); `ui/`;
`cli.py`.

## Acceptance Criteria

### Engine shape
1. `ScheduleEngine.__init__` matches HLD §7.2 exactly: `registry_path` positional, everything
   else keyword-only with real defaults (`state_dir`, `clock`, `monotonic`, `launcher`,
   `schedule_store_factory`, `process_supervisor_factory`, `run_repository_factory`,
   `eval_interval_seconds=DEFAULT_EVAL_INTERVAL_SECONDS (15.0)`,
   `max_concurrent_global=DEFAULT_MAX_CONCURRENT_GLOBAL (3)`). Every collaborator is injectable
   so unit tests need no monkeypatching and no subprocesses — the same style `Supervisor` uses.
2. `start()` / `tick(now=None)` / `shutdown()` / `status_snapshot()` are each independently
   callable, and **no `signal.signal(...)` appears anywhere in this module**. `start()` calls
   `FireIntentStore.reconcile_orphans(...)` and emits `schedule.fire_orphaned` per returned
   record; it performs zero launches.
3. `tick()` self-rate-limits to `eval_interval_seconds` using the injected `monotonic`,
   independent of how often the caller calls it (the supervisor loop runs at 2 s). It drains the
   run-now request directory first (AC12), then evaluates, then hands every `FireDecision` with
   `fire=True` to the launcher. Evaluation is bounded by `SCHEDULE_TICK_BUDGET_SECONDS (0.5)`:
   on exceeding it, emit `schedule.tick_budget_exceeded` naming the schedule it stopped at and
   return — child monitoring must never be starved.
4. `FireDecision` is the frozen dataclass in HLD §7.2. **Every** no-fire outcome carries a
   distinct `reason` string from a named module-level enum/constant set: `not_due`,
   `jitter_pending`, `never`, `disabled`, `completed`, `quarantined`, `until_satisfied`,
   `already_fired`, `missed`, `overlap_skip`, `overlap_queued`, `concurrency`, `file_invalid`.
   No bare `False` returns and no ad-hoc strings — `ao schedule list` renders these verbatim.

### Policy
5. Backlog collapse: when several instants are due, `evaluate` advances to the **most recent**
   one before any policy check, bounded by `MAX_CATCHUP_STEPS (10000)` so a pathological cron
   cannot spin. It never emits more than one `FireDecision` per schedule per tick, and never
   backfills (ADR-0014 D4).
6. Overlap, per HLD §6.3/§6.4: `skip` writes a `suppressed(reason="overlap")` record (burning
   the key) and emits `schedule.skipped_overlap`; `queue` sets `state.queued_fire_key` when
   empty and emits `schedule.skipped_overlap` with `reason="queue_full"` when not, and the
   queued fire is drained on the first tick after the active run ends; `allow` falls through to
   the concurrency caps. There is **no `replace`** — a value of `replace` is a load-time error
   in `T-Sd1Kq7`, and this module must not implement one.
7. "Active" is derived, not cached: for each `fire_key` in `state.active_fire_keys`, the record's
   `run_id` is checked via `run_repository_factory(root).load_state(run_id).status == "running"`
   with `process_supervisor_factory(root).is_running(run_id)` as the PID-level probe. Keys whose
   run reached a terminal status or whose pid is gone are retired in the same pass
   (`retire_finished`).
8. Catch-up, per HLD §6.5: lateness `<= grace_seconds` fires silently; lateness beyond it with
   `catch_up == "once"` and within `catch_up_window_seconds` emits `schedule.catch_up` and
   fires; anything else writes `suppressed(reason="missed")`, advances the anchor, and emits
   `schedule.missed` at WARNING.
9. Jitter is deterministic: `int(fire_key[:8], 16) % (jitter_seconds + 1)`, added to
   `scheduled_for` to give `effective_due`. The **fire key is derived from `scheduled_for`, not
   from the jittered time**, so a restart mid-jitter re-derives the same key. `now <
   effective_due` returns `jitter_pending`, not `not_due`.
10. Concurrency caps: per-workspace `binding.max_concurrent` and service-wide
    `max_concurrent_global`, both counted from live scheduled runs (AC7's derivation). A
    concurrency skip emits `schedule.skipped_concurrency` and **does not burn the fire key** —
    unlike overlap and missed. This asymmetry is deliberate (a transient resource shortage
    should still fire once a slot frees inside the grace window); state it in a comment.
11. Isolation and quarantine (NFR-2): every per-binding `evaluate` is wrapped so any exception
    is caught, logged as `schedule.eval_failed`, and counted via `bump_eval_error`; at
    `MAX_CONSECUTIVE_EVAL_ERRORS (5)` the schedule is set `quarantined` and
    `schedule.quarantined` emitted. A `ScheduleFileError` from one workspace's store emits
    `schedule.file_invalid` and skips **that workspace only**. Neither can propagate out of
    `tick()`.
12. Run-now requests (HLD §6.9): `drain_run_now_requests()` reads and **deletes** each
    `<state_dir>/schedules/requests/*.json`, discarding any older than
    `REQUEST_TTL_SECONDS (300)` with `schedule.request_expired`. A drained request synthesizes a
    `FireDecision` with `scheduled_for = requested_at` and runs the full policy path, except that
    `force=true` bypasses **overlap only** — never the concurrency caps, never `until`.
13. `status_snapshot()` returns exactly HLD §12's `schedules` payload shape (totals, next fire,
    live run count, last tick timing, per-workspace list). It is a pure read of
    `ScheduleStateStore` plus in-memory tick timing; it performs no evaluation and never raises.

### Tests (each independently assertable — do not fold these into one mega-test)
14. Overlap matrix: {skip, queue, allow} × {no active run, one active run, queue already full} —
    nine tests asserting the `reason`, whether a fire record was written, and whether
    `state.queued_fire_key` changed. Plus a drain test: queued fire launches on the first tick
    after the active run reaches a terminal status.
15. Catch-up boundaries: lateness at `grace_seconds - 1 / + 1` and `catch_up_window - 1 / + 1`,
    for both `once` and `skip`; and a backlog test where the clock jumps forward past N=50
    instants asserting exactly **one** `FireDecision` and N-1 `schedule.missed`-or-burned keys.
16. Jitter: same fire key ⇒ same offset in a fresh process; offset always in
    `[0, jitter_seconds]`; a tick between `scheduled_for` and `effective_due` returns
    `jitter_pending`; a restart between them still fires exactly once.
17. At-most-once at the engine level: two `evaluate` calls at the same instant produce one fire;
    a clock rewound by one hour produces zero additional fires.
18. Concurrency: caps enforced at both levels; the asymmetry in AC10 asserted explicitly (an
    overlap skip leaves `exists(fire_key) == True`, a concurrency skip leaves it `False`).
19. Isolation: a binding whose evaluation raises does not prevent the next binding in the same
    workspace from firing; five consecutive raises quarantine it and the sixth tick does not call
    `evaluate` for it; one workspace's unparseable `.ao/schedules.yaml` does not affect another's
    fires.
20. Budget: with a stubbed `monotonic` that jumps past the budget mid-loop, `tick()` returns
    early, emits `schedule.tick_budget_exceeded`, and — critically — leaves the schedules it did
    not reach in an unchanged state so the next tick picks them up.
21. `uv run pytest -q tests/service` green with the full-suite delta reported; ruff + format
    clean; mypy whole-tree count reported; coverage ≥80 % on `service/schedule_engine.py`.

## Risks
- **This module shares a process with every dashboard.** AC11 and AC20 are the blast-radius
  controls; treat a failure of either as blocking, not as a should-fix.
- The overlap/concurrency key-burning asymmetry (AC10) is genuinely subtle and will read as a
  bug to a reviewer. AC18 pins it and the comment explains it — do both.
- Deriving "active" from run state on every evaluation (AC7) means `evaluate` does file I/O, so
  it is not pure in the I/O sense — only in the wall-clock sense. Do not let that erode into
  reading `datetime.now()` anywhere inside the module.
- `queue` depth of one, first-wins, will look like dropped work under a fast schedule. The
  suppressed records are the audit trail; make sure they carry `reason="queue_full"`.

## Dependencies
- `T-Sd1Kq7` (`ScheduleStore`, `ScheduleFile.bindings()`, `IntervalScheduler`, the binding
  model) and `T-Fr2Nx8` (`FireIntentStore`, `ScheduleStateStore`, `ScheduleEventLog`) must both
  have landed. Read their merged code, not the HLD.
- Declares a `ScheduledLauncher` Protocol (`launch(decision) -> FireIntentRecord`) that
  `T-Lp4Wt6` implements; unit tests here use a recording stub, never a real launcher.
- Reads (read-only): `ui/runs.py::RunRepository`, `ui/processes.py::ProcessSupervisor.is_running`,
  `service/registry.py`, `scheduler.py`.

## Pseudocode / Algorithm
```text
See HLD §6.2 (tick) and §6.3 (evaluate) for the full, line-by-line algorithm — it is normative
for this task and must be implemented as written, including the ordering of the checks:

  status/enabled -> until -> queued-drain -> next_fire -> backlog collapse -> not_due
  -> jitter -> lateness/catch-up -> already_fired -> overlap -> concurrency -> FIRE

Two orderings are load-bearing and must not be "tidied":
  * `already_fired` is checked AFTER catch-up, so a missed instant burns its key exactly once.
  * `concurrency` is checked LAST, so a fire blocked only by a full machine is retried next tick.
```

## Schemas / Interface Notes
- Interface / API: `service.schedule_engine.{ScheduleEngine, FireDecision, ScheduledLauncher
  (Protocol), FireReason}` + the constants `DEFAULT_EVAL_INTERVAL_SECONDS`,
  `SCHEDULE_TICK_BUDGET_SECONDS`, `MAX_CONSECUTIVE_EVAL_ERRORS`, `MAX_CATCHUP_STEPS`,
  `REQUEST_TTL_SECONDS`, `DEFAULT_MAX_CONCURRENT_GLOBAL`.
- Spec / data schema: consumes `T-Sd1Kq7`'s binding model; produces `T-Fr2Nx8`'s
  `FireIntentRecord` / `ScheduleState` mutations and HLD §12's `status_snapshot()` payload.
- Triggers / events: this is the module that decides when `cron` and `interval` triggers fire and
  emits every `schedule.*` event except `webhook.*` / `watch.*`.
- Artifacts: reads `<workspace>/.ao/schedules.yaml`; reads/writes `<state_dir>/schedules/`
  (state, fires, requests, events) via `T-Fr2Nx8`'s stores only.

## Handoff Boundary
- Upstream: `T-Sd1Kq7`, `T-Fr2Nx8`; HLD §6.1-§6.5/§6.9/§7.2/§14; ADR-0014 D1/D3/D4/D5.
- Downstream: `T-Lp4Wt6` (implements the launcher Protocol), `T-Sv5Hb3` (drives `tick()` from
  the service loop and merges `status_snapshot()` into hub status), `T-Cl6Jn9`
  (`ao schedule daemon --once` calls `tick()` directly), `T-Fw8Gp4` / `T-Wh9Kv1` (register
  additional `Scheduler` implementations in the engine's `SCHEDULERS` map), `T-Un0Lm6` (adds the
  `until` check at the documented point in `evaluate`).

## Artifacts
- Docs/comments: `meta/tickets/E-Sc9Rt4-scheduler-triggers/T-Ev3Qm5-schedule-engine-core/`
- Large outputs: N/A
