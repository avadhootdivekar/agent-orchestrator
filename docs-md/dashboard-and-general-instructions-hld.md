# Browser dashboard & general instructions — HLD/LLD (E-Ui7Kq2)

**Status:** Implemented · **Date:** 2026-07-24 · **ADR:** [ADR-0010](adr/ADR-0010-dashboard-architecture-and-general-instructions.md)

Two independently useful features shipped together because the dashboard is the natural
place to *see* the second one:

1. **`ao ui`** — a browser dashboard for file browsing, run control, and run statistics.
2. **General instructions** — instruction files declared once per workspace and applied to
   every task of every run.

---

## 1. General instructions

### 1.1 Requirement

> General instructions will be applied to each task regardless. These will be defined once
> in the workspace — not with each run — configured in one place (env vars, config file),
> and given to tasks dynamically even if the user has not specified them on `ao run`.

Two properties follow, and they drive every design decision below:

- **Workspace-scoped, not run-scoped.** Configure once; every subsequent run inherits them.
- **Applied regardless.** No flag needed. Adding an unrelated flag must not remove them.

### 1.2 Declaration layers

| Layer | Where | Scope |
|---|---|---|
| Project config | `.ao/config.yaml` → `general_instructions: [paths]` | Workspace (primary) |
| Environment | `AO_GENERAL_INSTRUCTIONS` (`os.pathsep`-separated) | Shell / CI |
| CLI | `--general-instruction PATH` (repeatable) | One invocation |
| Workflow spec | `general_instructions: [paths]` | One workflow |

Config-file paths resolve relative to the **config file's own directory**, consistent with
how `workflow` / `reposets` / `agents` already resolve there.

### 1.3 The layers are additive, not a precedence chain

This is the one setting in AO that deliberately does **not** follow the
`CLI > env > config > default` precedence of every other runtime setting.

The user-facing contract is "define them once and every task gets them regardless." Under a
precedence chain, a single `--general-instruction` on the command line would *replace* — and
therefore silently drop — the workspace's house rules. That is the opposite of the contract.

So `cli.resolve_general_instructions()` returns the **union**, de-duplicated, in
broadest-scope-first order:

```
project config  →  env var  →  CLI flags  →  workflow spec
```

Ordering is presentational (workspace-wide rules reach the agent before workflow-specific
ones); de-duplication is by path string, so a file named in two layers is passed once.

### 1.4 Flow through the engine

```
.ao/config.yaml ─┐
AO_GENERAL_…     ├─→ cli.resolve_general_instructions()  (union, dedup)
--general-instr  │            │
workflow spec ───┘            ▼
                   Orchestrator(general_instructions=[...])
                              │
                              ▼
              _resolve_general_instructions(workflow)
              • merges the workflow layer again (so direct library
                users of Orchestrator get it too — dedup makes the
                CLI's already-merged list harmless)
              • resolves each path through ArtifactStore.resolve
                (same workspace-root guard as task.instruction)
              • drops + logs anything that fails the guard
                              │
                              ▼
              TaskContext.general_instruction_paths  (paths only — NFR-1)
                              │
                              ▼
              executors/prompt.py :: build_prompt(ctx)
```

### 1.5 Prompt assembly — why append, not just interpolate

`build_prompt` fills `{general_instructions}` if the agent's `prompt_template` uses it. If
the template does **not** mention it, a trailing clause naming the paths is appended.

Placeholder-only would have been cleaner, but every `agents.json` written before this
feature — and `AgentSpec.prompt_template`'s own default — has no placeholder. A
placeholder-only design would silently drop the workspace's rules for exactly the configs
most likely to be in use, breaking "applied regardless" for the common case. Templates that
*do* position the placeholder keep full control and get no duplicate clause.

Both executors (`claude_cli`, `fake`) render through this one function, so the guarantee is
enforced in one place rather than re-implemented per executor. `FakeExecutor` also records
the rendered prompt in `.prompts`, which is how tests assert prompt assembly without
spawning a subprocess.

### 1.6 Failure handling and budgeting

- **A bad path does not fail the run.** One typo in a workspace-wide config would otherwise
  break every task of every workflow. The engine logs `general_instruction.rejected` and
  carries on; `ao validate` is the layer that reports such a path up front, and the
  dashboard's Workspace view shows an `exists` flag per path.
- **They count toward the token estimate.** `HeuristicTokenEstimator` includes
  `general_instruction_paths` in its input-bytes sum. Agents genuinely read these on every
  task; omitting them would under-estimate every task in a workspace that configures them,
  letting the budget gate admit work it cannot afford.

