# agent-orchestrator — Roadmap

> **Scope of this file.** A high-level status summary plus a 3–6 month forward view. It is
> deliberately *not* a ticket tracker — per-task detail lives in
> [`meta/tickets/`](tickets/), and design detail in [`docs-md/`](../docs-md/). Update the
> status table when an epic closes; revisit the horizon sections roughly quarterly.
>
> Last reviewed: **2026-09-07**

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
| **Browser dashboard (`ao ui`)** | **New** | Files, runs, stats, run control. Unauthenticated — §2. |
| **Multi-workspace service (`ao service`)** | **New** | One supervisor daemon serves every registered workspace's dashboard on its own port; boot-resume for orphaned runs; installable as a user systemd unit. Unauthenticated, same posture as `ao ui` — §2. |
| **General instructions** | **New** | Workspace-scoped rules applied to every task. |
| Authentication / multi-user | **Absent** | Deliberate for now — §2. |
| Cron / event triggers | **Designed, not built** | `Trigger` model exists; no daemon runs it yet. Service-owned scheduler designed (E-Sc9Rt4, ADR-0014) — §3.2. |

### Recently delivered

- **E-Bt4Xk9 — Benchmark tiers** (2026-07-23): S/M/L/XL tiers, per-tier budgets, parallel
  campaign runner, SWE-bench import/grading.
- **E-IasNXu — Parallel execution** (ADR-0007): opt-in wave/barrier scheduler.
- **E-XyfjuZ — Monitoring & self-healing**: `Monitor` ABC, recommend-mode breakers.
- **E-9h3m7k — Accurate usage metrics**: true per-task/run cost and token totals.
- **E-Ui7Kq2 — Dashboard + general instructions** (this change): see §2.
- **E-GIytcL — Multi-workspace service** (2026-08-28): `ao service` supervisor daemon (spawn/monitor/restart per-workspace dashboards, bounded auto-resume, hub, systemd install) — see §2a.
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
| No authentication | Server must stay on loopback; it can read files and spend money | §3 — Security & multi-user |
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
- A small hub (fixed port 8770, loopback-only, same unauthenticated posture as `ao ui`)
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

## 3. Next 3–6 months

Ordered by dependency, not by calendar. Each bullet is roughly epic-sized.

### 3.1 Security, secrets, and multi-user *(highest priority — gates everything hosted)*

The dashboard is open by design today and the engine has no notion of a caller identity.
Anything beyond single-user localhost needs:

- **Authentication** for `ao ui` — token/session to start, pluggable providers later.
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
- **Live updates** — stream run/task state and agent transcripts instead of polling.
- **In-browser editing** — edit instruction and spec files, with validation before save.
- **Run comparison** — diff two runs' cost, duration, and outputs.
- **Cancel for externally-started runs** — a supervisor-independent stop channel
  (generalizing the `stop_file` breaker into a first-class run-control primitive).

### 3.4 Execution correctness at higher parallelism

Per-task isolation (§2b) is the answer to this, and it has shipped — but **opt-in**, so the
default (`isolation: none`) still carries the original gap. To raise the default:

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
- **Dashboard is unauthenticated.** Loopback-by-default plus a startup warning is the whole
  mitigation. Addressed by §3.1.
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

---

## 5. How to use this file

- **Adding an epic?** Land it in `meta/tickets/`, then add a row to §1 and, if it changes
  the forward view, edit §3.
- **Hit a limitation you decided not to fix?** Record it in §4 with its consequence.
- **Reviewing quarterly?** Re-order §3 against what the project actually needs, and update
  the "Last reviewed" date at the top.
