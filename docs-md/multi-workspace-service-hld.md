# Multi-workspace service — HLD (E-GIytcL)

- Status: Accepted / shipped (2026-08-28) — implemented, independently reviewed, and late-gate verified with real subprocess + real systemd evidence (see epic STATUS.md)
- Related: [`ADR-0010`](adr/ADR-0010-dashboard-architecture-and-general-instructions.md) (dashboard architecture — subprocess launch, loopback security posture, launch records) · [`ADR-0012`](adr/ADR-0012-multi-workspace-service-supervisor.md) · epic ticket [`meta/tickets/E-GIytcL-multi-workspace-service/EPIC.md`](../meta/tickets/E-GIytcL-multi-workspace-service/EPIC.md)

## 1. Problem

Today `ao ui` (`cli.py::ui_cmd`, `UI_DEFAULT_PORT=8765`) is one `uvicorn` process bound to one
port, serving one workspace, run in the foreground of a terminal. Anyone who wants the
dashboard for more than one workspace, or wants it to survive a reboot, hand-writes a
systemd unit per workspace and picks ports by hand. Every ticket in `ao-runner-finplan`'s
history of standing up a new epic-runner workspace has repeated this by-hand ritual.

The ask: **one user-level service** that serves **every registered workspace**, each on its
own dashboard/port, that comes back automatically after a reboot or service restart — no
per-workspace scripting, no hand-written unit files.

## 2. Locked design decisions

These were fixed by the user before design work started and are **not re-litigated** by this
document or its early-gate review; they are recorded here so the implementation has one
place to check itself against.

1. One supervisor daemon per user (`ao service run`, foreground). It spawns one **child
   process per registered workspace**, reusing the existing single-workspace `ao ui`
   app/server wholesale (no multi-bind-in-one-process design). It monitors children and
   restarts crashed ones with backoff. SIGTERM to the supervisor gracefully stops child UIs;
   agent runs are detached and must survive service restarts.
2. Registration is explicit: `ao service add <dir>` / `remove` / `list`. The registry is the
   single source of truth for which workspaces the service serves.
3. Registry/config file — `~/.config/ao/service.yaml` (XDG_CONFIG_HOME-respecting,
   `AO_SERVICE_CONFIG` env override for tests). Holds the workspace list: root path,
   optional pinned port, `autoresume` (default true). Runtime state (boot-resume
   bookkeeping, supervisor pid) lives separately under `~/.local/state/ao/`
   (XDG_STATE_HOME-respecting, env-overridable).
4. Port resolution precedence per workspace: **P1** workspace's own `.ao/config.yaml`
   (`ui.port`) > **P2** registry entry's pinned port > **P3** random free port (bind port 0),
   persisted back into the registry so URLs are stable across restarts. Conflicts (two
   workspaces resolving to the same port, or the port already bound by something else) are
   logged, surfaced in `ao service status`, and resolved by falling back the loser to a
   random port.
5. Auto-resume is default-on. At supervisor boot, for each `autoresume` workspace, scan run
   states + the dashboard's own UI launch records for a run that is `running`
   (RunState-level) but whose owning PID is dead, and spawn `ao resume --run-id <id>`
   detached. At most one auto-resume attempt per run per boot, with cross-boot bookkeeping
   so a poisoned run cannot loop-resume across rapid restart cycles.
6. A small hub on a fixed control port (default **8770** — distinct from `UI_DEFAULT_PORT`
   8765, which is untouched): an HTML index of workspaces/ports/run summaries, plus
   `GET /api/service/status` JSON. `ao service status` reads that API when the daemon is up,
   else falls back to registry + state files. Loopback-only, same unauthenticated posture
   and startup warning as `ao ui`.
7. `ao service install` writes a **user**-level systemd unit
   (`~/.config/systemd/user/ao.service`); `--print` only prints it. `ao service start/stop`
   shell out to `systemctl --user` when available, else print manual-run guidance. No
   systemd is invoked in tests — unit-file generation is tested as text only.
8. Standalone `ao ui` is unchanged.

## 3. Prior-art reuse audit

