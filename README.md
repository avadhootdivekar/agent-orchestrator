# agent-orchestrator

A **config-driven, DAG-based agent orchestration engine** (Python) that drives multi-step,
multi-agent workflows to completion — with retries, resume, and full artifact traceability.

---

## Contents

- [Install](#install)
- [Core concepts](#core-concepts)
- [Spec files](#spec-files)
- [Quick start](#quick-start)
- [CLI reference](#cli-reference)
- [Environment variables](#environment-variables)
- [Configuring multiple repos](#configuring-multiple-repos)
- [Schema reference](#schema-reference)
- [Repo layout](#repo-layout)
- [Development](#development)

---

## Install

```bash
# Requires Python 3.11+  (uv is the preferred package manager)
uv pip install -e ".[dev]"   # dev extras: pytest, ruff, mypy

# The `ao` CLI is now available:
uv run ao --help
```

Using plain pip:

```bash
pip install -e ".[dev]"
ao --help
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

## Quick start

### 1. Validate your specs

```bash
uv run ao validate \
  --workflow  specs/examples/workflow.json \
  --reposets  specs/examples/reposet.json \
  --agents    specs/examples/agents.json
```

Expected output:

```
OK: all specs valid
```

Validation checks JSON Schema conformance, cross-references (every `agent` key exists in agents.json, every `repo_set` key exists in reposet.json, every `depends_on` id exists), and cycle detection.

### 2. Run a workflow

```bash
uv run ao run \
  --workflow  specs/examples/workflow.json \
  --reposets  specs/examples/reposet.json \
  --agents    specs/examples/agents.json
```

The engine prints a run ID and a status table when the run completes:

```
Run:    feature-pipeline-20260616T120000Z
Status: succeeded

Task                           Status          Attempts
-------------------------------------------------------
design                         succeeded       1
implement                      succeeded       1
test                           succeeded       1
```

Exit code is `0` on success, `1` on failure or error.

### 3. Resume after failure

If a run fails mid-way, resume it by run ID — completed tasks are skipped (their outputs are already on disk):

```bash
uv run ao resume \
  --run-id   feature-pipeline-20260616T120000Z \
  --workflow  specs/examples/workflow.json \
  --reposets  specs/examples/reposet.json \
  --agents    specs/examples/agents.json
```

### 4. Check run status

```bash
uv run ao status \
  --run-id   feature-pipeline-20260616T120000Z \
  --workflow  specs/examples/workflow.json \
  --reposets  specs/examples/reposet.json \
  --agents    specs/examples/agents.json
```

---

## CLI reference

```
ao validate   Validate workflow, reposets, and agents specs (no execution)
ao run        Run a workflow from scratch
ao resume     Resume a previously interrupted or failed run
ao status     Show current status of a run
```

All commands accept:

| Option | Description |
|--------|-------------|
| `--workflow PATH` | Path to workflow JSON or YAML **(required)** |
| `--reposets PATH` | Path to reposets JSON or YAML (or set `AO_REPOSETS`) |
| `--agents PATH` | Path to agents JSON or YAML (or set `AO_AGENTS`) |

`ao resume` and `ao status` also require `--run-id RUN_ID`.

---

## Environment variables

| Variable | Description |
|----------|-------------|
| `AO_REPOSETS` | Default path to the reposets config (overridden by `--reposets`) |
| `AO_AGENTS` | Default path to the agents config (overridden by `--agents`) |
| `AO_WORKSPACE_ROOT` | Override the `workspace_root` from the reposet (useful in CI) |

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
