# STATUS

- ID: `T-Sv9d4k-supervisor-boot-resume`
- Updated At: 2026-08-28
- State: Done
- Owner: developer agent

## This update
- Implemented the supervisor daemon and boot-resume module against `T-Gr8s2a`'s actual
  merged `service/registry.py`/`service/ports.py`/`service/paths.py` (read, not guessed
  from the HLD): `service/boot_resume.py` (`scan_resumable_runs`, `BootResumeGuard`,
  `Decision`, `ResumeCandidate`) and `service/supervisor.py` (`Supervisor`, `ManagedChild`,
  `RestartBackoff`, `acquire_singleton_lock`/`release_singleton_lock`,
  `SupervisorLockHeldError`, `SupervisorSnapshot`).
- All three blocking early-gate items (AC15/16/18) implemented as core requirements, each
  independently tested, not folded into a mega-test:
  - **AC15 (singleton flock)**: `acquire_singleton_lock`/`release_singleton_lock` wrap an
    exclusive, non-blocking `flock` on `<state_dir>/supervisor.lock`, held process-lifetime
    (released only from `Supervisor.shutdown()`). Contention raises `SupervisorLockHeldError`
    naming the holder's pid (best-effort, read from the lock file's own contents, written by
    the holder on acquire). Tested by actually spawning a real subprocess that holds the
    lock, confirming contention, `SIGKILL`-ing it, and confirming a fresh acquire then
    succeeds (`TestSingletonLock::test_a_killed_holder_releases_the_lock_for_a_fresh_acquire`)
    — not just an assertion about flock's documented semantics, per the AC's explicit
    instruction. Also verified `Supervisor.start()` itself fails fast on contention.
  - **AC16 (boot-resume idempotency re-check)**: `Supervisor._decide_and_act` re-checks
    `ProcessSupervisor(root).is_running(run_id)` and re-loads
    `RunRepository(root).load_state(run_id).status` immediately before calling
    `launch_resume` — never cached from the scan — and skips WITHOUT calling
    `guard.record_attempt(...)` if either check shows the run is no longer a valid
    candidate. Tested with a fake `ProcessSupervisor`/`RunRepository` pair whose scan-time
    view (dead pid, `status="running"`) differs from its act-time view, for both the
    "became alive" and "reached terminal" races, each asserting `launch_resume` was NOT
    called and the attempt budget was NOT spent (a fresh `BootResumeGuard.decide` for the
    same candidate still returns `APPROVE`) — plus a positive-control test proving the same
    harness DOES launch+record when nothing changed between scan and act.
  - **AC18 (orphan reclamation)**: `Supervisor.start()` reads the *previous* boot's
    `supervisor.json` before writing its own, and for each `{root, port, pid}` whose
    `(root, port)` matches this boot's freshly-resolved assignment, best-effort-confirms
    liveness (`/proc/<pid>/cmdline` contains both `root` and `port` when readable, degrading
    to pid-liveness-only elsewhere) and `SIGTERM`s (then `SIGKILL`s if still alive after a
    grace window) before spawning the replacement. Tested with a fake previous
    `supervisor.json` naming a real, still-alive decoy process: confirmed `SIGTERM`'d before
    the replacement spawns on a `(root, port)` match, and confirmed untouched when the port
    doesn't match or the previous snapshot's `boot_id` equals this boot's own.
- Should-fix items AC19-22 also implemented: EADDRINUSE backstop (N consecutive fast exits
  within a startup-grace window reassigns the port via a fresh P3 pick for that boot only,
  surfaced as `ManagedChild.reassignment_reason`, distinct from ordinary `last_error`), child
  log redirection (`<state_dir>/logs/<slug>.log`, referenced in `status_snapshot()`),
  best-effort OS boot id (`/proc/sys/kernel/random/boot_id`, `None`/advisory-only elsewhere),
  and a fixed inter-spawn stagger (`SPAWN_STAGGER_SECONDS`).
- AC17 (registry writes via the locked `mutate()`): port resolution is computed exactly
  once (P3 picks are real random binds — resolving twice would hand a child a different
  port than what gets persisted) and merged onto the CURRENT on-disk registry via
  `ServiceRegistry.mutate(lambda current: persist_resolution(current, resolution))` — never
  a bare `load()`+`save()`. Tested that a P3-picked port round-trips through the registry
  file, and that a workspace added concurrently (on disk, not in this Supervisor's own
  in-memory snapshot) survives the merge untouched.
