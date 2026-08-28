# ADR-0010 — Browser dashboard architecture, and general instructions as an additive workspace layer

- Status: **Accepted** (2026-07-24 — shipped)
- Date: 2026-07-24
- Deciders: Avadhoot Divekar (user), Claude (developer role)
- Related: **ADR-0003** (settings precedence — this ADR records the one deliberate exception) · **ADR-0007** (parallel execution) · design [`dashboard-and-general-instructions-hld.md`](../dashboard-and-general-instructions-hld.md) · [`meta/ROADMAP.md`](../../meta/ROADMAP.md)

## Context

The project had no UI: every run was a CLI invocation, and inspecting a run meant reading
JSON under `.orchestrator/runs/`. The ask was a browser dashboard covering directory/code
browsing, run control (start from a typed prompt, resume, cancel, browse, delete), and run
statistics — with credentials and login explicitly left out for now.

Separately, and independently: instruction files that apply to **every** task, defined once
per workspace rather than per run, and reaching tasks even when `ao run` is invoked without
mentioning them.

Five decisions were coupled enough to record together.

---

## D1 — General-instruction layers are ADDITIVE, not a precedence chain

**Decision.** The effective set is the **union** of `.ao/config.yaml`,
`AO_GENERAL_INSTRUCTIONS`, `--general-instruction`, and the workflow's own
`general_instructions`, de-duplicated, in broadest-scope-first order.

**This is a deliberate, documented exception to ADR-0003.** Every other runtime setting
(`model`, `effort`, `max_parallel`, quota knobs) uses `CLI > env > config > default`, where
a higher layer *replaces* a lower one.

**Why the exception.** The requirement is "defined once in the workspace… given to tasks
dynamically even if the user has not specified them on `ao run`." Under a precedence chain,
adding one `--general-instruction extra.md` would *replace* the config layer and silently
drop the workspace's house rules — the precise opposite of "applied regardless." Replacement
semantics are right for a *value* (one model, one effort level); union semantics are right
for a *set of rules that all apply*.

**Consequence.** There is deliberately no way to suppress a configured general instruction
for a single run. If that is ever needed, it should be an explicit opt-out flag
(`--no-general-instructions`), not a silent side effect of naming a different file.

---

## D2 — General instructions are appended to the prompt when the template does not position them

**Decision.** `executors/prompt.py::build_prompt` fills a `{general_instructions}`
placeholder when the agent's `prompt_template` uses one, and otherwise **appends** a clause
naming the paths.

**Why not placeholder-only.** Cleaner in principle, wrong in practice: every `agents.json`
written before this feature — including `AgentSpec.prompt_template`'s own default — has no
placeholder. A placeholder-only design would silently drop the workspace's rules for exactly
the configs most likely to be in use, breaking D1's guarantee for the common case. Templates
that *do* position the placeholder keep full control and receive no duplicate clause.

**Consequence.** Prompt assembly is centralized in one function that both executors call, so
the guarantee is enforced once rather than re-implemented per executor. `FakeExecutor`
renders through the same function and records the result, which is how prompt assembly is
asserted in tests without spawning a subprocess.

---

## D3 — The run prompt is a per-run input artifact, distinct from general instructions