Two things were verified against the existing codebase before any new code was written,
because getting them wrong would mean solving an already-solved problem twice or badly:

### 3.1 Do agent runs already survive their parent dying?

Yes. `ui/processes.py::ProcessSupervisor._spawn` starts every `ao run`/`ao resume` child
with `start_new_session=True` and `stdin=subprocess.DEVNULL`, and does **not** hold a
reference across process restarts (a fresh `ProcessSupervisor` instance has an empty
`_children` dict). `start_new_session` makes the run its own session/process-group leader
with no controlling terminal; if its direct parent (an `ao ui` child, or eventually a
supervisor-spawned resume) exits, the OS reparents the orphan to init/subreaper, which reaps
it on exit. `ProcessSupervisor._alive()` already has the fallback path for exactly this case
("a launch inherited from a previous dashboard process... fall back to the signal probe").
**No change to `ui/processes.py` is required** — the file is not touched by this epic.

The one place this reasoning has a documented caveat is systemd's `KillMode` — see §6.5.

### 3.2 Is there an existing sub-CLI pattern to extend?

No `add_typer` sub-app exists anywhere in the codebase yet — every `ao` command
(`cli.py`) is a flat `@app.command()`, and the one other Typer app (`ao-bench`,
`bench/cli.py`) is registered as its own **separate** console-script entry point, not nested
under `ao`. `ao service <subcommand>` is the first nested sub-app; `service/cli.py` defines
its own `typer.Typer()` and `cli.py` gains exactly one line:
`app.add_typer(service_cli.app, name="service")`.

## 4. Module layout

All new code lives under `src/agent_orchestrator/service/` (a new top-level package, not
under `ui/`, because it manages `ui/` instances rather than being one) — kept fully separate
from `models.py`, `executors/`, `spec.py`, `validate.py`, `specs/*.schema.json`, `templates/`,
which other in-flight work owns.

```
service/
  __init__.py     — package docstring, re-exports
  paths.py        — XDG config/state dir resolution + env override constants
  registry.py     — WorkspaceEntry/ServiceRegistryFile (pydantic), atomic + file-locked
                    (flock) load/save, read-lock-merge-write for concurrent writers
  ports.py        — P1>P2>P3 resolution, tier-first conflict detection, free-port probe,
                    persistence (via registry.py's locked save, not a bare write)
  boot_resume.py  — resumable-run scan (reuses ProcessSupervisor.reconcile + RunRepository),
                    cross-boot guard/bookkeeping (attempts, cooldown, per-boot dedup,
                    immediate-before-spawn idempotency re-check), best-effort OS-boot-id read
  supervisor.py   — Supervisor: singleton lock, orphan reclamation, spawns/monitors/
                    restarts ao-ui children with backoff + EADDRINUSE backstop, orchestrates
                    boot-resume, owns the SIGTERM shutdown path
  hub.py          — FastAPI hub app (HTML index + /api/service/status); lazy fastapi import
  systemd.py      — unit-file text generation + install/start/stop helpers
  cli.py          — `service` Typer sub-app: add/remove/list/status/install/start/stop/run
```

`project_config.py` gains one nested model:

```python
class UIConfig(BaseModel):
    port: int | None = None

class ProjectConfig(BaseModel):
    ...
    ui: UIConfig = UIConfig()
```

Pydantic v2's default `extra="ignore"` (unchanged, nothing to configure) means an `ao`
binary predating this epic loading a config with an unfamiliar `ui:` key is unaffected, and
this `ao` loading an old config with no `ui:` key gets `UIConfig()` defaults — both
directions byte-compatible. No `specs/*.schema.json` file describes `ProjectConfig` (that
schema machinery is for workflow/reposet/agent specs only), so none needs updating.

## 5. Data model

### 5.1 Registry file — `~/.config/ao/service.yaml`

```yaml
workspaces:
  - root: /home/user/proj1        # absolute path, the single source of truth for identity
    port: 8801                    # optional pin (P2); null/absent = resolved at boot (P3)
    autoresume: true              # default true
  - root: /home/user/proj2
    port: null
    autoresume: true
```

