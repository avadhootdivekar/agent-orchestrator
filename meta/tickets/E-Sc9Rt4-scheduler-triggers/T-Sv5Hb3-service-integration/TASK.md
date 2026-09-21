# TASK: T-Sv5Hb3-service-integration

## Metadata
- Task ID: `T-Sv5Hb3-service-integration`
- Epic ID: `E-Sc9Rt4-scheduler-triggers`
- Owner: developer agent
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 2 days

## Requirements Mapping
- Requirement IDs: FR-2, FR-10, NFR-1, NFR-2 (see `../EPIC.md`)

## Description
Wire the engine into the daemon: registry schema additions, the `ao service run` loop, the kill
switches, the hub status merge, and the systemd unit delta. This is the task that makes the
scheduler actually run, and the task that must prove it did not change anything for a workspace
without schedules (NFR-1).

Read HLD §5.2, §6.1 and §12, and ADR-0014 D1. Read the merged `service/cli.py::run` — its
`stop_event.wait(HUB_TICK_INTERVAL_SECONDS)` loop, its lazy `uvicorn` import, its
`build_status_provider` closure, and its two-branch `supervisor.start()` exception handling — and
extend that shape rather than restructuring it.

Files you own (create/edit freely):
- `src/agent_orchestrator/service/registry.py` (edit: add `WorkspaceEntry.schedules` and a
  top-level `ServiceRegistryFile.schedules` policy block)
- `src/agent_orchestrator/service/cli.py` (edit: `run` command wiring, new options, `add`
  gaining `--no-schedules`, `status` output)
- `src/agent_orchestrator/service/systemd.py` (edit: unit rendering gains the webhook flags when
  enabled)
- `tests/service/test_service_schedule_integration.py` (new); additive edits to
  `tests/service/test_registry.py`, `test_cli_e2e.py`, `test_systemd.py`

Do NOT touch: `service/schedule_engine.py`, `service/fire_store.py`, `service/schedule_launch.py`,
`service/supervisor.py`, `service/hub.py`, `schedules/`, `ui/`, `cli.py`.

## Acceptance Criteria

### Registry additions
1. `WorkspaceEntry.schedules: bool = True` — the machine operator's per-workspace opt-out —
   settable via `ao service add --no-schedules`, shown in `ao service list`. `ServiceRegistryFile`
   gains an optional top-level `schedules: ServiceScheduleConfig` block with
   `enabled: bool = True`, `eval_interval_seconds: float = 15.0`, `max_concurrent_global: int = 3`
   and a nested `webhook: {enabled: False, host: "127.0.0.1", port: 8771}`.
2. Both additions are strictly additive with defaults, so an existing registry file loads
   unchanged and a **newer** file loaded by an older `ao` is ignored key-by-key (pydantic's
   existing `extra="ignore"`). A test loads a pre-epic registry fixture verbatim and asserts the
   parsed result.

### `ao service run` wiring
3. `ao service run` constructs `ScheduleEngine` **after** `supervisor.start()` returns and calls
   `engine.start()`, so orphan-fire reconciliation runs once and so an auto-resumed run is
   already visible to the first overlap check. Startup order is asserted by a test.
4. The monitor loop calls `engine.tick()` immediately after `supervisor.tick()` in the existing
   `while not stop_event.is_set()` body. The engine self-rate-limits; the loop interval stays
   `HUB_TICK_INTERVAL_SECONDS = 2.0` and is **not** changed.
5. `engine.tick()` is wrapped so that an exception escaping the engine is logged
   (`schedule.engine_tick_failed`, ERROR) and the loop continues. The engine already isolates
   per-schedule failures (`T-Ev3Qm5` AC11); this is the outer backstop, and a test kills the
   engine with a raising stub and asserts the supervisor keeps ticking.
6. Kill switches: `ao service run --no-schedules` skips constructing the engine entirely (not
   merely skipping `tick()`), and a registry-level `schedules.enabled: false` does the same. A
   workspace entry with `schedules: false` is skipped by the engine. All three are tested.
7. Webhook flags: `--webhook/--no-webhook`, `--webhook-host`, `--webhook-port` (defaults from the
   registry block, then the constants). When enabled, the listener app from `T-Wh9Kv1` is served
   on its own uvicorn thread alongside the hub thread; when not, no socket is opened. **Default
   off.** Binding a non-loopback `--webhook-host` prints the same loud warning `ao ui --host`
   prints. Until `T-Wh9Kv1` lands, wire the flags and leave a documented `NotImplementedError`
   guard behind `--webhook` — do not fake it.
8. `shutdown` path: the `finally` block calls `engine.shutdown()` before `supervisor.shutdown()`,
   stops the webhook server the same way the hub server is stopped (`should_exit = True` +
   bounded `join`), and never lets an engine shutdown error prevent supervisor shutdown.

