# STATUS

- ID: `T-et7Edf-run-cost-cap-gate`
- Updated At: 2026-11-10
- State: Draft
- Owner: TBD (assigned at sprint start)

## This update
- Ticket created by the `architect` agent as part of the E-Qp4Zm8 design package. Not started.
- Scope: General engine feature `ao run/resume --cost-cap-usd`: sticky `RunState.cost_cap_usd` + non-latching `builtin.cost_cap` gate at dispatch and boundary; sentinel cap feed.

## Evidence
- None yet. No implementation has begun. Design basis:
  `docs-md/scheduled-run-governance-hld.md` and `docs-md/adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md`.

## Risks / Blockers
- Not blocked. No E-Sc9Rt4 dependency; may start immediately. Coordinate engine.py edits with any concurrent engine epic.

## Next actions
1. Read the upstream tasks' **merged code** (including E-Sc9Rt4's), not only the HLD, before starting.
2. Implement against the acceptance criteria in `TASK.md`, in AC order; report the pytest delta, ruff/format, mypy, coverage.
3. Publish the option name `cost_cap_usd` and trip id `builtin.cost_cap` to `T-PpE5lf`/`T-nI4DBf`.
