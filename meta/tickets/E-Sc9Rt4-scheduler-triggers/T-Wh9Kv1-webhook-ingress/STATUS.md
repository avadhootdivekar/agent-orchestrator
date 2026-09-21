# STATUS

- ID: `T-Wh9Kv1-webhook-ingress`
- Updated At: 2026-09-06
- State: Draft
- Owner: TBD (assigned at sprint start)

## This update
- Ticket created by the `architect` agent as part of the E-Sc9Rt4 design package. Not started.
- Scope: `service/webhook.py` — the project's first authenticated endpoint: a default-off HMAC-verified listener with replay, nonce, rate and size defences that enqueues deliveries for the engine to fire through its normal policy path.

## Evidence
- None yet — no implementation has begun. Design basis:
  `docs-md/scheduler-triggers-hld.md` and `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md`.

## Risks / Blockers
- Blocked until: `T-Sd1Kq7`, `T-Ev3Qm5`, `T-Sv5Hb3` and `T-Cl6Jn9` have landed.

## Next actions
1. Read the upstream task's **merged code**, not only the HLD, before starting (the interface-drift
   rule this epic inherits from E-GIytcL).
2. Implement against the acceptance criteria in `TASK.md`, in AC order.
3. Hand the threat model and the accepted limitations (in-memory nonce/bucket reset on restart; hub on the same machine is still unauthenticated) to `T-Se4Bk5` for audit.
