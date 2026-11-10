# STATUS

- ID: `T-lJu9oZ-quota-window-math`
- Updated At: 2026-11-10
- State: Draft
- Owner: TBD (assigned at sprint start)

## This update
- Ticket created by the `architect` agent as part of the E-Qp4Zm8 design package. Not started.
- Scope: Pure day/week/month calendar windows in a timezone (DST, midnight gap, week start) for `schedules/windows.py`.

## Evidence
- None yet. No implementation has begun. Design basis:
  `docs-md/scheduled-run-governance-hld.md` and `docs-md/adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md`.

## Risks / Blockers
- Not blocked. No upstream dependency; may start immediately (before E-Sc9Rt4).

## Next actions
1. Read the upstream tasks' **merged code** (including E-Sc9Rt4's), not only the HLD, before starting.
2. Implement against the acceptance criteria in `TASK.md`, in AC order; report the pytest delta, ruff/format, mypy, coverage.
3. Hand `Window`/`windows_for` to `T-Uz0Uol` and `T-PpE5lf`.
