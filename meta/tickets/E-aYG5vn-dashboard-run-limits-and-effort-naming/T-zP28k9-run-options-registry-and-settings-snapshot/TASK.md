# TASK: T-zP28k9-run-options-registry-and-settings-snapshot

## Metadata
- Task ID: `T-zP28k9-run-options-registry-and-settings-snapshot`
- Epic ID: `E-aYG5vn-dashboard-run-limits-and-effort-naming`
- Owner: `unassigned`
- Created: `2026-11-09`
- Last Updated: `2026-11-09`
- Status: `Done`
- Estimate: `< 3 days`
- Phase: Foundation (first; shared with E-Sn7tL2)
- Agent: developer (+ reviewer)

## Requirements Mapping
- Requirement IDs: E-aYG5vn FR-1, FR-2; enables E-Sn7tL2 FR-1/FR-7/FR-22

## Description
Build one **run-options registry** (`src/agent_orchestrator/run_options.py`): per option name, type/enum, CLI flag(s)
incl. deprecated aliases, env var, `.ao/config.yaml` key, help text, UI group/label, whether resumable. Refactor
`cli.py` `_resolve_run_settings` and sibling resolvers for `run` and `resume` to read it (one shared code path; no
behaviour change). Derive `ui/processes.py` `ALLOWED_OPTIONS`/`ALLOWED_BOOL_OPTIONS` from it. Add
`GET /api/schema/run-options` (JSON schema + UI metadata). Persist `state.effective_settings` (`{key: {value,
source}}`, plus `history[]` per resume) and per-task effective limits at dispatch: `thinking_effort`, `turn_budget`,
`max_turns`, `autocompact`, `timeout_seconds`, `task_cost_cap_usd`, `max_attempts` (FR-2). Old states load fine.

## Acceptance Criteria
1. Every existing CLI flag/env/config option resolves exactly as before (parametrised regression test over the registry).
2. Allow-list derived from the registry; unknown option keys still dropped; argv-injection negative test.
3. `state.effective_settings` written on run start and appended on resume with sources; per-task fields present after dispatch.
4. Schema endpoint snapshot test.

## Risks
- Refactor of central CLI wiring — keep behaviour byte-identical, land with exhaustive tests before new options.

## Dependencies
- None

## Pseudocode / Algorithm
```text
OPTIONS = [Option('max_parallel', int, flag='--max-parallel', env='AO_MAX_PARALLEL', config='max_parallel', ui=('Run','Max parallel')), ...]
resolve(OPTIONS, cli, env, config, workflow) -> {k: Resolved(value, source)}
ui allow-list = {o.name: o.flag for o in OPTIONS if o.ui}
```

## Schemas / Interface Notes
- `run_options.Option` dataclass
- `state.effective_settings`
- `GET /api/schema/run-options`

## Handoff Boundary
- Upstream: none
- Downstream: E-aYG5vn T-f0d7gm, T-pMpo4O, T-xNFBww; E-Sn7tL2 T-Sn2Set

## Artifacts
- Docs/comments: `meta/tickets/E-aYG5vn-dashboard-run-limits-and-effort-naming/T-zP28k9-run-options-registry-and-settings-snapshot/`
- Large outputs: `N/A`

## Standard gates (every task)
- `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` green; UI changes also `npm test` + `npm run build` in `ui/`.
- New behaviour off / absent settings ⇒ byte-identical behaviour (golden tests stay green).
- Work only in `./agent-orchestrator` on the epic branch; test against temp workspaces; never `install.sh` / restart `ao.service`.
- Update this TASK.md + STATUS.md and the epic STATUS.md rollup together.
