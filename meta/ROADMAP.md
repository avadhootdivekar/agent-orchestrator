# agent-orchestrator — Roadmap

> **Scope of this file.** A high-level status summary plus a 3–6 month forward view. It is
> deliberately *not* a ticket tracker — per-task detail lives in
> [`meta/tickets/`](tickets/), and design detail in [`docs-md/`](../docs-md/). Update the
> status table when an epic closes; revisit the horizon sections roughly quarterly.
>
> Last reviewed: **2026-10-05** (authentication epic E-Da5Tn9 closed; other sections as of 2026-09-07)

---

## 1. Current status (summary)

The orchestrator is a working, self-hosting agent workflow engine: it drives its own
development through `meta/ao/`, has a benchmark harness with published results, and now
ships a browser dashboard.

**Maturity: usable beyond its author, not yet hardened for multi-user or hosted use.**

| Capability | State | Notes |
|---|---|---|
| DAG engine (deps, artifacts, resume, idempotent skip) | **Stable** | Core execution model; resumable from artifacts. |
| Declarative specs + JSON Schema validation | **Stable** | `workflow` / `reposet` / `agents`; `ao validate`. |
| Executors (`claude_cli`, `fake`) behind an ABC | **Stable** | Full per-turn transcript capture. |
| Retries, timeouts, cancellation | **Stable** | |
| Token + USD budgeting, estimator, rate limits | **Stable** | Pre-run gate, post-run reconcile against actuals. |
| Circuit breakers (9 conditions, `hard` / `recommend`) | **Stable** | Operator extension via `ao resume --extend-breaker`. |
| Structured logging, `status.json` snapshots | **Stable** | |
| Dynamic task injection (`emit_tasks`) + loops | **Stable** | |
| Conditional branching / routing (multi-endpoint) | **Stable** | Route cones, `not_taken`, join policies. |
| Quota-exhaustion handling | **Stable** | Waits per episode without consuming retry budget. |
| Agent monitoring + opt-in self-healing | **Stable** | Rule-based default; agent monitor optional. |
| Parallel execution (`max_parallel`) | **Stable, opt-in** | Default 1 = serial. Co-scheduled tasks are still not checked for overlapping outputs *unless* isolation is enabled — see the row below and §4. |
| **Per-task git isolation + rebase integration** | **New, opt-in** | Worktree per task per repo; squash → rebase → verify → CAS-landing onto an ao-owned integration ref; T0-T4 conflict ladder; soft `touches`/hotspot co-scheduling that never withholds a slot. Default off (`isolation: none`). E-Wk9Tz3 / ADR-0013 — §2b. |
| Cost/token accounting per task and per run | **Stable** | Cumulative across retries. |
| Benchmark harness (`ao-bench`, S/M/L tiers, SWE-bench import) | **Stable** | See `docs-md/benchmarking-framework-hld.md`. |
| Installable CLI (`ao`, `ao-bench`) | **Stable** | `install.sh`; snapshot-install semantics. |
| **Browser dashboard (`ao ui`)** | **New** | Files, runs, stats, run control. Opt-in login (local accounts + TOTP), off by default — §3.1. |
| **Multi-workspace service (`ao service`)** | **New** | One supervisor daemon serves every registered workspace's dashboard on its own port; boot-resume for orphaned runs; installable as a user systemd unit. Hub and dashboards can require login (`ao service install --auth`); off by default — §3.1. |
| **General instructions** | **New** | Workspace-scoped rules applied to every task. |
| **Cross-run result cache (`ao run --cache`, `ao cache`)** | **New, opt-in** | Reuses an identical, previously successful task's declared outputs instead of re-dispatching the agent. Double opt-in (operator flag + `cache: true` per task), default off, `shadow` measure-only mode. NOT prompt caching. E-Rc4Hk8 / ADR-0019 — §2c. **Value unproven: G0 not yet run.** |
| Authentication / multi-user | **New, opt-in** | Local accounts + optional TOTP 2FA for `ao ui` and the `ao service` hub (E-Da5Tn9, ADR-0021). Per-realm sessions, no roles/SSO yet — §3.1. |
| Cron / event triggers | **Designed, not built** | `Trigger` model exists; no daemon runs it yet. Service-owned scheduler designed (E-Sc9Rt4, ADR-0014) — §3.2. |

