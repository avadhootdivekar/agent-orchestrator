# TASK: T-Sn2Set-sentinel-settings-plumbing

## Metadata
- Task ID: `T-Sn2Set-sentinel-settings-plumbing`
- Epic ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Owner: `unassigned`
- Created: `2026-11-09`
- Last Updated: `2026-11-09`
- Status: `Draft`
- Estimate: `< 3 days`
- Phase: P1
- Agent: developer

## Requirements Mapping
- Requirement IDs: FR-1, FR-22 (settings part), NFR-1

## Description
Add `SentinelSpec` (pydantic, `models.py`) exactly as EPIC §4.7, `WorkflowSpec.sentinel`, `ProjectConfig.sentinel`
(+ commented sample in `project_config.py`), env `AO_SENTINEL_*`, and CLI flags on `ao run`, `ao resume`, `ao new --run`
(`--sentinel/--no-sentinel`, `--sentinel-llm/--no-sentinel-llm`, `--sentinel-powers`, `--sentinel-budget-usd`,
`--max-parallel-ceiling`, repeatable `--sentinel-set key=value`). **Register every option in the shared run-options
registry from E-aYG5vn T-zP28k9** so CLI, dashboard schema/allow-list and `state.effective_settings` all pick it up —
no hand-wired duplicates. `ao validate` validates the block. Template params `sentinel_enabled`, `sentinel_llm`,
`sentinel_powers`, `sentinel_budget_pct` in both builtin templates render into `workflow.json`. Orchestrator receives a
resolved `SentinelSettings` object; engine ignores it in this task (wiring only).

## Acceptance Criteria
1. Precedence per ADR-0003 verified by tests (workflow > CLI > env > config > default), with sources recorded in `state.effective_settings`.
2. `--sentinel-set` dotted keys validated against the schema; bad key/value → clear error, exit code 2.
3. `ao new <template> --param sentinel_enabled=true --run` produces a workflow.json with the block and passes run options through.
4. Golden test: no sentinel block ⇒ workflow.json/state.json byte-identical to before.
5. CliRunner e2e for run/resume/new with flags.

## Risks
- Duplicating the run/resume wiring again — must go through the registry.

## Dependencies
- E-aYG5vn T-zP28k9
- T-Sn1Adr

## Pseudocode / Algorithm
```text
registry.register(Option('sentinel.enabled', flag='--sentinel/--no-sentinel', env='AO_SENTINEL', config='sentinel.enabled', type=bool, group='Sentinel'))
... one entry per SentinelSpec leaf (generated from the pydantic model where possible)
resolve_settings(registry, cli, env, config, workflow) -> EffectiveSettings{key: (value, source)}
```

## Schemas / Interface Notes
- `SentinelSpec` per EPIC §4.7
- env names `AO_SENTINEL_<DOTTED_UPPER>`
- template.yaml params with enum/bool types

## Handoff Boundary
- Upstream: T-zP28k9 registry
- Downstream: T-Sn3Tck, T-Sn4Rec, T-Sn5Ui1 (schema-driven form), T-Sn7Bkl, T-Sn8Gen

## Artifacts
- Docs/comments: `meta/tickets/E-Sn7tL2-sentinel-long-running-overseer/T-Sn2Set-sentinel-settings-plumbing/`
- Large outputs: `N/A`

## Standard gates (every task)
- `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` green; UI changes also `npm test` + `npm run build` in `ui/`.
- New behaviour off / absent settings ⇒ byte-identical behaviour (golden tests stay green).
- Work only in `./agent-orchestrator` on the epic branch; test against temp workspaces; never `install.sh` / restart `ao.service`.
- Update this TASK.md + STATUS.md and the epic STATUS.md rollup together.
