# TASK: T-Sn9Pow-llm-powers-directives-routed

## Metadata
- Task ID: `T-Sn9Pow-llm-powers-directives-routed`
- Epic ID: `E-Sn7tL2-sentinel-long-running-overseer`
- Owner: `developer (claude-sonnet-5-5)`
- Created: `2026-11-09`
- Last Updated: `2026-11-09`
- Status: `Done`
- Estimate: `< 3 days`
- Phase: P3
- Agent: developer (+ reviewer)

## Requirements Mapping
- Requirement IDs: FR-15, FR-16, FR-22 (panel v2); D2, D3

## Description
Powers `advise` / `act`; LLM `inject`, `edit_pending` (if `allow_graph_edits`), `kill` (if `allow_kill`; rationale
≥40 chars, min running minutes, per-hour cap, never barrier/emitter) through the shared checker. Directives +
checkpoint acknowledgement (`OV-R12b`) and digest `sentinel{...}` block in overseer-runner. routed-runner opt-in: the
`act` generation acts as the L2 planner when idle slots and no backlog (trigger `idle_slots_no_backlog`), with a
routed-runner-specific contract section. Dashboard panel v2: generation timeline + spend, proposals (applied /
quarantined with rule), directives and their acknowledgements.

## Acceptance Criteria
1. Checker tests per power and per flag; LLM kill limits enforced.
2. Overseer e2e: directive raised → next checkpoint must acknowledge (missing ack fails ckpt-check).
3. routed-runner e2e (scripted driver): idle slots → generation proposes unit → injected with sentinel origin.
4. UI tests for panel v2.

## Risks
- Thrash between sentinel and checkpoint — authority rules + shared caps; covered by an e2e scenario.

## Dependencies
- T-Sn8Gen
- T-Sn6Brr
- T-Sn4Rec
- T-Sn5Ui1

## Pseudocode / Algorithm
```text
N/A — policy layer over existing checker + inbox
```

## Schemas / Interface Notes
- `sentinel-contract.md` (per template)
- checkpoint verdict `directive_responses[]`

## Handoff Boundary
- Upstream: T-Sn8Gen
- Downstream: T-SnXAcc

## Artifacts
- Docs/comments: `meta/tickets/E-Sn7tL2-sentinel-long-running-overseer/T-Sn9Pow-llm-powers-directives-routed/`
- Large outputs: `N/A`

## Standard gates (every task)
- `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` green; UI changes also `npm test` + `npm run build` in `ui/`.
- New behaviour off / absent settings ⇒ byte-identical behaviour (golden tests stay green).
- Work only in `./agent-orchestrator` on the epic branch; test against temp workspaces; never `install.sh` / restart `ao.service`.
- Update this TASK.md + STATUS.md and the epic STATUS.md rollup together.
