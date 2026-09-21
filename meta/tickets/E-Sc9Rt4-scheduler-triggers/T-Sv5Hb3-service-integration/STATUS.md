# STATUS

- ID: `T-Sv5Hb3-service-integration`
- Updated At: 2026-09-06
- State: Draft
- Owner: TBD (assigned at sprint start)

## This update
- Ticket created by the `architect` agent as part of the E-Sc9Rt4 design package. Not started.
- Scope: registry schema additions, `ao service run` engine wiring with three kill switches and an outer tick backstop, the hub-status merge via `build_status_provider`, and the systemd unit delta.

## Evidence
- None yet — no implementation has begun. Design basis:
  `docs-md/scheduler-triggers-hld.md` and `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md`.

## Risks / Blockers
- Blocked until: `T-Ev3Qm5` and `T-Lp4Wt6` have landed. `T-Wh9Kv1` is a forward dependency for the webhook server behind the flags this task adds.

## Next actions
1. Read the upstream task's **merged code**, not only the HLD, before starting (the interface-drift
   rule this epic inherits from E-GIytcL).
2. Implement against the acceptance criteria in `TASK.md`, in AC order.
3. Hand the registry policy block to `T-Cl6Jn9` and the wired webhook flags to `T-Wh9Kv1`; confirm with `git diff` that `service/cli.py` grew a sibling rather than being restructured.
