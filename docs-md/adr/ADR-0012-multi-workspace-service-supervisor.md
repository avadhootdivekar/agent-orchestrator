# ADR-0012 — Multi-workspace service: one supervisor, per-workspace child processes, detached runs

- Status: **Accepted / shipped** (2026-08-28) — design locked by user before implementation, not re-litigated; implemented, early-gate + final-gate reviewed, KillMode=process empirically verified against a real systemd unit
- Date: 2026-08-28
- Deciders: Avadhoot Divekar (user), Claude (dev-epic role)
- Related: **ADR-0010** (dashboard architecture — the subprocess-launch and loopback-security
  posture this ADR extends rather than replaces) · design
  [`multi-workspace-service-hld.md`](../multi-workspace-service-hld.md) ·
  [`meta/ROADMAP.md`](../../meta/ROADMAP.md) §2a (shipped) / §3.1/§3.6 (still-open adjacent gaps)

## Context

`ao ui` is one `uvicorn` process for one workspace on one port. Running the dashboard for N
workspaces meant N hand-run terminals or N hand-written systemd units, with ports picked by
hand and no shared story for "come back after a reboot." The ask was a single user-level
service that serves every registered workspace on its own port, self-heals crashed dashboard
processes, and resumes interrupted runs after a reboot — without per-workspace scripting.

Four decisions were coupled enough to record together.

---

## D1 — Supervisor spawns one child `ao ui` process per workspace; it does not multi-bind one process

**Decision.** `ao service run` is a long-lived supervisor that shells out to
`ao ui --workspace <root> --port <port>` once per registered workspace, monitors each child,
and restarts a crashed one with backoff. It does **not** grow `ui/app.py` into a
multi-tenant, multi-port ASGI app inside one process.

**Why not one multi-bound process.** A single process serving N workspaces means one bad
workspace (a corrupt `state.json`, a pathological file-browse request, an unhandled
exception in one workspace's route) can take down every other workspace's dashboard too, and
a crash-restart cycle restarts everyone's UI at once, including workspaces with fine health.
Per-workspace processes give each workspace exactly the failure isolation `ao ui` already
has today (ADR-0010 D4: "a crashing run takes the UI down with it" was already accepted for
a single workspace; multiplying that blast radius across every registered workspace would be
a regression, not neutral).

**Why reuse `ao ui` wholesale instead of a new lighter app.** `create_app`/`DashboardService`
already carry all of ADR-0010's decisions (subprocess run launch, loopback default,
`SecurityMiddleware`, launch-record persistence). Building a second, parallel dashboard
server for the multi-workspace case would either duplicate all of that or drift from it.
Spawning the existing CLI command is the same "single execution path through the engine"
argument ADR-0010 D4 already made for runs, applied one level up: the supervisor drives the
same `ao ui` an operator would type, so there is exactly one code path to keep correct.

**Consequence.** The supervisor's child-management code (`service/supervisor.py`) is a
process manager over `Popen` objects, structurally identical in shape to
`ui/processes.py::ProcessSupervisor` (retain `Popen`, `poll()`-reap, never a bare PID-alive
check for a tracked child) — deliberately, not by coincidence; the zombie-reaping lesson that
module's docstring records applies unchanged here.

---

## D2 — SIGTERM to the supervisor stops dashboards, never agent runs; the mechanism is "never touch it," not "explicitly spare it"

**Decision.** The supervisor's shutdown path signals each `ao ui` child's own PID directly
(not a process group). It never enumerates or signals whatever that child, or a run, may
itself have spawned.

**Why this is safe by construction, not by a special case.** `ui/processes.py::_spawn`
already starts every `ao run`/`ao resume` child with `start_new_session=True`, which makes it
its own session and process-group leader. Signaling a process group only ever reaches the
group's own members; a detached grandchild in a different session was never a candidate for
group-signal delivery in the first place, and the supervisor's shutdown path does not even
attempt a group-wide signal — it targets one PID (the `ao ui` child) and stops there. This
was verified by reading `ProcessSupervisor._alive()`'s own docstring, which already documents
the exact "orphan reparented to init, reaped by it" scenario this ADR relies on, rather than
being newly asserted here.

**The one place this reasoning needed a correction: systemd `KillMode`.** systemd's default
`KillMode=control-group` sends the stop signal to *every* process in the unit's cgroup — and
`start_new_session` escapes a process group/session, but **not** a cgroup. Left at the
default, `systemctl --user stop ao` would kill in-flight agent runs despite D2 being correct
at the process level, silently reintroducing the exact failure this feature exists to
prevent. The generated unit (§ADR text below, HLD §7) sets `KillMode=process` explicitly, so
systemd signals only the supervisor's own main PID and D2's shutdown path is what actually
governs what stops. This was caught during design, before any code was written, specifically
by asking "what actually delivers the SIGTERM in the real deployment target" rather than
stopping at "the Python-level signal call looks right."

