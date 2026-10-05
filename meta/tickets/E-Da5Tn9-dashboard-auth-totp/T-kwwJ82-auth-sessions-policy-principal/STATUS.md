# STATUS

- ID: `T-kwwJ82-auth-sessions-policy-principal`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane C)
- Scope: `MVP` · Sprint: `S1` · Estimate: `2 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope; lane B → C. Changes:
  - **Moved to T-kzEzwy:** `constants`, `errors` and `seams` (developer finding D-3).
  - **Single timeline.** Sessions use one `CLOCK_BOOTTIME` monotonic clock instead of dual-clock
    expiry (reviewer finding R-7).
  - **Session proof** (D25): `proof_hash` and `proof_matches` (dev-security #2).
  - **Revocation:** sessions are keyed by `user_id` (dev-security #1).
  - **Mutation discipline:** every mutation ends in `put()`, and `principal_for` is the only
    `Principal` builder (reviewer findings R-1, R-5).
  - **Principal v2:** a `roles` tuple, `user_id`, `amr`, `auth_time`, `provider`;
    `require_principal` and `auth_enabled` (dev-security #6, dev-critic C-4).
  - **Policy:** per-app policy tables replace decorators (developer D-1, reviewer R-7), and
    `totp_requirement` is the single requirement predicate (reviewer R-4).
  - **`VerifiedIdentity`** is seeded in `provider.py` here.
  - Design and tickets only; **no code written**.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the E-Da5Tn9
  design package (design and tickets only; **no code written**). The `Principal` contract (HLD §2.6)
  is frozen for the approval-gates epic.

## Evidence
- None yet.

## Risks / Blockers
- **OQ-8:** the manager must confirm the `Principal` v2 shape (the `roles` tuple and the added
  fields) with the approval-gates epic **before `principal.py` is merged**. Implementation may
  proceed; merging waits for the confirmation.

## Next actions
1. manager: confirm OQ-8 with the approval-gates epic and record the outcome here.
2. developer: implement once T-kzEzwy lands, run the TASK.md verification, and record the results
   here.