### 1.7 A run prompt is *not* a general instruction

Requirement 1 (dashboard text box → prompt file) and requirement 2 (general instructions)
are separate mechanisms and stay separate:

| | Run prompt | General instructions |
|---|---|---|
| Scope | One run | Workspace |
| Says | *what this run should do* | *how every task should behave* |
| Set by | `ao run --prompt` / `--prompt-file`, or the UI text box | config / env / CLI / workflow |
| Lands in | `WorkflowSpec.prompt_path` (an artifact a task lists in `inputs`) | the prompt of every task |
| On resume | Never rewritten — see below | Re-resolved fresh |

`ao resume` deliberately has **no** `--prompt`. The prompt is a per-run *input artifact* the
original `ao run` already materialized, and tasks that consumed it have outputs on disk.
Rewriting it mid-run would make the run irreproducible from its own artifacts, breaking the
resumability invariant. General instructions, being invocation-scoped context rather than
artifacts, *are* re-resolved on resume, so a workspace can add house rules and have a
resumed run's remaining tasks pick them up.

---

## 2. Dashboard

### 2.1 Layering

```
┌───────────────────────────────────────────────┐
│ ui/  React + TypeScript + Vite (no runtime deps)│
│      builds → src/agent_orchestrator/ui/static/ │
└───────────────────────┬───────────────────────┘
                        │ HTTP /api
┌───────────────────────▼───────────────────────┐
│ ui/app.py    FastAPI adapter                  │  ← the ONLY module needing [ui]
│              routes → service, exc → status   │
├───────────────────────────────────────────────┤
│ ui/service.py  DashboardService               │  ← NO framework import
│                the whole API as plain Python  │
├──────────────┬─────────────┬──────────────────┤
│ ui/files.py  │ ui/runs.py  │ ui/processes.py  │
│ browsing     │ stats       │ subprocess supervision │
└──────────────┴─────────────┴──────────────────┘
```

Everything below `app.py` imports only the core dependency set, so the whole dashboard is
unit-testable without installing the `ui` extra or starting a server. `FileBrowser`,
`RunRepository`, and `ProcessSupervisor` are injected into `DashboardService`, so tests
substitute fakes rather than monkeypatching.

### 2.2 Runs are subprocesses, not in-process work

A run is long, expensive, and cancellable. Hosting the engine inside the web server would
mean a dashboard restart kills in-flight work and a crashing run takes the UI down with it.

So the dashboard shells out to the same CLI an operator would type
(`python -m agent_orchestrator.cli run …`), which keeps exactly one execution path through
the engine.

- **Own process group** (`start_new_session=True`) so cancel signals the whole tree —
  signalling only the direct child would orphan the `claude` processes the engine spawned
  and leave them spending tokens.
- **`sys.executable -m`, not a bare `ao`** — a globally installed `ao` on `PATH` is often a
  stale snapshot of a different version, a failure mode this project has hit before.
- **Launch records persisted** under `.orchestrator/ui/launches/` so cancel still works
  after the dashboard itself restarts; an in-memory registry would strand every running job.
- **Children are reaped** via a retained `Popen`. Without this an exited child stays a
  zombie — still signalable — and every liveness check would report it running forever.
- **Prompts are passed by file, never argv** — argv is visible in `ps` to every user on the
  box and is subject to `ARG_MAX`.
- **Options are allow-listed** (`ALLOWED_OPTIONS`), never free-form passthrough. The UI is
  unauthenticated in this release, so a request body must never be able to inject arbitrary
  argv into a subprocess.

**Run-id attribution.** The engine derives `run_id` itself, so the dashboard learns it by
diffing the runs directory against a pre-launch snapshot, excluding ids another record has
already claimed. `None` is a normal outcome for a slow start and is reconciled on the next
read. Concurrent launches in the same instant remain a theoretical mis-attribution risk —
see the roadmap.

**Cancel is two steps, in order:** kill the process group, then rewrite the run state to
`cancelled` (through `RunStateStore.save`, so `status.json` stays consistent per ADR-002).
The engine cannot record its own cancellation once signalled; without step two the run would
sit at `running` forever and never be resumable or deletable.

### 2.3 File browsing

Shows **everything** under its roots — hidden dotfiles and binaries included — because
inspecting `.ao/`, `.orchestrator/`, and `.git/` is the entire point.

