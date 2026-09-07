# STATUS

- ID: `T-Fr2Nx8-fire-store-and-state`
- Updated At: 2026-09-06
- State: Draft
- Owner: TBD (assigned at sprint start)

## This update
- Ticket created by the `architect` agent as part of the E-Sc9Rt4 design package. Not started.
- Scope: `service/fire_store.py` — the at-most-once fire-intent store (fsync-durable), per-schedule runtime state, and the structured JSONL event log.

## Evidence
- None yet — no implementation has begun. Design basis:
  `docs-md/scheduler-triggers-hld.md` and `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md`.

## Risks / Blockers
- Blocked until: `T-Sd1Kq7`'s `service/statefile.py` lands (AC8). The rest of this task can start in parallel on day 1 of Sprint 1.

## Next actions
1. Read the upstream task's **merged code**, not only the HLD, before starting (the interface-drift
   rule this epic inherits from E-GIytcL).
2. Implement against the acceptance criteria in `TASK.md`, in AC order.
3. Hand off to `T-Ev3Qm5` with the landed store signatures; flag the single-writer assumption on `state.json` in the handoff.