**Decision.** `WorkflowSpec.prompt_path` names an artifact; `ao run --prompt` /
`--prompt-file` (and the dashboard's text box) write into it before the run starts; a task
consumes it by listing the same path in `inputs`. `ao resume` has **no** `--prompt`.

**Why separate from general instructions.** They answer different questions — *what should
this run do* versus *how should every task in this workspace behave* — and have different
lifetimes. Folding the prompt into the general-instruction mechanism would make it
workspace-sticky, so the next run would silently inherit the previous run's prompt.

**Why resume cannot rewrite it.** The prompt is an input artifact the original run already
materialized, and tasks that consumed it have outputs on disk. Rewriting it mid-run would
make the run irreproducible from its own artifacts, violating the resumability invariant.
General instructions, being invocation-scoped context rather than artifacts, *are*
re-resolved on resume.

**Consequence.** A prompt supplied for a workflow that declares no `prompt_path` is a hard
error at both the CLI and the API, never a silent drop — otherwise an operator could pay for
a run that ignored everything they typed.

---

## D4 — The dashboard launches runs as subprocesses of the same CLI, not in-process

**Decision.** `ao ui` shells out to `python -m agent_orchestrator.cli run …` in its own
process group, persisting a launch record under `.orchestrator/ui/launches/`.

**Why not in-process.** A run is long, expensive, and cancellable. Hosting the engine inside
the web server would mean a dashboard restart kills in-flight work and a crashing run takes
the UI down with it. Subprocesses keep exactly one execution path through the engine — the
same one an operator drives — which is also what makes an honest e2e test possible.

**Supporting choices, each with a failure it prevents:**

- *Own process group; cancel signals the group.* Signalling only the direct child would
  orphan the `claude` processes the engine spawned, leaving them spending tokens.
- *`sys.executable -m`, not a bare `ao`.* A globally installed `ao` is frequently a stale
  snapshot of a different version — a failure mode this project has already hit.
- *Launch records on disk.* An in-memory registry would strand every running job across a
  dashboard restart, leaving no way to cancel.
- *Retain `Popen` and reap.* Without reaping, an exited child stays a zombie — still
  signalable — so every liveness probe would report it running forever. (Found by test.)
- *Prompts by file, never argv.* argv is world-readable via `ps` and bounded by `ARG_MAX`.
- *Allow-listed options.* The UI is unauthenticated in this release, so a request body must
  never be able to inject arbitrary argv into a subprocess.

**Accepted limitation.** `run_id` is derived by the engine, so the dashboard learns it by
diffing the runs directory after launch, excluding already-claimed ids. Concurrent launches
in the same instant could in principle be mis-attributed. A `--run-id` flag on `ao run`
would remove the guesswork; recorded on the roadmap rather than fixed here.

**Cancel is two steps.** Kill the process group, then rewrite the run state to `cancelled`
via `RunStateStore.save` (keeping `status.json` consistent per ADR-002). A signalled engine
cannot record its own cancellation; without the second step the run would sit at `running`
forever and never be resumable or deletable.

---

## D5 — All dashboard logic sits in a framework-free service layer; FastAPI is a thin adapter

**Decision.** `ui/service.py` exposes the entire dashboard API as plain Python with **no web
framework import**; `ui/app.py` maps HTTP verbs onto it and translates exceptions to status
codes. Only `app.py` requires the optional `[ui]` extra.

**Why.** Three things fall out: the whole dashboard is unit-testable without a server or the
extra installed; swapping the transport (a different framework, a TUI, an MCP server) touches
one file; and `FileBrowser` / `RunRepository` / `ProcessSupervisor` can be injected, so tests
substitute fakes instead of monkeypatching — which is the repo's pluggability rule applied to
a new subsystem.

**Consequence.** `fastapi`/`uvicorn` are an optional extra, so a core install stays on the
original five runtime dependencies. The frontend is React + Vite (per the user's choice),
built into the package and committed, so `pip install` ships a working dashboard without
requiring node at install time; CI rebuilds it to prove it remains reproducible from source.

---

## D6 — Statistics are derived, never separately persisted

**Decision.** Every dashboard number is computed on demand from `state.json`. No new
persisted state, no counters.

**Why.** Same reasoning as `compute_run_usage_totals` and the monitor bookkeeping helpers: a
separately maintained counter can drift from the audit trail and can double-count on resume;
a pure function of persisted state cannot. Recomputation is O(tasks) and resume-safe by
construction.

**Consequence.** `compute_run_active_seconds` was promoted from a private helper in
`breakers.py` into `models.py`, with `breakers.py` delegating to it, so the dashboard's
"actual time" stat and the `run_active_seconds` circuit breaker share one definition and
cannot diverge.

---

## D7 — No authentication in this release; loopback by default

**Decision.** Per the requirement to leave credentials and login out for now, the dashboard
is unauthenticated. `ao ui` binds `127.0.0.1` by default and prints a prominent warning when
an operator binds anything else.

**Why this is tolerable now, and only now.** The threat model is a single developer on their
own machine. The server can read any file under its roots and can spend money by launching
runs, so exposure off-box is the entire risk — and loopback plus a warning addresses exactly
that, while the path guard and the option allow-list bound what an authenticated-by-locality
caller can do.

**Consequence.** This blocks every hosted or multi-user scenario. Authentication and
configurable secrets are the top item on the roadmap (§3.1); nothing here should be treated
as a durable security posture.

---

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Precedence chain for general instructions (ADR-0003-consistent) | A single CLI flag would silently drop the workspace's house rules — the opposite of the requirement (D1). |
| `{general_instructions}` placeholder only | Silently drops rules for every pre-existing `agents.json` and the default template (D2). |
| Prompt delivered as a general instruction | Would make a per-run input workspace-sticky; the next run inherits the last prompt (D3). |
| Run the engine in-process in the web server | A dashboard restart kills in-flight runs; a run crash takes the UI down (D4). |
| stdlib `http.server`, zero new deps | Hand-rolled routing/JSON/static serving and weaker test ergonomics, for a dependency that is already optional. |
| Serve the frontend from a CDN or require `npm` at install | A local-first ops dashboard must work offline and without node. |
| Persist dashboard stats as counters | Drift and double-counting on resume (D6). |
