# TASK: T-Cl6Jn9-schedule-cli

## Metadata
- Task ID: `T-Cl6Jn9-schedule-cli`
- Epic ID: `E-Sc9Rt4-scheduler-triggers`
- Owner: developer agent
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 3 days

## Requirements Mapping
- Requirement IDs: FR-9, NFR-5 (see `../EPIC.md`)

## Description
The operator surface: an `ao schedule` Typer sub-app, registered with the same one-line
`app.add_typer(...)` pattern `ao service` uses, plus the foreground `daemon` mode that makes
local iteration and end-to-end testing possible without installing a systemd unit.

This is the task where the epic becomes usable by a human. Its two hardest requirements are not
the flags: they are (a) `run-now` behaving identically whether or not a daemon is running, and
(b) `list` telling the operator when nothing will ever fire because no daemon is up — the
silent-failure mode that would otherwise dominate this feature's support burden.

Read HLD §9 and §6.9. Read the merged `service/cli.py` for the sub-app registration pattern and
the live-daemon-probe-with-file-fallback idiom `ao service list`/`status` already use.

Files you own (create/edit freely):
- `src/agent_orchestrator/schedules/cli.py` (new — the `ao schedule` sub-app)
- `src/agent_orchestrator/cli.py` (edit: **exactly one** `app.add_typer(schedule_app,
  name="schedule")` line plus its import — confirm with `git diff` at handoff)
- `tests/schedules/test_cli_e2e.py` (new)

Do NOT touch: `service/` (read-only imports), `ui/`, `engine.py`, `models.py`, `specs/`.

## Acceptance Criteria

### Read commands
1. `ao schedule list [--workspace ROOT] [--all] [--json]` prints
   `ID / KIND / ENABLED / STATUS / NEXT FIRE / LAST FIRE / LAST RUN`, sourced from the live hub
   when reachable and from `<state_dir>/schedules/state.json` when not — the same
   probe-then-fallback shape `ao service status` already uses. `--all` covers every registered
   workspace; the default is the workspace containing the cwd.
2. **`ao schedule list` prints a clear warning to stderr when no live daemon is found** (no
   supervisor lock holder, no reachable hub), naming `ao service start` / `ao schedule daemon` as
   the fixes. NFR-5: "my schedule never fired" must not be silent.
3. `ao schedule next [-n 10] [--json]` lists upcoming fires across the selected workspaces,
   soonest first, computed from the same `Scheduler` implementations the engine uses — never a
   second cron implementation.
4. `ao schedule history [ID] [-n 50] [--json]` reads `T-Fr2Nx8`'s event log newest-first,
   filtered by schedule when an id is given. It shows **suppressed** fires too, with their
   reason — what did *not* happen is the diagnostic the operator needs.
5. `ao schedule validate [--workspace ROOT]` loads and validates `.ao/schedules.yaml`, printing
   each error with schedule id and field, exit 0/1. The same validation is invoked by
   `ao validate` (a one-line addition there is in scope) and additionally **warns**, never
   errors, when a workflow spec declares a non-manual trigger with no binding (ADR-0014 D2).

### Write commands
6. `ao schedule add ID (--cron EXPR | --interval SECONDS | --watch GLOB | --webhook)
   (--workflow PATH | --template NAME [--param k=v]... [--instance-id PAT])` plus `--tz`,
   `--prompt-file`, `--overlap`, `--catch-up`, `--catch-up-window`, `--jitter`,
   `--max-concurrent`, `--run-arg k=v` (repeatable), `--until-artifact` /
   `--until-gate --until-field [--until-equals]`, `--max-runs`, `--secret-env` / `--secret-file`,
   `--disabled`, `--from-workflow PATH`. Every write goes through `ScheduleStore.mutate` so a
   concurrent daemon read never sees a half-written file. Adding an existing id is an error
   unless `--force`.
7. `--run-arg k=v` is parsed by the **existing** `_parse_param_options` helper in `cli.py`
   (reuse it; it already handles the missing-`=` and empty-key cases) and validated against
   `RunArgs` — an unknown key is a named error listing the allowed ones, never a silent drop.
8. `--from-workflow PATH` loads the workflow spec and materializes one binding per `cron` /
   `interval` trigger it declares, deriving the id from the workflow id plus an index when `ID`
   is not given. This is the bridge that keeps the spec-level declaration and the workspace-level
   binding consistent.
9. `ao schedule remove ID`, `enable ID`, `disable ID` mutate the file under the lock. `enable`
   on a `completed` schedule also resets its `runs_count` and clears the `completed` status (the
   documented way to restart an `until` loop); `disable` records `requested_by` in a
   `schedule.disabled` event.
10. `ao schedule run-now ID [--force] [--local | --queue]`: with a live daemon, writes a request
    file into `<state_dir>/schedules/requests/` and prints that the fire will occur within one
    evaluation interval; with no daemon, performs the fire **in-process using `T-Lp4Wt6`'s
    `ScheduledLauncher`** so the two paths cannot diverge. `--local` and `--queue` force each
    branch. `--force` bypasses overlap only, never the concurrency caps, never `until`.

### Daemon mode
11. `ao schedule daemon [--workspace ROOT] [--once] [--interval SECONDS] [--state-dir PATH]`
    runs the same `ScheduleEngine` in the foreground. `--once` performs exactly one `tick()`,
    prints the resulting decisions (one line each, with the `reason`), and exits with the number
    of fires as a machine-readable count on stdout. Without `--once` it loops until SIGINT, using
    the same `stop_event.wait(...)` shape `service/cli.py` uses (not `time.sleep`, for PEP-475
    shutdown latency).
