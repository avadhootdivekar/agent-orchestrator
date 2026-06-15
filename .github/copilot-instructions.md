# GitHub Copilot — repository custom instructions

> **Pointer file.** The authoritative, hand-maintained agent instructions are Claude-native and live in [`../CLAUDE.md`](../CLAUDE.md) and under [`../.claude/`](../.claude/). They are **not duplicated here** — read those files for full detail so there is a single source of truth.

## Project in one paragraph

This repo is an **agent orchestration framework / tool** written in **Python**. It drives multi-step, multi-agent workflows to completion: it orchestrates agent-based workflows, models task dependencies as a **DAG** (with inputs/outputs expressed as artifacts/file paths and cron-style periodic + event triggers), and is **fully config-driven** — orchestration metadata lives in **structured files (JSON/YAML)**, while the payloads those files reference may be markdown, source code, or directories.

## When working here

- Read [`../CLAUDE.md`](../CLAUDE.md) first — it holds the design principles, general rules, code-quality and testing expectations, and the command reference. Follow it.
- Make the **smallest correct change**; reuse existing patterns before adding abstractions; no magic literals; validate structured specs against their schema; keep core logic deterministic (inject RNG/clock).
- Tests: `pytest`. Lint/format/types (once configured): `ruff check . && ruff format --check . && mypy .`. Don't claim tests pass without running them.
- Tickets/learnings live under `../ad/`; docs under `../docs-md/`.

## Adopting a specialized role

For specialized work, open the matching guide under [`../.claude/agents/`](../.claude/agents/) and follow it as your instructions:

| Role | File | Use for |
|------|------|---------|
| architect | `architect.md` | Full-epic design + sprint planning; writes tickets to `../ad/tickets/`. |
| manager | `manager.md` | End-to-end delivery orchestration with evidence gates + ticket sync. |
| dev-epic | `dev-epic.md` | Epic decomposition (MVP/non-MVP + traceability), resumable drive-to-done. |
| developer | `developer.md` | General Python implementation (engine, specs, CLI). |
| reviewer | `reviewer.md` | Critical code/design review before merge. |
| tester | `tester.md` | Unit + integration tests with `pytest`. |
| dev-security | `dev-security.md` | Security audit (explicit / pre-release). |
| dev-critic | `dev-critic.md` | Deep architecture/lock-in review (explicit). |

Epic/task work is tracked under [`../ad/tickets/`](../ad/tickets/) — follow the ID format and status-sync conventions in `../ad/tickets/README.md`.

## Editing instructions

Change the `.claude/` originals and `CLAUDE.md`, not this pointer.
