# Epic: E-GIytcL-multi-workspace-service

## Metadata
- Epic ID: `E-GIytcL-multi-workspace-service`
- Title: Multi-workspace service — one supervisor daemon serving every registered workspace's dashboard, with auto-resume after reboot
- Owner: dev-epic agent
- Created: 2026-08-28
- Last Updated: 2026-08-28 (closed)
- Status: Done
- Origin ask: user request on branch `ad/multi-workspace-service` — design decisions (architecture, registration, registry file, port precedence, auto-resume, hub, systemd) were locked by the user before this epic started and are recorded verbatim in the HLD/ADR rather than re-derived here.
- Mirror: [`meta/tickets/E-GIytcL-multi-workspace-service/EPIC.md`](../../meta/tickets/E-GIytcL-multi-workspace-service/EPIC.md) (task-level tracking lives there; this file is the running narrative + evidence log).

## Goal
Replace the current one-`ao ui`-process-per-workspace-by-hand pattern with a single
user-level `ao service run` supervisor that:
- spawns one **child `ao ui` process per registered workspace** (own port each), reusing
  the existing single-workspace app/server wholesale;
- monitors children and restarts crashed ones with backoff;
- on SIGTERM, gracefully stops child UIs — never the detached agent runs they may have
  launched, which must survive both a supervisor restart and (via a `KillMode=process`
  systemd unit) a `systemctl --user stop`;
- auto-resumes runs a reboot orphaned (`RunState.status=="running"`, owning PID dead),
  bounded so a poisoned run cannot loop-resume forever;
- serves a small hub (fixed port 8770) listing every workspace + a status API;
- is installable as a **user**-level systemd unit (`ao service install`).

Full design: [`docs-md/multi-workspace-service-hld.md`](../multi-workspace-service-hld.md).
Decision record: [`docs-md/adr/ADR-0012-multi-workspace-service-supervisor.md`](../adr/ADR-0012-multi-workspace-service-supervisor.md).

## Acceptance Criteria (testable)
See `meta/tickets/E-GIytcL-multi-workspace-service/EPIC.md`'s FR-1..FR-7/NFR-1..NFR-3 for
the full, traceable list. Summary:
1. `ao service add/remove/list` manage a registry at `~/.config/ao/service.yaml`
   (XDG/env-overridable) — round-trip tested, no real `~` touched by tests.
2. Port resolution is P1 (`.ao/config.yaml` `ui.port`) > P2 (registry pin) > P3 (random,
   persisted back) with deterministic conflict fallback, surfaced in `ao service status`.
3. `ao service run` spawns/monitors/restarts child `ao ui` processes with backoff and
   stops them (not runs) on SIGTERM — proven by an integration test with a decoy detached
   process that survives shutdown.
4. Boot-resume scans dashboard-launched runs for `status=="running"` + dead PID and
   spawns `ao resume --run-id` detached, capped at one attempt/run/boot with a cross-boot
   cooldown + quarantine after `MAX_AUTO_RESUME_ATTEMPTS`.
5. Hub serves an HTML index + `GET /api/service/status`; `ao service status` falls back
   to registry/state files when the daemon is down.
6. `ao service install [--print]` generates a unit with `KillMode=process` (required —
   systemd's default `control-group` kill mode would otherwise kill detached runs despite
   the process-level SIGTERM handling being correct); `--print` touches no disk; no test
   invokes real systemd.
7. `ProjectConfig.ui.port` is additive and byte-compatible both directions (pydantic
   default `extra="ignore"`).
8. Standalone `ao ui` is unchanged.
9. `uv run pytest -q` stays fully green with an exact before/after count recorded below;
   `ruff check .` / `ruff format --check .` / `uv run mypy src` introduce zero *new*
   findings versus the baseline captured below (3 pre-existing format-only files and 4
   pre-existing `_version.py` mypy errors are NOT this epic's to fix — out of scope,
   unrelated, recorded so nobody mistakes them for a regression this epic introduced).