`ServiceRegistry.load`/`.save` mirror `RunStateStore`'s atomic write-then-rename pattern
(`tmp` file + `os.replace`) so a crash mid-write cannot corrupt the registry a running
supervisor is about to reload.

### 5.2 Runtime state — `~/.local/state/ao/service/`

- `supervisor.lock` — held (`flock`, exclusive, non-blocking) for the process lifetime of
  `ao service run`. A second instance that cannot acquire it fails fast with a clear error
  naming the holder, rather than running a silent split-brain second supervisor (early-gate
  correction #3 — see ADR-0012). Released automatically on process exit however it dies,
  which is exactly the property needed: it is not itself the orphan-reclamation mechanism
  (that needs finer-grained per-child bookkeeping, next bullet), but it is what prevents two
  *live* supervisors from both acting at once.
- `supervisor.json` — `{pid, boot_id, os_boot_id, started_at, hub_port, children:
  [{root, port, pid}]}`, written on `ao service run` startup and kept current, used both by
  `ao service status`/`stop` as a fallback when the hub HTTP API is unreachable, and by the
  *next* boot's orphan-reclamation step (§6.1/§6.3) to find a previous incarnation's
  still-alive children after an ungraceful crash. `os_boot_id` is a best-effort read of the
  OS's own boot identifier (Linux: `/proc/sys/kernel/random/boot_id`; `None` where
  unavailable) — an additional, independent signal alongside PID liveness that a "dead PID"
  determination really does span a reboot and not just a PID-reuse coincidence within one
  continuous uptime.
- `boot_resume.json` — per `(workspace_root, run_id)`: `attempts`, `last_boot_id`,
  `last_attempted_at`, `cooldown_until`. See §6.4.
