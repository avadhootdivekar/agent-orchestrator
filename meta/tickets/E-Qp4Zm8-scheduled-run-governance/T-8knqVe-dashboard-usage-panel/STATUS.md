# STATUS

- ID: `T-8knqVe-dashboard-usage-panel`
- Updated At: 2026-11-10
- State: Draft
- Owner: TBD (assigned at sprint start)

## This update
- Ticket created by the `architect` agent as part of the E-Qp4Zm8 design package. Not started.
- Scope: Three API routes + `ScheduleUsage.tsx` meters, paused-run Resume, governance event feed (D-3 dashboard record).

## Evidence
- None yet. No implementation has begun. Design basis:
  `docs-md/scheduled-run-governance-hld.md` and `docs-md/adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md`.

## Risks / Blockers
- Blocked until E-Sc9Rt4 `T-Ap1Xs3` (its Sprint 2), `T-rwj4nV`, `T-nI4DBf`, `T-Uz0Uol` have merged.

## Next actions
1. Read the upstream tasks' **merged code** (including E-Sc9Rt4's), not only the HLD, before starting.
2. Implement against the acceptance criteria in `TASK.md`, in AC order; report the pytest delta, ruff/format, mypy, coverage.
3. Hand the POST surface to `T-lTW88y` for security review.
