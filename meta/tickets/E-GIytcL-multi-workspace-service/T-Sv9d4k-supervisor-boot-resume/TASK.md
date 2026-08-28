# TASK: T-Sv9d4k-supervisor-boot-resume

## Metadata
- Task ID: `T-Sv9d4k-supervisor-boot-resume`
- Epic ID: `E-GIytcL-multi-workspace-service`
- Owner: developer agent
- Created: 2026-08-28
- Last Updated: 2026-08-28
- Status: Done
- Estimate: < 3 days

## Requirements Mapping
- Requirement IDs: FR-3, FR-4, NFR-1, NFR-2 (see `../EPIC.md`)

## Description
The supervisor daemon: spawns one `ao ui` child per registered workspace, monitors and
restarts crashed children with backoff, handles graceful SIGTERM shutdown (stops UIs,
never runs), and runs the boot-resume scan/decide/act sequence. Depends on
`T-Gr8s2a-registry-ports-config` (`service/registry.py`, `service/ports.py`) — read that
task's ticket + the actual merged code before starting, do not re-derive the registry/port
API from the HLD alone.

Files you own (create/edit freely):
- `src/agent_orchestrator/service/supervisor.py` (new)
- `src/agent_orchestrator/service/boot_resume.py` (new)
- `tests/service/test_supervisor.py`, `tests/service/test_boot_resume.py` (new)

Do NOT touch: `service/registry.py`, `service/ports.py`, `service/paths.py` (read-only —
import from them, do not edit), `service/hub.py`, `service/systemd.py`, `service/cli.py`
(owned by `T-Hb3x7q-hub-systemd-cli` — this task's `Supervisor` class exposes whatever
that task needs as a plain Python API; do not add a Typer/CLI layer here),
`ui/processes.py`, `ui/runs.py`, `models.py`, `executors/`, `spec.py`, `validate.py`,
`specs/*.schema.json`, `templates/`, `cli.py`.

## Acceptance Criteria

### Boot-resume (`service/boot_resume.py`)
1. `scan_resumable_runs(workspace_root: str, *, supervisor_factory=ProcessSupervisor,
   repo_factory=RunRepository) -> list[ResumeCandidate]` — for each `LaunchRecord` from
   `supervisor_factory(workspace_root).reconcile()` with `run_id is not None and
   finished_at is not None`, load `RunState` via `repo_factory(workspace_root)` and yield
   a candidate iff `state.status == "running"`. Factories are injectable specifically so
   tests can substitute a `ProcessSupervisor` whose `reconcile()` is driven by fixture
   `LaunchRecord`s with fake PIDs, without spawning anything real.
2. `BootResumeGuard` (state loaded from/saved to `<state_dir>/boot_resume.json`,
   `state_dir` and `clock: Callable[[], datetime]` both injectable, default clock =
   `datetime.now(UTC)`): `.decide(candidate, boot_id) -> Decision` where `Decision` is one
   of `APPROVE` / `SKIP_ALREADY_THIS_BOOT` / `SKIP_COOLDOWN` / `SKIP_QUARANTINED`, per
   HLD §6.4's exact rule order (per-boot dedup → max-attempts quarantine → cooldown →
   approve). `.record_attempt(candidate, boot_id)` updates the bookkeeping entry
   (`attempts += 1`, `last_boot_id`, `last_attempted_at`, `cooldown_until = now +
   COOLDOWN_SECONDS`) and persists it immediately (atomic write, same pattern as
   `registry.py`) — a crash between two resume attempts must not lose the count.
   `MAX_AUTO_RESUME_ATTEMPTS = 3`, `COOLDOWN_SECONDS = 60` as named module constants (not
   magic literals), overridable via constructor args for tests that need to assert the
   boundary without waiting/faking 60 real entries.
3. Bookkeeping key is `f"{workspace_root}|{run_id}"` (workspace_root must be the same
   resolved-absolute form the registry uses — reuse whatever normalization
   `service/registry.py` established, do not invent a second one).

### Supervisor (`service/supervisor.py`)
4. `ManagedChild` dataclass: `root: str`, `port: int`, `popen: subprocess.Popen | None`,
   `restart_count: int`, `next_retry_at: float | None` (monotonic), `last_error: str |
   None`, `stable_since: float | None`.
5. `RestartBackoff` (or equivalent pure helper): base 1.0s, factor 2.0, cap 60.0s;
   resets once a child has been up continuously past a 30.0s stability window. Unit-test
   this in isolation against a fake monotonic clock — do not only test it embedded inside
   the full supervisor loop.
