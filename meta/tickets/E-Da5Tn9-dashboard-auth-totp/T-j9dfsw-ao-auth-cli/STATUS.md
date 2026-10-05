# STATUS

- ID: `T-j9dfsw-ao-auth-cli`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane B)
- Scope: `MVP` · Sprint: `S2` · Estimate: `3 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope after the Phase-4
  consultations (HLD §28).
  - **Estimate and lane:** 2.5 d → 3 d; lane Q → B (HLD §24.2 #12).
  - **New command `enrollment-token`.** `add-user --require-totp` and `reset-2fa` now print a
    one-time enrollment token, because forced enrollment requires it (dev-security #5, ADR-0021 D5).
  - **Lockouts.** `unlock` and `remove-user` act on `lockouts.json` in the **state** directory,
    keyed by the immutable `user_id` (reviewer R-7b, dev-security #1/#3, ADR-0021 D9/D10).
  - **`status`** adds the state directory, `lockouts.json` health, the pending-token count and the
    parent-directory checks (dev-security #12).
  - **Imports and prompts:** lazy imports inside command bodies, mismatch re-prompt and EOF → exit 1
    (developer D-8).
  - **Clarifications:**
    - `disable-2fa` clears a non-enrolled user's `totp_required` (the §19.1 fix path).
    - `enrollment-token` prints notes rather than refusing when the CLI's resolved policy says the
      token looks unnecessary, because the server may read a different env.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the
  E-Da5Tn9 design package (design and tickets only; **no code written**). The CLI is the only
  account-administration and bootstrap surface (no web sign-up).

## Evidence
- None yet.

## Risks / Blockers
- None. Watch item: CliRunner + `typer.prompt(hide_input=True)` (A-9). Verify on day 1.

## Next actions
1. developer: implement once T-kzEzwy, T-s6sJmB, T-8NQP8J, T-PlEROT and T-CsT5gk land. Run the
   verification and record the coverage of `auth/cli.py` here.
