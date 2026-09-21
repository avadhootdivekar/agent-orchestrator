# STATUS

- ID: `T-Ri7Dz2-run-id-flag`
- Updated At: 2026-09-06
- State: Draft
- Owner: TBD (assigned at sprint start)

## This update
- Ticket created by the `architect` agent as part of the E-Sc9Rt4 design package. Not started.
- Scope: `ao run --run-id` with a path-safe pattern guard and a duplicate-directory refusal, plumbed through `ProcessSupervisor`'s existing options allow-list so scheduled launches never poll for their own run id.

## Evidence
- None yet — no implementation has begun. Design basis:
  `docs-md/scheduler-triggers-hld.md` and `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md`.

## Risks / Blockers
- Blocked until: nothing — independent, can start any time in Sprint 1. `T-Lp4Wt6` needs it before its zero-latency-attribution AC can pass.

## Next actions
1. Read the upstream task's **merged code**, not only the HLD, before starting (the interface-drift
   rule this epic inherits from E-GIytcL).
2. Implement against the acceptance criteria in `TASK.md`, in AC order.
3. Hand the option name and the `ALLOWED_OPTIONS` entry to `T-Lp4Wt6`; do NOT edit `meta/ROADMAP.md` (owned by `T-Dc6Zr2`).
