# AGENTS.md — agent-orchestrator

> **This file is a thin pointer. The authoritative agent instructions are Claude-native and live in [`CLAUDE.md`](CLAUDE.md) and under [`.claude/`](.claude/).**
> They are intentionally not duplicated here — read the source files so there is one place to maintain.

## What this repo is

An **agent orchestration framework / tool** (Python) that drives multi-step, multi-agent workflows to completion:

- Orchestrates multiple agent-based workflows to *done*.
- Models dependencies as a **DAG** with explicit inputs/outputs as artifacts/file paths, plus **cron-style periodic** and event triggers.
- Is **config-driven**: all orchestration metadata (dependencies, IO contracts, schedules, retries) lives in **structured files (JSON/YAML)**; the payloads they reference can be markdown, source, directories — anything.

## Read these (authoritative)

| Need | File |
|------|------|
| Project context, design principles, rules, commands | [`CLAUDE.md`](CLAUDE.md) |
| Specialized role guides (adopt the matching one) | [`.claude/agents/`](.claude/agents/) — `developer`, `reviewer`, `tester`, `dev-security`, `dev-critic` |
| Skills (repo intelligence / search) | [`.claude/skills/`](.claude/skills/) |
| Repeatable command workflows (commit/push, memorize) | [`.claude/commands/`](.claude/commands/) |

## How to use the role guides

There is no native subagent runtime outside Claude Code, so when a task calls for a specialized role (reviewing, testing, security audit, architecture critique), **open the matching `.claude/agents/<role>.md` and follow it as your operating instructions** for that task.

## Editing instructions

Edit the `.claude/` originals and `CLAUDE.md`. This file and the Copilot/Cursor pointers (`.github/copilot-instructions.md`, `.cursor/rules/`) should rarely change.
