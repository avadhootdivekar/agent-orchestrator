# STATUS

- ID: `T-Ev3Qm5-schedule-engine-core`
- Updated At: 2026-09-06
- State: Draft
- Owner: TBD (assigned at sprint start)

## This update
- Ticket created by the `architect` agent as part of the E-Sc9Rt4 design package. Not started.
- Scope: `service/schedule_engine.py` — the signal-free decision engine: evaluate/tick, backlog collapse, overlap, catch-up, deterministic jitter, concurrency caps, per-schedule isolation and quarantine.

## Evidence
- None yet — no implementation has begun. Design basis:
  `docs-md/scheduler-triggers-hld.md` and `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md`.

## Risks / Blockers
- Blocked until: `T-Sd1Kq7` and `T-Fr2Nx8` have both landed and their merged code has been read.

## Next actions
1. Read the upstream task's **merged code**, not only the HLD, before starting (the interface-drift
   rule this epic inherits from E-GIytcL).
2. Implement against the acceptance criteria in `TASK.md`, in AC order.
3. Hand off the `ScheduledLauncher` Protocol to `T-Lp4Wt6` and the `tick()`/`status_snapshot()` signatures to `T-Sv5Hb3`.
