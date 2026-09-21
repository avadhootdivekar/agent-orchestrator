# TASK: T-Fr2Nx8-fire-store-and-state

## Metadata
- Task ID: `T-Fr2Nx8-fire-store-and-state`
- Epic ID: `E-Sc9Rt4-scheduler-triggers`
- Owner: developer agent
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 2 days

## Requirements Mapping
- Requirement IDs: FR-7, FR-10 (see `../EPIC.md`)

## Description
The durability layer: the fire-intent store that makes a fire **at-most-once** across crashes,
reboots and clock changes; the per-schedule runtime state; and the structured event log that
`ao schedule history` and hub status both read. This is the single most correctness-sensitive
module in the epic — everything else is policy on top of these three files.

Read HLD §5.3, §6.6 and §12, and ADR-0014 D5, before starting. Read
`service/boot_resume.py` for the idiom to mirror (atomic write-then-rename, corrupt-file warn-
and-start-clean, injectable clock, named constants not magic literals) — and note the rule its
comments enforce, which applies verbatim here: *an attempt not made must not consume a budget*.

Files you own (create/edit freely):
- `src/agent_orchestrator/service/fire_store.py` (new)
- `tests/service/test_fire_store.py` (new)

Do NOT touch: `service/boot_resume.py`, `service/registry.py`, `service/supervisor.py`,
`schedules/` (read-only imports from `T-Sd1Kq7`), `ui/`, `cli.py`.

## Acceptance Criteria

### Fire-intent store
1. `fire_key(workspace_root, schedule_id, scheduled_for) -> str` is
   `sha256(f"{root}|{schedule_id}|{scheduled_for.isoformat()}").hexdigest()`, a module-level
   pure function, with `workspace_root` normalized via the same `str(Path(x).resolve())`
   expression `service/registry.py` uses. `scheduled_for` must be timezone-aware; a naive
   datetime raises rather than silently producing a different key.
2. `FireIntentRecord` (pydantic) carries every field in HLD §5.3 with
   `status: Literal["intended","launched","suppressed","orphaned","launch_failed"]`.
3. `FireIntentStore(state_dir)` exposes `exists(fire_key) -> bool`, `get(fire_key)`,
   `write(record)`, `update(fire_key, **fields)`, `write_suppressed(fire_key, reason, ...)`,
   `list(status=None, workspace_root=None, limit=None)`, and `prune(max_age_days, max_records)`.
   Records live at `<state_dir>/schedules/fires/<workspace_slug>/<fire_key>.json`, where
   `workspace_slug` reuses `supervisor.py::_slug_for_root` (import it; do not write a second
   slugger).
4. `write()` is atomic **and durable**: tmp file → `write_text` → `flush` → `os.fsync` →
   `os.replace`. The fsync is the point of the whole design (HLD §6.6) — a record that is only
   in the page cache when the machine loses power cannot make the fire at-most-once. A test must
   assert `fsync` is actually called (monkeypatch/spy), not merely that the file exists.
5. `exists()` returns `True` for a record in **any** status. This one rule is what suppresses
   re-firing, catch-up re-firing, and clock-rewind re-firing — document it in the method
   docstring with that reasoning, because a future reader will be tempted to scope it to
   `launched`.
6. `reconcile_orphans(pid_alive, run_exists) -> list[FireIntentRecord]` implements HLD §6.6's
   recovery: for each `intended` record, promote to `launched` when its pid is alive **or** its
   run directory exists, else mark `orphaned` and return it. Both probes are injected callables
   so tests need no real processes. It **never** relaunches and never deletes.

### Schedule state
7. `ScheduleState` (pydantic) carries every field in HLD §5.3 with
   `status: Literal["active","disabled","completed","quarantined"]`.
   `ScheduleStateStore(state_dir)` keeps them in one `<state_dir>/schedules/state.json` keyed
   `f"{workspace_slug}|{schedule_id}"`, and exposes `get`, `set_next_fire`, `record_fire`,
   `record_failure`, `set_status`, `set_queued`, `clear_queued`, `advance_anchor`,
   `bump_eval_error`, `clear_eval_errors`, `retire_finished(fire_keys)` and `all()`.
8. Every mutator persists immediately via `service/statefile.py`'s
   `write_json_model_atomic` — a crash between two fires must not lose `runs_count` or
   `consecutive_failures`. A corrupt `state.json` logs a warning and starts clean rather than
   blocking the daemon, exactly as `BootResumeGuard._load` does, and the docstring must say what
   that costs (counters reset).

### Event log
9. `ScheduleEventLog(state_dir)` appends one JSON object per line to
   `<state_dir>/schedules/events.jsonl` with the common fields in HLD §12, via
   `emit(event, level, **fields)`, and simultaneously logs through a module `logging.Logger` at
   the level HLD §12's table specifies. `read(limit, schedule_id=None, workspace_root=None)`
   returns the newest-first tail.
10. The log rotates at `EVENT_LOG_MAX_BYTES = 8 * 1024 * 1024` to a single `events.jsonl.1`
    (rename + fresh file), with no external rotation dependency. `read()` transparently spans
    both files. A malformed line is skipped with a warning, never raised.
