# TASK: T-j0k1l2-config-schema

## Metadata
- Task ID: `T-j0k1l2-config-schema`
- Epic ID: `E-it9xz2-installable-ao-tool`
- Owner: dev-epic
- Created: 2026-06-16
- Last Updated: 2026-06-16
- Status: Done
- Estimate: < 1 day

## Requirements Mapping
- Requirement IDs: FR-3, FR-6, NFR-3

## Description
Pydantic v2 schema for `.ao/config.yaml` — `ProjectConfig` model.

## Acceptance Criteria
1. Fields: `workflow`, `reposets`, `agents` (optional str), `workspace_root` (optional str), `env` (dict[str, str], default {}).
2. `env` validator coerces values to str; rejects non-mapping values.
3. Bad schema produces a `ConfigError` naming the offending field.
4. No new mandatory dependencies beyond pydantic (already in pyproject.toml).

## Artifacts
- `src/agent_orchestrator/project_config.py` — `ProjectConfig` class
