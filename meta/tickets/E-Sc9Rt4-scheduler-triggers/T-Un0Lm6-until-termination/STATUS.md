# STATUS

- ID: `T-Un0Lm6-until-termination`
- Updated At: 2026-09-06
- State: Draft
- Owner: TBD (assigned at sprint start)

## This update
- Ticket created by the `architect` agent as part of the E-Sc9Rt4 design package. Not started.
- Scope: `until` — inter-run loop termination on `max_runs` / `artifact_exists` / a gate JSON field, with a `completed` lifecycle that `ao schedule enable` resets, deliberately distinct from the engine's intra-run `LoopSpec`.

## Evidence
- None yet — no implementation has begun. Design basis:
  `docs-md/scheduler-triggers-hld.md` and `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md`.

## Risks / Blockers
- Blocked until: `T-Sd1Kq7`, `T-Ev3Qm5` and `T-Cl6Jn9` have landed. Coordinate the `schedule_engine.py` edit with `T-Ev3Qm5`'s owner if the tasks overlap.

## Next actions
1. Read the upstream task's **merged code**, not only the HLD, before starting (the interface-drift
   rule this epic inherits from E-GIytcL).
2. Implement against the acceptance criteria in `TASK.md`, in AC order.
3. Hand the `completed`/`runs_count` display contract to `T-Ap1Xs3` and the `LoopSpec`-vs-`until` contrast to `T-Dc6Zr2` for the docs.