### Recently delivered

- **E-Bt4Xk9 — Benchmark tiers** (2026-07-23): S/M/L/XL tiers, per-tier budgets, parallel
  campaign runner, SWE-bench import/grading.
- **E-IasNXu — Parallel execution** (ADR-0007): opt-in wave/barrier scheduler.
- **E-XyfjuZ — Monitoring & self-healing**: `Monitor` ABC, recommend-mode breakers.
- **E-9h3m7k — Accurate usage metrics**: true per-task/run cost and token totals.
- **E-Ui7Kq2 — Dashboard + general instructions**: see §2.
- **E-Da5Tn9 — Dashboard authentication** (2026-10-05, ADR-0021, `docs-md/dashboard-auth-hld.md`, user guide `docs-md/dashboard-authentication.md`): opt-in local accounts, scrypt passwords, optional TOTP + recovery codes, per-realm in-memory sessions with an API proof header, `ao auth` CLI, hub login page, file-browser denial of the credential stores.
- **E-GIytcL — Multi-workspace service** (2026-08-28): `ao service` supervisor daemon (spawn/monitor/restart per-workspace dashboards, bounded auto-resume, hub, systemd install) — see §2a.
- **E-Rc4Hk8 — Cross-run result cache** (2026-10-05, ADR-0019): opt-in, content-addressed, workspace-local reuse of identical successful tasks' declared outputs; `ao cache` admin commands; `shadow` mode; dashboard tag/tile; `ao-bench` forced off — see §2c.
- **E-Wk9Tz3 — Per-task git isolation** (2026-09-07, ADR-0013): worktree per task, squash+rebase integration behind a compare-and-swap, tiered conflict ladder, `ao hotspots`, `ao prune --worktrees-only` — see §2b.

### Designed, awaiting implementation (2026-09-07)

- **E-Sc9Rt4 — Service-owned scheduler & triggers** (ADR-0014, `docs-md/scheduler-triggers-hld.md`): `ScheduleEngine` inside `ao service`, `.ao/schedules.yaml` bindings, cron/interval + file-watch/webhook/`until` triggers, `skip|queue|allow` overlap, bounded catch-up, at-most-once fire store, `ao run --run-id`, `ao schedule` CLI + dashboard panel. 14 tasks / ~32 d.

---

## 2. Just landed: dashboard & general instructions

**Dashboard (`ao ui`)** — FastAPI + React, served from the installed package:

- Directory and code browsing across the workspace, including hidden and binary files,
  root-scoped and traversal-guarded.
- Run control: start a run from a typed prompt, resume, cancel, delete.
- Stats: workspace-wide totals (runs, tasks, cost, tokens, wall time, actual time) and the
  same breakdown per run, plus a per-task table and the captured CLI log.
- Workspace view showing the effective general instructions and whether each resolves.

**General instructions** — instruction files declared **once per workspace** and applied to
**every task of every run**, even when `ao run` is invoked without any related flag.
Declarable in `.ao/config.yaml`, `AO_GENERAL_INSTRUCTIONS`, `--general-instruction`, or a
workflow's own `general_instructions`; all four layers are **additive**, not a precedence
chain.

**Known limitations, carried forward as roadmap items:**

| Limitation | Consequence | Tracked in |
|---|---|---|
| Authentication is opt-in (off by default) | With it off, the server must stay on loopback; it can read files and spend money. With it on: see §4 residuals | §3.1 |
| Cancel only works for dashboard-launched runs | A run started from another terminal has no PID the dashboard owns | §3 — Run control |
| No file editing in the browser | Read-only browsing | §3 — Editing |
| Polling, not streaming | Up to a few seconds of staleness; no live transcript tail | §3 — Live updates |
| Run-id attribution is a directory diff | Two runs launched in the same instant could in principle be mis-attributed | §4 |

---

## 2a. Just landed: multi-workspace service

