# TASK: T-yfrfxv-auth-provider-second-factor

## Metadata
- Task ID: `T-yfrfxv-auth-provider-second-factor`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane C)
- Created: `2026-10-05`
- Last Updated: `2026-10-05`
- Status: `Done`
- Estimate: `2 days` · Sprint `S2`

## Requirements Mapping
- Requirement IDs: FR-4, FR-5, FR-6 (service part), FR-18 (2FA re-auth), FR-28, FR-29, FR-31, NFR-5
- ACs: AC-9 (service part), AC-22 (2FA re-auth part), AC-36 (service part), AC-37 (service part),
  AC-39 (STALE consume part), AC-40 (second-factor part)
- Invariants: S7, S8, S9, S10, S22, S23, S25
- Design: HLD §11.15.1, §11.15.5, §11.15.6 (`LocalTotpService` parts), §11.7, §11.8, §11.9 (mutation
  table), §20.3 #9/#10; §1 D7, D8, D10; ADR-0021 D5, D10

## Description
Implement `src/agent_orchestrator/auth/totp_service.py` (layer L3, no FastAPI).
- **Result types:**
  - `SecondFactorResult(method: SecondFactor, recovery_codes_remaining: int | None)`;
  - `EnrollmentChallenge(secret: bytes, secret_b32: str, otpauth_uri: str, issuer, account, algorithm, digits, period)`;
  - `EnrollmentResult(credential_epoch: int, recovery_codes: list[str])`.

  Every secret-bearing field is `field(repr=False)`.