The guard is containment, not filtering: every requested path is `Path.resolve()`d (which
collapses `..` **and** follows symlinks) and must still live under a configured root. That
is what makes a symlink pointing outside the workspace fail closed rather than become an
exfiltration primitive. Reads are bounded by `MAX_READ_BYTES`; binary files (NUL in the
first 8 KiB, the same heuristic git uses) return metadata with `text=None` rather than
mojibake.

### 2.4 Statistics

Every number is **derived on demand** from `state.json` — no new persisted state. Same
reason `compute_run_usage_totals` is derived: a separately maintained counter can drift from
the audit trail; a pure function cannot.

| Metric | Definition |
|---|---|
| Tasks | count, plus a breakdown by status |
| Cost / tokens | summed `cumulative_*` across tasks (includes every retry attempt) |
| **Wall time** | `updated_at − started_at` — includes operator pause/resume gaps |
| **Actual time** | Σ per-task `(ended_at − started_at)` — excludes those gaps |

Wall vs actual is a real distinction for agent runs: a run paused overnight and resumed has
a large wall time and an unchanged actual time. `compute_run_active_seconds` was promoted
into `models.py` so the dashboard's "actual time" and the `run_active_seconds` circuit
breaker share one definition and cannot drift.

### 2.5 Frontend

React + TypeScript + Vite. The original release had no runtime dependencies beyond React. Two
later epics each added a small, justified set, and all of them are bundled into the committed
build:

- `E-Fp7Qv2` (file preview, ADR-0011) added `dompurify`, `marked`, and `highlight.js`.
- `E-k3AMEr` (run graph, ADR-0017 D5) added `@xyflow/react` and `@dagrejs/dagre`, both MIT.

Built output is committed into the package so `pip install` ships a working dashboard without node.

**Run graph (E-k3AMEr).** The run detail view's Tasks section has a **Table | Graph** tab switch.
Table stays the default, and the chosen tab persists in `localStorage`. The tab switch appears only
when the backend returns a non-empty `graph_version`. The Graph tab (`ui/src/graph/RunGraph.tsx`)
is loaded with `React.lazy` + `Suspense`, so the React Flow and dagre chunk (≈ 82.5 KB gzip)
downloads only when the tab is first opened, and the initial dashboard load is unchanged. The
design and as-shipped deviations are in [`run-graph-canvas-hld.md`](run-graph-canvas-hld.md) §0.

Visual system follows the project's data-viz guidance: a validated palette, light and dark
both explicitly stepped (OS setting *and* an in-app toggle, toggle wins either way), one
hero figure per view, tabular figures only in aligned columns, and status chips that carry
a **glyph plus the status word** so color is never the only channel.

A missing `static/` (normal in a source checkout) degrades to a 503 explaining how to build
it; the API stays fully usable. Unknown `/api/*` paths return a JSON 404 rather than falling
through to the SPA, so a typo'd endpoint is a clear error and not a confusing HTML body.

### 2.6 API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | liveness + version |
| GET | `/api/workspace` | served root, config, roots |
| GET | `/api/general-instructions` | effective set + `exists` per path |
| GET | `/api/files?path=&root=` | directory listing (hidden + binary included) |
| GET | `/api/files/content?path=` | file contents / binary metadata |
| GET | `/api/workflows` | discovered specs (+ `prompt_path`) |
| GET | `/api/runs` | run list with per-run stats and liveness |
| GET | `/api/runs/stats` | workspace-wide totals |
| GET | `/api/runs/{id}` | per-run detail (tasks, breakers, routing, `graph_version`) |
| GET | `/api/runs/{id}/graph` | run graph (topology only): nodes, dependency edges, spawn edges, loops, routers, `warnings[]` (E-k3AMEr) |
| GET | `/api/runs/{id}/log` | captured CLI output |
| POST | `/api/runs` | start a run (workflow + prompt + options) |
| POST | `/api/runs/{id}/resume` | resume |
| POST | `/api/runs/{id}/cancel` | cancel |
| DELETE | `/api/runs/{id}` | delete run artifacts |
| GET | `/api/launches` | dashboard-initiated launches, each with derived `status`; `?status=` / `?since_hours=` filters |
| GET | `/api/launches/{launch_id}` | one launch + bounded `log_tail` (404 for malformed/unknown id) — see live-activity-and-tabs-hld Phase 3 |

Status codes: `403` traversal · `404` missing · `409` wrong state (delete a live run, resume
a running one, cancel a run the dashboard did not launch) · `400` bad request (prompt for a
workflow with no `prompt_path`) · `422` schema violation.