**`ao service`** — a single user-level supervisor daemon (`ao service run`) that serves
every registered workspace's dashboard, replacing the by-hand "one `ao ui` process per
workspace, one hand-written systemd unit per workspace" pattern:

- Explicit registration (`ao service add/remove/list`) against a registry file
  (`~/.config/ao/service.yaml`); one child `ao ui` process per workspace, monitored and
  restarted with backoff.
- Port resolution: a workspace's own `.ao/config.yaml` (`ui.port`) beats a registry pin,
  which beats a random free port persisted back into the registry for stability; conflicts
  are resolved deterministically and surfaced in `ao service status`.
- Bounded, default-on boot-resume: a run left `running` by a reboot with a dead owning PID
  is auto-resumed once per boot, with a cross-boot cooldown and quarantine so a poisoned run
  cannot loop-resume.
- A small hub (fixed port 8770, loopback-only, same default posture as `ao ui` — login is opt-in, §3.1)
  listing every workspace, plus `GET /api/service/status`.
- `ao service install [--print]` writes a **user** systemd unit
  (`Restart=on-failure`, `KillMode=process` — required so systemd's default control-group
  kill mode does not reach a detached in-flight agent run; empirically verified against a
  real `systemctl --user` unit, not just asserted).

See [`docs-md/multi-workspace-service-hld.md`](../docs-md/multi-workspace-service-hld.md)
and [`docs-md/adr/ADR-0012-multi-workspace-service-supervisor.md`](../docs-md/adr/ADR-0012-multi-workspace-service-supervisor.md).
Standalone `ao ui` is unchanged. Same known limitations as §2's dashboard apply to each
served workspace (no auth, polling not streaming, etc.) — carried in §3.1/§3.3, not
duplicated here. Boot-resume is scoped to dashboard-launched runs only (a bare-terminal
`ao run` has no PID artifact the service can observe) — documented, not a defect.

---

## 2b. Just landed: per-task git isolation

**Opt-in, default off.** A task declaring `isolation: worktree` runs in its own `git worktree`
on branch `ao/<run_id>/<task_id>`, created at dispatch from the current integration head:

- **Landing is a rebase, not a merge.** Squash the task to one commit, rebase it onto the
  integration head, run a verify step on the *post-rebase* tree, then land with an atomic
  `git update-ref` compare-and-swap under a per-repo lock. The integration branch is an
  ao-owned ref that is never checked out, so a ref move can never fail on a dirty tree.
- **Conflicts are repaired, not just reported** — a cost-ordered ladder: free git auto-merge →
  free mechanical resolvers (`rerere` replay, union merge, regenerate for lockfiles) → one
  bounded LLM merge-resolver dispatch, budgeted as an attempt of the same task → one re-run on
  the fresh base → the operator, with the worktree and branch retained for inspection.
- **Co-scheduling prefers non-overlapping tasks** using soft `touches` hints plus an
  `ao hotspots` churn/conflict signal, and **never** withholds a slot because of overlap. A
  provable no-op at `max_parallel == 1`.
- **Two flags, deliberately.** `--isolation {none,worktree}` (env `AO_ISOLATION`, config
  `isolation.mode`) is a *fill-in default* for tasks that declare nothing; `--no-isolation`
  (env `AO_NO_ISOLATION`) is the kill switch that overrides even an explicit per-task value and
  logs what it overrode.
- **Cleanup is first-class**: `ao prune --worktrees-only` reaps a crashed run's orphaned
  worktrees and `ao/` refs.

See [`docs-md/task-isolation-hld.md`](../docs-md/task-isolation-hld.md) and
[`ADR-0013`](../docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md); §25 of the
HLD records every place the shipped system differs from the design.

**Known limitations at first ship** (carried in §4, not hidden): the T2 resolver's tool/push
containment is not yet effective; `ao prune` still leaks the `ao/` refs of a *successful* run;
integration tier counters are not populated, so conflict volume is only visible in `run.log`; and
`ao resume` after an operator-unresolvable failure re-runs the task rather than adopting a manual
fix. `refs/heads/ao/**` and `refs/ao/**` are reserved for the engine.

---

## 2c. Just landed: cross-run result cache