- **`LocalTotpService`**, constructed with `store, lockouts, guard, provider: LocalPasswordProvider,
  settings, audit, clock, entropy` (HLD §11.15.1). Every credential check goes through
  `AttemptGuard.attempt`.
  - `verify_second_factor(session, *, code=None, recovery_code=None, client)`: HLD §11.15.5
    verbatim. The guard's `verify` runs **one** `store.mutate`: `consume_totp` or
    `consume_recovery`, with `user_id` and `credential_epoch`.
    - `REPLAYED` → `reason "replayed"`; `INVALID` or a malformed code → `reason "invalid"`. Both are
      counted.
    - `STALE` → `AuthError(NOT_AUTHENTICATED)`, raised inside `verify`, so it is **not** counted.
    - `reset_on_success=True`, then `cas_mark_login`.
  - `begin_enrollment(session, client, *, current_password=None, enrollment_token=None)`. Checks run
    in this order:
    1. `insecure_transport` (neither `client.secure` nor `client.is_loopback`). **v2.1 (security
       M2):** `ClientInfo.is_loopback` now means loopback peer **and** loopback `Host` **and** no
       `Forwarded` / `X-Forwarded-*` header; it is computed by T-rpKCjP's `client_info(scope,
       runtime)` (HLD §11.15.1, §11.18). This service's logic is unchanged: it only reads the flag;
       `proxy_suspected` is not consulted here;
    2. policy `off` → `TOTP_DISABLED_BY_POLICY`;
    3. already enrolled → `TOTP_ALREADY_ENROLLED`;
    4. a FULL session → `provider.verify_current_password(...)`. A `None` password →
       `INVALID_REQUEST`.
    5. a PARTIAL_ENROLL session → the guard with `failure_event=AUTH_ENROLLMENT_TOKEN_FAILURE` and a
       `verify` that runs `consume_enrollment_token`. A missing or empty token is a failed attempt
       (`AttemptResult(ok=False, reason="invalid")`, counted, with **no** store write). A failure
       raises `INVALID_CODE` with `reason "invalid"`.

    It then generates the secret and the otpauth URI (issuer `settings.totp_issuer`, account =
    username).
  - `confirm_enrollment(session, code, client)`:
    - `insecure_transport`; policy `off`;
    - `match_totp_step` against `session.pending_totp_secret`. A miss → `INVALID_CODE`. The route
      counts the pending attempts; this service does not touch the session.
    - `enroll_totp(..., user_id, epoch, completes_login=(state == PARTIAL_ENROLL))`. Its errors map
      as `StaleIdentityError` → `NOT_AUTHENTICATED` and `AlreadyEnrolledError` →
      `TOTP_ALREADY_ENROLLED`.
    - on PARTIAL_ENROLL: `lockouts.reset`;
    - audit `auth.totp.enrolled` (`source=web`).
  - `disable_totp(session, current_password, code, client)`:
    - `totp_requirement(settings.totp, rec.totp_required) != NONE` → `TOTP_REQUIRED`. This check
      runs **before** any password check.
    - not enrolled → `TOTP_NOT_ENROLLED`;
    - `provider.verify_current_password`;
    - a code check through the guard (`consume_totp` or `consume_recovery`; see
      `classify_code`);
    - `remove_totp(set_required=None)` under the identity guard; audit `auth.totp.disabled`.
  - `regenerate_recovery_codes(session, current_password, code, client)`: `insecure_transport`;
    enrolled; password; code; then `replace_recovery_codes` under the identity guard; audit.
  - `classify_code(code) -> ("totp", normalized) | ("recovery_code", normalized) | None`. After
    stripping spaces: 6 ASCII digits → TOTP; otherwise, if it normalizes to 16 Crockford characters
    → recovery code; otherwise `None`, which is a counted invalid attempt.
- **Runtime wiring:** extend `build_auth_runtime` (`runtime.py`, from T-XchniS). When the provider
  is a `LocalPasswordProvider`, set `runtime.totp = LocalTotpService(...)` sharing the same `store`,
  `lockouts`, `guard`, `audit` and `clock`. Otherwise set `runtime.totp = None`.

## Inputs / Outputs
- **Inputs:**
  - T-XchniS: `AttemptGuard`, `LocalPasswordProvider`, `ClientInfo`, `runtime.py`.
  - T-8NQP8J: `consume_totp`, `consume_recovery`, `consume_enrollment_token`, `enroll_totp`,
    `remove_totp`, `replace_recovery_codes`, `StaleIdentityError`.
  - T-s6sJmB: `match_totp_step`, `new_totp_secret`, `otpauth_uri`, `generate_recovery_codes`,
    `normalize_*`.
  - T-kwwJ82: `totp_requirement`.
- **Outputs:**
  - `src/agent_orchestrator/auth/totp_service.py`, plus a small edit to `runtime.py`
    (`build_auth_runtime` wires `totp`)
  - `tests/auth/test_totp_service.py`
  - second-factor cases appended to `tests/auth/test_event_loop.py`

## Acceptance Criteria
All checks are in `tests/auth/test_totp_service.py` unless stated otherwise. They use `FakeClock`,
`SeededEntropy`, `FastFakeHasher` and codes computed from the known secret at the fake time.

1. **TOTP verify** (S7, AC-39 part):
   - a valid code → `SecondFactorResult(method=TOTP)`, `last_used_step` = the matched step,
     `lockouts.reset` called once, `last_login_at` set;
   - the same code again → `INVALID_CODE` with `reason "replayed"` (one `record_failure`);
   - a wrong code → `reason "invalid"` (counted);
   - a malformed code (`"12345"`, `"abcdef"`) → `reason "invalid"`, counted, with zero
     `UserStore.mutate` calls;
   - a session whose epoch was bumped after issue → `NOT_AUTHENTICATED`, with **zero**
     `record_failure` calls.
2. **Recovery verify** (S8): accepted once. `recovery_codes_remaining` drops 10 → 9, and the same code
   → `INVALID_CODE`. Lower case, separators and `I/L/O` are normalized.
3. **Begin, rejections:**
   - `ClientInfo(secure=False, is_loopback=False)` → `INSECURE_TRANSPORT`, with zero store or guard
     calls;
   - policy `off` → `TOTP_DISABLED_BY_POLICY`;
   - already enrolled → `TOTP_ALREADY_ENROLLED`.

   Loopback over http and remote over https are both allowed. These unit tests construct
   `ClientInfo` directly; the header-based loopback matrix (a loopback peer with forwarding headers
   or a non-loopback `Host` → refused) is AC-43, owned by T-rpKCjP and T-KQ6ZrY.
4. **Begin, FULL session** (AC-22 part): `current_password=None` → `INVALID_REQUEST`; a wrong
   password → `INVALID_CREDENTIALS` (one `record_failure`); a correct one → a challenge with a
   20-byte secret, a 32-character Base32 string, and an ASCII URI with the configured issuer and
   account.
5. **Begin, PARTIAL_ENROLL session** (AC-36 part, S22). Each of these gives `INVALID_CODE` with
   `reason "invalid"` and exactly one `record_failure`:
   - a missing token;
   - an empty token;
   - a wrong token;
   - an expired token (`FakeClock` advanced past `ENROLLMENT_TOKEN_TTL_SECONDS`);
   - an already used token.

   A valid token → a challenge, and the token is cleared, so a second use fails.
6. **Confirm** (S10):
   - `INSECURE_TRANSPORT` and `off` rejections;
   - a wrong code → `INVALID_CODE`, and `users.json` is unchanged (byte compare);
   - a correct code → `EnrollmentResult(epoch + 1, 10 codes)`, `totp.last_used_step` = the matched
     step, and `enrollment_token` is null;
   - with `completes_login` (PARTIAL_ENROLL) → `lockouts.reset` is called and `last_login_at` set;
   - a stale epoch → `NOT_AUTHENTICATED`;
   - a concurrent enrollment (patched) → `TOTP_ALREADY_ENROLLED`;
   - one `auth.totp.enrolled` event with `source=web`.
7. **Disable** (AC-9 part, S10):
   - under `required`, or for a `totp_required` user → `TOTP_REQUIRED`, with **no** hasher call;
   - not enrolled → `TOTP_NOT_ENROLLED`;
   - a wrong password → `INVALID_CREDENTIALS`;
   - a wrong code → `INVALID_CODE`;
   - success with a TOTP code and, separately, with a recovery code → `totp` null, codes cleared,
     epoch +1, `totp_required` unchanged, and one `auth.totp.disabled` event.
8. **Regenerate:**
   - `INSECURE_TRANSPORT` for remote http;
   - success → 10 new codes, every old code rejected afterwards, epoch +1, and one
     `auth.recovery_codes.regenerated` event.
9. **Sticky TOTP** (AC-9 part, S9): an enrolled user with the policy `off` can still complete
   `verify_second_factor`. `begin_enrollment` under `off` is refused.
10. **Event loop** (AC-40 second-factor part, `test_event_loop.py`): verify, begin (token path) and
    confirm run every `mutate`, `LockoutStore.*` and `AuditLog.record` call off the loop thread.
11. **No secrets:** the `repr()` of every result type contains no secret, code or token. A capturing
    `AuditLog` sees no code, token or secret in `details` (sentinel grep).
12. **Wiring:** `build_auth_runtime` with a `LocalPasswordProvider` → `runtime.totp` is a
    `LocalTotpService` that shares the runtime's `guard` and `store`. With a fake provider →
    `runtime.totp is None`.
13. ruff and mypy are clean. Coverage of `totp_service.py` is ≥ 90 %.

## Risks
- **Counting the enrollment token twice, through the guard and through the route.** Only the guard
  counts it. The route counts only the *confirm* attempts on the session.
- **Code classification ambiguity.** `classify_code` is a single pure function that is unit-tested.

## Dependencies
- **Upstream:** T-XchniS, plus its S1 inputs (T-8NQP8J, T-s6sJmB, T-kwwJ82).
- **Downstream:** T-KQ6ZrY (the second-factor routes), T-U2ERMo.

## Pseudocode / Algorithm
- HLD §11.15.5 and §11.15.6, verbatim, with these precisions:
  - In `begin_enrollment` the guard returns an `AttemptResult`, so the test is
    `if not result.ok`. The `verify` wraps the bool from `consume_enrollment_token` into
    `AttemptResult`.
  - The web-path `remove_totp` and `replace_recovery_codes` are wrapped in the single
    `store.with_identity(...)` helper (HLD §11.9, built by T-8NQP8J). Do not re-implement the
    check. `StaleIdentityError` → `AuthError(NOT_AUTHENTICATED)`.
  - A **missing** `enrollment_token` in `begin_enrollment` (PARTIAL_ENROLL) is a failed guard
    attempt (`ok_if(False, reason="invalid")`), so it is counted. It is not an `invalid_request`
    (HLD §2.4 E4, §11.15.6). The token is consumed by `begin`.

## Schemas / Interface Notes
- The method names and keyword arguments above are what T-KQ6ZrY's handlers call (HLD §11.18
  handler table).
- `SecondFactor` comes from `auth/model.py`. The audit event names come from `AuditEventName`.

## Handoff Boundary
- **Upstream:** the guard, the provider and the store mutations.
- **Downstream:** a framework-free second-factor service. The routes own the session-side attempt
  counters, rotation and cookies.

## Verification

```
python -m pytest -q tests/auth/test_totp_service.py tests/auth/test_event_loop.py
python -m pytest -q tests/auth --cov=agent_orchestrator.auth --cov-report=term-missing
ruff check src tests && ruff format --check src tests && mypy src
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-yfrfxv-auth-provider-second-factor/`