12. `--workspace ROOT` scopes the engine to a single workspace by synthesizing a one-entry
    registry in memory — it must **not** write to the real `~/.config/ao/service.yaml`.
    `--state-dir` (and `AO_SERVICE_STATE_DIR`) keep every test out of a real `$HOME`.

### Tests
13. `CliRunner` against the **root** `agent_orchestrator.cli:app` (not the sub-app), so the real
    registration is exercised — the pattern `tests/service/test_cli_e2e.py` established. Full
    lifecycle: `add` → `list` → `validate` → `disable` → `enable` → `history` → `remove`,
    asserting `.ao/schedules.yaml` content after each write and the exit code of each.
14. `run-now` with no daemon: asserts a run directory appears (fake executor) and the fire record
    links `fire_key → run_id`; `run-now` with a simulated live daemon: asserts a request file
    appears and **no** run was launched by the CLI process.
15. AC2's warning: a test asserting the stderr line is present with no daemon and absent with a
    simulated one.
16. `daemon --once` with a fixed clock and a `kind: interval` binding: exactly one fire, exit
    code 0, and a second `--once` immediately after produces zero fires (at-most-once).
17. `--from-workflow` against a spec declaring a cron trigger produces the expected binding; a
    spec declaring only `manual` produces none and says so.
18. `git diff --stat src/agent_orchestrator/cli.py` shows exactly the import + `add_typer` lines
    plus the one-line `ao validate` hook. Reported in `STATUS.md`.
19. `uv run pytest -q` green with the delta reported; ruff + format clean; mypy whole-tree count
    reported; coverage ≥80 % on `schedules/cli.py`.

## Risks
- Ten subcommands is a lot of surface for one 3-day task. If it slips, the shippable subset in
  priority order is: `list`, `add`, `enable`/`disable`, `daemon --once`, `run-now`, `history`,
  `next`, `validate`, `remove`, `--from-workflow`. Say so in `STATUS.md` rather than cutting
  tests.
- `run-now`'s two branches (AC10) are exactly where a second launch path gets accidentally
  written. Reuse `ScheduledLauncher`; a reviewer should be able to grep this module and find no
  `Popen`.
- Detecting "is a daemon live" needs care: the supervisor lock is `flock`-held, so a
  non-blocking acquire attempt that succeeds proves nothing is running (and must then release
  immediately). Prefer probing the hub first and using the lock only as a fallback.
- Touching top-level `cli.py` beyond the two lines will be rejected at review (AC18).

## Dependencies
- `T-Sd1Kq7` (store + models), `T-Fr2Nx8` (event log + state), `T-Ev3Qm5` (engine, for `daemon`),
  `T-Lp4Wt6` (launcher, for `run-now --local`), `T-Sv5Hb3` (registry policy block). Read all
  five's merged code.

## Pseudocode / Algorithm
```text
FUNCTION run_now(schedule_id, force, mode):
  root  = resolve_workspace()
  file  = ScheduleStore(root).load()
  b     = file.binding(schedule_id) OR EXIT(1, "no such schedule")
  live  = daemon_is_live()                       # probe hub first; supervisor.lock as fallback
  IF mode == "queue" OR (mode IS AUTO AND live):
      write_request({workspace_root: root, schedule_id: b.id, requested_at: now(),
                     force: force, requested_by: "cli"})
      ECHO f"queued; will fire within one evaluation interval"
      RETURN 0
  # local branch -- SAME launcher the daemon uses, never a second launch path
  decision = synthesize_decision(root, b, scheduled_for=now(), force=force)
  IF NOT decision.fire:  ECHO f"not fired: {decision.reason}";  RETURN 0
  record = ScheduledLauncher(state_dir).launch(decision)
  ECHO f"fired: run_id={record.run_id}"
  RETURN 0
```

## Schemas / Interface Notes
- Interface / CLI: the full `ao schedule` surface in HLD §9.1, plus `ao schedule daemon` (§9.2)
  and a one-line hook into `ao validate`.
- Spec / data schema: writes `<workspace>/.ao/schedules.yaml` via `ScheduleStore.mutate`; writes
  `<state_dir>/schedules/requests/<uuid>.json`.
- Triggers / events: emits `schedule.enabled` / `schedule.disabled` with `requested_by`; reads
  every event for `history`.
- Artifacts: `.ao/schedules.yaml`, the request drop directory, and (via `run-now --local`) a real
  run directory.

## Handoff Boundary
- Upstream: `T-Sd1Kq7`, `T-Fr2Nx8`, `T-Ev3Qm5`, `T-Lp4Wt6`, `T-Sv5Hb3`; HLD §9/§6.9.
- Downstream: `T-Fw8Gp4` / `T-Wh9Kv1` / `T-Un0Lm6` (each adds its own flags to `add`),
  `T-Ap1Xs3` (the dashboard mirrors these operations over HTTP), `T-Te3Qw8` (drives the e2e tier
  through this CLI).

## Artifacts
- Docs/comments: `meta/tickets/E-Sc9Rt4-scheduler-triggers/T-Cl6Jn9-schedule-cli/`
- Large outputs: N/A