- One defensive fix found and applied beyond the AC list: `Supervisor.__init__` now
  normalizes every registry root the same way `registry.py::save()` does
  (`str(Path(root).resolve())`), unconditionally. Without it, a caller-supplied
  `ServiceRegistryFile` with a non-normalized root could desync `PortResolution.ports`
  (keyed by the pre-normalization root) from `self._registry` after `mutate()` replaces it
  with `ServiceRegistry.save()`'s normalized return value — silently skipping that
  workspace's child spawn. True production callers (`ServiceRegistry().load()` output) are
  already normalized, but the constructor no longer depends on that being true.

## Evidence
- Files created: `src/agent_orchestrator/service/boot_resume.py`,
  `src/agent_orchestrator/service/supervisor.py`, `tests/service/test_boot_resume.py`,
  `tests/service/test_supervisor.py`.
- Files touched outside this task's ownership: none. Confirmed via `git status --short`
  before/after — `service/registry.py`, `service/ports.py`, `service/paths.py`,
  `ui/processes.py`, `ui/runs.py` all read-only imports, zero edits.
- `uv run pytest -q tests/service/`: **66 passed**, 0 failed (42 new: 15 in
  `test_boot_resume.py`, 27 in `test_supervisor.py`; the other 24 are `T-Gr8s2a`'s
  pre-existing registry/ports/project-config-ui tests, unaffected).
- `uv run pytest -q` (full suite): **1883 passed, 7 skipped, 0 failed** vs. the epic
  baseline of 1841 passed/7 skipped/0 failed after `T-Gr8s2a` — exactly +42, zero
  regressions.
- `uv run ruff check src/agent_orchestrator/service/ tests/service/`: all checks passed.
- `uv run ruff format --check` on the same paths: all files already formatted.
- `uv run mypy src` (whole tree): **4 errors**, all in `src/agent_orchestrator/
  _version.py` (generated file, pre-existing baseline) — **0 new errors** introduced by
  this task's files, count unchanged (4 → 4).
- `test_supervisor.py`'s `TestShutdown::test_shutdown_never_touches_a_process_outside_its_own_child_set`
  is the epic's crux NFR-1 claim, verified with a real assertion (not a comment): a decoy
  process spawned `start_new_session=True` outside the `Supervisor`'s own child set is
  confirmed alive (`decoy.poll() is None`) after `.shutdown()`.
- Characterization tests (AC23's final bullet) pin the three `ProcessSupervisor.
  reconcile()` behaviors `scan_resumable_runs` relies on but does not itself define:
  returns every persisted record (not just live ones), sets `finished_at` on a dead-pid
  record, and leaves `exit_code` at `None` for a record no `Popen` in the calling instance
  ever tracked — so a future dashboard-only change to `ui/processes.py` fails this suite
  loudly instead of silently breaking boot-resume.

## Risks / Blockers
- None blocking. Carried forward from the epic: `KillMode=process` in the generated
  systemd unit (owned by `T-Hb3x7q`) is the one safety-critical mechanism this task's tests
  cannot exercise (no real systemd in the suite, per the epic's locked decision) — `.
  shutdown()`'s own process-only (never process-group) signaling is fully tested here, but
  the systemd `KillMode` interaction is `T-Hb3x7q`'s/`T-Rv5m1t`'s verification gap to close
  or document, not this task's.
- `Supervisor.start()`'s constructor accepts `process_supervisor_factory`/
  `run_repository_factory` (defaulting to the real `ProcessSupervisor`/`RunRepository`
  classes) beyond the exact param list the ticket's AC6 spells out — added for symmetry
  with `scan_resumable_runs`'s own injectable factories (AC1) and used by this task's own
  AC16 tests; all keyword-only with real-class defaults, so `T-Hb3x7q`'s expected
  `Supervisor(registry=..., ao_executable=..., state_dir=..., hub_port=..., clock=...,
  monotonic=..., child_spawner=...)` call shape is unaffected.

## Next actions
1. Handoff to `T-Hb3x7q-hub-systemd-cli`: import `Supervisor` (`start`/`tick`/`shutdown`/
   `status_snapshot`) and `boot_resume` module as specified; build the hub server +
   `ao service run`/`status` CLI + systemd unit generation on top, installing its own
   `SIGTERM`/`SIGINT` handlers that call `supervisor.shutdown()` (this class is
   deliberately signal-free, AC11).
2. `T-Rv5m1t-test-review-e2e` late-gate once `T-Hb3x7q` lands.