11. `emit()` never raises: an unwritable state dir degrades to log-only. A daemon must not die
    because its event log is full.

### Tests
12. At-most-once: write `intended`, then assert `exists()` is `True` for every status the record
    can subsequently reach; assert `reconcile_orphans` with `pid_alive=False, run_exists=False`
    yields exactly one `orphaned` record and performs **zero** launches (there is no launcher
    here — assert the return value and the persisted status).
13. Durability: a spy asserting `os.fsync` is called inside `write()`; a truncated `.tmp` file
    left in the directory is never returned by `list()` or `get()`.
14. Key stability: the same `(root, schedule_id, scheduled_for)` yields the same key across
    processes; a non-normalized root yields the same key as its resolved form; a naive
    `scheduled_for` raises.
15. State: `record_fire` increments `runs_count` and sets `last_run_id`/`last_fire_at`;
    `bump_eval_error` five times then a `clear_eval_errors` resets; a corrupt `state.json` is
    replaced cleanly with a logged warning.
16. Events: one test per event name in HLD §12 asserting the JSONL line's fields and the logger
    level; a rotation test crossing `EVENT_LOG_MAX_BYTES` (inject a small value) asserting
    `read()` still spans both files; a malformed-line test; an unwritable-dir test asserting
    `emit()` returns normally.
17. `uv run pytest -q tests/service/test_fire_store.py` green, full-suite delta reported, ruff +
    format clean, mypy whole-tree count reported, coverage ≥80 % on `service/fire_store.py`.

## Risks
- The fsync in AC4 is the kind of detail that gets dropped as "slow" during a later
  optimization pass. The docstring must state that removing it removes the at-most-once
  guarantee, and AC13's spy test must fail loudly if it goes.
- `exists()` returning `True` for `suppressed` records looks like a bug to a fresh reader
  (why does a *skipped* fire block a *later* attempt?). AC5's mandated docstring is the defence.
- One `state.json` for every workspace means one lock domain. The engine is single-threaded and
  single-process, so no lock is specified — if that ever stops being true, this file needs the
  `mutate()` treatment. Note it in the module docstring.

## Dependencies
- `T-Sd1Kq7` must have landed `service/statefile.py` (AC8 uses it). The two tasks may start in
  parallel; this one integrates `statefile` at the end of day 1.
- Reads (read-only): `service/supervisor.py::_slug_for_root`, `service/boot_resume.py` (idiom),
  `service/paths.py::default_state_dir`.

## Pseudocode / Algorithm
```text
FUNCTION FireIntentStore.write(record):
  path = state_dir/"schedules"/"fires"/slug(record.workspace_root)/f"{record.fire_key}.json"
  path.parent.mkdir(parents=True, exist_ok=True)
  tmp = path.with_suffix(".json.tmp")
  WITH open(tmp, "w") AS f:
      f.write(record.model_dump_json(indent=2))
      f.flush()
      os.fsync(f.fileno())        # REQUIRED: page-cache-only is not at-most-once
  os.replace(tmp, path)           # atomic

FUNCTION FireIntentStore.reconcile_orphans(pid_alive, run_exists) -> list[record]:
  orphaned = []
  FOR r IN self.list(status="intended"):
      IF r.pid IS NOT NULL AND pid_alive(r.pid):
          self.update(r.fire_key, status="launched");  CONTINUE
      IF r.run_id IS NOT NULL AND run_exists(r.workspace_root, r.run_id):
          self.update(r.fire_key, status="launched");  CONTINUE
      self.update(r.fire_key, status="orphaned")
      orphaned.append(self.get(r.fire_key))
  RETURN orphaned                  # caller emits schedule.fire_orphaned; NOBODY relaunches
```

## Schemas / Interface Notes
- Interface: `service.fire_store.{fire_key, FireIntentRecord, FireIntentStore, ScheduleState,
  ScheduleStateStore, ScheduleEventLog}` + the constants `EVENT_LOG_MAX_BYTES`,
  `FIRE_RECORD_MAX_AGE_DAYS`.
- Spec / data schema: `<state_dir>/schedules/state.json`,
  `<state_dir>/schedules/fires/<slug>/<fire_key>.json`,
  `<state_dir>/schedules/events.jsonl` — all three shapes are fixed by HLD §5.3/§12.
- Triggers / events: emits the full `schedule.*` / `webhook.*` / `watch.*` event vocabulary; it
  does not itself decide when they happen.
- Artifacts: everything under `<state_dir>/schedules/` except `requests/` and `watch/`.

## Handoff Boundary
- Upstream: HLD §5.3/§6.6/§12; ADR-0014 D5; `T-Sd1Kq7`'s `service/statefile.py`.
- Downstream: `T-Ev3Qm5` (the engine is the only writer of state and the only decider of fires),
  `T-Lp4Wt6` (two-phase launch), `T-Cl6Jn9` (`ao schedule history` reads the event log),
  `T-Sv5Hb3` (hub status reads `ScheduleStateStore.all()`).

## Artifacts
- Docs/comments: `meta/tickets/E-Sc9Rt4-scheduler-triggers/T-Fr2Nx8-fire-store-and-state/`
- Large outputs: N/A
