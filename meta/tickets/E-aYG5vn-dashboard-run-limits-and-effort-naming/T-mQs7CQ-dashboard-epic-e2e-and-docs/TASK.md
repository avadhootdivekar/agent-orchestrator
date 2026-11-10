# TASK: T-mQs7CQ-dashboard-epic-e2e-and-docs

## Metadata
- Task ID: `T-mQs7CQ-dashboard-epic-e2e-and-docs`
- Epic ID: `E-aYG5vn-dashboard-run-limits-and-effort-naming`
- Owner: `unassigned`
- Created: `2026-11-09`
- Last Updated: `2026-11-09`
- Status: `Done`
- Estimate: `< 3 days`
- Phase: close
- Agent: tester + developer

## Requirements Mapping
- Requirement IDs: E-aYG5vn §7 acceptance

## Description
E2E: launch from dashboard (New Run and Template Launch) with thinking effort, turn budget, autocompact, max turns;
verify argv/effective settings/state and that the run page + task panel show them; resume with changed options shows
history. Docs: dashboard guide, CLI reference, template READMEs, migration note for `effort`→`turn_budget`. Tickets
and STATUS rollups synced.

## Acceptance Criteria
1. [x] E2E green (API + CliRunner level) — `tests/ui/test_e2e_run_options_dashboard.py`.
2. [x] Docs updated — README, `docs-md/run-options-and-limits-guide.md` (incl. effort→turn_budget migration note), template READMEs.
3. [x] Epic STATUS reflects completion (all but gated T-9ymKja).

## Risks
- None notable.

## Dependencies
- T-pMpo4O
- T-xNFBww

## Pseudocode / Algorithm
```text
N/A
```

## Schemas / Interface Notes
- N/A

## Handoff Boundary
- Upstream: all E-aYG5vn tasks except T-9ymKja
- Downstream: epic close; T-SnXAcc reuses fixtures

## Artifacts
- Docs/comments: `meta/tickets/E-aYG5vn-dashboard-run-limits-and-effort-naming/T-mQs7CQ-dashboard-epic-e2e-and-docs/`
- Large outputs: `N/A`

## Standard gates (every task)
- `pytest -q`, `ruff check . && ruff format --check .`, `mypy .` green; UI changes also `npm test` + `npm run build` in `ui/`.
- New behaviour off / absent settings ⇒ byte-identical behaviour (golden tests stay green).
- Work only in `./agent-orchestrator` on the epic branch; test against temp workspaces; never `install.sh` / restart `ao.service`.
- Update this TASK.md + STATUS.md and the epic STATUS.md rollup together.