- `port_resolution.json` — the last port-resolution pass's conflicts (for `ao service
  status` to surface even when read from disk rather than the live API).

Both directories independently honor an env override (`AO_SERVICE_CONFIG` for the exact
registry file path, `AO_SERVICE_STATE_DIR` for the state directory) precisely so tests never
touch a real `$HOME`. Registry **writes** (both `ao service add/remove` and the supervisor's
own port-persistence step) go through the same file lock and a read-lock-merge-write cycle,
not bare load-then-save — atomic write-then-rename prevents corruption but not a lost update
(early-gate correction #2), so every writer re-reads the latest file under the lock before
merging its own change and saving.

## 6. Supervisor behavior

### 6.1 Startup sequence (`ao service run`)

0. **Acquire `<state_dir>/supervisor.lock` (exclusive, non-blocking).** Fail fast with a
   clear "another `ao service run` appears to be active (pid N)" error on contention —
   this is the singleton guard (early-gate correction #3): nothing downstream is safe to
   run twice concurrently against the same registry/state files.
1. Load the registry (empty registry ⇒ supervisor idles serving only the hub, not an error).
2. Resolve a port per workspace (§6.2); persist any newly-picked random ports back to the
   registry immediately via the read-lock-merge-write path (§5.2), so a crash right after
   does not re-pick different ports next boot and does not clobber a concurrent
   `ao service add`.
3. **Orphan reclamation.** Read the *previous* `supervisor.json` (if any) written before
   this boot's own — a stale one is evidence of an ungraceful previous exit, since a
   graceful `shutdown()` clears it. For each of its recorded `{root, port, pid}` children
   whose `(root, port)` still matches this boot's resolved assignment, best-effort confirm
   the pid is both alive and plausibly the same process (Linux: match `/proc/<pid>/cmdline`
   against the expected `ao ui --workspace <root> ... --port <port>` argv; degrade to
   "trust the pid liveness check alone" where `/proc` is unavailable) and `SIGTERM` it
   before spawning a replacement (early-gate correction #4 — see ADR-0012). This is
   distinct from the port-conflict logic in §6.2, which only reconciles ports among
   workspaces newly resolving *this* boot; without this step a crash-restart cycle
   EADDRINUSE-loops against its own orphaned children forever.
4. Boot-resume scan (§6.4) and spawn approved `ao resume` invocations, detached — this
   step, including its idempotency re-check, runs **only here**, once, never on a later
   monitor-loop tick (§6.4 makes this explicit to remove any ambiguity between "at boot"
   and "periodically").
5. Spawn one `ao ui` child per workspace (`[ao_executable, "ui", "--workspace", root,
   "--host", "127.0.0.1", "--port", str(port)]`, `start_new_session=True`, `Popen` retained
   for `poll()`-based reaping — identical pattern to `ProcessSupervisor._spawn`; stdout/
   stderr redirected to a per-workspace log file under `<state_dir>/logs/`, mirroring
   `ui/processes.py`'s own `logs_dir` convention, so a failing child is diagnosable from
   more than a bare exit code). A small fixed delay (order of hundreds of ms) between
   successive spawns bounds the worst case of every registered workspace's boot-resume
   *and* dashboard-spawn happening in the same instant (thundering-herd mitigation —
   deliberately a fixed constant, not a general concurrency-limiter subsystem).
   `supervisor.json` is updated with each spawned child's `{root, port, pid}` as it starts.
6. Start the hub server (uvicorn, loopback, fixed/overridable port) on a background thread;
   a bind failure here is fatal to startup (logged and raised), never silently swallowed —
   a supervisor with a dead hub thread is a supervisor an operator has no way to observe.
7. Install `SIGTERM`/`SIGINT` handlers; run the monitor loop until a stop is requested.

### 6.2 Port resolution (P1 > P2 > P3)

For each registered workspace, in registry order:

1. **P1** — load `.ao/config.yaml` if present; if `ui.port` is set, that wins outright
   (an explicit per-project pin always wins, even over a registry pin — the workspace owns
   its own config).
2. **P2** — else the registry entry's `port`, if set.
3. **P3** — else bind `("127.0.0.1", 0)` to let the OS pick a free port, and persist that
   pick into the registry entry's `port` so it is stable on the next boot (an operator who
   never chose a port should not see the dashboard move every restart).

**Conflict handling**, applied after every workspace's P1/P2/P3 value is computed. The
tie-break is **tier-first, registry-order second** — a lower-precedence pick must never
displace a higher-precedence one just because it was resolved earlier (early-gate
correction #1; the original registry-order-only rule contradicted its own stated rationale
that a workspace's own P1 pin should never be silently overridden):

- Among all workspaces resolving to the same numeric port, the one with the
  highest-precedence source (P1 beats P2 beats P3) keeps it. Ties within the same tier
  (e.g. two P2 pins colliding) fall back to registry order — first keeps it.
- Every workspace that loses its slot is *re-resolved* one tier down from where it lost
  (a P2 that lost to a P1 falls to P3, not back to a re-check of P2) and the conflict is
  recorded.
- A P1/P2 port that is not actually free right now (probed with a real bind to the
  *specific* port, not `bind(0)`) is treated the same way: the workspace that can't get its
  own requested port falls back to P3, and the conflict is recorded. This is the resolution-
  time check; §6.3 covers the complementary spawn-time backstop for a port that looked free
  at resolution but fails to bind when the child actually starts.

Every recorded conflict is written to `port_resolution.json` and included verbatim in the
hub's `/api/service/status` payload and in `ao service status`'s fallback path.

### 6.2a Bind-host resolution (per workspace)

Added post-epic (same change set as the first real migration, 2026-08-28): two live
pre-service deployments (`ao-runner-finplan`, `ao-runner-ai-models`) bind `0.0.0.0` by
explicit operator request, so a loopback-only supervisor would have silently reverted a
deliberate choice on migration.

Each child's `--host` resolves like ports, minus the random tier and persistence
(absence just means loopback): **P1** workspace `.ao/config.yaml` `ui.host` > **P2**
registry entry `host` (settable via `ao service add --host`) > default `127.0.0.1`
(`ports.resolve_host`). A non-loopback resolution logs the same UNAUTHENTICATED-exposure
warning `ao ui --host` prints, once per child at spawn. The hub links `0.0.0.0`/`::`
binds via `127.0.0.1` (a bind address is not a connectable URL) and annotates the actual
bind; explicit LAN hosts are linked as-is.

### 6.3 Child monitoring and restart backoff

Each managed child (`ManagedChild`: workspace root, port, `Popen`, restart count, next-retry
time, `last_error`) is polled once per monitor-loop tick (`poll()` — never a bare
PID-liveness probe, for the same zombie-reaping reason `ProcessSupervisor` already
documents). A child that exited is restarted after an exponential backoff (base 1s, ×2,
capped at 60s), and the backoff resets once a restarted child has stayed up past a stability
window (30s) — a flapping child does not get restarted in a tight loop, and a child that
failed once transiently is not penalized forever.

**EADDRINUSE backstop.** A child that exits *fast* (within a short startup-grace window,
e.g. 3s — distinct from the stability window above, which is about staying up, not starting
up) on several consecutive restarts is not a flapping-but-eventually-fine process; it is a
port that resolution-time couldn't detect as busy (a genuine external conflict, not a
reclaimable orphan — orphan reclamation already ran once at §6.1 step 3 and only runs at
boot) but that is busy in practice. After a bounded number of fast-fail cycles (default 3,
named constant), the workspace is re-resolved through P3 (bypassing its P1/P2 pin for this
boot only — the registry's pin is left untouched, so the next boot tries the pin again
fresh) and the reassignment is surfaced in status as a distinct condition, not retried
identically forever.

### 6.4 Boot-resume: scan, decide, act

**Scan** (`boot_resume.scan_resumable_runs`) reuses two existing pieces rather than
reimplementing PID liveness:

```
records = ProcessSupervisor(workspace_root).reconcile()   # marks dead-PID records finished
by_run = group_by(records, key=run_id, skipping=run_id is None)   # newest-first preserved
for run_id, run_records in by_run:
    if any(r.finished_at is None for r in run_records):
        continue                       # some launch of this run is still alive — leave it
    state = RunRepository(workspace_root).load_state(run_id)
    if state.status == "running":      # engine never got to mark it terminal
        yield ResumeCandidate(workspace_root, run_id, *_recover_spec_paths(run_records))
