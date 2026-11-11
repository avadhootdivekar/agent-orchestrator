# STATUS

- ID: `T-Sv5Hb3-service-integration`
- Updated At: 2026-11-11
- State: Done — all ACs implemented (AC10 closed by w26-05)
- Owner: TBD (assigned at sprint start)

## Sync 2026-11-11 (w27-02, developer; docs only; HEAD `c4e3871`)
- Supersedes the w24-07 block below, which still says Partial: AC10 closed by w26-05 (`4ec01b4`), all ACs met. Nothing unfinished; known limit: the status-fallback summary counts every schedule in `state.json` (no opt-in filter).

## Reconciliation 2026-11-11 (By: developer · Role: developer · unit `w24-07-a1-task-status-sync` · docs only)
- Checked against branch `ad/enhancements-10nov` at HEAD `1d5c6f9`. Any older line below that says "not started", "uncommitted" or "in the working tree" is superseded by this block where they disagree.
- State: **Partial — all ACs implemented and reviewed except AC10 (status fallback)**
- **Demoted from Done**: the task's own STATUS records AC10 as partial. Unmet: **AC10** - when the live hub snapshot is unavailable, `ao service status` falls back to a raw `state.json` dump instead of the formatted schedules summary (review finding L4, w14-03, open).
- Everything else met and reviewed (w14-03: H2 fixed - engine start failure degrades to no scheduler). 30 integration tests.
- The `Not started`/`Evidence: None yet` lines below are template residue; ignore them.

## This update (2026-11-11)
- **Implemented.** `registry.py` (`WorkspaceEntry.schedules` opt-in default False, `ServiceScheduleConfig`/`ServiceWebhookConfig`, `set_schedules`; policy block now survives add/remove/save), `cli.py` (`add --schedules`, new `set`, `list` SCHEDULES column, `run` flags `--no-schedules/--webhook*`, `build_schedule_engine`/`monitor_tick`/`shutdown_daemon`, `build_status_provider(schedule_engine=)`, `status` summary + `schedules/state.json` fallback, `install --webhook*`), `systemd.py` (webhook ExecStart flags only when enabled).
- Tests: `tests/service/test_service_schedule_integration.py` (30). AC7 server is a documented `NotImplementedError` guard (`build_webhook_app`) until `T-Wh9Kv1`; it runs before the Supervisor so nothing is locked/spawned.
- F23 choice: document the gap (HLD §6.1) and pin it with a test; engine re-reads the registry file.
- Engine/launcher cycle solved with a small deferred-launcher shim in `cli.py`; `schedule_engine.py`/`schedule_launch.py` untouched.
- Earlier note (superseded): ticket created 2026-09-06 by the `architect` agent. Not started then.
- Ticket created by the `architect` agent as part of the E-Sc9Rt4 design package. Not started.
- Scope: registry schema additions, `ao service run` engine wiring with three kill switches and an outer tick backstop, the hub-status merge via `build_status_provider`, and the systemd unit delta.

## Evidence
- None yet — no implementation has begun. Design basis:
  `docs-md/scheduler-triggers-hld.md` and `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md`.

## Risks / Blockers
- Blocked until: `T-Ev3Qm5` and `T-Lp4Wt6` have landed. `T-Wh9Kv1` is a forward dependency for the webhook server behind the flags this task adds.

## Next actions
1. Read the upstream task's **merged code**, not only the HLD, before starting (the interface-drift
   rule this epic inherits from E-GIytcL).
2. Implement against the acceptance criteria in `TASK.md`, in AC order.
3. Hand the registry policy block to `T-Cl6Jn9` and the wired webhook flags to `T-Wh9Kv1`; confirm with `git diff` that `service/cli.py` grew a sibling rather than being restructured.

## 2026-11-11 — review fixes (w16-03, from w14-03) — By: developer · Role: developer · Date: 2026-11-11
- **H2 fixed**: `ao service run` wraps `build_schedule_engine`; `EngineLockHeldError` (or any start failure) logs `schedule.engine_start_failed`, the half-started engine is shut down (lock released) and the service continues with `engine=None` (hub reports `schedules.enabled=false`, dashboards stay up). Test `test_run_engine_start_failure_degrades_to_no_scheduler`; mutant "no guard" killed.
- L4 (status fallback is a raw state.json dump) remains open, AC10 partial.

## 2026-11-11 — AC10 closed (w26-05) — By: developer · Role: developer · Date: 2026-11-11
- `ao service status` with the hub unreachable now prints, after the raw persisted sections, a formatted one-line summary from `<state_dir>/schedules/state.json` (`_persisted_schedules_summary`): active/disabled/quarantined counts, earliest active `next_fire_at`, live scheduled runs, webhook "unknown" (not persisted). Read-only; a corrupt file degrades to no summary. Resolves review finding L4.
- Tests (fixed timestamps, CliRunner): formatted summary, empty state, corrupt state. `tests/service`: all pass; ruff/format/mypy clean on touched files.
- Known limit: persisted state has no opt-in/registry filter, so counts include any schedule present in state.json.
