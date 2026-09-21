# EPIC: E-GIytcL-multi-workspace-service

## Metadata
- Epic ID: `E-GIytcL-multi-workspace-service`
- Title: Multi-workspace service — one supervisor daemon serving every registered workspace's dashboard, with auto-resume after reboot
- Owner: dev-epic agent
- Created: 2026-08-28
- Last Updated: 2026-08-28 (closed)
- Status: Done
- Origin ask: user request (see task prompt) — design decisions locked by the user before this epic started; not re-litigated (see HLD §2 / ADR-0012).

## Summary
- Goal: Replace one-`ao ui`-process-per-workspace-by-hand with a single user-level
  `ao service run` supervisor that spawns one child `ao ui` dashboard process per
  registered workspace (its own port), monitors/restarts crashed children, gracefully
  stops UIs (not runs) on SIGTERM, auto-resumes runs orphaned by a reboot, and serves a
  small hub (fixed port 8770) listing every workspace. Registration via
  `ao service add/remove/list`; `ao service install` writes a user systemd unit.
- Scope In: `service/` package (registry, port resolution, supervisor, boot-resume, hub,
  systemd unit generation), `ao service` CLI sub-app, `ProjectConfig.ui.port`, tests
  (unit/integration/e2e), HLD + ADR-0012, ROADMAP update.
- Scope Out: authentication (same deferred posture as `ao ui`), write-conflict detection
  across workspaces, non-systemd process managers, live-streaming hub UI, editing
  `models.py`/`executors/`/`spec.py`/`validate.py`/`specs/*.schema.json`/`templates/`
  (owned by other in-flight work — hard boundary).

## Requirements

### MVP (must-have)
- FR-1 (Functional): `ao service add <dir> [--port] [--no-autoresume]` /
  `remove <dir>` / `list` manage `~/.config/ao/service.yaml` (XDG/env-overridable) as the
  single source of truth for registered workspaces. Verification: unit tests (round-trip
  load/save) + CliRunner e2e.
- FR-2 (Functional): Port resolution P1 (`.ao/config.yaml` `ui.port`) > P2 (registry pin)
  > P3 (random free port, persisted back) with deterministic conflict fallback (later
  registry entry loses, re-resolved via P3) and conflicts surfaced in `ao service status`.
  Verification: unit tests covering all three precedence levels + a forced conflict.
- FR-3 (Functional): `ao service run` foreground supervisor spawns one child `ao ui`
  process per workspace (reusing the existing app/server wholesale, no multi-bind),
  monitors + restarts crashed children with exponential backoff, and on SIGTERM stops
  child UIs (direct PID, not process group / not runs) after a grace period.
  Verification: integration tests against fake `sh -c` children (spawn/crash/restart/
  graceful-shutdown), asserting a decoy detached process survives shutdown.
- FR-4 (Functional): Boot-resume — scan `RunState.status=="running"` runs whose owning
  `LaunchRecord` PID is dead (reusing `ProcessSupervisor.reconcile()` +
  `RunRepository`), and spawn `ao resume --run-id <id>` detached via
  `ProcessSupervisor.launch_resume`, capped at one attempt per run per boot with a
  cross-boot cooldown + max-attempts quarantine. Verification: unit tests against fixture
  run states/launch records with fake clocks/pids (dedup, cooldown, quarantine all
  independently asserted).
- FR-5 (Functional): Hub server (fixed port 8770, loopback, same security posture as
  `ao ui`) serving an HTML index + `GET /api/service/status`; `ao service status` reads
  it when the daemon is up, else falls back to registry/state files.
  Verification: unit test of the status payload shape + CliRunner e2e of the fallback path.
- FR-6 (Functional): `ao service install [--print]` generates a **user**-level systemd
  unit (`ExecStart` = absolute `ao` path + `service run`, `Restart=on-failure`,
  `KillMode=process` — required so systemd's default control-group signal delivery does
  not kill detached agent runs, see ADR-0012 D2) with `--print` never touching disk;
  `start`/`stop` shell out to `systemctl --user` when present, else print guidance.
  Verification: unit tests on rendered unit text (asserting `KillMode=process` and
  `ExecStart` resolution branches) + CliRunner e2e of `install --print`, no real systemd
  invoked anywhere in the suite.
- FR-7 (Non-functional / spec ergonomics): `ProjectConfig.ui.port: int | None = None`,
  additive, pydantic default `extra="ignore"` keeps old/new binaries mutually
  compatible in both directions. Verification: unit test loading an old config (no `ui:`
  key) and a config with `ui: {port: N}`.
- NFR-1 (Reliability): agent runs must survive both a supervisor SIGTERM and (via
  `KillMode=process`) a `systemctl --user stop`. Verification: integration test's decoy
  detached process (FR-3) + unit-file assertion (FR-6).