```

This only considers runs that were launched **through the dashboard** (they have a
persisted `LaunchRecord`, hence a known PID to have checked); a run started from a bare
terminal `ao run` has no PID the service can observe and is out of scope — documented
limitation, not a gap in the reused mechanism.

**The candidate must carry the original launch's spec paths, and grouping is what makes
that reliable** (both learned from the first live deployment, 2026-08-29 — see the epic
`STATUS.md` post-epic defect entry). A resume spawned with `--run-id` alone only works in a
workspace whose `.ao/config.yaml` sets a global `workflow`; the live runner workspaces
deliberately do not (each epic owns its own workflow file), so the child exits at once with
`--workflow is required`. `ResumeCandidate` therefore also carries `workflow_path` (a
first-class `LaunchRecord` field) plus `reposets`/`agents`, which have no record field and
are read back out of the recorded argv.

Grouping matters because a single run accumulates **one record per launch attempt**, and
those records do not carry equal information — a resume spawned by a *pre-fix* boot-resume
persisted a record with `workflow_path=None` and a bare `--run-id`-only argv. Emitting one
candidate per record and leaving `BootResumeGuard`'s `run_id` dedup to pick a winner would
select whichever record sorts newest, i.e. exactly those information-free ones on any
workspace that already hit the bug. So: **one candidate per run**, with each spec path taken
independently from the newest record that actually carries it (`_recover_spec_paths`;
`workflow_path` also falls back to its own `--workflow` argv flag). For the same reason
liveness is evaluated across *all* of a run's records rather than per record — a live resume
child alongside an older finished record must not yield a candidate.

**Decide** (`boot_resume.BootResumeGuard`) applies bookkeeping loaded from
`boot_resume.json`, keyed by `f"{workspace_root}|{run_id}"`:

- Skip if `last_boot_id` already equals the current boot's id (dedup within one boot — the
  monitor loop must not re-attempt a run it already tried this boot on a later tick).
- Skip if `attempts >= MAX_AUTO_RESUME_ATTEMPTS` (default 3) — quarantined; needs a manual
  `ao resume`. Surfaced in `ao service status`.
- Skip if `now < cooldown_until` (default cooldown 60s from the last attempt) — this is what
  stops a poisoned run from loop-resuming across *rapid* supervisor restart cycles (each
  restart gets a fresh `boot_id`, so the per-boot dedup alone cannot catch this; the cooldown
  is deliberately keyed by wall-clock time, not boot count).
- Otherwise: approve, and record `attempts += 1`, `last_boot_id = current`,
  `last_attempted_at = now`, `cooldown_until = now + COOLDOWN`.

**Act**: for each approved candidate, **immediately before spawning** — not earlier in the
scan — re-check `ProcessSupervisor(workspace_root).is_running(run_id)` and re-load
`RunState.status`; if either now shows the run alive/terminal, skip without recording an
attempt (early-gate correction #1). This closes the window between "scan observed a dead
PID" and "act spawns a resume" during which a human could already have clicked Resume from
that workspace's own dashboard, or the engine could have exited cleanly in between — without
it, boot-resume can hand a second engine invocation the same run state. Only then call
`ProcessSupervisor(workspace_root).launch_resume(run_id)` — reusing the exact "spawn
`ao resume --run-id <id>`, detached, with a fresh persisted `LaunchRecord`" mechanism the
dashboard already uses for a user-clicked Resume, so the resumed run is immediately
visible/cancellable from that workspace's own dashboard UI too.

Both the clock and the pid-liveness probe used through `reconcile()` are injectable, so the
decision logic is deterministic and testable against fixture run states/launch records
without real subprocesses or real time. `reconcile()`'s three behaviors this module depends
on but does not itself define — it returns *every* persisted record (not just live ones),
it is what sets `finished_at` on a dead-PID record, and it leaves `exit_code` at `None` for
a record no `Popen` in this process ever tracked — are pinned by characterization tests in
this module's own test file (§10), not assumed silently, since `ui/processes.py` is out of
scope for this epic and could change those behaviors for dashboard-only reasons.

`supervisor.json`'s `os_boot_id` (§5.2) is consulted as a secondary, best-effort signal
alongside PID liveness: it does not change the APPROVE/SKIP decision by itself (a missing
or unreadable boot id must never block a real reboot's resume, which is the epic's headline
scenario), but a *changed* OS boot id since the bookkeeping entry's `last_attempted_at`
is stronger evidence that "PID is dead" really does mean "the machine rebooted," not a
same-uptime PID-reuse coincidence, and is included in the recorded decision's rationale for
`ao service status` to display.

### 6.5 Shutdown

`SIGTERM`/`SIGINT` sets a stop event. The monitor loop, once woken, sends `SIGTERM` (not a
process-group signal) to each `ao ui` child's own PID **only** — deliberately not the
process group, and deliberately not touching whatever the child itself spawned — waits up to
a grace period, `SIGKILL`s stragglers, reaps them, stops the hub server, and exits. Agent-run
subprocesses are never signaled by the supervisor at all; §3.1 established they survive by
already being detached from any process this service manages.

**systemd caveat, addressed in the generated unit (§7):** systemd's default `KillMode` is
`control-group`, which signals *every* process in the unit's cgroup on stop — including a
detached grandchild agent run, since `start_new_session` escapes the process group/session
but **not** the cgroup. Left at the default, `systemctl --user stop ao` would kill in-flight
runs despite everything in §3.1/§6.5 being correct at the process-group level. The generated
unit therefore sets `KillMode=process` explicitly, so systemd only ever signals the
supervisor's own PID and the supervisor's own shutdown path (which deliberately does not
touch run subprocesses) is what actually governs what stops.

## 7. systemd unit

`ao service install [--print] [--hub-port N]`:

```ini
[Unit]
Description=Agent Orchestrator multi-workspace service
After=network.target

