# STATUS

- ID: `T-Sd1Kq7-schedule-binding-model`
- Updated At: 2026-09-06
- State: Draft
- Owner: TBD (assigned at sprint start)

## This update
- Ticket created by the `architect` agent as part of the E-Sc9Rt4 design package. Not started.
- Scope: the `schedules/` package (binding model, JSON Schema, locked store), `service/statefile.py`, and the additive `Trigger.interval_seconds` / `IntervalScheduler` change.

## Evidence
- None yet — no implementation has begun. Design basis:
  `docs-md/scheduler-triggers-hld.md` and `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md`.

## Risks / Blockers
- Blocked until: nothing — this is the epic's first task and starts on day 1 of Sprint 1, in parallel with `T-Fr2Nx8`.

## Next actions
1. Read the upstream task's **merged code**, not only the HLD, before starting (the interface-drift
   rule this epic inherits from E-GIytcL).
2. Implement against the acceptance criteria in `TASK.md`, in AC order.
3. Hand off to `T-Ev3Qm5` and `T-Lp4Wt6` with the exact landed signatures, and record the `service/statefile.py` refactor of the three existing call sites as a follow-up.
