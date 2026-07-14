# TASK: T-g7h8i9-ao-init-command

## Metadata
- Task ID: `T-g7h8i9-ao-init-command`
- Epic ID: `E-it9xz2-installable-ao-tool`
- Owner: dev-epic
- Created: 2026-06-16
- Last Updated: 2026-06-16
- Status: Done
- Estimate: < 0.5 day

## Requirements Mapping
- Requirement IDs: FR-5

## Description
`ao init` CLI command that scaffolds `.ao/config.yaml` with commented example fields.

## Acceptance Criteria
1. `ao init` creates `.ao/config.yaml` in the current directory (or `--dir`).
2. File contains commented examples for `workflow`, `reposets`, `agents`, `workspace_root`, `env`.
3. Second invocation exits with error "already exists".
4. Prints "Next steps" after success.

## Artifacts
- `src/agent_orchestrator/cli.py` — `init_cmd` command
- `src/agent_orchestrator/project_config.py` — `scaffold_init`, `_INIT_TEMPLATE`