**Consequence.** A run that outlives its dashboard (crash, restart, or `systemctl --user stop
ao`) is expected and is picked back up by boot-resume (D4) on the next boot that finds it
still marked `running` with a dead owning PID — not treated as a bug to prevent.

---

## D3 — Port resolution is P1 (per-workspace config) > P2 (registry pin) > P3 (random, persisted); conflicts fall back the *later* claimant, never the earlier one

**Decision.** `.ao/config.yaml`'s new `ui.port` always wins for that workspace, over
whatever the service registry says. A workspace with no opinion falls back to whatever the
registry operator pinned. A workspace with neither gets a random free port that is written
back into the registry immediately, so it stays stable across restarts. Two workspaces
resolving to the same port are resolved deterministically: **first in registry order keeps
the port; every later one is re-resolved as if unpinned.**

**Why per-workspace config outranks the registry.** The registry is a *service-level*
concern (which workspaces this service happens to know about); `.ao/config.yaml` is the
workspace's own declaration, portable across however many services or scripts might serve
it. A workspace author who has already pinned a port for their own reasons (a bookmarked URL,
a firewall rule, a reverse-proxy config) should not have that silently overridden by whichever
order they happened to run `ao service add` in.

**Why persist the random pick rather than re-roll every boot.** A workspace with no pin at
all still deserves a stable URL across restarts — re-rolling on every boot would make "the
dashboard moved again" a routine annoyance for the one class of workspace (unpinned) that
was already the lowest-friction case to fix permanently: write the pick down once.

**Why the earlier registry entry wins a collision, not the later one.** Registry order is the
only deterministic, config-driven ordering available (no other priority signal exists between
two P1/P2-tied workspaces) — an arbitrary-but-stable rule beats an arbitrary-and-unstable one
(e.g., a re-resolution based on process/scan order, which is not guaranteed stable run to
run). Whichever workspace loses a collision is always re-resolved through P3 and its pick is
persisted too, so a collision self-heals into two stable, distinct ports rather than
recurring every boot.

**Consequence.** `ao service status`/the hub API must surface every conflict it resolved,
not just log it, per the original requirement ("surface it in `ao service status`") — a
silently-relocated dashboard is worse than a logged one, because the operator has no way to
discover the new URL otherwise.

---

## D4 — Boot-resume reuses `ProcessSupervisor.reconcile()` + `RunRepository`, and is scoped to dashboard-launched runs only

**Decision.** A run is a boot-resume candidate only if it has a `LaunchRecord` (i.e. it was
started through some `ao ui` instance, past or present) whose PID `reconcile()` finds dead,
**and** its `RunState.status` is still `"running"`. The scan performs no new PID-liveness
logic of its own.

**Why scope to dashboard-launched runs.** A bare-terminal `ao run` has no persisted PID
anywhere the service can observe — there is no artifact to check liveness against without
inventing a new one, and inventing a parallel run-tracking mechanism only for this feature
would duplicate `ui/processes.py`'s job. Runs going forward are expected to be dashboard- or
service-launched in a `ao service`-managed workspace; a terminal-launched run in such a
workspace is documented as out of scope for auto-resume (HLD §6.4), not silently mishandled.

**Why reuse `reconcile()` instead of a fresh liveness check.** `reconcile()` already
implements "mark a persisted launch record finished if its PID is gone," using the same
zombie-safe reaping path a tracked child gets and the same signal-probe fallback an inherited
(untracked) one gets — which is exactly the situation a freshly-booted supervisor is in with
respect to every run any previous process (however many restarts ago) launched. Reimplementing
this would risk a second, subtly different liveness definition drifting from the one the
dashboard itself relies on for its own "is this run still going" display.

**Why a cooldown separate from per-boot dedup.** Per-boot dedup (skip if already attempted
this `boot_id`) only prevents redundant attempts *within* one supervisor lifetime; it does
nothing against a supervisor that itself restarts rapidly (each restart mints a fresh
`boot_id`). A wall-clock cooldown, plus a hard cap on total attempts before quarantining the
run for manual `ao resume`, is what actually bounds the cost of a poisoned run that dies
again within seconds of every resume attempt.

**Consequence.** Boot-resume bookkeeping (`boot_resume.json`) is state, not registry
configuration — it belongs under `~/.local/state/ao/`, not `~/.config/ao/service.yaml`,
matching the config/state split D3 already draws for ports vs. runtime bookkeeping.

---

## Early-gate corrections (2026-08-28)