**Run graph additions (E-k3AMEr, all additive).**
- `GET /api/runs/{id}/graph` returns `200` with a `RunGraph` for every run whose `state.json` is
  readable, including degraded ones. `source: "unavailable"` and `spawn_data: "not_recorded"` come
  with human-readable `warnings[]`. It returns `404` for an unknown run, a traversal-shaped id, or
  an unreadable `state.json`. It never returns hook argv, instructions, integration commands, or
  file contents.
- `RunDetail.graph_version` is a 16-hex-char fingerprint of the run's topology, computed by the
  same function (`ui/graph.py::compute_graph_version`) from the same `state.json` as the graph
  itself. The client refetches `/graph` only when it changes, so status and cost come from the
  existing 3 s detail poll.
- `TaskStat` gains `dispatch_cycle: int` and `not_taken_reason: str | null`.

Full contract: [`run-graph-canvas-hld.md`](run-graph-canvas-hld.md) §14.2.

### 2.7 Security posture — explicitly deferred (superseded by opt-in auth)

> **Update (E-Da5Tn9, 2026-10-05):** opt-in local-account login with optional TOTP now exists
> (`ao ui --auth`, `ao auth`); see [`dashboard-auth-hld.md`](dashboard-auth-hld.md) and the user
> guide [`dashboard-authentication.md`](dashboard-authentication.md). The paragraph below is
> the original posture and remains exact when auth is off (the default).

**No authentication in this release**, per the requirement to leave credentials and login
out for now. Mitigations: loopback-by-default bind, a startup warning when binding anything
else, the allow-listed option surface, and the path guard. Configurable secrets and auth are
the top roadmap item — see [`meta/ROADMAP.md`](../meta/ROADMAP.md) §3.1.

---

## 3. Testing

| Tier | Location | What it proves |
|---|---|---|
| Unit (Python) | `tests/ui/test_files.py`, `test_runs.py`, `test_processes.py`, `test_service.py`, `test_edge_cases.py` | Path guard, stats math, supervisor lifecycle, service logic — no server, no subprocesses (except the supervisor's own) |
| Unit (frontend) | `ui/src/test/` | Formatting, status encoding, component behaviour w/ mocked fetch |
| Integration | `tests/ui/test_api_integration.py` | Real FastAPI routing, status-code mapping, route precedence |
| E2E | `tests/ui/test_e2e_ui.py` | Real uvicorn process + real HTTP + a real `ao run` child: prompt → run → stats → delete |
| E2E (CLI) | `tests/test_e2e_cli_prompt_and_instructions.py` | `--prompt`, `--prompt-file`, `--general-instruction`, env/config layers |
| Unit + integration | `tests/test_general_instructions.py` | Layer merging, prompt assembly, engine wiring, estimator accounting |

Notable behaviours pinned by tests: symlink escapes are refused; a broken symlink still
lists; cancel kills grandchildren; a traversal path in a config is dropped without failing
the run; a prompt for a workflow without `prompt_path` is rejected rather than dropped; and
a live run cannot be deleted.

---

## 4. Deferred (roadmap)

- **UI-based dynamic workflow generation** — build/edit a DAG in the browser. Explicitly
  out of scope here; the highest-value next dashboard step.
  - **Read-only half: delivered by [`E-k3AMEr-run-graph-canvas`](../meta/tickets/E-k3AMEr-run-graph-canvas/EPIC.md)
    (implemented 2026-09-27).** It adds a *Graph* tab to the run detail view: a pannable/zoomable
    canvas of a run's tasks with two toggleable edge sets.
    - **Execution order**: the dependency DAG, plus the actual start order.
    - **Spawned by**: which task created which dynamically injected task.

    It also adds a hover preview plus a pinned task-detail panel (cost, duration, retries,
    dispatches, wait time). Design: [`run-graph-canvas-hld.md`](run-graph-canvas-hld.md) ·
    [ADR-0017](adr/ADR-0017-run-graph-provenance-snapshot-and-canvas.md).
  - **Still deferred:** building or *editing* a DAG in the browser. The new `/graph` endpoint is a
    run read model, not a spec round-trip format, so an editor needs its own spec-shaped API. The
    canvas and layout seam are reusable. First, though, the hover card and detail panel need to
    move from the raw `RunGraph` prop to view-model props (Gate G3 Warning #1, run-graph HLD
    §0.4 FU-3).
- Authentication and configurable secrets.
- Live streaming instead of polling; in-browser editing; run comparison.
- Cancelling runs the dashboard did not launch.
