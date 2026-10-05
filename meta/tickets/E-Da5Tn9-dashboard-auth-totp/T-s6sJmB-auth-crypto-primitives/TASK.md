# TASK: T-s6sJmB-auth-crypto-primitives

## Metadata
- Task ID: `T-s6sJmB-auth-crypto-primitives`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane A)
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `2.5 days` · Sprint `S1`

## Requirements Mapping
- Requirement IDs: FR-3, FR-4 (pure part), FR-5, FR-6 (URI), FR-28 (token format), NFR-2, NFR-4, NFR-7
- ACs: AC-5 (hashing part), AC-6, AC-8 (generation, normalization and hashing part)
- Design: HLD §11.6 (`passwords.py`), §11.7 (`totp.py`), §11.8 (`recovery.py`), §12.2, §12.6; HLD D6, D8; ADR-0021 D4, D5 (the token format)

## Description
Implement the three **pure** crypto modules (layer L1) of `src/agent_orchestrator/auth/`. They use
the stdlib only: no FastAPI, no pydantic, no file I/O, no store. Constants, `BusyError` and
`AuthError` come from T-kzEzwy (`constants.py`, `errors.py`, `seams.py`).

- **`passwords.py`** (HLD §11.6):
  - `ScryptParams` (`n`, `maxmem()`) and `CURRENT_PARAMS = ScryptParams(SCRYPT_LOG2_N, SCRYPT_R,
    SCRYPT_P)`.
  - `normalize_password` (NFKC; no strip, no case change).
  - `PasswordPolicy` with `violations()` (`too_short`, `too_long`, `control_characters`,
    `equals_username`) and `check()`. `check()` raises `AuthError(PASSWORD_POLICY,
    extra={"violations": [...]})`.
  - `format_hash` and `parse_hash`. **v2:** `parse_hash` enforces `10 ≤ ln ≤ SCRYPT_MAX_LOG2_N`,
    `1 ≤ r ≤ SCRYPT_MAX_R`, `1 ≤ p ≤ SCRYPT_MAX_P` and
    `128 · 2^ln · r ≤ SCRYPT_MAX_HASH_MEMORY_BYTES` (dev-security #12). Anything else raises
    `MalformedHashError`.
  - `hash_password`; `verify_password` (`hmac.compare_digest`; a malformed hash returns False and
    logs one ERROR line per process); `needs_rehash`.
  - The `PasswordHasher` protocol and the production `BoundedScryptHasher`:
    - a `ThreadPoolExecutor(max_workers=HASH_CONCURRENCY)`;
    - a pending counter capped at `HASH_QUEUE_MAX` that raises `BusyError`. Counting the 503 as an
      address failure is the guard's job (T-XchniS);
    - `dummy_hash`, computed **once** at construction with the hasher's params.
  - `MalformedHashError` (module-local, never user-facing).
- **`totp.py`** (HLD §11.7):
  - `new_totp_secret`, `b32encode_secret`, `b32decode_secret`;
  - `hotp` (RFC 4226), `totp_step`, `normalize_totp_code` (ASCII digits only);
  - `match_totp_step`, which computes **every** candidate in the window (no early exit);
  - `otpauth_uri`, pure ASCII.
  - Replay protection is **not** here; it lives in `store.consume_totp` (T-8NQP8J).
- **`recovery.py`** (HLD §11.8):
  - the Crockford alphabet and the I/L/O aliases;
  - `generate_recovery_codes(entropy, *, count=RECOVERY_CODE_COUNT)`;
  - `normalize_recovery_code`, `hash_recovery_code`;
  - `new_recovery_records`, which returns `(salt_hex, hash_hex)` pairs. They are converted to
    `RecoveryCodeHash` by the store, so this module stays pydantic-free;
  - `find_unused_match`.
  - **v2:** CLI-issued enrollment tokens (T-8NQP8J, T-j9dfsw) reuse exactly this format and these
    functions (`generate_recovery_codes(entropy, count=1)`, `normalize_recovery_code`,
    `hash_recovery_code`).
- **Test doubles** (in `tests/auth/helpers.py`, never in the package; reviewer finding R-12):
  - `TEST_PARAMS = ScryptParams(10, 8, 1)`;
  - `FastFakeHasher`:
    - `hash(raw)` returns `"fake$" + raw`;
    - `verify(raw, encoded)` returns `encoded == "fake$" + raw` and counts calls in `verify_calls`;
    - `needs_rehash()` returns a configurable bool;
    - `dummy_hash` is `format_hash(CURRENT_PARAMS, bytes(16), bytes(32))`, so it parses to the
      current params;
    - `raise_busy=True` makes `verify` raise `BusyError`.

## Inputs / Outputs
- **Inputs:** HLD §11.6–§11.8, §12.2, §12.6; T-kzEzwy (`constants`, `errors`, `seams`).
- **Outputs:**
  - `src/agent_orchestrator/auth/passwords.py`, `totp.py`, `recovery.py`
  - `tests/auth/test_passwords.py`, `test_totp.py`, `test_recovery.py`
  - `tests/auth/helpers.py` (+`TEST_PARAMS`, +`FastFakeHasher`)

## Acceptance Criteria
1. **Round-trip.** `hash_password(..., params=TEST_PARAMS)` round-trips through `parse_hash`. The
   string matches `^\$scrypt\$v=1\$ln=\d{1,2},r=\d{1,2},p=\d{1,2}\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/]+$`
   (HLD §12.1). The salt is 16 bytes and the dk 32 bytes. `CURRENT_PARAMS` is `ln=15, r=8, p=3`.
   (`test_passwords.py`)
2. **`parse_hash` bounds (v2).**
   - `MalformedHashError` for: `ln=9`, `ln=18`, `r=0`, `r=17`, `p=0`, `p=17`; `ln=17, r=9` (over
     128 MiB); a `v=2` prefix; a missing field; invalid base64.
   - `ln=17, r=8` (exactly 128 MiB) parses.
   - `hashlib.scrypt` is never called while parsing: a patched spy is never invoked.
   (`test_passwords.py::test_parse_bounds`)
3. **`verify_password`.**
   - True for the right password, False for a wrong one.
   - False for a malformed hash; the first such call logs exactly one ERROR record, and a second
     call logs none (`caplog`).
   - NFKC-equivalent inputs verify: a composed vs a decomposed `é`, and fullwidth `Ａ` vs `A`.
   - A password whose UTF-8 form exceeds `MAX_PASSWORD_BYTES` still runs exactly one scrypt (patched
     counter) and returns False.
4. **Real params.** One test with the **real** `CURRENT_PARAMS` hashes and verifies successfully,
   proving `maxmem` is passed. Every other test uses `TEST_PARAMS`.
5. **`needs_rehash`** is True when any of `ln`, `r`, `p` or `dklen` differs from the given params,
   and False when all match.
6. **`PasswordPolicy`.**
   - `violations()` returns each code exactly as specified: length on the NFKC form; C0 and DEL
     control characters; a case-insensitive username match.
   - `check()` raises `AuthError` with code `password_policy` and `extra["violations"]`.
   - The error's `detail` and `str()` never contain the password (sentinel check).
7. **`BoundedScryptHasher`.**
   - With `queue_max=1` and one verify in flight, a second concurrent verify raises `BusyError`.
   - With a patched slow `verify_password`, the observed maximum number in flight never exceeds
     `HASH_CONCURRENCY`.
   - `dummy_hash` parses to the hasher's params, and `hash_password` is called exactly once during
     construction.
   - Tests run through `run_async` (no pytest-asyncio).
8. **HOTP vectors.** RFC 4226 counters 0–9 with key `b"12345678901234567890"` →
   `755224, 287082, 359152, 969429, 338314, 254676, 287922, 162583, 399871, 520489`.
   (`test_totp.py`)
9. **TOTP vectors.** RFC 6238 SHA-1, 8 digits, at T = 59, 1111111109, 1111111111, 1234567890,
   2000000000 and 20000000000 → `94287082, 07081804, 14050471, 89005924, 69279037, 65353130`.
   6-digit codes equal the last six digits.
10. **`match_totp_step`** (fixed `now_unix`):
    - accepts the codes for steps `s-1`, `s` and `s+1`, returning the matched step;
    - rejects `s±2`;
    - with a patched `hotp` counter, computes exactly `2*window+1` candidates even when the first
      matches;
    - skips negative candidates near the epoch.

    `normalize_totp_code` accepts `"123 456"` and `"123-456"`, and rejects 5 or 7 digits and
    Arabic-Indic digits (`None`).
11. **`otpauth_uri`.** `otpauth_uri("JBSWY3DPEHPK3PXP", issuer="ao@devbox", account="alice")`
    equals exactly
    `otpauth://totp/ao%40devbox:alice?secret=JBSWY3DPEHPK3PXP&issuer=ao%40devbox&algorithm=SHA1&digits=6&period=30`,
    and the result is ASCII-only.
12. **Recovery codes** (`test_recovery.py`, `SeededEntropy`).
    - Ten distinct codes, each matching `^[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){3}$`.
    - `generate_recovery_codes(entropy, count=1)` returns a single code in the same format (the
      enrollment-token path).
    - `normalize_recovery_code` maps lowercase, spaces, hyphens and I/L/O, and returns `None` for a
      wrong length or invalid characters.
    - `hash_recovery_code(n, salt)` equals `sha256(salt + n.encode("ascii")).hexdigest()`.
    - `new_recovery_records` gives each code its own 16-byte salt.
    - `find_unused_match` computes a hash for **every** record (patched counter equals
      `len(records)` even when record 0 matches), never matches a used record, and returns the
      first unused matching index.
13. **No test doubles in the package.** `grep -rn "TEST_PARAMS\|FastFakeHasher" src/` returns
    nothing. All three modules import with fastapi, starlette, uvicorn and pydantic blocked; T-kzEzwy's
    boundary test picks them up through `LAYERS`.
14. `ruff check`, `ruff format --check` and `mypy` are clean for the new files. Line coverage of the
    three modules is ≥ 95 %.

## Risks
- **`maxmem` misconfiguration.** The real-params test (AC-4) catches it.
- **Leading-zero TOTP codes.** Codes are always strings, never integers (AC-9).
- **Slow tests.** Use `TEST_PARAMS` everywhere except AC-4.
- **Timing leaks.** There is no early exit in `match_totp_step` or `find_unused_match`
  (AC-10, AC-12).

## Dependencies
- **Upstream:** T-kzEzwy.
- **Downstream:**
  - T-8NQP8J: `consume_totp`, `consume_recovery` and the enrollment tokens call `match_totp_step`,
    `find_unused_match` and the recovery helpers;
  - T-XchniS: `PasswordHasher`, `BoundedScryptHasher`, `FastFakeHasher`;
  - T-yfrfxv;
  - T-j9dfsw.

## Pseudocode / Algorithm
See HLD §11.6 (`verify_password`, `BoundedScryptHasher.verify`), §11.7 (`hotp`, `match_totp_step`,
`otpauth_uri`) and §11.8. Additional task-level steps:

```text
parse_hash(encoded):
  m = STRICT_RE.fullmatch(encoded)                      # "$scrypt$v=1$ln=(\d+),r=(\d+),p=(\d+)$(b64)$(b64)"
  IF m IS None: RAISE MalformedHashError("format")
  ln, r, p = ints;  IF NOT (10 <= ln <= SCRYPT_MAX_LOG2_N and 1 <= r <= SCRYPT_MAX_R and 1 <= p <= SCRYPT_MAX_P):
      RAISE MalformedHashError("parameters out of bounds")
  IF 128 * (1 << ln) * r > SCRYPT_MAX_HASH_MEMORY_BYTES: RAISE MalformedHashError("memory bound")
  salt, dk = b64decode with re-padding (binascii.Error -> MalformedHashError)
  RETURN ParsedHash(ScryptParams(ln, r, p, dklen=len(dk)), salt, dk)
```

## Schemas / Interface Notes
- Interfaces: exactly the signatures in HLD §11.6–§11.8; do not rename them. The one difference is
  that `new_recovery_records` returns plain `(salt_hex, hash_hex)` pairs, which the store wraps.
- Hash strings: HLD §12.2.

## Handoff Boundary
- **Upstream:** the HLD and T-kzEzwy.
- **Downstream:** the public names above, importable without any web framework.

## Verification
From the worktree root. In agent worktrees, use the manager-provided interpreter instead of
`uv run`.

```
python -m pytest -q tests/auth/test_passwords.py tests/auth/test_totp.py tests/auth/test_recovery.py tests/auth/test_import_boundary.py
python -m pytest -q tests/auth --cov=agent_orchestrator.auth.passwords --cov=agent_orchestrator.auth.totp --cov=agent_orchestrator.auth.recovery --cov-report=term-missing
ruff check src/agent_orchestrator/auth tests/auth && ruff format --check src/agent_orchestrator/auth tests/auth && mypy src/agent_orchestrator/auth
```

## Artifacts
- Docs and comments: `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-s6sJmB-auth-crypto-primitives/`
- Large outputs: none
