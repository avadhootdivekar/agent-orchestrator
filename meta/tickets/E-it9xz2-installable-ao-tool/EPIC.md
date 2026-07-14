# EPIC: E-it9xz2-installable-ao-tool

## Metadata
- Epic ID: `E-it9xz2-installable-ao-tool`
- Title: Installable AO (Agent Orchestrator) Tool
- Owner: dev-epic
- Created: 2026-06-16
- Last Updated: 2026-06-16
- Status: Done

## Summary
- Goal: Make AO installable with one command and discoverable per-project via local config, so users can run `ao` from any project without passing repetitive flags.
- Scope In: `install.sh`, per-project config discovery (walk-up), `ao init` command, Pydantic config schema, tests, README update.
- Scope Out: Web-hosted installer URL, interactive `ao init` wizard, multi-level config merging.

## Requirements
- FR-1: `install.sh` — one-command install; idempotent; hook comment for future web-hosted version.
- FR-2: Per-project config discovery walks up from cwd to git root or fs root.
- FR-3: Config fields: `workflow`, `reposets`, `agents` (paths); optional `workspace_root`, `env`.
- FR-4: CLI flags + env vars always override config file.
- FR-5: `ao init` scaffolds `.ao/config.yaml` with comments.
- FR-6: Config validated via Pydantic; bad fields produce named errors.
- NFR-1: Discovery logic is unit-tested (walk-up, override precedence).
- NFR-2: `install.sh` is bash-compatible; no non-portable bashisms.
- NFR-3: No new mandatory dependencies.

## Task List
- [x] `T-a1b2c3-install-script` — Write `install.sh`
- [x] `T-j0k1l2-config-schema` — Pydantic schema for `.ao/config.yaml`
- [x] `T-d4e5f6-config-discovery` — Config walk-up logic + CLI wiring
- [x] `T-g7h8i9-ao-init-command` — `ao init` CLI command
- [x] `T-m3n4o5-tests` — Unit + integration tests
- [x] `T-p6q7r8-docs` — Update README

## Risks and Dependencies
- Tasks T-d4e5f6 and T-g7h8i9 depend on T-j0k1l2 (schema must exist first).
- T-m3n4o5 depends on all implementation tasks.

## Links
- Design doc: `docs-md/ai-epics/E-it9xz2-installable-ao-tool.md`
- Output artifacts: `install.sh`, `src/agent_orchestrator/project_config.py`, `tests/test_project_config.py`
