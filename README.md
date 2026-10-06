# agent-orchestrator

A **config-driven, DAG-based agent orchestration engine** (Python) that drives multi-step,
multi-agent workflows to completion — with retries, resume, and full artifact traceability.

---

## Contents

- [Install](#install)
- [Quickstart](#quickstart)
- [Core concepts](#core-concepts)
- [Spec files](#spec-files)
- [CLI reference](#cli-reference)
- [Dashboard (`ao ui`)](#dashboard-ao-ui)
  - [Authentication](#authentication)
- [Workflow templates](#workflow-templates)
- [General instructions](#general-instructions)
- [Environment variables](#environment-variables)
- [Per-project config file](#per-project-config-file)
- [Parallel execution](#parallel-execution)
- [Result cache (opt-in)](#result-cache-opt-in)
- [Configuring multiple repos](#configuring-multiple-repos)
- [Workspace-only mode (no repo set)](#workspace-only-mode-no-repo-set)
- [Schema reference](#schema-reference)
- [Advanced workflows](#advanced-workflows)
- [Repo layout](#repo-layout)
- [Development](#development)

---

## Install

### One-command install (recommended)

Run the bundled installer from the repo root.  It checks for `uv`, installs it
if missing, then installs `ao` as a global tool:

```bash
bash install.sh
```

After the script finishes, `ao` is on your `$PATH`.  If your shell doesn't pick
it up immediately, restart it or run:

```bash
source "$HOME/.local/bin/env"
```

### Beta flavor (parallel install)

The installer also supports installing a second, fully-separate **beta** snapshot
alongside the stable one — useful for trying an unreleased branch without touching your
working `ao`:

```bash
AO_FLAVOR=beta bash install.sh     # or: bash install.sh --flavor beta
```

| | stable (default) | beta |
|---|---|---|
| commands | `ao`, `ao-bench` | `ao-beta`, `ao-bench-beta` |
| uv tool/bin dirs | uv's defaults | isolated under `~/.local/share/ao-beta/` |
| commit stamp | `installed.commit` | `beta.commit` |

`--flavor <name>` (CLI flag) overrides `AO_FLAVOR` (env var); an unrecognized value is
rejected. `AO_FLAVOR` unset behaves exactly like the single-flavor installer always has.

**`.ao/config.yaml` and every `AO_*` env var are shared between flavors** — they're
resolved per-project at runtime, not by the installer, so there's nothing to namespace.
Don't add a per-flavor config or env prefix for them. See
[`docs-md/install-flavors.md`](docs-md/install-flavors.md) for the full rationale.

### Manual install (development)

```bash
# Requires Python 3.11+  (uv is the preferred package manager)
uv pip install -e ".[dev]"   # dev extras: pytest, ruff, mypy

# The `ao` CLI is now available:
uv run ao --help
```

---

## Quickstart

### 1. Scaffold a project config

In your project directory:

```bash
ao init
```

This creates `.ao/config.yaml` with commented example fields.  Edit it:

```yaml
workflow: path/to/workflow.json
reposets: path/to/reposet.json
agents:   path/to/agents.json
```

Once the config is in place, `ao` discovers it automatically when you run any
command from that directory (or any subdirectory up to the git root):

```bash
ao validate    # no flags needed
ao run
```

Explicit flags always override the config file:

```bash
ao validate --workflow other.json   # overrides config
```

### 2. Validate your specs

```bash
ao validate \
  --workflow  specs/examples/workflow.json \
  --reposets  specs/examples/reposet.json \
  --agents    specs/examples/agents.json
```

Expected output:

```
OK: all specs valid
```

### 3. Run a workflow

```bash
ao run \
  --workflow  specs/examples/workflow.json \
  --reposets  specs/examples/reposet.json \
  --agents    specs/examples/agents.json
```

The engine prints a status table on completion.  Exit code is `0` on success,
`1` on failure.

### 4. Resume or inspect a run

```bash
# Resume an interrupted run by its run ID (completed tasks are skipped):
ao resume --run-id <run-id> --workflow ... --reposets ... --agents ...

# Check the status of any past run:
ao status --run-id <run-id> --workflow ... --reposets ... --agents ...
```

---

## Core concepts

| Concept | What it is |
|---------|-----------|
| **Workflow** | A named DAG of tasks, described in a JSON/YAML file and validated against a schema. |
| **Task** | One unit of work: an `agent`, an `instruction` file path, declared `inputs` and `outputs`, optional `depends_on`, retries, and timeout. |
| **Agent** | A named executor configuration (e.g. `claude_cli`, `fake`). Agents receive only file paths — never file contents (context-hygiene invariant). |
| **RepoSet** | A named binding of one or more repos to a `workspace_root`. A single orchestrator can drive many different repo sets. |
| **Artifact** | Any file path produced or consumed by a task. The engine tracks whether outputs exist to decide skip/retry/resume. |
| **Run** | One execution of a workflow. Run state is persisted atomically so a run can be resumed after failure or interruption. |

### How the engine works

```
workflow.json  ──► DAG build ──► topological sort ──► for each task:
                                                         1. skip if outputs already exist (resume/idempotency)
                                                         2. assert all declared inputs exist on disk
                                                         3. run agent with retry/timeout
                                                         4. verify declared outputs were produced
                                                         5. save run state atomically
```

Dependencies can be declared two ways — both are honoured:
- **Explicit**: `"depends_on": ["task-a", "task-b"]`
- **Implicit**: if task B lists an output of task A as an input, the engine infers the edge automatically.

---

## Spec files

Three files define a complete orchestration:

| File | Schema | Purpose |
|------|--------|---------|
| `workflow.json` | `specs/workflow.schema.json` | DAG of tasks, triggers, defaults |
| `reposet.json` | `specs/reposet.schema.json` | Named repo bindings and workspace roots |
| `agents.json` | `specs/agents.schema.json` | Agent registry (executor, prompt template, etc.) |

Working examples live in [`specs/examples/`](specs/examples/):

```
specs/examples/
  workflow.json        — 3-task design → implement → test pipeline
  workflow-no-repo-set.json — 2-task workspace-only example (no `repo_set`, no reposets file)
  reposet.json         — two named repo sets (default-set, experiment-set)
  agents.json          — architect, developer, tester agents (claude_cli executor)
  instructions/        — example instruction files referenced by path from workflow.json
    design.md
    implement.md
    test.md
```

---

## CLI reference

```
ao init       Scaffold a per-project .ao/config.yaml (run once per project)
ao templates  List available workflow templates (built-in + workspace-registered)
ao new        Scaffold a run instance from a workflow template
ao validate   Validate workflow, reposets, and agents specs (no execution)
ao run        Run a workflow from scratch
ao resume     Resume a previously interrupted or failed run
ao status     Show current status of a run
ao prune      Remove stale run artifacts from a workspace (reclaim disk space)
ao cache      Manage the opt-in RESULT cache: ls | stats | show | rm | prune | clear | verify
ao ui         Serve the browser dashboard (needs the optional `ui` extra)
ao auth       Manage dashboard accounts, TOTP and lockouts (see Authentication)
ao report-usage     Cross-run cost/retry/rework rollup by (agent, model, effort) + verdicts, feedback, survival
ao report-survival  How much of a run's code changes survived into a ref (git only)
ao rate       Record your own good/ok/bad rating of a run or task (local, no telemetry)
```

Usefulness signals (verdicts, diff survival, your ratings) are documented in
[`docs-md/usage-signals-hld.md`](docs-md/usage-signals-hld.md); the dashboard's **Usage** tab shows the same rollup.

All commands accept:

| Option | Description |
|--------|-------------|
| `--workflow PATH` | Path to workflow JSON or YAML |
| `--reposets PATH` | Path to reposets JSON or YAML (optional when the workflow has no `repo_set` — see [Workspace-only mode](#workspace-only-mode-no-repo-set)) |
| `--agents PATH` | Path to agents JSON or YAML |

If a flag is omitted, AO checks (in order):
1. The matching environment variable (`AO_WORKFLOW`, `AO_REPOSETS`, `AO_AGENTS`)
2. The nearest `.ao/config.yaml` (or `ao.yaml`) found by walking up from the current directory

`ao resume` and `ao status` also require `--run-id RUN_ID`. `ao status` additionally accepts
`--workspace PATH` (or `AO_WORKSPACE_ROOT`) as a shortcut so you can check a run without
resupplying the full `--workflow`/`--reposets`/`--agents` triplet.

### `ao prune`

Run state accumulates under `<workspace_root>/.orchestrator/runs/` — one directory per run.
`ao prune` reclaims disk space by deleting old run directories, the same way `docker system
prune` cleans up unused containers:

```bash
# Preview what would be deleted (runs older than 7 days, the default)
ao prune --workspace /path/to/workspace --dry-run

# Actually delete runs older than 30 days
ao prune --workspace /path/to/workspace --older-than 30

# Delete every run directory regardless of age
ao prune --workspace /path/to/workspace --older-than 0
```

| Option | Default | Description |
|--------|---------|-------------|
| `--workspace, -w PATH` | — (required) | Workspace root to prune |
| `--older-than DAYS` | `7` | Delete runs older than this many days (`0` = all) |
| `--dry-run` | `false` | Print what would be deleted without deleting |

---

## Dashboard (`ao ui`)

A browser dashboard for browsing the workspace, controlling runs, and reading run
statistics.  Needs the optional `ui` extra:

```bash
uv sync --extra ui                    # in this repo
pip install 'agent-orchestrator[ui]'  # installed elsewhere

ao ui                                 # http://127.0.0.1:8765
ao ui --port 9000 --workspace /path/to/repo --open
```

| Option | Default | Description |
|--------|---------|-------------|
| `--host HOST` | `127.0.0.1` | Interface to bind.  Loopback by default; unauthenticated unless `--auth` — see [Authentication](#authentication) |
| `--port, -p PORT` | `8765` | Port to listen on |
| `--workspace, -w PATH` | cwd | Workspace root to serve.  Env: `AO_WORKSPACE_ROOT` |
| `--open` | `false` | Open the dashboard in your browser once it is serving |
| `--reload` | `false` | Auto-reload on code changes (development) |
| `--auth / --no-auth` | off | Require login (local accounts). Env: `AO_UI_AUTH`. See [Authentication](#authentication) |
| `--auth-totp off\|optional\|required` | `off` | TOTP policy with `--auth`. Env: `AO_UI_AUTH_TOTP` |
| `--auth-dir DIR` | `~/.config/ao/auth` | Credential store directory. Env: `AO_AUTH_DIR` |

**What it does**

- **Files** — browse directories and read files anywhere under the workspace, *including
  hidden dotfiles and binaries*.  Paths that would escape the workspace (traversal strings
  or symlinks) are refused.
- **Runs** — workspace-wide totals (runs, tasks, cost, tokens, wall time, actual execution
  time) and the same breakdown per run, with a per-task table and the captured CLI log.
- **New run** — pick a workflow, type a prompt, optionally override model / effort /
  parallelism / budget, and start it.  The prompt is written to the workflow's declared
  `prompt_path`, exactly as `ao run --prompt` does.
- **Run graph** — in a run's detail view, switch the Tasks section from **Table** to **Graph**
  for a pannable, zoomable canvas of every task, including dynamically injected ones. Toggle
  between **Execution order** (the dependency DAG, with each task's actual start order `#n`) and
  **Spawned by** (which task created which, for `emit_tasks` and loop clones). Hover or focus a
  node for a quick card with status, duration, cost, and retries. Click a node, or pick a search
  result, to pin a detail panel with timing, token usage, dispatches, and navigable
  parent/children/dependency links. Live runs refresh in place. Runs from before this feature
  still render, with a banner explaining which data is missing. See
  [`docs-md/run-graph-canvas-hld.md`](docs-md/run-graph-canvas-hld.md).
- **Live task activity** — a fixed three-row **Now running** box tops every run page (and a
  compact one sits under each live run in the list): per running task the model, effort, turns,
  live tokens (`~` = estimate, a lower bound until the task settles), cost of finished attempts,
  elapsed time, the last tool call, and an *idle* chip after 5 minutes without transcript output.
  More than three running tasks scroll inside the same box; it never grows or collapses. Polling
  pauses while the page (or workspace tab) is hidden. See
  [`docs-md/live-activity-and-tabs-hld.md`](docs-md/live-activity-and-tabs-hld.md).
- **Tabs** — the dashboard is a tabbed workspace: open runs, tasks, graphs and files side by side
  as tabs (closable, drag or Alt+←/→ to reorder). Plain click navigates the current tab,
  Ctrl/Cmd/middle-click or the ⧉ button opens a new tab. The tab set persists in the browser and
  the active tab is in the URL hash (`#/run?id=…`), so a link restores it, including in a new
  browser tab.
- **Run control** — resume an interrupted run, cancel a running one, delete old runs.
- **Workspace** — the effective [general instructions](#general-instructions), with a flag
  showing whether each path actually resolves.

Runs start as separate `ao run` processes, so closing the browser (or restarting the
dashboard) does not stop them.

### Authentication

Login is **opt-in** and off by default: until you enable it the dashboard is unauthenticated, can
read any file under the workspace and can start runs that cost money, so it binds loopback.
Never bind another interface without enabling auth (and, off-loopback, a TLS proxy). Full guide:
[`docs-md/dashboard-authentication.md`](docs-md/dashboard-authentication.md).

**Enable (first-user bootstrap).** Accounts are created from the CLI only:

```bash
ao auth add-user alice            # prompts for a password (min 12 chars); creates the store
ao ui --auth                      # or AO_UI_AUTH=1, or ui.auth.enabled: true in .ao/config.yaml
ao auth status                    # effective settings + where each came from, permissions, counts
```

`ao ui --auth` with no accounts exits 78 (a configuration error) instead of serving. The workspace
config (`ui.auth` in `.ao/config.yaml`, see [Per-project config](#per-project-config-file)) **may
only tighten** settings; anything that would weaken a default is refused with exit 78, and
`trusted_proxies` is env-only. A workspace-config-only `enabled: false` also refuses to start
while accounts exist; disable on purpose with `--no-auth` or `AO_UI_AUTH=0`.

**Two-factor (TOTP) policy** (`--auth-totp` / `AO_UI_AUTH_TOTP` / `ui.auth.totp`):

- `off` (default) / `optional` / `required`. Users enroll in the account menu (QR code + manual
  secret) or on the host with `ao auth enable-2fa USER`; ten single-use **recovery codes** are
  shown once.
- Under `required`, a user who has not enrolled needs an **operator-issued enrollment token** at
  first login (`ao auth enrollment-token USER`, valid 60 minutes), even if the account was created
  without `--require-totp`. To switch an existing deployment to `required`, issue one per
  unenrolled user and hand it over.
- Under `off`, users who are already enrolled are **still asked for a code** (enrollment is
  sticky). `ao auth status` prints these notes.
- Lost device: sign in with a recovery code and re-enroll, or `ao auth reset-2fa USER` prints a
  fresh enrollment token. Remote enrollment over plain HTTP is refused; enroll over TLS, on the
  host, or through an SSH tunnel.
- A TOTP policy below `required` that comes only from the config file warns; pin it with
  `AO_UI_AUTH_TOTP` / `--auth-totp`.

**Hub and dashboards are separate logins.** Cookies are scoped by host, not port, so the
`ao service` hub and every workspace dashboard are separate *realms*, each with its own session
and login. Sessions live in memory (a restart signs everyone out); the idle limit is 30 minutes
and the hard limit 12 hours (`AO_UI_AUTH_IDLE_MINUTES`, `AO_UI_AUTH_ABSOLUTE_HOURS`).

**Plain HTTP, TLS proxies.** `ao` serves plain HTTP; a non-loopback bind prints a loud warning.
For remote access put a TLS reverse proxy in front and set `AO_UI_AUTH_TRUSTED_PROXIES`
(env only, e.g. `127.0.0.1`); the proxy must keep the `Host` header and send
`X-Forwarded-Proto`/`-For`. Without it, proxied requests count as remote, the server logs a
`proxy_suspected` warning, and all remote users share one throttle bucket. With
`trusted_proxies=127.0.0.1`, bind the app to a unix socket or firewall its port: any local process
could otherwise claim a client address via `X-Forwarded-For`.

**Where things live.** Credentials (password hashes, **TOTP seeds in clear**, recovery-code
hashes) are in `~/.config/ao/auth/` (`AO_AUTH_DIR`); lockouts and the audit log in
`~/.local/state/ao/auth/` (`AO_AUTH_STATE_DIR`). Because a stolen `users.json` holds the second
factor too, back the credential directory up **encrypted only**. The file browser never serves
either directory or `~/.config/ao/service.env`.

**Threat model, in one paragraph.** Authenticated means full access (no roles); same-user
processes, including the agents `ao` runs, are out of scope. A cookie harvested by a local
listener cannot call the API, because every API call also needs a per-session proof header kept in
the page; the remaining residuals are that the hub index page still renders for a bare cookie and
that cookie tossing can force a logout. `/api/docs` (and the hub's `/docs`) cannot be used in a
browser with auth on; `--port 0` is refused with auth on.

**Service.** `ao service install --auth` bakes `--auth` into the systemd unit (preferred), or put
`AO_UI_AUTH=1` in `~/.config/ao/service.env`, then `ao auth add-user`, then
`systemctl --user restart ao`. The unit carries `RestartPreventExitStatus=78`, so a bad auth
configuration (exit 78) is not restart-looped. **Stale-install warning:** a global `ao` that
predates this feature silently ignores `AO_UI_AUTH`/`ui.auth` (only an explicit `--auth` flag
errors), so the dashboards would start *unauthenticated* with no error. After upgrading run
`bash install.sh --force`, then verify with `ao auth status` (it exists only in new builds).

> **Release note.** Dashboard authentication is new and opt-in: `ao auth`, `--auth`,
> `--auth-totp`, `--auth-dir`, `ao service install --auth`. Nothing changes unless enabled.

**Deferred:** UI-based dynamic workflow generation (building or editing a DAG in the browser) is
on the roadmap, not in this release. The run graph above is read-only.

---

## Workflow templates

Pre-built, parameterized workflow skeletons that scaffold a complete run instance — with a
rendered workflow DAG, prompt file, and instruction assets — in one command. Templates are
registered at config time and are never authored in the UI.

### Discovery and usage

List available templates:

```bash
ao templates
```

Scaffold a run instance from a template (the cheapest example is the builtin `routed-runner`
with documentation route):

```bash
ao new routed-runner my-task --param repo_set=main --param type=documentation
```

The command produces:
- An instance directory with ID `e-XXXXXX-my-task` (workspace-relative)
- A rendered `workflow.json` validated against your agents and reposets
- A `prompt.md` file (user-editable, survives re-scaffolding on the same ID)
- Shared instruction assets materialized once per workspace

Then run the instance:

```bash
ao run --workflow workflows/routed-runner/runs/e-XXXXXX-my-task/workflow.json ...
```

Or start it from the dashboard's **"From template"** mode, which auto-discovers the scaffolded
workflow.

### Registration

Templates are discovered from three sources (in precedence order):

1. **Workspace-registered** — paths listed in `.ao/config.yaml`:
   ```yaml
   templates:
     - path/to/my-template         # a template directory
     - templates/                   # a directory of template directories (scanned 1 level)
   ```
2. **Built-in** — shipped with this package (e.g., `routed-runner`).
3. **Ad-hoc path** — `ao new /path/to/template/dir …` accepts a directory directly.

For more detail, see the [workflow templates HLD](docs-md/workflow-templates-hld.md).

---

## General instructions

Instruction files that apply to **every task of every run** — house rules, coding standards,
review checklists — declared **once per workspace** rather than per run.  They reach tasks
even when `ao run` is invoked without mentioning them.

Declare them in any of four places:

```yaml
# .ao/config.yaml — the "define once per workspace" home.
# Paths are relative to this file's directory.
general_instructions:
  - instructions/house-rules.md
  - instructions/coding-standards.md
```

```bash
export AO_GENERAL_INSTRUCTIONS="/abs/a.md:/abs/b.md"   # os.pathsep-separated
ao run --general-instruction extra.md                   # repeatable, one invocation
```

```jsonc
// workflow.json — instructions this particular workflow requires
{ "general_instructions": ["instructions/api-conventions.md"] }
```

**The layers are additive, not a precedence chain.**  This is the one setting in AO that
deliberately does *not* follow `CLI > env > config > default`: the effective set is the
**union** of all four layers, de-duplicated.  A `--general-instruction` on the command line
*adds* to your workspace rules — it never silently replaces them.  (See
[ADR-0010](docs-md/adr/ADR-0010-dashboard-architecture-and-general-instructions.md) D1.)

Notes:

- Only **paths** are passed to agents, never file contents — the same context-hygiene
  invariant the rest of the engine holds to.
- A path that cannot be resolved is skipped with a log warning rather than failing the run;
  `ao validate` and the dashboard's Workspace view are where you catch a typo.
- Instruction file sizes count toward the token estimate, so budget gating stays accurate.

### Run prompts are a different thing

A **run prompt** says *what this run should do*; **general instructions** say *how every task
should behave*.  Give a workflow a `prompt_path`, list it in a task's `inputs`, and write it
per run:

```jsonc
// workflow.json
{
  "prompt_path": "prompts/run.md",
  "tasks": [{ "id": "plan", "agent": "architect",
              "instruction": "instructions/plan.md",
              "inputs": ["prompts/run.md"] }]
}
```

```bash
ao run --prompt "Add rate limiting to the /orders API"
ao run --prompt-file ./feature-request.md
```

`ao resume` has no `--prompt`: the prompt is a per-run input artifact the original run
already materialized, and rewriting it mid-run would make the run irreproducible from its own
artifacts.  Start a new run to change the prompt.

---

## Environment variables

All runtime settings can be provided via environment variable.  Precedence is
always: **CLI flag > env var > `.ao/config.yaml` > built-in default**.

| Variable | CLI equivalent | Description |
|----------|---------------|-------------|
| `AO_WORKFLOW` | `--workflow` | Default path to the workflow file |
| `AO_REPOSETS` | `--reposets` | Default path to the reposets config |
| `AO_AGENTS` | `--agents` | Default path to the agents config |
| `AO_WORKSPACE_ROOT` | — | Override the `workspace_root` from the reposet (useful in CI) |
| `AO_MODEL` | `--model` | Claude model for all agents (alias `sonnet`/`opus`/`haiku` = latest; or a pinned id e.g. `claude-sonnet-5-5`) |
| `AO_EFFORT` | `--effort` | Effort level: `low`, `medium`, or `high` |
| `AO_MAX_ATTEMPTS` | `--max-attempts` | Max task attempts (overrides workflow `defaults.retries.max_attempts`) |
| `AO_MAX_TURNS` | `--max-turns` | Max turns per Claude invocation (overrides effort-derived value) |
| `AO_MAX_PARALLEL` | `--max-parallel` | Max independent ready tasks to run at once (default: `1` = serial); see [Parallel execution](#parallel-execution) |
| `AO_RECORD_GIT_HEADS` | `--record-git-heads/--no-record-git-heads` | Record git HEADs at run/task start+settle for `ao report-survival` serial attribution (default: on; off = no head-recording git calls, coarser survival attribution — isolation `landed_ranges` still recorded) |
| `AO_QUOTA_MAX_WAIT_SECONDS` | `--quota-max-wait` | Max seconds to wait during a quota-exhaustion episode before failing (default: 21600 = 6 h) |
| `AO_QUOTA_POLL_SECONDS` | `--quota-poll-interval` | Seconds between quota-exhaustion re-run attempts (default: 900 = 15 min) |
| `AO_GENERAL_INSTRUCTIONS` | `--general-instruction` | `os.pathsep`-separated instruction paths applied to **every** task; **additive**, not an override — see [General instructions](#general-instructions) |
| `AO_CACHE` | `--cache` / `--no-cache` | Opt-in **result cache** for the run: `1`/`true`/`yes`/`on`, `0`/`false`/`no`/`off`, or `shadow` (measure only); empty = unset; any other value (including `refresh`, which does not exist) = off plus a warning. See [Result cache (opt-in)](#result-cache-opt-in) |
| `AO_UI_WORKSPACE` | `ao ui --workspace` | Workspace the dashboard serves (used by `ao ui --reload`) |
| `AO_UI_AUTH` | `ao ui/service run --auth/--no-auth` | Require dashboard/hub login (default off); see [Authentication](#authentication) |
| `AO_UI_AUTH_TOTP` | `--auth-totp` | TOTP policy: `off` / `optional` / `required` |
| `AO_AUTH_DIR` | `--auth-dir`, `ao auth --auth-dir` | Credential store directory (default `~/.config/ao/auth`) |
| `AO_AUTH_STATE_DIR` | — | Lockout/audit state directory (default `~/.local/state/ao/auth`) |
| `AO_UI_AUTH_TRUSTED_PROXIES` | — (env only) | Comma-separated reverse-proxy addresses whose `X-Forwarded-*` headers are trusted |
| `AO_UI_AUTH_IDLE_MINUTES`, `AO_UI_AUTH_ABSOLUTE_HOURS` | — | Session idle (default 30) / absolute (default 12) limits |
| `AO_UI_AUTH_LOCKOUT_THRESHOLD`, `AO_UI_AUTH_LOCKOUT_BASE_SECONDS`, `AO_UI_AUTH_LOCKOUT_MAX_SECONDS`, `AO_UI_AUTH_ADDRESS_THRESHOLD` | — | Account lockout and per-address throttle tuning (defaults 5 / 30 s / 900 s / 20) |
| `AO_UI_AUTH_MIN_PASSWORD_LENGTH`, `AO_UI_AUTH_TOTP_ISSUER` | — | Minimum password length (default 12); TOTP issuer label (default `ao@<host>`) |

---

## Per-project config file

Create `.ao/config.yaml` (or `ao.yaml`) at your project root so you don't have
to pass `--workflow`/`--reposets`/`--agents` every time.  Run `ao init` to
scaffold a commented starter file.

```yaml
# .ao/config.yaml
workflow:  path/to/workflow.json   # relative to this file
reposets:  path/to/reposet.json
agents:    path/to/agents.json

# workspace_root: /path/to/workspace  # optional: overrides reposet value

# env:                  # optional: set env vars before any ao command
#   MY_TOKEN: abc123

# --- Runtime execution settings (env var equivalents shown) ---
# max_attempts: 3          # AO_MAX_ATTEMPTS — max task attempts (1 = no retry)
# max_turns: 30            # AO_MAX_TURNS    — max turns per claude invocation
# model: sonnet # AO_MODEL        — claude model for all agents
# effort: medium           # AO_EFFORT       — low / medium / high
# max_parallel: 1          # AO_MAX_PARALLEL — max independent ready tasks run at once (1 = serial)
# record_git_heads: true  # AO_RECORD_GIT_HEADS — record git HEADs for report-survival

# --- Claude usage-quota exhaustion handling ---
# quota_max_wait_seconds: 21600   # AO_QUOTA_MAX_WAIT_SECONDS — give up after 6h
# quota_poll_seconds: 900         # AO_QUOTA_POLL_SECONDS     — poll every 15 min

# --- Dashboard authentication (opt-in). The workspace file may only TIGHTEN settings: a value
# that weakens a default is refused at startup (exit 78); trusted_proxies is env-only. ---
# ui:
#   auth:
#     enabled: true                 # AO_UI_AUTH   (enabled: false here, with accounts, refuses to start)
#     totp: required                # AO_UI_AUTH_TOTP  (below `required` from config only: warns)
#     session_idle_minutes: 15      # AO_UI_AUTH_IDLE_MINUTES      (default 30; lower only)
#     session_absolute_hours: 8     # AO_UI_AUTH_ABSOLUTE_HOURS    (default 12; lower only)
#     lockout_threshold: 3          # AO_UI_AUTH_LOCKOUT_THRESHOLD (default 5; lower only)
#     min_password_length: 16       # AO_UI_AUTH_MIN_PASSWORD_LENGTH (default 12; raise only)
# --- Result cache (off by default; see "Result cache (opt-in)" below) ---
# cache:
#   enabled: false            # AO_CACHE=1|0|shadow / --cache / --no-cache (CLI/env win)
#   mode: "on"                # "on" | "shadow" (measure only); quote it, bare on = YAML true
```

**Discovery**: AO walks up from your current directory, stopping at the git root
or filesystem root.  The first `.ao/config.yaml` (preferred) or `ao.yaml` it
finds is used.

**Precedence**: `--flag` > `AO_*` env var > config file value > built-in default.

---

## Claude usage-quota exhaustion

When Claude hits its session/daily/weekly usage limit, `ao` detects the message,
waits, and retries automatically — it does **not** count quota exhaustion as a
task failure or consume retry attempts.

### How it works

1. `ClaudeCliExecutor` detects the quota message (`"You've hit your * limit"`) in
   the subprocess output via a single regex (one editable location in `claude_cli.py`).
2. The engine reverses any token-budget charge for the task, sleeps
   `quota_poll_seconds` (default: 15 min), then re-queues the same task.
3. This repeats until either:
   - The task succeeds (quota replenished) → the max-wait timer resets.
   - `quota_max_wait_seconds` elapses since the episode began → the run fails with
     `event="quota.max_wait_exceeded"`.

The max-wait timer is **per exhaustion episode** and resets after each
successfully completed task, so a long run with occasional quota hits does not
accumulate wait time unfairly.

### Configuration

```bash
# Wait up to 8 hours; poll every 10 minutes
ao run --quota-max-wait 28800 --quota-poll-interval 600 --workflow ...

# Or via env vars (useful in CI)
export AO_QUOTA_MAX_WAIT_SECONDS=28800
export AO_QUOTA_POLL_SECONDS=600
ao run --workflow ...
```

Both settings can also be set in `.ao/config.yaml` (see [Per-project config file](#per-project-config-file)).

---

## Parallel execution

By default `ao` runs one task at a time (`max_parallel=1`), always in the same deterministic order.
You can opt into running multiple independent *ready* tasks concurrently — up to a bound — which
speeds up wide DAGs where wall-clock time is dominated by the `claude` subprocess waiting on its own
I/O rather than on CPU.

### How it works

1. Each "wave," the engine computes the set of tasks whose dependencies have already settled and
   dispatches up to `max_parallel` of them at once on a bounded thread pool.
2. Structural/routing tasks (`emit_tasks`, loop-gate tasks, router tasks) always run **solo**: the
   engine drains everything else in flight before starting one and admits nothing new until it
   settles, so dynamic task injection, loops, and routing stay exactly as safe as they are today.
3. Every `RunState` mutation, budget/quota/breaker decision, and `save()` call happens on the main
   thread only — workers only execute the task and hand back a result. `max_parallel=1` (the
   default) is byte-identical to the serial engine: same dispatch order, same events, same final
   state.

Full design: [`docs-md/adr/ADR-0007-parallel-task-execution.md`](docs-md/adr/ADR-0007-parallel-task-execution.md).

### Configuration

```bash
# Run up to 4 independent ready tasks concurrently
ao run --max-parallel 4 --workflow ...

# Or via env var (useful in CI)
export AO_MAX_PARALLEL=4
ao run --workflow ...
```

Also settable in `.ao/config.yaml` (see [Per-project config file](#per-project-config-file)).

**Precedence**: `--max-parallel` > `AO_MAX_PARALLEL` > `.ao/config.yaml: max_parallel` > default `1`.
`0` is treated as unset and falls through to the default (serial, **no error**) — the same
treatment `--quota-max-wait 0` / `--max-attempts 0` already get. A negative value is rejected:
`ao run --max-parallel -1` exits `1` with `ERROR: --max-parallel must be >= 1`.

---

## Result cache (opt-in)

> **New: opt-in result cache.** `ao run --cache` (or `AO_CACHE=1|shadow`) reuses an identical,
> previously successful task's declared outputs instead of re-dispatching the agent, for tasks
> whose workflow opts in with `cache: true`. Off by default; `--no-cache` is a kill switch;
> `ao cache ls|stats|show|rm|prune|clear|verify` manage it. Not Claude prompt caching.

This is a **result** cache: when a task is identical to one that already succeeded (same rendered
prompt, same `claude` argv and CLI version, same instruction / input files, same prior output
content, same git HEADs), `ao` restores the earlier run's declared **output files** and marks the
task `succeeded` without dispatching an agent, spending a retry or charging a budget. It is **not**
Anthropic *prompt* caching (`cache_read_input_tokens`, `--autocompact`, `ao report-timing`'s cache
hit rate), which is a separate, always-on provider feature.

Agent output is non-deterministic: **a hit replays ONE earlier successful result**, it does not
re-sample. That is why the cache is a *double opt-in*.

### Turning it on

| Who | How |
|-----|-----|
| **Operator** (per run) | `--cache` / `--no-cache` on `ao run` and `ao resume` > `AO_CACHE` (`1`, `0`, `shadow`) > `.ao/config.yaml` `cache.enabled` + `cache.mode` > **off**. `--no-cache` / `AO_CACHE=0` always win. |
| **Workflow author** (per task) | `cache: true` on a task, or `defaults.cache: true` with `cache: false` on exclusions. Task beats defaults; `false` always means never. A task nobody opts in is never looked up. |

Both must agree. The author opts in a task only when its **whole effect is captured by its declared
`outputs`** (see the "Result cache (opt-in)" section of
[`.claude/skills/workflow-authoring/SKILL.md`](.claude/skills/workflow-authoring/SKILL.md)).

```bash
ao run --cache --workflow ...            # looks up opted-in tasks, stores their successes
AO_CACHE=shadow ao run --workflow ...    # measure only: records "would hit", restores nothing
ao run --no-cache --workflow ...         # kill switch (beats AO_CACHE and config)
```

`ao run` prints one stderr banner, for example
`Result cache: on (source=cli), 1 of 2 static task(s) opted in, at <workspace>/.orchestrator/cache`
(in `on` mode followed by `(agent-writable; avoid for untrusted prompts)`), and a summary line after
the run, `Result cache: hits=2 (saved ~$1.2345 est., ~54000 tokens, ~312s) would_hits=0 misses=1
stored=1 ineligible=1`. Per-task detail is in `status.json` (`tasks[].result_cache`), `ao
report-usage` (`result_cache` in `--json`), `ao report-outcomes` (`settle_reason: cached`) and the
dashboard ("cached" tag, "Result cache" tile). Avoided spend (`saved_*`) is an estimate and is never
netted into real cost.

### Configuration (`.ao/config.yaml`)

```yaml
# cache:
#   enabled: false            # AO_CACHE=1|0|shadow / --cache / --no-cache (CLI/env win)
#   mode: "on"                # "on" | "shadow" (measure only); quote it, bare on = YAML true
#   max_bytes: 1073741824     # total size cap; least-recently-used entries evicted to 90%
#   max_entry_bytes: null     # results bigger than this are not stored (default min(64 MiB, max_bytes))
#   ttl_days: 30              # entries older than this are misses (null = never expire)
#   include_repo_heads: true  # key includes HEAD of each git repo in the repo set
#   max_input_bytes: 536870912  # per-lookup hashing cap (bigger => task not cacheable)
#   max_input_files: 20000      # per-lookup file-count cap for directory inputs
```

`ao init` scaffolds this block (commented). Committing `enabled: true` turns the cache on for every
clone and service run of the repo; do that only on purpose. The cache lives in
`<workspace>/.orchestrator/cache/` (mode `0o700`, self-ignoring) and is per workspace.

### Shadow mode

`AO_CACHE=shadow` (or `cache.enabled: true` with `cache.mode: "shadow"`) performs the full lookup and
key computation but **never restores**: every task still dispatches and still stores, and a valid
entry is recorded as a *would hit*, with the cost it would have avoided. Use it to measure the hit
rate before trusting `on`: `ao report-usage --json` -> `result_cache` (`lookups`, `would_hits`,
`avoidable_cost_usd`), and `ao cache stats --json` for store growth. The procedure, the decision rule
and a report template are in
[`docs-md/result-cache-g0-protocol.md`](docs-md/result-cache-g0-protocol.md) (the value check
itself has not been run yet; it is a deferred operator task that needs consent and a real workload).

### `ao cache` commands

Each takes `--workspace/-w PATH` (default `AO_WORKSPACE_ROOT`, then the repo set's `workspace_root`
of the workflow named in `.ao/config.yaml`, i.e. the workspace `ao run` uses) and `--json` (exactly
one JSON document, also on a non-zero exit). They work even when the run mode is off. Limits
(`max_bytes`, `ttl_days`) come from the `.ao/config.yaml` found from the current directory.

| Command | What it does |
|---------|--------------|
| `ao cache ls [--limit N] [--sort lru\|created\|size]` | List entries (key prefix, last used, created, outputs, bytes, source task/run, cost) |
| `ao cache stats` | Entries, bytes, limits, oldest/newest, expired entries, anomalies |
| `ao cache show KEY_OR_PREFIX` | One entry: provenance, outputs, blob presence, key components (prefix: 4-64 lowercase hex, unique) |
| `ao cache rm KEY_OR_PREFIX` | Remove exactly one entry: **the way to re-roll a bad cached result** (then re-run) |
| `ao cache prune [--max-bytes N] [--older-than DAYS] [--dry-run]` | Remove invalid, expired and least-recently-used entries, unreferenced blobs, stale temp files and stale restore leftovers |
| `ao cache clear [--yes]` | Delete every entry and blob (`--yes` required unless stdin is a terminal) |
| `ao cache verify` | Read-only integrity check; exit 1 on corruption |

Exit codes: `0` ok; `1` not found / ambiguous / store problem / refused / corruption found; `2`
bad argument (malformed prefix, bad `--sort`, out-of-range number, a `--workspace` that is not an
existing directory). `ao prune` does **not** touch the result cache; use `ao cache prune`. There is
no `refresh` mode, no `rm --run/--task` and no `verify --repair`.

### What to know before you rely on it

- **Deleting an output to force a redo restores the cached copy instead.** Use `ao cache rm <key>`
  and re-run, or `--no-cache`.
- With the default `skip_if_outputs_exist: true` the cache only helps when outputs are absent (clean
  checkout, fresh clone, deleted outputs). Pin full model ids, not aliases.
- A **fail-closed** eligibility check excludes tasks with hooks, isolation, `emit_tasks`, routing or
  loops, no outputs, a non-`claude` command, or an output under `.git` / `.claude` / `CLAUDE.md` / CI
  config; the reason is in `status.json`. Only a final settled success is stored, and only if the key,
  the repo HEADs and the tracked files outside the declared outputs did not change during the run.
- Accepted residuals (pass `--no-cache` for untrusted repositories or prompts): provider and endpoint
  environment (`ANTHROPIC_BASE_URL`, `CLAUDE_CODE_USE_BEDROCK`, `CLAUDE_CODE_USE_VERTEX`, region) is not
  in the key; the tracked-file guard runs `git status`, which can execute a `filter.<x>.clean` command
  from the agent-writable git config; uncommitted tracked edits made before the run are not in the key;
  the cache directory is agent-writable (same-uid forgery is not defended); cached outputs persist
  until `ao cache rm|clear|prune`.
- `ao-bench` always runs with `--no-cache` and `AO_CACHE=0`.

Design, threat model and as-built deviations:
[`docs-md/cross-run-result-cache-hld.md`](docs-md/cross-run-result-cache-hld.md) (read §0 first) and
[`docs-md/adr/ADR-0019-cross-run-result-cache.md`](docs-md/adr/ADR-0019-cross-run-result-cache.md).

---

## Configuring multiple repos

A single `reposet.json` can declare several named sets. Each set binds a `workspace_root`
(artifact paths are resolved relative to this) and one or more repos.

```json
{
  "version": "1.0",
  "repo_sets": {
    "monorepo": {
      "workspace_root": "/work/monorepo",
      "repos": [
        { "id": "api",      "path": "/work/monorepo/api",      "role": "primary" },
        { "id": "frontend", "path": "/work/monorepo/frontend",  "role": "support" }
      ]
    },
    "multi-repo": {
      "workspace_root": "/work",
      "repos": [
        { "id": "svc-a",  "path": "/work/svc-a",  "role": "primary" },
        { "id": "svc-b",  "path": "/work/svc-b",  "role": "support" },
        { "id": "shared", "path": "/work/shared",  "role": "support" }
      ]
    }
  }
}
```

A workflow references exactly one `repo_set` by name:

```json
{
  "id": "cross-service-feature",
  "repo_set": "multi-repo",
  ...
}
```

The engine passes all repo paths to the agent as `{repos}` in the prompt template — but
only as paths, never content. This is the **context-hygiene invariant**: the orchestrator
engine never reads artifact files; that's the agent's job.

See `specs/examples/reposet.json` for a complete working example.

---

## Workspace-only mode (no repo set)

A workflow does not have to name a `repo_set`. If `repo_set` is omitted (or `null` / `""`), `ao`
runs in **workspace-only mode**: no `reposets.json` is needed, agents get no repo paths, and every
input, output and the run state (`.orchestrator/`) live under a single workspace directory. Use it
for docs, research or data pipelines that are not git checkouts. A workflow that *does* declare a
`repo_set` behaves exactly as before (reposets still required).

```bash
# validate: only --workflow and --agents are needed
ao validate --workflow specs/examples/workflow-no-repo-set.json --agents specs/examples/agents.json

# run, naming the workspace explicitly (recommended)
ao run --workflow specs/examples/workflow-no-repo-set.json --agents specs/examples/agents.json \
       --workspace ./my-workspace
```

[`specs/examples/workflow-no-repo-set.json`](specs/examples/workflow-no-repo-set.json) is a two-task
example (`design` → `write-up`) whose inputs/outputs are workspace-relative paths.

**Workspace root resolution** (no-repo-set mode; first match wins):

1. `--workspace` / `-w` (on `ao run` and `ao resume`)
2. `AO_WORKSPACE_ROOT`
3. `workspace_root:` in `.ao/config.yaml`
4. the directory containing the discovered `.ao/` config
5. the current directory — `ao` prints `NOTE: ... using current directory as workspace` to stderr
   in this case, so pass `--workspace` if you did not mean to write `.orchestrator/` there

With a `repo_set` the rule is unchanged: `AO_WORKSPACE_ROOT` > the reposet's `workspace_root`
(`--workspace` also takes precedence there).

What to know:

- **Agents are still required** (`--agents`); only reposets become optional.
- **Containment:** artifact paths are resolved inside the workspace and escapes (`..`, absolute
  paths outside it, symlinks out) are rejected.
- **Git features degrade, not fail:** with no repos, `isolation: worktree` falls back to `none`
  (validate prints a warning), diff-survival records no repos, and the opt-in result cache keys on
  workspace content only.
- **`ao new`:** omitting `--param repo_set=...` scaffolds a workspace-only workflow and prints a
  NOTE (or a WARNING if a reposets file is configured, in case you forgot the param). The built-in
  `routed-runner` / `overseer-runner` templates still contain git-oriented stages (branch,
  final push) that only make sense with a repo.
- The dashboard (`ao ui` / `ao service`) can launch and boot-resume workspace-only runs.

Design: [`docs-md/no-repo-set-mode-hld.md`](docs-md/no-repo-set-mode-hld.md),
[`ADR-0022`](docs-md/adr/ADR-0022-no-repo-set-mode.md).

---

## Schema reference

Full schemas are in `specs/` and are authoritative (validated by `ao validate`):

| Schema | Location | Key required fields |
|--------|----------|-------------------|
| Workflow | `specs/workflow.schema.json` | `version`, `id`, `repo_set`, `tasks` |
| RepoSet | `specs/reposet.schema.json` | `version`, `repo_sets` |
| Agents | `specs/agents.schema.json` | `version`, `agents` |

### Task fields (workflow.json → tasks[])

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `id` | string | — | Unique task ID (`[a-z0-9][a-z0-9-_]*`) |
| `agent` | string | — | Key in agents.json |
| `instruction` | string (path) | — | Path to the instruction/prompt file |
| `inputs` | string[] | `[]` | Artifact paths that must exist before this task runs |
| `outputs` | string[] | `[]` | Artifact paths this task is expected to produce |
| `depends_on` | string[] | `[]` | Explicit task-ID dependencies |
| `retries.max_attempts` | int | `1` | Max total attempts (1 = no retry) |
| `retries.backoff_seconds` | float | `0` | Delay between attempts |
| `timeout_seconds` | int | — | Hard wall-clock limit per attempt |
| `skip_if_outputs_exist` | bool | `true` | Skip this task if all outputs exist (idempotency) |
| `cache` | bool | unset (= not opted in) | Result-cache **author opt-in** (also `defaults.cache`): `true` = this task's whole effect is captured by its declared outputs, so an identical previous success may be reused **when the operator also enables the cache**; `false` = never. See [Result cache (opt-in)](#result-cache-opt-in) |

### Trigger types (workflow.json → triggers[])

| Type | Extra fields | Example |
|------|-------------|---------|
| `manual` | — | `{"type": "manual"}` |
| `cron` | `schedule` (5/6-field cron), `timezone` | `{"type": "cron", "schedule": "0 7 * * *"}` |
| `event` | `event` (key) | `{"type": "event", "event": "pr-merged"}` |

### Agent executor types (agents.json → agents → executor)

| Value | Behaviour |
|-------|-----------|
| `claude_cli` | Runs `claude -p "<rendered-prompt>"` via subprocess |
| `fake` | Deterministic no-op for tests/dry-runs; touches declared outputs |

---

## Advanced workflows

Beyond a static task DAG, `workflow.json` supports dynamic task injection, bounded
iteration loops, and token-budget enforcement — used when a workflow's shape isn't known
until run time (e.g. a breakdown task fanning out into N implementation tasks, or a
review→fix loop that repeats until a gate task says stop):

| Feature | Schema field | Reference |
|---------|-------------|-----------|
| Dynamic task injection (incl. fan-out/aggregate and review-triggered fix pipelines) | `tasks[].emit_tasks` + `task_manifest_path` | [`docs-md/guide-dynamic-task-injection.md`](docs-md/guide-dynamic-task-injection.md), [`docs-md/e2e-playground-testing.md`](docs-md/e2e-playground-testing.md), [`specs/examples/workflow-dynamic.json`](specs/examples/workflow-dynamic.json), [`workflow-dynamic-fanout.json`](specs/examples/workflow-dynamic-fanout.json), [`workflow-dynamic-pipeline.json`](specs/examples/workflow-dynamic-pipeline.json) |
| Bounded loops (review/fix cycles) | `loops[]` (`body`, `gate_task_id`, `gate_output_path`) | [`docs-md/e2e-playground-testing.md`](docs-md/e2e-playground-testing.md), [`specs/examples/workflow-loop.json`](specs/examples/workflow-loop.json) |
| Token budget / rate limiting | `budget` (`total_tokens`, `rate`, `on_exhaustion`) | [`docs-md/token-budgeting-hld.md`](docs-md/token-budgeting-hld.md), [`specs/examples/workflow-budget.json`](specs/examples/workflow-budget.json) |
| Per-task log capture for dynamic runs | — | [`docs-md/logging-dynamic-workflows-hld.md`](docs-md/logging-dynamic-workflows-hld.md) |

A full worked example combining all three (design → implement → review pipeline with a
dynamic fan-out and a bugfix/review loop) lives in [`playground/sum-of-array/`](playground/sum-of-array/)
— see [`playground/README.md`](playground/README.md).

---

## Repo layout

```
agent-orchestrator/
  src/agent_orchestrator/   Python package (engine, CLI, executors, DAG, …)
    ui/                       Dashboard backend + built frontend (served by `ao ui`)
    auth/                     Dashboard/hub authentication (`ao auth`, sessions, TOTP)
  ui/                       Dashboard frontend source (React + Vite) — see ui/README.md
  specs/                    JSON schemas + example spec files
    *.schema.json             Authoritative schemas
    examples/                 Working example specs
  tests/                    pytest unit + integration tests
    ui/                       Dashboard unit / integration / e2e tests
  docs-md/                  Design docs, ADRs, walkthroughs
  meta/                     Tickets, prompts, learnings, memories, AO configs
    ROADMAP.md                Status summary + 3–6 month roadmap
  .claude/                  Authoritative agent/skill/command assets
```

---

## Development

```bash
# Install (one-time). Add the `ui` extra to work on (or test) the dashboard.
uv pip install -e ".[dev]"
uv sync --extra ui --extra dev

# Tests
uv run pytest -q

# Lint + format + types
uv run ruff check . && uv run ruff format --check . && uv run mypy src

# Validate the bundled example specs
uv run python -m agent_orchestrator.validate specs/
# or
uv run ao validate \
  --workflow specs/examples/workflow.json \
  --reposets specs/examples/reposet.json \
  --agents   specs/examples/agents.json
```

### Working on the dashboard

The frontend lives in `ui/` (React + TypeScript + Vite) and **builds into the Python
package** at `src/agent_orchestrator/ui/static/`.  That build output is committed, so
`pip install` ships a working dashboard without needing node — re-run the build after
changing anything under `ui/src`.

```bash
make ui-install       # npm install (one-time)
make ui               # build the frontend, then serve on http://127.0.0.1:8765
make ui-dev           # vite dev server w/ hot reload, proxying /api to a running `make ui`
make ui-build         # rebuild the committed frontend bundle
make test-ui          # every dashboard test tier (python + frontend)
```

See [`ui/README.md`](ui/README.md) for the frontend layout and conventions.

### Where to look next

- **Roadmap and current status** — [`meta/ROADMAP.md`](meta/ROADMAP.md)
- **Detailed walkthrough** — from writing your first spec to driving a multi-task epic to
  completion and recovering from failures —
  [`docs-md/guide-epic-walkthrough.md`](docs-md/guide-epic-walkthrough.md)
- **Dashboard authentication** — user guide
  [`docs-md/dashboard-authentication.md`](docs-md/dashboard-authentication.md), design
  [`docs-md/dashboard-auth-hld.md`](docs-md/dashboard-auth-hld.md)
- **Dashboard & general-instruction design** —
  [`docs-md/dashboard-and-general-instructions-hld.md`](docs-md/dashboard-and-general-instructions-hld.md)
