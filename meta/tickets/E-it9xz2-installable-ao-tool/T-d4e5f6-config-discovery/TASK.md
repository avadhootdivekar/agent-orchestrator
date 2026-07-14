# TASK: T-d4e5f6-config-discovery

## Metadata
- Task ID: `T-d4e5f6-config-discovery`
- Epic ID: `E-it9xz2-installable-ao-tool`
- Owner: dev-epic
- Created: 2026-06-16
- Last Updated: 2026-06-16
- Status: Done
- Estimate: < 1 day

## Requirements Mapping
- Requirement IDs: FR-2, FR-4, NFR-1

## Description
Walk-up config discovery (`find_project_config`), path resolver (`load_project_config`),
and CLI wiring (`_resolve_config_defaults` in `cli.py`).

## Acceptance Criteria
1. `find_project_config(start)` walks from `start` upward, checking `.ao/config.yaml` then `ao.yaml` in each directory.
2. Stops at `.git` boundary or filesystem root.
3. Finds config in the git root directory itself (config checked before stop condition).
4. CLI flag > env var > config file precedence.
5. Missing flag with no config file shows helpful error mentioning `.ao/config.yaml`.

## Artifacts
- `src/agent_orchestrator/project_config.py` — `find_project_config`, `load_project_config`, `apply_project_config_env`
- `src/agent_orchestrator/cli.py` — `_resolve_config_defaults`
