# STATUS

- ID: `T-Ap1Xs3-dashboard-schedules`
- Updated At: 2026-09-06
- State: Draft
- Owner: TBD (assigned at sprint start)

## This update
- Ticket created by the `architect` agent as part of the E-Sc9Rt4 design package. Not started.
- Scope: five `/api/schedules` routes behind a typed exception hierarchy (behavior in `ui/service.py`, <=5-line adapters in `ui/app.py`) plus one React Schedules panel following `ui/README.md` conventions, with the committed `make ui-build` output.

## Evidence
- None yet — no implementation has begun. Design basis:
  `docs-md/scheduler-triggers-hld.md` and `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md`.

## Risks / Blockers
- Blocked until: `T-Sd1Kq7`, `T-Fr2Nx8`, `T-Cl6Jn9` and `T-Un0Lm6` have landed.

## Next actions
1. Read the upstream task's **merged code**, not only the HLD, before starting (the interface-drift
   rule this epic inherits from E-GIytcL).
2. Implement against the acceptance criteria in `TASK.md`, in AC order.
3. Verify with `git status` that `src/agent_orchestrator/ui/static/` actually changed, and record the unauthenticated-mutation posture note (AC17) for `T-Se4Bk5`.
