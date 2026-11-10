# STATUS

- ID: `T-PpE5lf-pre-fire-gate-and-reservation`
- Updated At: 2026-11-10
- State: Draft
- Owner: TBD (assigned at sprint start)

## This update
- Ticket created by the `architect` agent as part of the E-Qp4Zm8 design package. Not started.
- Scope: Gate step in `evaluate()` (between overlap and concurrency), authoritative `check_and_reserve()` under flock, prompt snapshot, fail-closed argv check, retire hook for cap state.

## Evidence
- None yet. No implementation has begun. Design basis:
  `docs-md/scheduled-run-governance-hld.md` and `docs-md/adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md`.

## Risks / Blockers
- Blocked until E-Sc9Rt4 `T-Ev3Qm5`, `T-Lp4Wt6`, `T-Ri7Dz2` and this epic's `T-Uz0Uol`, `T-et7Edf`, `T-tPlK1T` have merged.

## Next actions
1. Read the upstream tasks' **merged code** (including E-Sc9Rt4's), not only the HLD, before starting.
2. Implement against the acceptance criteria in `TASK.md`, in AC order; report the pytest delta, ruff/format, mypy, coverage.
3. Hand the retire hook + lock helper to `T-nI4DBf`.
