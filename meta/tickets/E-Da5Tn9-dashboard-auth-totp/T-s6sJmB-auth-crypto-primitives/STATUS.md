# STATUS

- ID: `T-s6sJmB-auth-crypto-primitives`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane A)
- Scope: `MVP` · Sprint: `S1` · Estimate: `2.5 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate unchanged (2.5 d).
  - Test doubles move to `tests/auth/helpers/crypto.py` (the helpers package, design-review M2).
  - New hard DAG edge T-s6sJmB → T-8NQP8J (design-review minor 2): land `totp.py` and
    `recovery.py` first, by S1 day 3; T-8NQP8J no longer stubs them.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope. Changes:
  - **Foundation.** The task now builds on T-kzEzwy, so it no longer creates `constants.py`
    (developer finding D-3).
  - **`parse_hash` bounds** (AC-2): `ln ≤ 17`, `r`/`p` ≤ 16, at most 128 MiB (dev-security #12).
  - **Test doubles** (`TEST_PARAMS`, `FastFakeHasher`) moved to `tests/auth/helpers.py`
    (reviewer finding R-12).
  - **Enrollment tokens** reuse the recovery-code format (dev-security #5).
  - No-early-exit checks are now explicit ACs.
  - Design and tickets only; **no code written**.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the E-Da5Tn9
  design package (design and tickets only; **no code written**).
  - The specification is in HLD §11.6–§11.8 and §12.6.
  - The RFC vectors were verified with stdlib `hmac` at design time (HLD §25.4).

## Evidence
- None yet.

## Risks / Blockers
- None. Starts once T-kzEzwy lands. T-8NQP8J needs `totp.py` and `recovery.py` by S1 day 3.

## Next actions
1. developer: implement `totp.py` and `recovery.py` first, then `passwords.py`; run the TASK.md
   verification, and record the results and coverage here.