**Opt-in, default off, double opt-in.** E-Rc4Hk8 / [ADR-0019](../docs-md/adr/ADR-0019-cross-run-result-cache.md) /
[`docs-md/cross-run-result-cache-hld.md`](../docs-md/cross-run-result-cache-hld.md) (§0 = what shipped and every deviation).
It is **not** Claude prompt caching (that is ADR-0015's separate scope).

- **What it does.** For a task the workflow author opted in (`cache: true`) and the operator enabled
  (`--cache` / `AO_CACHE` / `.ao/config.yaml cache.*`), the engine computes a sha256 key over the
  rendered prompt, the exact `claude` argv, a CLI fingerprint, input/instruction contents, prior
  output contents and repo HEADs. A hit restores the stored declared outputs and settles the task
  `succeeded` with no dispatch, no retry and no budget charge; avoided spend is reported separately
  (`saved_*`, an estimate). A miss dispatches as today and stores the final settled success behind
  three purity guards (key unchanged, no HEAD moved, no tracked file changed).
- **Operability.** `ao cache ls|stats|show|rm|prune|clear|verify` (each `--json`); `rm <key>` then a
  re-run is the way to re-roll a frozen result; `shadow` mode measures the would-hit rate without
  restoring anything. Dashboard "cached" tag and "Result cache" tile. `ao-bench` always runs with
  `--no-cache`. When off (the default) the engine executes and imports no cache code and
  `status.json` / CLI text are byte-identical (tests I-1, I-2).
- **Safety.** The store is agent-writable, so it is treated as hostile data: spec-derived restore
  destinations only, sensitive paths (`.git`, `.claude`, CI config, ...) never written, total parsers,
  symlinks never followed or evicted, a cache bug disables the cache for the run rather than killing it.
- **Delivered with** 20 tasks, three review gates (G1a, G1b, G2) all PASS, full suite 6903 passed /
  10 skipped / 0 failed, cache-package coverage 98.71%.

**Not yet true / open follow-ups** (carried, not hidden):
- **G0, the value check, has not been run.** The protocol and tooling shipped
  ([`docs-md/result-cache-g0-protocol.md`](../docs-md/result-cache-g0-protocol.md)); executing it needs
  multi-day `AO_CACHE=shadow` runs of a real consumer workflow (finplan) with the operator's consent.
  Until it passes, `on` is not recommended to any consumer. If the would-hit rate is negligible the
  feature stays shipped but off and ADR-0019 ALT-8 (`ao run --reuse-from <run>`) is the next step.
  Owner: parent or operator, post-merge; the parent confirms the decision-rule thresholds (OQ-6).
- **Merge with E-Ag7Pw3 (approval gates) and E-Da5Tn9 (dashboard auth):** classify new fields RULED,
  keep the approval check before the lookup seam, recapture the I-2 goldens if output changed, rebuild
  the UI bundle (HLD §24.2).
- **Accepted residuals** (use `--no-cache` for untrusted repositories): provider/endpoint env not in the
  key; the guard-3 probe can execute a git `filter.<x>.clean` from the agent-writable config; dirty
  tracked edits made before the lookup are not in the key; the cache directory is agent-writable
  (HLD §0.4, ADR-0019 addendum A4).

---

## 3. Next 3–6 months

Ordered by dependency, not by calendar. Each bullet is roughly epic-sized.

### 3.1 Security, secrets, and multi-user *(highest priority — gates everything hosted)*

**Authentication has shipped (E-Da5Tn9, 2026-10-05, opt-in).** `ao ui` and the `ao service` hub
can require login with local accounts (scrypt), optional TOTP and recovery codes, per-realm
in-memory sessions bound to a proof header, and an `ao auth` CLI; see
[`docs-md/dashboard-authentication.md`](../docs-md/dashboard-authentication.md) and
[`docs-md/dashboard-auth-hld.md`](../docs-md/dashboard-auth-hld.md) (ADR-0021). It is **off by
default**; the engine still has no notion of a caller beyond the dashboard's `Principal`.
Follow-ups for hosted or multi-user use, **in priority order**:

1. **Hub-run handoff** — one login for the hub that carries to the dashboards it launches
   (today: one login per realm).
2. **Store-scoped SSO** — sessions shared through the store instead of per-process memory.
3. **RBAC** on `Principal.roles` (always `[]` today: authenticated means full access).
4. **API tokens** for scripts and CI (today a script must repeat the browser handshake).
5. **OIDC / LDAP / trusted-header providers** behind the existing provider seam (OQ-10: needs
   provider-contributed public routes and a proof handoff for redirect flows).
6. **Fail-closed remote binds** — refuse a non-loopback bind without auth (today: deprecation notice).
7. **Hub showing child auth state** (OQ-4), so a workspace config switching auth off is visible.
8. **At-rest TOTP seed encryption** (seeds are in clear in `users.json`).
9. Smaller follow-ups (store-permission re-check after startup is done): record the OS uid in CLI audit
    events; a client-rendered hub index that carries the proof (closes the cookie-only hub index
    residual); `dompurify` bump past 3.4.12 (moderate advisory); a `DashboardService.start_run`
    path restriction; WebAuthn and native TLS flags. No cut-line item was dropped (HLD §24.1).

Still open from the original security list:

- **Configurable secrets** — a secrets backend behind an ABC (env / file / external store),
  so agent credentials stop riding on ambient environment variables.
- **Authorization on triggers and run control** — who may start, cancel, or delete a run.
- **Per-run sandboxing** — bound what an agent process can touch, beyond today's
  workspace-root path guard.
- **Audit trail** — who did what, alongside the existing run/monitor decision records.

### 3.2 Scheduling and triggers

`Trigger` (manual / cron / event) is modelled and validated but nothing executes a schedule.

- A scheduler daemon that owns cron triggers and materializes runs. *Designed: E-Sc9Rt4 / ADR-0014 — the `ao service` supervisor owns it.*
- Event triggers (file watch, webhook) behind the same interface.
- Dashboard surface for upcoming/recent scheduled runs.

### 3.3 Dashboard depth

- **UI-driven workflow construction** — build and edit a DAG visually and emit a valid
  spec. *(Explicitly deferred from the current epic; the highest-value next dashboard step.)*
  - **Read-only half: delivered** by `E-k3AMEr-run-graph-canvas` (implemented 2026-09-27). The
    run detail view has a lazy-loaded **Graph** tab with a run-graph canvas, toggleable
    **Execution order** and **Spawned by** views, a hover card, and a pinned task-detail panel.
    The engine now records spawn provenance (`RunState.spawned_by`) and a write-once per-session
    workflow snapshot. See [`docs-md/run-graph-canvas-hld.md`](../docs-md/run-graph-canvas-hld.md)
    (§0 lists the as-shipped deviations and follow-ups) and
    [ADR-0017](../docs-md/adr/ADR-0017-run-graph-provenance-snapshot-and-canvas.md).
  - **Editor half: still deferred.** Building or editing a DAG in the browser. Prerequisite debt
    from the run-graph epic: the hover card and detail panel take the raw `RunGraph` rather than
    view-models (Gate G3 Warning #1), and `RunGraph.tsx` should have its hover/drag state split
    out into hooks first (Gate G3 Warning #3). Both are listed in the run-graph HLD §0.4.
  - *Natural follow-on:* a **timeline/Gantt view** of a run. It needs per-dispatch interval
    history, which is deliberately not recorded by E-k3AMEr (HLD R-10).
- **Live updates** — stream run/task state and agent transcripts instead of polling.
- **In-browser editing** — edit instruction and spec files, with validation before save.
- **Run comparison** — diff two runs' cost, duration, and outputs.
- **Cancel for externally-started runs** — a supervisor-independent stop channel
  (generalizing the `stop_file` breaker into a first-class run-control primitive).

### 3.4 Execution correctness at higher parallelism

Per-task isolation (§2b) is the answer to this, and it has shipped — but **opt-in**, so the
default (`isolation: none`) still carries the original gap. To raise the default:

- **R-15 (strategic, E-Rc4Hk8): isolation vs the result cache.** The result cache (§2c) is
  ineligible for any task with `isolation: worktree` or while the run's integration is active
  (ADR-0019 D25), because the checkout HEAD moves at barriers. If isolation ever becomes the
  default, almost no task stays eligible and the cache's value collapses. The recorded migration
  path is the executor-level `CachingExecutor` (ADR-0019 ALT-7), which works inside worktrees and
  with hooks but forces a budget pre-charge on hits. Decide this **before** defaulting isolation on.
- **Workspace isolation per task** (worktree-style) so parallel agents cannot collide.
  **Delivered: E-Wk9Tz3 / ADR-0013** — including soft overlap-aware co-scheduling in place of
  hard write-conflict gating. Remaining work is adoption and confidence, not design.
- **Write-conflict detection** between co-scheduled tasks for runs that do *not* enable
  isolation (declared-output overlap analysis, and a runtime guard). Still open, and now the
  narrower question: whether it is worth building at all, or whether the answer is simply
  "enable isolation".
- **Measure the cost of isolation on a real consumer** before recommending it as the default:
  the cold-rebuild cost per worktree and the first-barrier collision rate against a chronically
  dirty checkout are both still predicted rather than observed.
- **Cross-epic note (resolved, and the recommendation stands).** E-Wk9Tz3 fast-forwards the
  workspace's checked-out branch at barriers *per run*; two concurrent runs in one workspace
  (reachable via E-Sc9Rt4 `overlap: allow` or a manual second `ao run`) would race on that
  fast-forward. ADR-0013 D8 now states the multi-run policy — a per-workspace run lock, with
  `workspace_lock: "require"` degrading the second run to `isolation: none` rather than racing.
  **Keep the scheduler's per-workspace cap at 1 for isolated workflows anyway.** The lock makes
  a cap violation *degrade safely*; it does not make it *desirable*, since the second run
  silently loses isolation. The cap remains the better default; the lock is the backstop.
- **Backpressure** tied to the budget and quota subsystems.

### 3.5 Executor and provider breadth

- Additional executors behind the existing `Executor` ABC (other agent CLIs; a direct API
  executor without a CLI dependency).
- A local/offline executor for cheap deterministic testing at scale.

### 3.6 Operations and distribution

- Package publication and versioned releases.
- Artifact-store backends beyond local filesystem (S3-compatible) behind `ArtifactStore`.
- Retention/GC policy for run artifacts beyond `ao prune`.
- **Result-cache follow-ons (non-MVP; HLD `cross-run-result-cache-hld.md` §2.3, ADR-0019):**
  - **Run G0** on a real consumer (above); the post-merge go/no-go for recommending `on`.
  - Remote, shared or S3 cache backends (the `CacheStore` / `CacheAdmin` ABCs are provisional);
    HMAC-authenticated entries; `dir_fd`-walk (openat-style) store and restore I/O.
  - An executor-level `CachingExecutor` (ALT-7) and an explicit `ao run --reuse-from <run-id>` (ALT-8).
  - Caching with isolation, integration or hooks; directory or dynamic outputs.
  - Dependency-aware invalidation (e.g. a FAIL verdict evicting its producers' keys), cache warming,
    cost-aware eviction, cross-workspace sharing.
  - Recording the resolved model id from the transcript; user-level (`~/.claude/**`) context
    fingerprinting; memoizing hashes within a run; per-entry hit counters.
  - An `ao validate` warning for `cache: true` on an ineligible task; `ao cache explain`; dashboard
    launch controls, a runs-list column, a Usage-tab surface.
  - **Deferred in Rev 3:** a `refresh` mode, `ao cache rm --run R --task T`, `ao cache verify --repair`.
  - Hygiene ticket for the deferred gate NITs (HLD §0.6 FU-4) and the pre-existing missing-inputs
    `dispatch_cycle` reset (HLD §23.2).

---

## 4. Known gaps and risks (carried, not scheduled)

These are accepted trade-offs. They are recorded so they are chosen rather than forgotten.

- **Parallel write conflicts, without isolation.** With `max_parallel > 1` and
  `isolation: none` (still the default), co-scheduled tasks are not checked for overlapping
  outputs — keeping them disjoint is the spec author's job (ADR-0007). A live run has already
  produced real file contention. **Now avoidable rather than merely accepted**: enabling
  per-task isolation (§2b) removes the shared working tree the conflict happens in. The gap
  stays recorded because isolation is opt-in and off by default.
- **Isolation's rough edges at first ship, all fixed by epic close (2026-09-11)** (E-Wk9Tz3, all
  tracked, none silent): the T2 merge-resolver's tool/push containment — fixed by forcing `Bash`/
  `Task`/`WebFetch`/`WebSearch` off for every resolver dispatch regardless of the agent's own tool
  policy, which is the real containment (a shell that never exists cannot plant a hook or push by any
  transport); `ao prune` leaking the `ao/` refs of a fully successful run — fixed by discovering a
  run's repos from its own persisted `RunState.integration.repos` record, not only by probing worktree
  directories the engine has already removed; integration tier counters never incrementing — fixed,
  `tier_counts`/`tier_reached`/`conflicted_count` now accumulate correctly and surface in `status.json`
  and the dashboard; `ao resume` after a T4 failure hard-resetting the retained worktree instead of
  adopting an operator's manual fix — fixed, `TaskIntegrationState.mode` now resets to `"normal"` on a
  T4 failure so a resumed dispatch is a plain retry. **Still an accepted limitation, not fixed**: the
  engine suppresses repository *hooks* but not git-attribute `filter`/`merge` drivers, so a repository
  with an expensive or untrusted driver configured should not be run under isolation. See
  `docs-md/task-isolation-hld.md` §25.
- **Dashboard authentication: accepted residual risks** (E-Da5Tn9 sign-off, HLD §6.3 A4/A6/A10
  and "As built"). Login is opt-in, so with it off the old posture applies (loopback plus a
  startup warning). With it on: authenticated = full access and same-user processes (including
  agents) can read the credential store (A10, out of scope); a harvested cookie cannot call the
  API but still renders the hub index and cookie tossing can force a logout (A4);
  `DashboardService.start_run` still accepts an absolute `workflow_path` (the launcher can already
  run arbitrary agent code); behind a reverse proxy without `AO_UI_AUTH_TRUSTED_PROXIES` all
  remote users share one throttle bucket (account lockout can be used as a DoS), and with
  `trusted_proxies=127.0.0.1` a local process can claim a client address via `X-Forwarded-For`;
  IPv6 clients are throttled per `/64`; the lockout phantom table is a bounded eviction oracle;
  a hard link bypasses the file-browser path denial; a stale global `ao` fails open when auth is
  enabled only by `service.env`; per-session counters are not concurrency-exact; TOTP seeds are
  in clear. Details and rationale: `docs-md/dashboard-auth-hld.md` "As built".
- **Run-id attribution.** The dashboard learns a run's id by diffing the runs directory
  after launch. Concurrent launches in the same instant could mis-attribute; ids already
  claimed are excluded, which bounds but does not eliminate this. A `--run-id` flag on
  `ao run` would remove the guesswork.
- **Global `--model` clobbers per-agent models.** A run-level override rewrites every
  `AgentSpec.model`, silently downgrading a pinned agent (ADR-0003 proposes the fix).
- **Estimator uses file sizes, not tokens.** Deliberately pessimistic; directory inputs are
  stat'd top-level only.
- **Global `ao` installs are snapshots.** A stale global install silently lacks new features
  until `install.sh` is re-run.
- **Result cache: value unproven and residuals accepted (E-Rc4Hk8).** The would-hit rate on a real
  workflow is unmeasured (G0 not run). Accepted residuals: the key omits provider/endpoint env and
  dirty tracked edits made before the lookup; the guard-3 probe can execute a `filter.<x>.clean`
  command from the git config; the cache directory is agent-writable. All opt-in; `--no-cache`
  for untrusted work. See §2c.

---

## 5. How to use this file

- **Adding an epic?** Land it in `meta/tickets/`, then add a row to §1 and, if it changes
  the forward view, edit §3.
- **Hit a limitation you decided not to fix?** Record it in §4 with its consequence.
- **Reviewing quarterly?** Re-order §3 against what the project actually needs, and update
  the "Last reviewed" date at the top.
