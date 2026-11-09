# TASK: T-9ymKja-effort-means-thinking-flip

## Metadata
- Task ID: `T-9ymKja-effort-means-thinking-flip`
- Epic ID: `E-aYG5vn-dashboard-run-limits-and-effort-naming`
- Owner: `unassigned`
- Created: `2026-11-09`
- Last Updated: `2026-11-09`
- Status: `Blocked`
- Estimate: `< 3 days`
- Phase: gated follow-up
- Agent: developer

## Requirements Mapping
- Requirement IDs: E-aYG5vn FR-10

## Description
Final step of the owner-approved naming: `effort` / `--effort` / `AO_EFFORT` become aliases of **thinking effort**
(matching Claude's own `--effort`). Because value sets overlap (`low|medium|high|xhigh`), the flip cannot be detected
from values: `ao validate` and the CLI emit an error-level notice for one release if a spec still contains a legacy
`effort` key on an agent that also lacks `turn_budget` (likely un-migrated), with a pointer to `ao migrate effort-keys`.

## Acceptance Criteria
1. Alias flip tests; un-migrated detection notice tests; docs + changelog updated.

## Risks
- Silent semantic change in un-migrated workspaces — mitigated by gate + detection notice.

## Dependencies
- T-f0d7gm
- owner gate

## Pseudocode / Algorithm
```text
N/A
```

## Schemas / Interface Notes
- `effort` → alias of `thinking_effort`

## Handoff Boundary
- Upstream: T-f0d7gm
- Downstream: none

## Artifacts
- Docs/comments: `meta/tickets/E-aYG5vn-dashboard-run-limits-and-effort-naming/T-9ymKja-effort-means-thinking-flip/`
- Large outputs: `N/A`

## Standard gates (every task)
- `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` green; UI changes also `npm test` + `npm run build` in `ui/`.
- New behaviour off / absent settings ⇒ byte-identical behaviour (golden tests stay green).
- Work only in `./agent-orchestrator` on the epic branch; test against temp workspaces; never `install.sh` / restart `ao.service`.
- Update this TASK.md + STATUS.md and the epic STATUS.md rollup together.
