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
- [Environment variables](#environment-variables)
- [Per-project config file](#per-project-config-file)
- [Configuring multiple repos](#configuring-multiple-repos)
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
ao validate   Validate workflow, reposets, and agents specs (no execution)
ao run        Run a workflow from scratch
ao resume     Resume a previously interrupted or failed run
ao status     Show current status of a run
ao prune      Remove stale run artifacts from a workspace (reclaim disk space)
```

All commands accept:

| Option | Description |
|--------|-------------|
| `--workflow PATH` | Path to workflow JSON or YAML |
| `--reposets PATH` | Path to reposets JSON or YAML |
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

## Environment variables

All runtime settings can be provided via environment variable.  Precedence is
always: **CLI flag > env var > `.ao/config.yaml` > built-in default**.

| Variable | CLI equivalent | Description |
|----------|---------------|-------------|
| `AO_WORKFLOW` | `--workflow` | Default path to the workflow file |
| `AO_REPOSETS` | `--reposets` | Default path to the reposets config |
| `AO_AGENTS` | `--agents` | Default path to the agents config |
| `AO_WORKSPACE_ROOT` | — | Override the `workspace_root` from the reposet (useful in CI) |
| `AO_MODEL` | `--model` | Claude model for all agents (e.g. `claude-sonnet-4-6`) |
| `AO_EFFORT` | `--effort` | Effort level: `low`, `medium`, or `high` |
| `AO_MAX_ATTEMPTS` | `--max-attempts` | Max task attempts (overrides workflow `defaults.retries.max_attempts`) |
| `AO_MAX_TURNS` | `--max-turns` | Max turns per Claude invocation (overrides effort-derived value) |
| `AO_QUOTA_MAX_WAIT_SECONDS` | `--quota-max-wait` | Max seconds to wait during a quota-exhaustion episode before failing (default: 21600 = 6 h) |
| `AO_QUOTA_POLL_SECONDS` | `--quota-poll-interval` | Seconds between quota-exhaustion re-run attempts (default: 900 = 15 min) |

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
# model: claude-sonnet-4-6 # AO_MODEL        — claude model for all agents
# effort: medium           # AO_EFFORT       — low / medium / high

# --- Claude usage-quota exhaustion handling ---
# quota_max_wait_seconds: 21600   # AO_QUOTA_MAX_WAIT_SECONDS — give up after 6h
# quota_poll_seconds: 900         # AO_QUOTA_POLL_SECONDS     — poll every 15 min
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
  specs/                    JSON schemas + example spec files
    *.schema.json             Authoritative schemas
    examples/                 Working example specs
  tests/                    pytest unit + integration tests
  docs-md/                  Design docs, ADRs, walkthroughs
  ad/                       Tickets, prompts, learnings/memories
  .claude/                  Authoritative agent/skill/command assets
```

---

## Development

```bash
# Install (one-time)
uv pip install -e ".[dev]"

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

For a detailed walkthrough — from writing your first spec to driving a multi-task epic to
completion and recovering from failures — see
[`docs-md/guide-epic-walkthrough.md`](docs-md/guide-epic-walkthrough.md).
