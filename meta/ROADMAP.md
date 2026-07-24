# agent-orchestrator — Roadmap

> **Scope of this file.** A high-level status summary plus a 3–6 month forward view. It is
> deliberately *not* a ticket tracker — per-task detail lives in
> [`meta/tickets/`](tickets/), and design detail in [`docs-md/`](../docs-md/). Update the
> status table when an epic closes; revisit the horizon sections roughly quarterly.
>
> Last reviewed: **2026-07-24**

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
| Parallel execution (`max_parallel`) | **Stable, opt-in** | Default 1 = serial. **No write-conflict detection** — see §4. |
| Cost/token accounting per task and per run | **Stable** | Cumulative across retries. |
| Benchmark harness (`ao-bench`, S/M/L tiers, SWE-bench import) | **Stable** | See `docs-md/benchmarking-framework-hld.md`. |
| Installable CLI (`ao`, `ao-bench`) | **Stable** | `install.sh`; snapshot-install semantics. |
| **Browser dashboard (`ao ui`)** | **New** | Files, runs, stats, run control. Unauthenticated — §2. |
| **General instructions** | **New** | Workspace-scoped rules applied to every task. |
| Authentication / multi-user | **Absent** | Deliberate for now — §2. |
| Cron / event triggers | **Spec'd, not scheduled** | `Trigger` model exists; no daemon runs it — §3. |

### Recently delivered

- **E-Bt4Xk9 — Benchmark tiers** (2026-07-23): S/M/L/XL tiers, per-tier budgets, parallel
  campaign runner, SWE-bench import/grading.
- **E-IasNXu — Parallel execution** (ADR-0007): opt-in wave/barrier scheduler.
- **E-XyfjuZ — Monitoring & self-healing**: `Monitor` ABC, recommend-mode breakers.
- **E-9h3m7k — Accurate usage metrics**: true per-task/run cost and token totals.
- **E-Ui7Kq2 — Dashboard + general instructions** (this change): see §2.

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

- A scheduler daemon that owns cron triggers and materializes runs.
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

Parallel execution ships opt-in with a real, documented gap (§4). To raise the default:

- **Write-conflict detection** between co-scheduled tasks (declared-output overlap analysis,
  and a runtime guard).
- **Workspace isolation per task** (worktree-style) so parallel agents cannot collide.
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

- **Parallel write conflicts.** With `max_parallel > 1`, co-scheduled tasks are not checked
  for overlapping outputs — keeping them disjoint is the spec author's job (ADR-0007). A
  live run has already produced real file contention. Addressed by §3.4.
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
