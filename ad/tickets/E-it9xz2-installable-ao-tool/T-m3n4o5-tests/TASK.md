# TASK: T-m3n4o5-tests

## Metadata
- Task ID: `T-m3n4o5-tests`
- Epic ID: `E-it9xz2-installable-ao-tool`
- Owner: dev-epic
- Created: 2026-06-16
- Last Updated: 2026-06-16
- Status: Done
- Estimate: < 1 day

## Requirements Mapping
- Requirement IDs: NFR-1

## Description
Unit + integration tests for config discovery, schema, `ao init`, and CLI integration.

## Acceptance Criteria
1. Walk-up discovery logic fully tested (7 cases including git-boundary, prefer .ao/config.yaml, return None).
2. Schema validation tested (5 cases).
3. `load_project_config` tested (6 cases: paths, bad files, env).
4. `scaffold_init` tested (5 cases).
5. `ao init` CLI tested (3 cases).
6. CLI config-discovery integration tested (3 cases: from project config, flag override, missing-args error).
7. All 102 tests pass.

## Artifacts
- `tests/test_project_config.py`
