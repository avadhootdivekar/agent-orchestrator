# STATUS

- ID: `T-Fw8Gp4-file-watch-trigger`
- Updated At: 2026-09-06
- State: Draft
- Owner: TBD (assigned at sprint start)

## This update
- Ticket created by the `architect` agent as part of the E-Sc9Rt4 design package. Not started.
- Scope: `service/watch.py` + `FileWatchScheduler` — polled, root-guarded, debounced, bounded file watching whose `scheduled_for` is the newest changed mtime, making event fires replay-safe.

## Evidence
- None yet — no implementation has begun. Design basis:
  `docs-md/scheduler-triggers-hld.md` and `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md`.

## Risks / Blockers
- Blocked until: `T-Sd1Kq7`, `T-Fr2Nx8`, `T-Ev3Qm5` and `T-Cl6Jn9` have landed. Coordinate the one-line `SCHEDULERS`-map edit with `T-Ev3Qm5`'s owner if the tasks overlap.

## Next actions
1. Read the upstream task's **merged code**, not only the HLD, before starting (the interface-drift
   rule this epic inherits from E-GIytcL).
2. Implement against the acceptance criteria in `TASK.md`, in AC order.
3. Hand the `file_watch` kind to `T-Ap1Xs3` for rendering and flag the glob/symlink handling for `T-Se4Bk5`'s review.