6. `Supervisor.__init__(registry: ServiceRegistryFile, *, ao_executable: list[str],
   state_dir: Path, hub_port: int, clock=..., monotonic=..., child_spawner=...)` — inject
   the argv prefix used to spawn a child `ao ui` (mirror `ProcessSupervisor`'s own
   reasoning: default to `[sys.executable, "-m", "agent_orchestrator.cli"]`, never a bare
   `ao`, for the same "stale global install" reason documented in `ui/processes.py`) and a
   `child_spawner` callable so integration tests can swap in `["sh", "-c", "..."]` fake
   children instead of real `ao ui`/uvicorn processes (no `[ui]` extra, no network, no
   real dashboard needed to test the process-management logic).
7. `Supervisor.start()`: resolve ports (`service.ports.resolve_ports` +
   `persist_resolution`, saving via `ServiceRegistry`), run boot-resume (scan → decide →
   for each `APPROVE`, call `ProcessSupervisor(root).launch_resume(run_id)` then
   `guard.record_attempt(...)`), spawn one child per workspace via `child_spawner`,
   populate `self._children: dict[str, ManagedChild]`.
8. `Supervisor.tick()`: one monitor-loop pass — `poll()` each child's `Popen` (never a
   bare PID-alive check, same zombie-reaping reasoning as `ui/processes.py`), and for any
   that exited, either restart (respecting `RestartBackoff`, writing `last_error`) or wait
   for `next_retry_at`. This must be callable standalone (not only inside a blocking
   `run()` loop) so tests can single-step it deterministically instead of racing real
   timers.
9. `Supervisor.shutdown(grace_seconds: float = 10.0)`: SIGTERM each child's **own PID**
   (never a process group, never anything the child itself may have spawned — ADR-0012
   D2), poll until grace elapses or all exited, SIGKILL stragglers, reap
   (`proc.wait()`). Must NOT touch any process this `Supervisor` did not itself spawn as
   a direct child.
10. `Supervisor.status_snapshot() -> dict` — the exact payload shape `service/hub.py`
    (next task) will serve verbatim from `GET /api/service/status`: per-workspace
    `{root, port, pid, state, restart_count, last_error}`, `conflicts` (from port
    resolution), recent boot-resume decisions, `supervisor_pid`, `hub_port`,
    `uptime_seconds`. Also write this same information to
    `<state_dir>/supervisor.json`/`port_resolution.json` per HLD §5.2 so `T-Hb3x7q`'s
    `ao service status` fallback path (daemon not reachable) has something to read.
11. No `signal.signal(...)` registration inside `Supervisor` itself — that belongs to the
    `ao service run` CLI command body (`T-Hb3x7q`), which should call
    `supervisor.shutdown()` from its own signal handler. Keep this class signal-free and
    directly testable (call `.shutdown()` like any other method).

### Tests
12. `test_boot_resume.py`: fixture `LaunchRecord`/`RunState` combinations proving (a) an
    alive PID is never a candidate, (b) a dead PID with `status="succeeded"` is never a
    candidate (finished cleanly, not orphaned), (c) a dead PID with `status="running"` IS
    a candidate, (d) `BootResumeGuard` dedups within one `boot_id`, enforces cooldown
    across two different `boot_id`s within the cooldown window, and quarantines after
    `MAX_AUTO_RESUME_ATTEMPTS` — each on a fake clock, zero real `sleep`.
13. `test_supervisor.py` (integration-flavored, still no real network/uvicorn): spawn
    fake children via `sh -c 'trap : TERM; sleep 100'`-style scripts; assert `.tick()`
    detects a killed child and restarts it after backoff; assert repeated rapid crashes
    grow the backoff delay; assert `.shutdown()` terminates children within the grace
    period and reaps them (no zombies — assert via `psutil`-free `os.waitpid`-safe check,
    e.g. `popen.poll() is not None` post-shutdown); assert a **decoy** process spawned
    with `start_new_session=True` *outside* the `Supervisor`'s own child set (simulating a
    detached agent run) is still alive after `.shutdown()` — this is the test that proves
    NFR-1, not just an assertion about supervisor internals.
14. `uv run pytest -q tests/service/` green (report count delta vs. the epic baseline in
    your handoff). `uv run ruff check src/agent_orchestrator/service/` +
    `ruff format --check` clean. `uv run mypy src` — report the whole-tree error count,
    not just this task's files.

