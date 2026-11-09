# TASK: T-xNFBww-dashboard-limits-panels

## Metadata
- Task ID: `T-xNFBww-dashboard-limits-panels`
- Epic ID: `E-aYG5vn-dashboard-run-limits-and-effort-naming`
- Owner: `unassigned`
- Created: `2026-11-09`
- Last Updated: `2026-11-09`
- Status: `Draft`
- Estimate: `< 3 days`
- Phase: UI
- Agent: developer (UI + backend)

## Requirements Mapping
- Requirement IDs: E-aYG5vn FR-3, FR-4, FR-8

## Description
Run page "Limits & settings" section (running and completed runs): every effective setting with its source and
consumption vs cap where measurable — run cost vs USD caps (engine breakers and overseer `run_budget_usd` + current
stage from `overseer-config.json`/latest digest), tokens vs token budget, wall clock vs `run_wall_clock_seconds`, active
seconds vs `run_active_seconds`, injected tasks vs caps (incl. sentinel cap), max_parallel (+ live override/ceiling),
default task timeout, quota settings. Task detail panel + NowRunning rows: thinking effort, turn budget / max turns with
turns used, autocompact, timeout (elapsed vs limit), task cost vs cap, attempts used vs max. Usage view groups by
`(agent, model, turn budget, thinking effort)`. Pre-upgrade runs show "—" with tooltip. Backend: extend
`ui/runs.py` `RunDetail`/`RunningTaskBrief` (additive fields).

## Acceptance Criteria
1. API tests for new fields incl. old state.json fixtures.
2. UI tests for running + completed + pre-upgrade runs; progress bars against caps.
3. Usage grouping test with mixed old/new rows.

## Risks
- Overseer template budget is template-specific — isolate the reader behind a small adapter keyed by template name.

## Dependencies
- T-zP28k9
- T-f0d7gm

## Pseudocode / Algorithm
```text
N/A
```

## Schemas / Interface Notes
- `RunDetail.limits[]: {key,label,value,source,used?,cap?,unit}`
- `RunningTaskBrief` + task detail effective fields

## Handoff Boundary
- Upstream: T-zP28k9
- Downstream: T-mQs7CQ; layout shared with T-Sn5Ui1

## Artifacts
- Docs/comments: `meta/tickets/E-aYG5vn-dashboard-run-limits-and-effort-naming/T-xNFBww-dashboard-limits-panels/`
- Large outputs: `N/A`

## Standard gates (every task)
- `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` green; UI changes also `npm test` + `npm run build` in `ui/`.
- New behaviour off / absent settings ⇒ byte-identical behaviour (golden tests stay green).
- Work only in `./agent-orchestrator` on the epic branch; test against temp workspaces; never `install.sh` / restart `ao.service`.
- Update this TASK.md + STATUS.md and the epic STATUS.md rollup together.