A `reviewer` and an `architect` pass were run in parallel against this ADR, the HLD, and
all four task tickets before any implementation started (dev-epic's mandatory early gate).
Both independently verified every factual claim about existing code
(`ui/processes.py`, `ui/security.py`, `project_config.py`) against the real source rather
than trusting the HLD's description of it. Neither proposed changing D1-D4 themselves;
both found gaps in what D1-D4 need to *actually hold* once real processes, real crashes,
and a real systemd user manager are involved. Six corrections were folded into the HLD and
task tickets as a result (HLD sections cited are post-correction):

1. **Boot-resume needs an idempotency check immediately before spawning, not just a state
   scan.** `ProcessSupervisor.launch_resume` has no "is this run already running" guard of
   its own — that check lives one layer up in the dashboard service, which boot-resume
   bypasses by calling `launch_resume` directly. Without a fresh `is_running`/`RunState`
   re-check right before the spawn, a run resumed by a human from the dashboard in the
   same window a boot-resume approval was already in flight for gets two engines against
   one run state. Added as HLD §6.4 step, `T-Sv9d4k` AC.
2. **Registry writes need read-lock-merge-write, not just atomic rename.** Atomic
   write-then-rename (the pattern borrowed from `runstate.py`) prevents corruption, not a
   lost update — `Supervisor.start()` persisting P3-picked ports from a copy of the
   registry it loaded at boot can silently drop a workspace `ao service add` registered
   concurrently. Added `service/registry.py` file-lock support (HLD §5.1) that every
   registry-mutating caller (CLI commands and the supervisor's port-persistence step)
   goes through.
3. **A singleton lock is required.** Nothing stopped a manually-run `ao service run`
   (which the HLD's own systemd guidance tells operators to do while diagnosing) from
   running concurrently with the systemd-managed instance — both would resolve and persist
   ports, both would boot-resume independently, and the second hub bind would fail
   silently on its background thread. Added an exclusive `flock` on
   `<state_dir>/supervisor.lock`, held for the process lifetime, with a fail-fast clear
   error on contention (HLD §6.1).
4. **Orphan reclamation at startup.** `KillMode=process`'s process-level SIGTERM handling
   (D2) governs a *graceful* stop; it says nothing about an *ungraceful* supervisor
   crash (OOM, unhandled exception, `SIGKILL`), after which `Restart=on-failure` starts a
   fresh supervisor while the old one's `ao ui` children — themselves detached the same
   way run subprocesses are — are still alive and bound to their ports. The new supervisor
   would otherwise EADDRINUSE-loop forever trying to rebind them. Startup now persists
   `(root, port, pid)` per managed child and, before spawning, best-effort-matches and
   SIGTERMs a surviving previous-incarnation child for the same `(root, port)` (HLD §6.1,
   §6.3).
5. **EADDRINUSE needs a backstop distinct from crash-restart backoff.** A port that is
   pinned (P1/P2) but genuinely held by something else forever (not a reclaimable orphan)
   must not backoff-retry the identical doomed port indefinitely. After a bounded number
   of fast (within-grace-window) exits, the workspace is re-resolved via P3 and the
   reassignment is surfaced in status, not silently retried forever (HLD §6.3).
6. **The hub must mount `SecurityMiddleware`, not just import its constants.** The
   original HLD §8 wording ("reuse `ui.security`... rather than re-deriving a second
   allowlist") was import-only in the task ticket as written — an allowlist that is never
   attached to the ASGI app enforces nothing. Corrected to require
   `app.add_middleware(SecurityMiddleware, allowed_hosts=...)`, identical to what
   `ui/app.py::create_app` already does (HLD §8).

Two further items were accepted as **documented, bounded mitigations** rather than full
subsystems, to keep the change narrow: a fixed small inter-spawn delay at boot (cheap
mitigation for N-workspace thundering-herd auto-resume, not a concurrency-limiter
subsystem) and best-effort OS-boot-id recording alongside `boot_id` (strengthens, does not
replace, the existing PID-liveness probe — degrades to today's behavior when unreadable,
e.g. non-Linux). One item was accepted as a **carried limitation, not fixed**: `bind(0)`
ephemeral-port selection for P3 has an inherent (documented, pre-existing per the port
resolution task's own risk note) TOCTOU race against another process grabbing the same
port before the real bind; a reserved fixed port band was considered and deferred as
unneeded complexity for the expected scale (a handful of workspaces per user).

One item is **recorded as a verification gap, not fixed by more code**: `KillMode=process`
is the single most safety-critical line in the whole design, and every test for it
(correctly, per the locked "no systemd in tests" decision) is text-only. A manual
verification against a real `systemctl --user` unit — the same evidentiary standard
`ui/security.py` already sets for its CSP claim (a real headless-browser screenshot, not
just a header-string assertion) — is attempted during the epic's late-gate task if a
systemd user session is available in the execution environment; if unavailable, the two
independent reviews' agreement on the underlying mechanism (cgroup membership survives
`start_new_session`/`setsid()`; only `control-group` `KillMode` walks the cgroup) stands as
the recorded justification, and this gap is carried forward explicitly rather than glossed
over.

## Consequences summary

- New `service/` package; `ui/processes.py` is read, not modified (§HLD 3.1) — the
  "runs already survive parent death" property was verified before writing any new code,
  not assumed.
- The generated systemd unit is the one place this epic makes an assertion about *external*
  process-management semantics (`KillMode=process`); every test for it is text-only — no
  test in this epic invokes real `systemctl`.
- `ao ui` itself (loopback default, `UI_DEFAULT_PORT=8765`, security posture) is byte-for-byte
  unchanged; the service is strictly additive.
