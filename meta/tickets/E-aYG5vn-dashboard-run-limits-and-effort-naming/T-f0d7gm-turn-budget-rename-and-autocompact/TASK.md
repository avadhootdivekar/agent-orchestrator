# TASK: T-f0d7gm-turn-budget-rename-and-autocompact

## Metadata
- Task ID: `T-f0d7gm-turn-budget-rename-and-autocompact`
- Epic ID: `E-aYG5vn-dashboard-run-limits-and-effort-naming`
- Owner: `unassigned`
- Created: `2026-11-09`
- Last Updated: `2026-11-09`
- Status: `Draft`
- Estimate: `< 3 days`
- Phase: Foundation
- Agent: developer

## Requirements Mapping
- Requirement IDs: E-aYG5vn FR-5 (CLI/docs), FR-6, FR-9

## Description
Naming (owner-approved 2026-11-09: **effort = thinking effort; turn budget = max-turn tier**):
- Canonical `turn_budget` (spec key on agents/tasks, `--turn-budget`, `AO_TURN_BUDGET`, config key), values
  `low|medium|high|xhigh` → 15/30/60/120 turns; `--max-turns` stays the explicit integer override.
- Canonical `thinking_effort` keeps `--thinking-effort` (UI label "Thinking effort"); fix help texts to list `low…max`.
- Legacy `effort` (spec key, `--effort`, `AO_EFFORT`) still means turn budget in this release, with a deprecation
  warning stating it will mean thinking effort after migration (T-9ymKja). Both given and different → error.
- `ao migrate effort-keys [--apply]` rewrites `effort`→`turn_budget` in workspace `specs/agents.json`, workflow
  templates/instances and `.ao/config.yaml` (dry-run diff by default).
- Help text: `--turn-budget` lists all four values with turn counts.
Autocompact (owner request 2026-11-09): **provider-neutral, first-class setting in CLI, config, spec and dashboard**,
following the existing `thinking_effort` precedent (defined neutrally in `models.py`, translated only by the executor
that supports it). Field `autocompact` (`auto` or a context-token threshold 100k–1M) on agent, task and run level
(`--autocompact`, `AO_AUTOCOMPACT`, config key, registry entry). Executors declare capabilities
(`Executor.supported_settings`, e.g. `{"thinking_effort", "autocompact"}` for `claude_cli`); `claude_cli` maps it to
Claude's `--autocompact` unless `extra_args` already has one (extra_args wins, warning logged). Setting it for an agent
whose executor lacks the capability ⇒ `ao validate` warning + one run-log warning, never an error (future
Codex/Gemini executors map or ignore). The registry carries `providers` metadata so CLI help and the dashboard show
"applies to: claude-cli". Recorded per task (T-zP28k9 FR-2). The existing `ao validate` missing-`--autocompact`
warning recognises the new field.

## Acceptance Criteria
1. Spec/CLI/env alias matrix tests incl. conflict error and deprecation warnings (CliRunner).
2. `ao migrate effort-keys` dry-run + apply tests on fixture workspaces; idempotent.
3. Autocompact precedence tests (task > agent > run/invocation) and argv mapping tests; extra_args precedence.
4. Docs: CLI reference, template READMEs (autocompact section now points to the field), cost-hygiene HLD note.

## Risks
- Sibling workspaces still use `effort` — warnings only in this release; flip gated by T-9ymKja.

## Dependencies
- T-zP28k9

## Pseudocode / Algorithm
```text
AgentSpec/TaskSpec: turn_budget, effort (deprecated alias, validator maps -> turn_budget + warning), autocompact
claude_cli argv: if autocompact and '--autocompact' not in extra_args: argv += ['--autocompact', str(v)]
```

## Schemas / Interface Notes
- `turn_budget: Literal[low,medium,high,xhigh]`
- `autocompact: 'auto' | int (100000..1000000)`

## Handoff Boundary
- Upstream: T-zP28k9
- Downstream: T-pMpo4O (form fields), T-xNFBww (display), T-9ymKja (flip)

## Artifacts
- Docs/comments: `meta/tickets/E-aYG5vn-dashboard-run-limits-and-effort-naming/T-f0d7gm-turn-budget-rename-and-autocompact/`
- Large outputs: `N/A`

## Standard gates (every task)
- `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` green; UI changes also `npm test` + `npm run build` in `ui/`.
- New behaviour off / absent settings ⇒ byte-identical behaviour (golden tests stay green).
- Work only in `./agent-orchestrator` on the epic branch; test against temp workspaces; never `install.sh` / restart `ao.service`.
- Update this TASK.md + STATUS.md and the epic STATUS.md rollup together.
