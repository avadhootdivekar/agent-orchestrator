# agent-orchestrator

An **agent orchestration framework / tool** (Python) that drives multi-step, multi-agent
workflows to completion.

> Status: early / greenfield. The orchestration engine is being built out; this README and
> [`CLAUDE.md`](CLAUDE.md) describe the intended shape.

## What it does

- **Orchestrates agent-based workflows** — coordinates multiple agents/tasks to drive complex
  work to *done*.
- **Models dependencies as a graph** — a **DAG** of tasks with explicit requirements, where
  **inputs and outputs are expressed as artifacts / file paths**, plus **cron-style periodic**
  tasks and event triggers.
- **Is config-driven** — orchestration is defined entirely in **structured files (JSON/YAML)**.
  All dependency wiring, IO contracts, schedules, retries, and metadata live in structured
  files; the *content* those files reference can be markdown, source code, directories —
  anything.

## Design principles

- **Declarative, structured specs** — orchestration metadata is validated against a schema;
  payloads are referenced **by path**, never inlined into the engine.
- **DAG-first** — tasks form a dependency graph; cycles are rejected; periodic/cron and event
  triggers are first-class.
- **Pluggable & extensible** — executors, schedulers, agents, and artifact/storage backends sit
  behind clean interfaces so they're swappable and testable in isolation.
- **Deterministic, idempotent, resumable** — a run is reproducible from its spec + artifacts;
  tasks are safe to retry; a workflow resumes from completed artifacts rather than redoing work.
- **Observable & safe-by-default** — run state, task status, logs, and artifacts are traceable;
  task execution is scoped and resource-bounded.

## Repo layout

| Path | Contents |
|------|----------|
| `src/` | Orchestrator core / Python package (as it lands) |
| `specs/` | Structured workflow/DAG spec files (JSON/YAML) |
| `ad/` | Tickets, prompts, learnings/memories |
| `docs-md/` | Documentation |
| `.claude/` | Authoritative agent/skill/command assets |

## Working in this repo (humans & AI agents)

Agent instructions are **Claude-native and authoritative**, with thin pointers for other tools:

- [`CLAUDE.md`](CLAUDE.md) — project context, design principles, rules, and commands.
- [`.claude/`](.claude/) — specialized role guides (`agents/`), skills, and command workflows.
- [`AGENTS.md`](AGENTS.md) — cross-tool pointer · [`.github/copilot-instructions.md`](.github/copilot-instructions.md) (Copilot) · [`.cursor/rules/`](.cursor/rules/) (Cursor).

When editing instructions, edit the `.claude/` originals and `CLAUDE.md` — the pointers rarely change.

## Development

```bash
pytest -q                                       # unit + integration tests
ruff check . && ruff format --check . && mypy . # lint, format, types (once configured)
```
