# Epic: E-it9xz2-installable-ao-tool

## Metadata
- Epic ID: `E-it9xz2-installable-ao-tool`
- Title: Installable AO (Agent Orchestrator) Tool
- Owner: dev-epic agent
- Created: 2026-06-16
- Last Updated: 2026-06-16
- Status: Done

## Goal
Make AO installable with a single command and discoverable per-project via a local config file, so users can run `ao` from any project without passing flags every time.

## Acceptance Criteria (testable)
1. `install.sh` runs on a clean machine, installs `uv` if missing, and installs `ao` globally via `uv tool install`; idempotent (safe to re-run).
2. `ao run` (no flags) in a directory with `.ao/config.yaml` picks up `workflow`, `reposets`, `agents` from that file; explicit flags override.
3. `ao init` scaffolds `.ao/config.yaml` with commented example fields in the current directory.
4. Config schema (Pydantic) validates `.ao/config.yaml` fields; bad files produce helpful errors.
5. Config discovery walks up from cwd to git root (or fs root) finding the nearest `.ao/config.yaml` or `ao.yaml`.
6. Unit tests for discovery (walk-up, override precedence), `ao init` output, schema validation — all pass via `uv run pytest -q`.
7. README updated with Installation and Quickstart sections.

## Requirements

### MVP — Functional
- FR-1: `install.sh` — one-command install from bash; idempotent; leaves hook comment for web-hosted version.
- FR-2: Per-project config discovery — walk-up from cwd, stopping at git root or fs root; supports `.ao/config.yaml` and `ao.yaml`.
- FR-3: Config fields: `workflow`, `reposets`, `agents` (required paths); optional `workspace_root`, `env` dict.
- FR-4: Explicit CLI flags and env vars always override config file values.
- FR-5: `ao init` command scaffolds `.ao/config.yaml` with comments.
- FR-6: Config validated against Pydantic schema; error message names the bad field.

### MVP — Non-functional
- NFR-1: Config discovery is tested (walk-up logic, override precedence).
- NFR-2: `install.sh` is POSIX-compatible (bash fine but no non-portable bashisms beyond bash).
- NFR-3: No new mandatory dependencies beyond what's already in `pyproject.toml` (pydantic is already there).

### Non-MVP (deferred)
- D-1: Web-hosted `install.sh` via `curl | bash` URL (hook comment added to script, not implemented).
- D-2: `ao init` interactive wizard (non-interactive scaffold is MVP).
- D-3: Config merging across multiple levels (project + user + global).

## Task Decomposition

| Task | Description | Requirements |
|------|-------------|--------------|
| T-a1b2c3-install-script | Write `install.sh` | FR-1, NFR-2 |
| T-d4e5f6-config-discovery | Config walk-up logic + CLI wiring | FR-2, FR-3, FR-4, NFR-1 |
| T-g7h8i9-ao-init-command | `ao init` CLI command | FR-5 |
| T-j0k1l2-config-schema | Pydantic schema for `.ao/config.yaml` | FR-3, FR-6 |
| T-m3n4o5-tests | Unit + integration tests | NFR-1 |
| T-p6q7r8-docs | Update README | FR-1 (docs) |

## Implementation Notes
- Config schema module: `src/agent_orchestrator/project_config.py`
- Config discovery function: `find_project_config(start: Path) -> Path | None`
- Walks from `start` upward; stops when it finds `.ao/config.yaml`, `ao.yaml`, `.git/`, or reaches fs root.
- `_load_all` in `cli.py` updated to call discovery before checking env vars.
- `install.sh` lives at repo root.

## Evidence Log

### 2026-06-16 — Iteration 1 (DONE)
- All 6 tasks completed in a single iteration.
- 29 new tests added; full suite 102/102 pass.
- ruff check: clean; mypy: clean (17 files).

## Risks & Blockers
- None.

## Next Actions
- None (epic complete).