[Service]
Type=simple
ExecStart=<absolute path to the running `ao`> service run --hub-port <N>
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=120
StartLimitBurst=5
TimeoutStopSec=30
KillMode=process
EnvironmentFile=-%h/.config/ao/service.env

[Install]
WantedBy=default.target
```

`<absolute path to the running ao>` resolution order: `sys.argv[0]` if its basename is `ao`
**and** the path `is_absolute()` (true for the normal console-script invocation; a relative
`sys.argv[0]` is rejected the same as a missing one — systemd requires an absolute
`ExecStart`) → `shutil.which("ao")` → error (a `python -m` dev invocation has no stable
absolute executable path to hand to systemd; installing from that context is refused with a
clear message rather than writing a unit that cannot run unattended). This mechanism is a
locked design decision (not revisited by the early-gate review's own separate observation
that a globally-installed `ao` can be a stale snapshot of a different version — see
`meta/ROADMAP.md` §4's existing "Global `ao` installs are snapshots" entry); `install`'s
printed guidance names that risk explicitly and points at `install.sh --force` as the
existing fix, rather than silently working around it by switching mechanisms.

`--hub-port <N>` is always rendered explicitly (default 8770 if `--hub-port` was not passed
to `install`), never left implicit — an implicit default that later changed would silently
desync a previously-installed unit from a new binary's default.

`EnvironmentFile=-%h/.config/ao/service.env` (leading `-` = optional, missing file is not a
startup failure) is new versus the original plan: the systemd **user manager**'s own
environment is minimal (no login shell profile), so without this, agent credentials
(whatever the `claude` CLI or an agent's executor needs) that a normal interactive `ao ui`
inherits from the operator's shell are simply absent for a boot-resumed run, and
authentication fails silently on the very first reboot. `install`'s printed guidance
mentions this file explicitly.

`RestartSec`/`StartLimitIntervalSec`/`StartLimitBurst` matter because systemd's own default
restart-storm guard (5 restarts in 10s) is tighter than this supervisor's own internal child
backoff (§6.3) operates on — without raising it, a supervisor that itself crash-loops on
startup (e.g. a config problem) hits systemd's default limit and lands in a permanent
`failed` unit state well before an operator would expect. `TimeoutStopSec=30` bounds how
long systemd waits for a graceful stop (§6.5's own grace period is shorter) before it would
otherwise escalate — kept comfortably above the supervisor's internal grace period rather
than racing it.

`--print` only prints the rendered unit (no file write — safe to run in CI/tests).
Otherwise it writes to `~/.config/systemd/user/ao.service` (`XDG_CONFIG_HOME`-respecting)
and prints next-step guidance: `systemctl --user daemon-reload && systemctl --user enable
--now ao`, a note that headless boxes need `loginctl enable-linger <user>` for the user
unit to start without an active login session (and to survive `systemd-logind`'s post-logout
user-scope cleanup, not just to enable boot-time start), and a note about populating
`~/.config/ao/service.env` with any credentials boot-resumed runs will need.

`ao service start`/`stop` shell out to `systemctl --user start/stop ao` when
`shutil.which("systemctl")` is truthy; otherwise they print guidance to run `ao service run`
directly (no systemd on this box). **No test invokes real `systemctl`** — unit-file
generation is asserted as text (contains `KillMode=process`, the right `ExecStart`, etc.),
and start/stop are tested by injecting a fake "systemctl found/not found" resolver.

## 8. Hub

`GET /` — minimal HTML: one row per registered workspace (root, resolved port, a link to
`http://127.0.0.1:<port>/`), plus a coarse run-count summary per workspace (reusing
`RunRepository.aggregate()` via the same `status_provider` callable `/api/service/status`
uses — **not** recomputed a second way — behind a short TTL cache, e.g. 5s, injectable clock,
since `aggregate()` fully parses every `state.json` in a workspace and the hub's own poller
plus every browser tab polling it would otherwise repeat that work on every request).
`GET /api/service/status` — JSON: per-workspace `{root, port, pid, state, restart_count,
last_error}`, `conflicts` (from §6.2), `boot_resume` (recent decisions), `supervisor_pid`,
`hub_port`, `uptime_seconds`.