- NFR-2 (Observability): every port conflict and boot-resume decision is recorded
  (state files) and surfced via `ao service status`, not just logged. Verification:
  covered by FR-2/FR-4/FR-5 tests.
- NFR-3 (Operability/spec ergonomics): zero real-`~`/network/systemd touches in the test
  suite — `AO_SERVICE_CONFIG`/`AO_SERVICE_STATE_DIR` env overrides used throughout.
  Verification: grep-level check in review + CI running unmodified in this sandbox.
- NFR-4 (Reliability/concurrency safety — added 2026-08-28 by the early-gate `reviewer`+
  `architect` pass, before implementation started): a second concurrent `ao service run`
  fails fast rather than running split-brain (singleton lock); a registry write concurrent
  with the supervisor's own port-persistence never silently loses either writer's change
  (read-lock-merge-write, not bare load/save); a boot-resume approval never spawns a
  second engine against a run state that is actually already live or terminal by the time
  the spawn happens (immediate-before-spawn re-check); an ungraceful supervisor crash
  followed by a restart reclaims (does not EADDRINUSE-loop against) its own orphaned
  previous children. Verification: dedicated tests per corrected task ticket ACs (see
  `T-Gr8s2a` AC7-9, `T-Sv9d4k` AC15-23, `T-Hb3x7q` AC14-19) — see ADR-0012 "Early-gate
  corrections" for full rationale.

### Non-MVP (deferred)
- Hub authentication / per-workspace dashboard authentication — tracked in
  `meta/ROADMAP.md` §3.1; would be validated by an auth-focused epic's own test suite.
- Write-conflict detection between workspaces sharing a filesystem root — tracked in
  `meta/ROADMAP.md` §3.4 (parallel-execution follow-on); validated there.
- Live-streaming hub status (WebSocket/SSE) instead of polling — tracked in
  `meta/ROADMAP.md` §3.3; would need a frontend epic.

### Stretch (nice-to-have, not required to ship)
- `--hub-port` override on `ao service run`/`install` (beyond the fixed 8770 default) —
  include only if it falls out of the design at no extra risk; not required to ship.

## Task List
- [x] `T-Gr8s2a-registry-ports-config` — registry (load/save/XDG/env), port resolution
  P1>P2>P3 + conflicts, `ProjectConfig.ui.port` (FR-1, FR-2, FR-7) — Done 2026-08-28
- [x] `T-Sv9d4k-supervisor-boot-resume` — supervisor spawn/monitor/restart/SIGTERM,
  boot-resume scan/decide/act (FR-3, FR-4, NFR-1, NFR-2) — Done 2026-08-28
- [x] `T-Hb3x7q-hub-systemd-cli` — hub server, systemd unit generation, `ao service`
  CLI sub-app wiring into `cli.py` (FR-5, FR-6, NFR-2) — Done 2026-08-28
- [x] `T-Rv5m1t-test-review-e2e` — full-suite baseline/after counts, ruff/mypy clean,
  late-gate e2e via `tester`, reviewer pass, docs/ROADMAP sync (all FRs/NFRs, traceability
  re-check)

## Risks and Dependencies
- Parallel epic(s) own `models.py`/`executors/`/`spec.py`/`validate.py`/
  `specs/*.schema.json`/`templates/` right now — hard boundary, zero edits there; only a
  minimal `add_typer` line lands in `cli.py`.
- systemd `KillMode` default is a real correctness trap for the "runs survive" guarantee
  if the generated unit does not override it — addressed explicitly in ADR-0012 D2 and
  covered by a unit-text assertion, not left implicit.
- Boot-resume is scoped to dashboard-launched runs only (documented limitation, not a
  defect) — a bare-terminal `ao run` has no PID artifact for the service to observe.
- `AO_SERVICE_CONFIG`/`AO_SERVICE_STATE_DIR` must be threaded through every test; a
  missed one risks writing to the sandbox's real `~/.config`/`~/.local/state`.

## Links
- Design doc: [`docs-md/multi-workspace-service-hld.md`](../../../docs-md/multi-workspace-service-hld.md)
- ADR: [`docs-md/adr/ADR-0012-multi-workspace-service-supervisor.md`](../../../docs-md/adr/ADR-0012-multi-workspace-service-supervisor.md)
- Epic context (dev-epic working doc): [`docs-md/ai-epics/E-GIytcL-multi-workspace-service.md`](../../../docs-md/ai-epics/E-GIytcL-multi-workspace-service.md)
- Output artifacts (if any): `output/E-GIytcL-multi-workspace-service/` (test logs)