### Status
9. `build_status_provider` gains an optional `schedule_engine` parameter and merges
   `engine.status_snapshot()` into the payload under a top-level `"schedules"` key, inside the
   same TTL cache. `hub.py` is **not** edited — it renders unknown keys harmlessly, which is why
   this is the correct seam (ADR-0010 D5's thin-adapter rule).
10. `ao service status` prints a schedules summary (counts, next fire, live scheduled runs,
    webhook state) from the live hub when reachable and from `<state_dir>/schedules/state.json`
    when not — mirroring the existing fallback behavior rather than inventing a second one.
11. NFR-1: with no `.ao/schedules.yaml` anywhere and no registry `schedules:` block, the
    `GET /api/service/status` payload is identical to the pre-epic payload **apart from** the new
    `"schedules"` key, and zero schedule events are written. Asserted by a dedicated test.

### systemd
12. `render_unit` gains the webhook flags in `ExecStart` **only when the caller enables them**,
    so an existing installed unit needs no regeneration for the MVP. `KillMode=process` and every
    existing hardening line are unchanged — assert that in the unit-text test, because losing
    `KillMode=process` would kill detached scheduled runs on `systemctl --user stop`.
13. `ao service install --print` output for the default (no-webhook) case is byte-identical to
    today. Asserted against the existing fixture.

## Risks
- This task edits the file that owns the daemon's lifecycle. A mistake here is a daemon that
  will not start, which takes every dashboard with it. Keep the diff shaped as "add a sibling
  next to the supervisor", not as a restructure, and confirm with `git diff` at handoff.
- AC5's outer backstop is easy to write as a bare `except Exception: pass`. It must log with the
  named event and the traceback, or a scheduler failure becomes invisible.
- The webhook thread (AC7) adds a second uvicorn server to a process that already has one. Reuse
  the hub's exact daemon-thread + `should_exit` pattern; do not introduce an event loop of your
  own.
- `ao service run`'s body is deliberately excluded from direct testing by the prior epic's
  policy (it blocks and binds a real port). Test the parts — `build_status_provider`, the
  registry model, `render_unit` — and the loop composition via a stubbed engine/supervisor pair.

## Dependencies
- `T-Ev3Qm5` (engine) and `T-Lp4Wt6` (launcher) must have landed.
- `T-Wh9Kv1` is a **forward** dependency for AC7's server: land the flags and the guard now, and
  that task removes the guard.
- Reads (read-only): `service/supervisor.py`, `service/hub.py`, `service/paths.py`,
  `ui/security.py`.

## Pseudocode / Algorithm
```text
# service/cli.py::run  -- the shape to extend, not restructure
supervisor = Supervisor(registry, state_dir=state_dir, hub_port=hub_port)
supervisor.start()                                    # existing two-branch exception handling

engine = None
IF schedules_enabled(registry, cli_flag):
    engine = ScheduleEngine(registry_path, state_dir=state_dir,
                            launcher=ScheduledLauncher(state_dir),
                            eval_interval_seconds=cfg.eval_interval_seconds,
                            max_concurrent_global=cfg.max_concurrent_global)
    engine.start()                                    # reconcile orphaned fires; no launches

status_provider = build_status_provider(supervisor, schedule_engine=engine)
hub_thread      = start_server(build_hub_app(status_provider), hub_host, hub_port)
webhook_thread  = start_server(build_webhook_app(...), wh_host, wh_port) IF webhook_enabled ELSE None

WHILE NOT stop_event.is_set():
    IF NOT stop_event.wait(HUB_TICK_INTERVAL_SECONDS):     # 2.0 s, unchanged
        supervisor.tick()
        IF engine IS NOT NULL:
            TRY: engine.tick()
            EXCEPT Exception AS e:
                logger.exception("schedule.engine_tick_failed")   # outer backstop; loop continues
FINALLY:
    IF engine IS NOT NULL: best_effort(engine.shutdown)
    supervisor.shutdown()
    stop_server(hub_thread); stop_server(webhook_thread)
```

## Schemas / Interface Notes
- Interface / CLI: `ao service run [--no-schedules] [--webhook/--no-webhook] [--webhook-host]
  [--webhook-port]`, `ao service add [--no-schedules]`, `ao service status` (schedules summary),
  `ao service install` (webhook flags when enabled).
- Spec / data schema: `~/.config/ao/service.yaml` gains `WorkspaceEntry.schedules` and the
  top-level `schedules:` policy block (HLD §5.2).
- Triggers / events: emits `schedule.engine_tick_failed`; hosts the webhook listener that
  receives `webhook.*` events.
- Artifacts: `<state_dir>/schedules/*` (via the engine), the rendered systemd unit.

## Handoff Boundary
- Upstream: `T-Ev3Qm5`, `T-Lp4Wt6`; HLD §5.2/§6.1/§12; ADR-0014 D1.
- Downstream: `T-Cl6Jn9` (reads the registry policy block to decide daemon-vs-local `run-now`),
  `T-Wh9Kv1` (fills in the listener behind the flags landed here), `T-Te3Qw8` (integration tier
  drives this composition).

## Artifacts
- Docs/comments: `meta/tickets/E-Sc9Rt4-scheduler-triggers/T-Sv5Hb3-service-integration/`
- Large outputs: N/A