Same posture as `ao ui`: loopback-only bind, no authentication, a startup warning if bound
elsewhere. This is enforced, not aspirational — the hub app **mounts**
`ui.security.SecurityMiddleware` (`app.add_middleware(SecurityMiddleware, allowed_hosts=
resolve_allowed_hosts(...))`), identically to how `ui/app.py::create_app` already does it,
reusing `ui.security`'s constants and resolver rather than re-deriving a second allowlist
(early-gate correction #6 — importing the constants without attaching the middleware
enforces nothing). `fastapi`/`uvicorn` are imported lazily inside `hub.py`/the `ao service
run` command body only — a core `ao` install (no `[ui]` extra) keeps working for `service
add/remove/list/status/install` (status falls back to the registry/state-file path when the
hub is unreachable, which also covers "extra not installed" the same way as "daemon not
running").

## 9. CLI surface

```
ao service add <dir> [--port N] [--no-autoresume]
ao service remove <dir>
ao service list
ao service status
ao service install [--print] [--hub-port N]
ao service start
ao service stop
ao service run [--hub-port N]
```

`cli.py` changes by exactly one import + one line:
`from .service.cli import app as service_app` / `app.add_typer(service_app, name="service")`.

`status`/`list`'s live-vs-fallback probe reads the hub port to check from the **persisted**
`supervisor.json.hub_port` (§5.2), never from the invoking CLI invocation's own
`--hub-port`/default — the daemon may have been started with a non-default `--hub-port`
by a different invocation entirely (early-gate correction, reviewer finding #4); the
persisted value is the only source of truth for "what port is the running daemon actually
on."

## 10. Testing (see epic ticket for task-level mapping)

- **Unit**: registry load/save round-trip (incl. XDG/env-override path resolution) *and*
  its read-lock-merge-write behavior under a simulated concurrent writer; port resolution
  P1>P2>P3 including tier-first conflict fallback and persistence of random picks;
  boot-resume decision logic against fixture run states/launch records with fake
  clocks/pids (per-boot dedup, cooldown, max-attempts quarantine, the immediate-before-spawn
  idempotency re-check); characterization tests pinning the three `reconcile()` behaviors
  boot-resume depends on (returns all records, sets `finished_at` on dead PIDs, leaves
  `exit_code` at `None` for untracked records); systemd unit-file text generation
  (`KillMode=process`, `EnvironmentFile`, `RestartSec`/`StartLimit*`, `ExecStart` resolution
  branches including the `is_absolute()` rejection case).
- **Integration**: supervisor spawning/monitoring/restarting-with-backoff fake children
  (`sh -c` scripts, not real `ao ui`/uvicorn — deterministic, no network, no `[ui]` extra
  needed); the EADDRINUSE fast-fail-then-reassign backstop; orphan reclamation (a fake
  previous `supervisor.json` naming a still-alive fake process, confirmed SIGTERM'd before
  a replacement spawns); the singleton lock (a second `Supervisor.start()` against a
  held lock fails fast rather than blocking or silently proceeding); graceful SIGTERM
  shutdown stops children but leaves a deliberately-detached decoy process running (proves
  the "runs survive" property directly, not just by inspection).
- **E2E (CliRunner)**: `add`/`list`/`remove`/`status`/`install --print` against a temp
  registry (`AO_SERVICE_CONFIG`/`AO_SERVICE_STATE_DIR` env-overridden) — no real `~`, no
  systemd, no real subprocess daemon. Hub tests separately assert `SecurityMiddleware` is
  actually mounted (a disallowed `Host` header gets a 421, not just "the allowlist constant
  is importable").

## 11. Non-goals (this epic)

- Authentication on the hub or the per-workspace dashboards (same deferred posture as `ao
  ui`, tracked in `meta/ROADMAP.md` §3.1).
- Write-conflict detection between workspaces sharing a filesystem location (out of scope;
  workspaces are assumed disjoint roots, same assumption as everywhere else in the project).
- A Windows/macOS-launchd equivalent of the systemd unit.
- Live-streaming hub UI (polling only, same as the per-workspace dashboard today).
