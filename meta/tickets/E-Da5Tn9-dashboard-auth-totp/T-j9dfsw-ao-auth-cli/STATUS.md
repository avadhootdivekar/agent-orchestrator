# STATUS

- ID: `T-j9dfsw-ao-auth-cli`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane B)
- Scope: `MVP` · Sprint: `S2` · Estimate: `3 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate, lane and sprint unchanged (3 d, lane B, S2). Changes:
  - **Security M6:** a config-sourced `store_dir` is never created or chmod-ed; it must exist and be
    private, or the operator passes `--auth-dir`. New AC 5c (= AC-46).
  - **Security M3 / design-review M3:** `status` reports the `ConfigRisk` flags (never exit 78 for
    them), the recent `auth.startup.*` events, and the required/off policy notes. New AC 5b (AC-45
    status part).
  - **Security L1 / L6:** mutating commands call `lockouts.ensure_name_key()`; a group-writable
    parent owned by the user now warns (AC 4 adjusted).
  - **Rebalance:** the store-busy multiprocess test (old AC 9) moved to T-U2ERMo; AC 8 reduced to
    the CLI invocations (T-kzEzwy's skeleton covers module imports).
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
- The 3-day estimate is tight after v2.1; report any overrun here early.

## Next actions
1. developer: implement once T-kzEzwy, T-s6sJmB, T-8NQP8J, T-PlEROT and T-CsT5gk land. Run the
   verification and record the coverage of `auth/cli.py` here.