## Design Decisions (locked before implementation — early-gate review target)
Recorded in full in ADR-0012; the four load-bearing ones an early-gate reviewer should
specifically stress-test:
- **D1** — one child `ao ui` process per workspace, not one multi-bound process (failure
  isolation; reuses ADR-0010's app/server wholesale rather than a parallel server).
- **D2** — SIGTERM safety is structural (`start_new_session=True`, already true today per
  the prior-art audit in HLD §3.1) plus one systemd-specific correction
  (`KillMode=process`, since the default `control-group` kill mode would otherwise reach
  detached grandchildren through the cgroup even though they've escaped the process
  group/session).
- **D3** — port precedence P1>P2>P3 with "later registry entry loses a collision, always
  re-resolved and persisted" as the deterministic tie-break.
- **D4** — boot-resume reuses `ProcessSupervisor.reconcile()` + `RunRepository` rather
  than a new liveness mechanism, scoped to dashboard-launched runs only (documented
  limitation), with a wall-clock cooldown (not just per-boot dedup) as the guard against
  a poisoned run looping across rapid supervisor restarts.

## Decomposition
4 tasks under `meta/tickets/E-GIytcL-multi-workspace-service/`, each with an explicit file
ownership boundary (no two tasks touch the same new file; `cli.py` gets exactly one
two-line edit, made only by the third task):

| Task | Owns | Requirements |
|---|---|---|
| `T-Gr8s2a-registry-ports-config` | `service/paths.py`, `service/registry.py`, `service/ports.py`, `project_config.py` (ui.port only) | FR-1, FR-2, FR-7 |
| `T-Sv9d4k-supervisor-boot-resume` | `service/supervisor.py`, `service/boot_resume.py` | FR-3, FR-4, NFR-1, NFR-2 |
| `T-Hb3x7q-hub-systemd-cli` | `service/hub.py`, `service/systemd.py`, `service/cli.py`, `cli.py` (2-line registration only) | FR-5, FR-6, NFR-2 |
| `T-Rv5m1t-test-review-e2e` | no new prod code (tests/docs only) | cross-cutting verification, all FR/NFR |

Sequenced (2 depends on 1; 3 depends on 1+2; 4 depends on 1+2+3) because the supervisor
needs the real registry/port API and the CLI needs the real supervisor API — each task's
ticket explicitly says "read the prior task's merged code, don't re-derive from the HLD
alone" to prevent interface drift between sequential deliveries.

## Evidence Log
- 2026-08-28 — **Baseline captured** (before any epic code): `uv run pytest -q` →
  **1817 passed, 7 skipped, 0 failed** (72.16s). `uv run ruff check .` → clean.
  `uv run ruff format --check .` → 3 pre-existing files would reformat
  (`tests/test_e2e_builtin_routed_runner.py`, `tests/test_executor.py`,
  `tests/test_task_settings_override.py`) — unrelated to this epic, not touched.
  `uv run mypy src` → 4 pre-existing errors, all in `src/agent_orchestrator/_version.py`
  (a hatch-build-generated file) — unrelated to this epic, not touched.
- 2026-08-28 — HLD + ADR-0012 written; epic + 4 task tickets created under
  `meta/tickets/E-GIytcL-multi-workspace-service/`.
- 2026-08-28 — **Early gate run**: `reviewer` and `architect` dispatched in parallel
  against the HLD/ADR/task tickets (before any implementation code). Both independently
  verified every factual claim about existing code (`ui/processes.py`, `ui/security.py`,
  `project_config.py`) by reading the real source, not the HLD's description of it.
  Findings: 3 blocking (boot-resume double-spawn race with no idempotency re-check, a
  registry lost-update from bare load-then-save under concurrent writers, no singleton
  lock allowing two supervisors to run split-brain) + 6 should-fix (orphan reclamation
  after an ungraceful crash, EADDRINUSE backstop, `EnvironmentFile` for systemd-inherited
  credentials, `is_absolute()` + explicit `--hub-port` in the generated unit, hub
  `SecurityMiddleware` was import-only not actually mounted, port-conflict tie-break was
  registry-order-only rather than tier-first) + several accepted minor/deferred items
  (thundering-herd stagger, OS-boot-id tracking, hub status TTL cache — all folded in as
  cheap mitigations; a fixed port band for P3 and empirical real-`systemctl`
  `KillMode=process` verification were explicitly deferred/carried, not silently dropped).
  No finding proposed changing the user's locked D1-D4 decisions themselves. All findings
  folded into HLD (§4-§10), ADR-0012 ("Early-gate corrections" section), and the three
  implementation task tickets' ACs (`T-Gr8s2a` AC7-9, `T-Sv9d4k` AC15-23, `T-Hb3x7q`
  AC14-19), plus a new epic requirement NFR-4 with traceability. Full agent reports
  preserved in this conversation's transcript; not re-copied here to avoid drift between
  two sources of truth — the HLD/ADR/tickets are now the authoritative post-review state.
