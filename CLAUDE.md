# agent-orchestrator — Claude Instructions

> Project intent / current ask: [`ad/prompts/prompt.md`](ad/prompts/prompt.md)
> Design & architecture docs (as they land): [`docs-md/`](docs-md/)
> Cross-tool note: this file and everything under `.claude/` are the **authoritative** agent instructions. Copilot and Cursor read thin pointer files that forward here — see [Cross-tool instructions](#cross-tool-instructions-claude--copilot--cursor).

---

## What this repo is

An **agent orchestration framework / tool**. It drives multi-step, multi-agent workflows to completion.

Core responsibilities:
- **Orchestrate agent-based workflows** — coordinate multiple agents/tasks to drive complex work to *done*.
- **Model dependencies as a graph** — a **DAG** of tasks with explicit requirements, **inputs/outputs expressed as artifacts / file paths**, plus **cron-style periodic** tasks and event triggers.
- **Config-driven** — orchestration is defined entirely in **structured files (JSON/YAML)**. All dependency wiring, IO contracts, schedules, retries, and metadata live in structured files; the *content* those files point to can be markdown, source code, directories — anything.

**Stack**: Python (greenfield). Pin tooling as the first modules land — default to `pytest`, `ruff` (lint + format), and `mypy`.

**Where things live** (intended layout — create dirs as work lands, don't scaffold empty):
- Orchestrator core / Python package → `src/`
- Structured workflow/DAG spec files (JSON/YAML) → `specs/`, or alongside the workflow they describe
- Tickets → `ad/tickets/` · learnings/memories → `ad/` · prompts → `ad/prompts/`
- Docs → `docs-md/`
- Agent/skill/command assets → `.claude/` (authoritative), mirrored by thin pointers for Copilot/Cursor

---

## Design principles

- **Declarative, structured specs** — orchestration metadata (dependencies, inputs/outputs, schedule, triggers, retries) is defined in JSON/YAML and validated against a schema. Payloads are referenced **by path**, never inlined into the engine.
- **DAG-first** — tasks form a dependency graph; inputs/outputs are artifacts at known paths; cycles are rejected; periodic/cron and event triggers are first-class.
- **Pluggable & extensible** — agents, executors, schedulers, and artifact/storage backends sit behind clean interfaces (ABCs/protocols + injection) so they're swappable and testable in isolation. Third-party integrations must never destabilize the core.
- **Deterministic, idempotent, resumable** — a run is reproducible from its spec + artifacts; tasks are safe to retry; a workflow resumes from completed artifacts rather than redoing work.
- **Observable** — run state, task status, logs, and produced artifacts are traceable end-to-end.
- **Safe by default** — agent/task execution is scoped and resource-bounded; long or expensive tasks are flagged, limited, and cancellable.

---

## General rules

- Always merge the latest target branch (typically `main`) before starting work.
- Make the **smallest correct change** that satisfies the requirement. Don't modify unrelated code.
- Follow the **operation mode** specified in the user's prompt (e.g. pipeline / dev-only / development / execution / test).
- Don't create documentation/status files unless explicitly asked or in pipeline mode.
- Don't echo long command outputs to the terminal — put status in the chat response only.
- If a better approach exists, suggest it — don't silently implement a suboptimal design.
- **Compaction**: when context grows significantly, compact it — keep a clear summary of current tasks/goals/designs/schemas and anything important; clear out internal reasoning, long analyses, finished snippets, and dead exploration. Don't compact back-to-back: keep a buffer of ≥5 iterations between compactions. If everything genuinely still matters, say so and let the user decide.
- Spawn independent subagents for clean, separable side tasks when it keeps your own context focused.

## Ensure the following are always considered

Tick each one explicitly (follow or skip, but state the status):
1. [optional] Create tickets at a high level.
2. Control subagents to do the work when subagents are relevant.
3. Build and unit tests pass on the change, no regressions.
4. Integration / end-to-end tests pass, no regression.
5. [optional] Deploy/run path works (engine runs a sample workflow end-to-end).
6. Tickets updated.
7. Design doc updated.
8. [optional] Memories and learnings added.

---

## Senior developer execution contract (mandatory)

- Act as an autonomous implementer with delivery ownership (not analysis-only).
- For implementation tasks, do not stop at pseudocode.
- Fail fast on ambiguous contracts/specs that can produce multiple valid implementations; mark **BLOCKED** with explicit assumptions/questions.
- Ensure completion gates before handoff: build passes, tests pass, contracts honored.

### Strict execution flow

1. **Validate input**: API/schema determinism + edge-case clarity.
2. **Check feasibility**: performance/scalability/dependency constraints.
3. **Implement**: real code changes in existing modules first.
4. **Refactor/reuse**: remove duplication, extract shared logic if reused 2+ times.
5. **Validate**: format/lint/types/tests/build + contract checks.

## Code quality

- Correctness first. Double-check syntax before proposing code.
- No duplicate logic — extract to a function/module if logic appears twice.
- Add sufficient comments to explain non-obvious choices; don't over-comment simple code.
- Follow existing patterns in adjacent files before introducing new abstractions.
- No hardcoded secrets/URLs/paths/env values — use config/env. Spec/schema constants are named, not magic literals.

## Testing

- Add unit tests for new logic; integration tests for new orchestration paths (DAG resolution, scheduling, artifact IO, executor boundaries).
- Cover error and edge paths (cycles, missing inputs, failed/retried/cancelled tasks, malformed specs) — not just the happy path.
- For anything stochastic or scheduled, use **fixed seeds / fixed clocks** so assertions are deterministic and replayable.
- Run tests before reporting completion: `pytest` (add `ruff`/`mypy` once configured).
- Report actual results — never claim tests pass without running them.

## Documentation

- Maintain docs under `docs-md/` in markdown.
- Don't create result/status files for untested work.
- Update existing docs when changing related functionality.

## CI / PR checks ownership

When scope requires, ensure PR checks enforce:
- Build / import sanity
- Unit tests (`pytest`)
- Integration tests
- Lint/format (`ruff`) and types (`mypy`)
- Schema validation for the structured spec files (JSON/YAML)

Prefer adding a coverage gate (target ≥80% unless project standard differs), static analysis, and dependency/security checks (`pip-audit`).

## Learning capture and compaction

- Add learnings from agent work only when genuinely applicable; avoid flooding repetitive notes each iteration.
- Maintain long-form learnings in `ad/learnings.md`.
- Maintain compact, curated learnings in `ad/learning-compact.md` for lightweight agent context.
- Keep each learning entry clearly separable (distinct markers/sections) to simplify review and conflict resolution.
- Prefer entry fields: learning statement, optional context (why/when), `By`, `Role`, `Date`.
- `ad/learning-compact.md` is the periodic distilled output from `ad/learnings.md`; refresh as needed.

---

## Ticket workspace & conventions

Epic/task execution is tracked under [`ad/tickets/`](ad/tickets/) (project-local, Jira-equivalent). Full rules in [`ad/tickets/README.md`](ad/tickets/README.md); templates in [`ad/tickets/_templates/`](ad/tickets/_templates/).

- **IDs**: Epic `E-<RANDOM>-<slug>` · Task `T-<RANDOM>-<slug>`, where `<RANDOM>` is exactly 6 alphanumerics (`[A-Za-z0-9]{6}`) and `<slug>` is lowercase kebab-case.
- **Layout**: epic docs under `ad/tickets/<EpicID>/` (`EPIC.md` + `STATUS.md`); task docs under `ad/tickets/<EpicID>/<TaskID>/` (`TASK.md` + `STATUS.md` + `HANDOFF.md` when present). Start from the templates.
- **Status sync**: any status change must stay consistent across `TASK.md`/`STATUS.md`/`HANDOFF.md` and the epic `EPIC.md`/`STATUS.md` rollup — never update one without the others.
- **Comment attribution**: `By: <actor>` · `Role: <user|developer|tester|architect|reviewer|manager|other>` · `Date: YYYY-MM-DD` · `Comment: ...`.
- **Large outputs** go under root `output/`, linked from ticket docs (keep ticket folders markdown-light).
- The `architect`, `manager`, and `dev-epic` agents own most ticket creation/sync; any agent doing ticket-scoped work follows these conventions.

---

## Cross-tool instructions (Claude / Copilot / Cursor)

The Claude-native files are the **single source of truth**:
- `CLAUDE.md` — this file (project context + rules).
- `.claude/agents/*.md` — specialized role guides.
- `.claude/skills/*/SKILL.md` — skills.
- `.claude/commands/*.md` — repeatable command workflows.

Other tools read thin pointer files that forward here, so there is no duplicated content to keep in sync:
- **GitHub Copilot** → `.github/copilot-instructions.md`
- **Cursor** → `.cursor/rules/agent-orchestrator.mdc`
- **Cross-tool standard** → `AGENTS.md`

When editing instructions, **edit the `.claude/` originals** — the pointers should rarely change.

## Skills reference

| Area | Reference |
|------|-----------|
| Repo intelligence / search / overlay tickets | [`.claude/skills/repo-intel/SKILL.md`](.claude/skills/repo-intel/SKILL.md) |

## Available agents

Claude-native subagents live in [`.claude/agents/`](.claude/agents/) and are invocable directly. Delegate when the task matches:

| Agent | Use for |
|-------|---------|
| `architect` | Full-epic design + sprint planning — requirements, orchestration-landscape/competitor analysis, HLD/LLD, spec/interface/event schemas, tasks ≤3 days. Writes to `ad/tickets/`. |
| `manager` | End-to-end delivery: turns a plan into verified outcomes by orchestrating other agents with evidence gates and ticket sync. |
| `dev-epic` | Epic decomposition into MVP/non-MVP requirements with traceability; drives tasks to evidence-backed completion; supports mid-epic resume. |
| `developer` | General Python implementation across the orchestrator (engine, spec handling, CLI). |
| `reviewer` | Critical code/design review before merge. |
| `tester` | Unit + integration tests (`pytest`); CI wiring; verifies by running suites. |
| `dev-security` | Security audit (explicit invoke / pre-release). |
| `dev-critic` | Deep architecture/lock-in review (explicit invoke only). |

## Key commands (quick reference)

> Greenfield — these become live once `pyproject.toml` / deps exist.

```bash
# Unit + integration tests
pytest -q

# Lint, format check, types
ruff check . && ruff format --check . && mypy .

# Validate structured spec files (example shape — wire to the real schema/CLI)
python -m agent_orchestrator.validate specs/
```
