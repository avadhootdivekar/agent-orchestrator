# STATUS

- ID: `T-s6sJmB-auth-crypto-primitives`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (lane A)
- Scope: `MVP` · Sprint: `S1` · Estimate: `2.5 d`

## This update
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented end to end; all 14 ACs met.
  - Source: `auth/totp.py`, `auth/recovery.py` (landed first), `auth/passwords.py`; all L1, stdlib
    plus L0 imports only.
  - Tests: `tests/auth/test_{totp,recovery,passwords}.py`; helpers `tests/auth/helpers/crypto.py`
    (`TEST_PARAMS`, `FastFakeHasher` with `rehash_needed` / `raise_busy` knobs, `hash_calls`).
  - Design notes (no contract change):
    - `new_recovery_records` returns `(salt_hex, hash_hex)` pairs and rejects an invalid code with
      `ValueError`. `find_unused_match` takes any `Sequence` of a read-only `RecoveryRecord`
      Protocol (`salt_hex`, `hash_hex`, `used_at`), which `store.RecoveryCodeHash` satisfies
      structurally, so L1 never imports the store.
    - `verify_password` returns `compare_digest(...) and not truncated`: an over-cap password runs
      one scrypt on its capped prefix and can never verify, even against a hash of that prefix.
      `hash_password` raises `ValueError` for an over-cap password (policy forbids it).
    - `parse_hash` regex is `re.ASCII` and limits `ln`/`r`/`p` to 1-2 digits, so a hostile
      "ln=9999999999..." or non-ASCII digits are a `format` error before `int()`.
    - `needs_rehash` of an unparseable hash returns True (it is never at current params).
    - `BoundedScryptHasher.hash` shares the `verify` queue bound; `close()` shuts the pool.
    - The malformed-hash "once per process" flag is the module global `_malformed_logged`
      (reset by an autouse fixture in the tests).

## Evidence
- `.venv/bin/python -m pytest -q tests/auth/test_passwords.py tests/auth/test_totp.py tests/auth/test_recovery.py tests/auth/test_import_boundary.py` -> 119 passed.
- `.venv/bin/python -m pytest -q tests/auth --cov=agent_orchestrator.auth.passwords --cov=agent_orchestrator.auth.totp --cov=agent_orchestrator.auth.recovery --cov-report=term-missing` -> 332 passed; passwords 100 %, totp 100 %, recovery 100 % (TOTAL 100 %).
- `ruff check` + `ruff format --check` on the 3 source files, 3 test files and `helpers/crypto.py` -> clean.
- `.venv/bin/mypy src` -> only the 4 pre-existing `_version.py` errors; no errors in the new modules.
- `grep -rn "TEST_PARAMS\|FastFakeHasher" src/` -> no matches. Import-boundary test passes with the three modules in `LAYERS` (no framework/pydantic imports).
- Full suite not run (instructed: targeted tests only).

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

## Risks / Blockers
- None. `totp.py` / `recovery.py` / `passwords.py` are all available to T-8NQP8J, T-XchniS,
  T-yfrfxv and T-j9dfsw.

## Next actions
1. Downstream tasks consume the public names (T-8NQP8J: `match_totp_step`, `find_unused_match`,
   `new_recovery_records`; T-XchniS: `PasswordHasher`, `BoundedScryptHasher`, `FastFakeHasher`).