- 2026-08-28 — **T-Gr8s2a-registry-ports-config landed** (developer agent). Independently
  re-verified (not just trusted the report): `uv run pytest -q tests/service/` -> 24
  passed; full suite -> 1841 passed, 7 skipped, 0 failed (+24 over the 1817 baseline,
  zero regressions); ruff/format clean on touched files; `mypy src` unchanged at the
  4-error baseline. Spot-checked the actual landed `service/registry.py`/`service/ports.py`
  source (not just the report) to confirm the API (`ServiceRegistry.mutate/add/remove`,
  `resolve_ports`/`persist_resolution`) is sound and matches the two blocking early-gate
  items (file-locked read-lock-merge-write registry writes; tier-first port-conflict
  tie-break) before briefing the next task against it.
- 2026-08-28 — **T-Sv9d4k-supervisor-boot-resume landed** (developer agent), built against
  T-Gr8s2a's real merged code. Independently re-verified: `uv run pytest -q tests/service/`
  -> 66 passed (+42); full suite -> 1883 passed, 7 skipped, 0 failed (+42 over the 1841
  baseline, zero regressions); ruff/format clean; `mypy src` unchanged at the 4-error
  baseline. `git status` confirmed no file outside this task's ownership (`service/
  supervisor.py`, `service/boot_resume.py`, `tests/service/test_supervisor.py`, `tests/
  service/test_boot_resume.py`) was touched. Spot-checked `Supervisor`'s public API
  (`start`/`tick`/`shutdown`/`status_snapshot`) and `boot_resume`'s (`scan_resumable_runs`,
  `BootResumeGuard.decide`/`record_attempt`, `Decision`) match exactly what `T-Hb3x7q` was
  briefed to expect. All three blocking early-gate items (AC15 singleton lock — tested by
  actually killing a subprocess holding it; AC16 immediate-before-spawn idempotency
  re-check; AC18 orphan reclamation from a previous boot's `supervisor.json`) confirmed
  implemented with dedicated tests, not folded into unrelated ones.
- **Note on shared working tree**: `git status` shows unrelated modifications (`models.py`,
  `engine.py`, `executors/fake.py`, `specs/*.schema.json`, `templates/builtin/routed-runner/
  *`, plus a new `meta/tickets/E-Tk7Qp2-per-task-effort/`) from a DIFFERENT epic running
  concurrently in this same working tree, per CLAUDE.md's parallel-epics policy. Confirmed
  none of this epic's delegated work touched any file outside its own ownership boundary.
  Full-suite pass counts above reflect the combined working tree at each point in time
  (unavoidable in a shared worktree); this epic's own contribution is independently
  verified via the `tests/service/`-scoped counts and `git status` file-list checks, not
  the full-suite delta alone.
- 2026-08-28 — **T-Hb3x7q-hub-systemd-cli landed** (developer agent), built against both
  prior tasks' real merged code. Independently re-verified: `tests/service/` -> 116 passed
  (+50); full suite -> 1933 passed, 7 skipped, 0 failed (+50 over the 1883 baseline, zero
  regressions); ruff/format clean; `mypy src` unchanged at 4 pre-existing errors. `git diff
  -- src/agent_orchestrator/cli.py` confirmed the exactly-2-line boundary (one import, one
  `add_typer` call). All six should-fix early-gate items (AC14-19: mounted `SecurityMiddleware`
  proven via a real 421, six new systemd unit fields including `EnvironmentFile`/
  `RestartSec`/`StartLimit*`, `is_absolute()` rejection, `hub_port` read from persisted
  state) confirmed implemented and independently tested.
- 2026-08-28 — **Late gate**: `tester` ran a real `ao service run` background-process
  exercise (two temp workspaces, distinct resolved ports, both dashboards answering HTTP,
  SIGTERM to the supervisor's own PID, both children stopped, a `start_new_session=True`
  decoy PID confirmed alive via `kill -0` afterward) AND attempted (this sandbox has a real
  systemd user session) the one recorded ADR-0012 verification gap: a real, non-persistent
  `systemd-run --user --collect -p KillMode=process` transient unit whose main process spawned
  a `setsid`-detached grandchild, confirming the grandchild survived `systemctl --user stop`
  — empirical proof of the central safety claim, not just text-assertion. I independently
  swept the machine afterward, found and cleaned up one incidental orphaned process left by
  the systemd test's own main-process tail (itself further, unplanned corroboration the
  mechanism works exactly as claimed), confirmed no other lingering processes/units.
- 2026-08-28 — **Final reviewer pass**: ran concurrently with the tester above. Raised one
  "blocking" finding (late-gate task never executed) that was a stale read racing the
  tester's own concurrent write to the same ticket file — stood down after re-reading the
  file's final content and cross-checking against my own independent process sweep, both of
  which confirm the real exercise happened. Four legitimate should-fix items (no logging at
  four silent-`except` points in `service/*.py`; a duplicated `_pick_free_port` between
  `ports.py`/`supervisor.py`; `time.sleep` vs `stop_event.wait` in the `run()` tick loop;
  no `shutdown()` cleanup on a non-lock exception mid-`Supervisor.start()`) were all applied
  via a follow-up fork and re-verified: `tests/service/` -> 116 passed (unchanged count,
  pure hardening); full suite -> 1933 passed, 7 skipped, 0 failed, zero regressions;
  ruff/mypy unchanged/clean.
- 2026-08-28 — **Epic closed.** Traceability re-check: every MVP requirement (FR-1..FR-7,
  NFR-1..NFR-4) maps to landed code with dedicated tests -- see the table below. Docs synced
  (`meta/ROADMAP.md` §2a + table row + bullet; HLD/ADR-0012 status headers -> Accepted/
  shipped). All four task tickets + the epic ticket flipped to Done with By/Role/Date
  attribution. **Baseline-to-final: 1817 -> 1933 passed (+116 net new tests, all in
  `tests/service/`), 0 failures at every verification checkpoint across four independent
  task boundaries plus this final gate.**

### Final MVP requirement traceability

| Requirement | Landed in | Test coverage |
|---|---|---|
| FR-1 registry add/remove/list | `service/registry.py`, `service/cli.py` | `test_registry.py` (9), `test_cli_e2e.py` (19) |
| FR-2 port resolution P1>P2>P3 + tier-first conflicts | `service/ports.py` | `test_ports.py` (9) |
| FR-3 supervisor spawn/monitor/restart/SIGTERM | `service/supervisor.py` | `test_supervisor.py` (27) + real subprocess late-gate |
| FR-4 boot-resume (scan/decide/act, idempotent, bounded) | `service/boot_resume.py`, `service/supervisor.py` | `test_boot_resume.py` (15) |
| FR-5 hub (HTML index + status API, mounted security) | `service/hub.py` | `test_hub.py` (11) |
| FR-6 systemd unit generation (`KillMode=process` + hardening) | `service/systemd.py` | `test_systemd.py` (20) + real `systemd-run` late-gate |
| FR-7 `ProjectConfig.ui.port` | `project_config.py` | `test_project_config_ui.py` (6) |
| NFR-4 concurrency/idempotency safety (singleton lock, registry lock, boot-resume re-check, orphan reclamation) | `service/registry.py`, `service/supervisor.py` | dedicated tests within `test_registry.py`/`test_supervisor.py` (real subprocess kills, real thread races, real `/proc` matching) |


## Risks & Blockers
- Parallel epic(s) own `models.py`/`executors/`/`spec.py`/`validate.py`/
  `specs/*.schema.json`/`templates/` right now — zero edits there by construction of the
  task file-ownership boundaries above.
- systemd `KillMode` default is a real correctness trap (see D2) — mitigated by an
  explicit unit-text assertion in `T-Hb3x7q`'s tests, not left to convention.
- Boot-resume is scoped to dashboard-launched runs (documented limitation, not a defect).

## Next actions
None — epic closed. Deferred/carried items for a future epic (not blockers): a fixed P3
port band (vs. `bind(0)` ephemeral, TOCTOU-documented), hub authentication (tracked in
`meta/ROADMAP.md` §3.1, inherited from `ao ui`'s existing deferred posture), and a
concurrency-limiter subsystem for boot-resume beyond the current fixed inter-spawn stagger
(current mitigation judged sufficient at the expected scale — a handful of workspaces per
user).