## Acceptance Criteria — early-gate additions (2026-08-28, see HLD §5.2/§6.1/§6.3/§6.4, ADR-0012 "Early-gate corrections")
A `reviewer`+`architect` early-gate pass (run before this task started) found real
correctness/safety gaps in the original plan below — three are BLOCKING (would corrupt run
state, lose registry writes, or allow two supervisors to run split-brain), the rest are
should-fix. All are now required:

15. **Singleton lock (blocking).** `Supervisor.start()` first acquires an exclusive,
    non-blocking `flock` on `<state_dir>/supervisor.lock`. On contention, fail fast with a
    clear error naming the situation (best-effort include the holder's pid if readable) —
    never block waiting for it, never proceed anyway. The lock is process-lifetime (held
    until the interpreter exits, however it exits) — do not release it manually anywhere
    except a clean `shutdown()`, and confirm by test that an ungraceful process death (not
    just `shutdown()`) still releases it (OS-level flock semantics guarantee this; write a
    test that actually kills a subprocess holding the lock and confirms a fresh acquire
    succeeds, not just an assertion about `flock`'s documented behavior).
16. **Boot-resume idempotency re-check (blocking).** In the "Act" step, immediately before
    calling `launch_resume(run_id)` — not earlier, not cached from the scan — re-check
    `ProcessSupervisor(workspace_root).is_running(run_id)` and re-load
    `RunRepository(workspace_root).load_state(run_id).status`. If either shows the run is
    no longer a valid candidate (already running again, or reached a terminal status),
    skip it and do NOT call `guard.record_attempt(...)` for it (an attempt not made should
    not consume the attempts/cooldown budget). This closes a real race: a human clicking
    Resume from the dashboard in the same window as a boot-resume approval must never
    result in two engines against one run state.
17. **Registry port-persistence must use `T-Gr8s2a`'s locked `mutate`/equivalent**, not a
    separate load-then-save — read that task's actual landed API (this ticket cannot
    specify its exact name since it's owned by the prior task) and use it. Do not
    reimplement locking here.
18. **Orphan reclamation at startup (should-fix, high-value).** Before spawning children,
    read the previous boot's `supervisor.json` (present + non-matching current `boot_id` ⇒
    evidence of an ungraceful prior exit, since a clean `shutdown()` clears/rewrites it).
    For each of its `{root, port, pid}` entries whose `(root, port)` matches this boot's
    freshly-resolved assignment, best-effort-confirm liveness (Linux: also check
    `/proc/<pid>/cmdline` contains both `root` and `port` if readable, to reduce false
    positives from PID reuse; degrade to pid-liveness-only where `/proc` is unavailable)
    and `SIGTERM` it (direct pid, same as `shutdown()`) before spawning the replacement.
    Without this, a crash + `Restart=on-failure` restart EADDRINUSE-loops against its own
    orphaned previous children forever.
19. **EADDRINUSE backstop (should-fix).** Track each child's spawn time; if it exits within
    a short startup-grace window (named constant, e.g. 3.0s) on `N` consecutive restart
    attempts (named constant, e.g. 3 — separate constant from `RestartBackoff`'s own
    knobs), stop retrying that exact port: re-resolve the workspace via P3 for this boot
    only (do not rewrite the registry's P1/P2 pin), spawn on the new port, and record the
    reassignment as a distinct, surfaced condition (not silently folded into ordinary
    restart-backoff `last_error`).
20. **Child log redirection (should-fix, cheap).** Each spawned `ao ui` child's stdout/
    stderr goes to `<state_dir>/logs/<slug-of-root>.log` (mirror `ui/processes.py`'s own
    `logs_dir` convention — same open-handle-then-close-in-parent pattern), not inherited
    or discarded. `last_error`/`status_snapshot()` should reference the log path so
    `ao service status` can point an operator at it.
21. **Best-effort OS boot id (should-fix, cheap).** Read `/proc/sys/kernel/random/boot_id`
    (Linux; `None` elsewhere or on any read error — never raise) into `supervisor.json`'s
    `os_boot_id` and into each `boot_resume.json` bookkeeping entry's recorded rationale.
    Advisory only — must never change an APPROVE/SKIP decision by itself; a run must still
    resume correctly on a platform where this is always `None`.
22. **Inter-spawn stagger (should-fix, cheap).** A small fixed delay (named constant, e.g.
    200-500ms) between successive child spawns in `start()` — a lightweight, bounded
    mitigation for many workspaces' boot-resume + dashboard-spawn landing in the same
    instant, not a general concurrency-limiter.
23. New/updated tests for AC15-22, each independently assertable (do not fold them all into
    one mega-test): singleton-lock contention (including the "killed holder" case above),
    boot-resume idempotency re-check (a fixture where the run is confirmed alive/terminal
    at act-time despite looking resumable at scan-time), orphan reclamation (a fake
    previous `supervisor.json` naming a still-alive fake process, confirmed SIGTERM'd
    before its replacement spawns, and confirmed NOT touched when `(root, port)` doesn't
    match), EADDRINUSE backstop (a fake child scripted to exit immediately N times,
    asserting reassignment after the Nth), and characterization tests pinning
    `ProcessSupervisor.reconcile()`'s three relied-upon behaviors (returns all records,
    not just live ones; sets `finished_at` on a dead-PID record; leaves `exit_code` at
    `None` for a record this process's `Supervisor` never `Popen`'d) — these exist so a
    future dashboard-only change to `ui/processes.py` fails this suite loudly instead of
    silently breaking boot-resume.

## Risks
- A genuinely blocking `run()` loop is hard to integration-test deterministically;
  structuring `start()`/`tick()`/`shutdown()` as separately callable (AC8/AC9) is what
  makes this testable without timing races — do not collapse them into one opaque
  `run()` that only a real signal can interrupt.
- Confirm empirically (not by inspection alone) that a `start_new_session=True` decoy
  process survives `Supervisor.shutdown()` in the actual test run — this is the crux
  claim of the whole epic and deserves a real assertion, not a comment.

## Dependencies
- `T-Gr8s2a-registry-ports-config` must have landed (`service/registry.py`,
  `service/ports.py`, `service/paths.py`) before this task's `Supervisor.start()` can be
  implemented against real code rather than a guess at the API.
- Reuses `ui/processes.py::ProcessSupervisor` (`.reconcile()`, `.launch_resume()`) and
  `ui/runs.py::RunRepository` (`.load_state()`) — read-only imports, no edits.

## Schemas / Interface Notes
- Interface: `service.supervisor.Supervisor` (`start`, `tick`, `shutdown`,
  `status_snapshot`), `service.boot_resume.scan_resumable_runs`,
  `service.boot_resume.BootResumeGuard` — exact names `T-Hb3x7q` will import for
  `ao service run`/`status`.
- Artifacts: `<state_dir>/supervisor.json`, `<state_dir>/boot_resume.json`,
  `<state_dir>/port_resolution.json`.

## Handoff Boundary
- Upstream: `T-Gr8s2a-registry-ports-config`; HLD §6, §7 boundary note; ADR-0012 D1/D2/D4.
- Downstream: `T-Hb3x7q-hub-systemd-cli` (CLI `run` command owns signal handling + calls
  into this `Supervisor`; hub serves `status_snapshot()`).

## Artifacts
- Docs/comments: `meta/tickets/E-GIytcL-multi-workspace-service/T-Sv9d4k-supervisor-boot-resume/`
- Large outputs: N/A

## Comments

By: agent
Role: developer
Date: 2026-08-28
Comment: Implemented and verified all AC1-23, including the three blocking early-gate
items (AC15 singleton lock, AC16 boot-resume idempotency re-check, AC18 orphan
reclamation). Files created: `src/agent_orchestrator/service/boot_resume.py`,
`src/agent_orchestrator/service/supervisor.py`, `tests/service/test_boot_resume.py`,
`tests/service/test_supervisor.py`. No files outside this task's ownership touched
(`registry.py`/`ports.py`/`paths.py`/`ui/processes.py`/`ui/runs.py` read-only, confirmed via
`git status`). `uv run pytest -q tests/service/`: 66 passed (42 new: 15 boot_resume + 27
supervisor), 0 failed. `uv run pytest -q` full suite: 1883 passed, 7 skipped, 0 failed
(baseline was 1841/7/0 — exactly +42, zero regressions). `uv run ruff check` /
`ruff format --check` on `src/agent_orchestrator/service/` and `tests/service/`: all clean.
`uv run mypy src`: 4 errors, all pre-existing in `_version.py` (unchanged baseline). See
`STATUS.md` for full per-AC evidence mapping. Full detail also reported to the requesting
agent in this session's final handoff message.
