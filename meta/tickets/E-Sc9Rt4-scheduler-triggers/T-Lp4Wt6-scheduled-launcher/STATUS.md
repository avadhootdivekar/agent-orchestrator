# STATUS

- ID: `T-Lp4Wt6-scheduled-launcher`
- Updated At: 2026-09-06
- State: Draft
- Owner: TBD (assigned at sprint start)

## This update
- Ticket created by the `architect` agent as part of the E-Sc9Rt4 design package. Not started.
- Scope: `service/schedule_launch.py` — the two-phase (intent-then-launch) `ScheduledLauncher` that turns a `FireDecision` into a detached `ao run` through the existing `ProcessSupervisor` path, with per-fire template instantiation and zero new argv surface.

## Evidence
- None yet — no implementation has begun. Design basis:
  `docs-md/scheduler-triggers-hld.md` and `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md`.

## Risks / Blockers
- Blocked until: `T-Ev3Qm5` (Protocol + `FireDecision`), `T-Fr2Nx8` (stores), `T-Sd1Kq7` (`RunArgs`) and `T-Ri7Dz2` (`run_id` option) have landed.

## Next actions
1. Read the upstream task's **merged code**, not only the HLD, before starting (the interface-drift
   rule this epic inherits from E-GIytcL).
2. Implement against the acceptance criteria in `TASK.md`, in AC order.
3. Hand the constructor signature to `T-Sv5Hb3` and confirm to `T-Cl6Jn9` that `run-now --local` reuses this class rather than reimplementing a launch.
