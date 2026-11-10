# STATUS

- ID: `T-Sv5Hb3-service-integration`
- Updated At: 2026-11-11
- State: Done
- Owner: TBD (assigned at sprint start)

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
