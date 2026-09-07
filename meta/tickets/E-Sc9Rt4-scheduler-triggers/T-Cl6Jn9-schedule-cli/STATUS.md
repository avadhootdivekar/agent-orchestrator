# STATUS

- ID: `T-Cl6Jn9-schedule-cli`
- Updated At: 2026-09-06
- State: Draft
- Owner: TBD (assigned at sprint start)

## This update
- Ticket created by the `architect` agent as part of the E-Sc9Rt4 design package. Not started.
- Scope: the `ao schedule` Typer sub-app (list/next/add/remove/enable/disable/run-now/history/validate) plus the foreground `daemon [--once]` dev/test mode, with `run-now` behaving identically with or without a live daemon.

## Evidence
- None yet — no implementation has begun. Design basis:
  `docs-md/scheduler-triggers-hld.md` and `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md`.

## Risks / Blockers
- Blocked until: `T-Sd1Kq7`, `T-Fr2Nx8`, `T-Ev3Qm5`, `T-Lp4Wt6` and `T-Sv5Hb3` have landed.

## Next actions
1. Read the upstream task's **merged code**, not only the HLD, before starting (the interface-drift
   rule this epic inherits from E-GIytcL).
2. Implement against the acceptance criteria in `TASK.md`, in AC order.
3. Hand the `ao schedule add` option surface to `T-Fw8Gp4`/`T-Wh9Kv1`/`T-Un0Lm6` (each extends it) and report the `git diff --stat` of top-level `cli.py`.
