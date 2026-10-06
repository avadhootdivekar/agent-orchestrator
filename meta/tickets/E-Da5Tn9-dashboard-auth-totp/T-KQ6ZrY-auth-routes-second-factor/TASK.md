# TASK: T-KQ6ZrY-auth-routes-second-factor

## Metadata
- Task ID: `T-KQ6ZrY-auth-routes-second-factor`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane C)
- Created: `2026-10-05`
- Last Updated: `2026-10-05`
- Status: `Done`
- Estimate: `3 days` · Sprint `S3`

## Requirements Mapping
- Requirement IDs: FR-4, FR-5, FR-6, FR-7, FR-9 (step routes), FR-18 (2FA re-auth), FR-28, FR-29
- ACs: AC-7 (cross-realm part), AC-8 (route part), AC-9, AC-10, AC-17 (full-configuration part),
  AC-22 (2FA part), AC-36 (route part), AC-37 (route part), **AC-43 (E4/E5/E7 part; v2.1)**
- Invariants: S7, S8, S9, S10, S22, S23, S28
- Design: HLD §2 (E3–E7, §2.2), §11.18 (ownership table, list-valued registry, handler table),
  §11.15.1 (`ClientInfo.is_loopback`, v2.1), §13.2 (test-file ownership), §14.2–§14.4, §10.7
  (extension seams), §20.3 #9/#17, §24.1 (cut-lines), §24.3 (merge edge); §1 D7, D8, D11;
  §28.9 (design-review M2, M3; security M2, M4); ADR-0021 D5, D7

## Description
Fill in **`src/agent_orchestrator/auth/http/routes_second_factor.py`** (v2.1, design-review M2).
T-rpKCjP creates it as a registered no-op stub; this task then owns it, and does **not** edit
`auth/http/routes.py`. The module defines `add_totp_routes(app, runtime)` (returns at once when
`runtime.totp is None`) and registers it at import with
`register_route_builder(LOCAL_PROVIDER_ID, add_totp_routes)`; `install_auth_routes` imports the
module through `_BUILTIN_ROUTE_MODULES` and runs it after the local-password builder. Helpers
(`user_payload`, `rotate`, `read_json_object`, `require_str`, `client_info`) are imported from
`routes.py`. All routes are flat and `async def`, follow the HLD §11.18 handler table exactly, and
obey rule R1a (handler annotations imported at module scope).
- **E3 `POST /api/auth/totp/verify`** (PARTIAL_SECOND_FACTOR plus proof):
  - a FULL session → 409 `already_authenticated`;
  - the body must contain **exactly one** of `code` and `recovery_code` (else 400);
  - then `runtime.totp.verify_second_factor`;
  - on `INVALID_CODE`: `remaining = sessions.record_second_factor_failure(session)`. Re-raise with
    `attempts_remaining`. At 0 the session is gone, so also clear the cookie;
  - on `NOT_AUTHENTICATED`: `sessions.destroy`, clear the cookie, re-raise;
  - on success: `issue(FULL, auth_method="password+totp", second_factor=method, replacing=session)`
    (a fresh absolute deadline) → audit `auth.login.success` (new `session_id`). For a recovery code,
    also `auth.recovery_code.used` with `recovery_codes_remaining`. Then the body with
    `used_recovery_code` and `session_proof`.
- **E4 `POST /api/auth/totp/enroll/begin`** (ENROLLMENT plus proof):
  - allowed keys by state: FULL → `{"current_password"}`, which is required (400 if missing);
    PARTIAL_ENROLL → `{"enrollment_token"}`, which is **optional at the schema level**: a missing
    token is a counted failure inside the service;
  - then `runtime.totp.begin_enrollment` → `sessions.set_pending_secret` → the E4 body.
- **E5 `POST /api/auth/totp/enroll/confirm`** (ENROLLMENT plus proof):
  - no pending secret → 409 `no_pending_enrollment`;
  - on `INVALID_CODE`: `sessions.record_confirm_failure` (which clears the secret at
    `MAX_ENROLL_CONFIRM_ATTEMPTS`), then re-raise with `attempts_remaining`;
  - on success: `rotate(...)` (T-rpKCjP helper) with the new epoch,
    `keep_absolute=(state == FULL)`, `auth_method="password+totp"`, `second_factor="totp"`; other
    sessions in the realm are destroyed; when it completed a login, audit `auth.login.success`. Then
    the E5 body with 10 `recovery_codes`.
- **E6 `POST /api/auth/totp/disable`** (AUTHENTICATED plus proof): `{current_password, code}` →
  `disable_totp` → `rotate(keep_absolute=True, auth_method="password", second_factor="none")` → the
  E6 body.
- **E7 `POST /api/auth/totp/recovery-codes`** (AUTHENTICATED plus proof): `{current_password,
  code}` → `regenerate_recovery_codes` → `rotate(keep_absolute=True)`, keeping the current
  `auth_method` → the E7 body.
- **Provider seam test (AC-10):** a test-only `RedirectFakeProvider(AuthProvider)` with
  `provider_id = "test-redirect"`, registered through `register_route_builder`. Its builder adds:
  - `GET /api/auth/test/start` → 302 to `https://idp.example/authorize?state=<s>`, with the state
    kept server-side in a test dict;
  - `GET /api/auth/test/callback?state=<s>` → `sessions.issue(FULL)` → 303 `/`.

  The test app is composed explicitly: `FastAPI()` plus `AuthMiddleware(policies={**DASHBOARD_ROUTE_POLICIES,
  ("GET", "/api/auth/test/start"): PUBLIC, ("GET", "/api/auth/test/callback"): PUBLIC})` plus
  `install_auth_routes(app, build_auth_runtime(..., provider=fake))` plus one protected probe route.
  `create_app` cannot be used, because its policy table is fixed (see Risks, OPEN_QUESTION).
  **Cut-line #3 (v2.1, design-review M3; HLD §24.1):** this redirect-shaped test is a stretch item.
  If the schedule slips, replace it with a credential-shaped fake provider registered through the
  same `register_route_builder` / `build_auth_runtime(provider=)` seam, and record the cut in the
  epic STATUS.
- **Full-configuration enumeration** in its own file
  `tests/auth/test_route_enumeration_full_config.py` (v2.1, design-review M2), using
  `tests/auth/helpers/enumeration.py`. No other task edits it.

## Inputs / Outputs
- **Inputs:** T-yfrfxv (`LocalTotpService`); T-rpKCjP (the `routes_second_factor.py` stub, the
  builder registry, `user_payload`, `rotate`, `read_json_object`, `client_info`); T-G7qByZ and
  T-QJ1vyQ (the full middleware).
- **Outputs:**
  - `src/agent_orchestrator/auth/http/routes_second_factor.py` (owned by this task after
    T-rpKCjP's stub)
  - `tests/auth/test_routes_second_factor.py`, `tests/auth/test_provider_seam.py`,
    `tests/auth/test_route_enumeration_full_config.py`

## Acceptance Criteria
All checks are in `tests/auth/test_routes_second_factor.py` unless stated otherwise. They use
`FakeClock`, `FastFakeHasher`, codes computed from the known secret, and `same_origin_headers` with
the latest proof.

1. **Contract conformance** (table-driven): every status and `code` listed for E3–E7 in HLD §2.4 is
   produced by at least one case, and the JSON key sets and types match exactly.
2. **TOTP login** (§14.2):
   - E2 → `second_factor_required`;
   - E3 with a valid code → `authenticated`, and **both** the cookie and the proof differ from the
     partial ones. The partial pair → 401.
   - E3 from a FULL session → 409 `already_authenticated`.
3. **Cross-realm replay** (AC-7 part, S7): two dashboard apps share a store. After code C succeeds on
   A, the same step on B → 401 `invalid_code` with `reason: "replayed"` and
   `attempts_remaining: 4`. The next step's code → success.
4. **Attempts:**
   - five wrong E3 codes → the 5th returns `attempts_remaining: 0` with a clear-cookie header, and
     the next E3 → 401 `not_authenticated`. Each failure adds one account-lockout failure, so a
     subsequent login is 429 while the lockout runs;
   - five wrong E5 codes → the next E5 → 409 `no_pending_enrollment`.
5. **Recovery login** (AC-8 part, §14.4):
   - `used_recovery_code: true` and `recovery_codes_remaining == 9`;
   - reusing that code → 401 `invalid_code`;
   - one `auth.recovery_code.used` event;
   - the resulting `Principal.amr == ("pwd", "rcv", "mfa")`.
6. **Forced enrollment** (AC-9c, AC-36 route part, S10/S22), under `required`:
   - E2 → `enrollment_required`, and a protected route → 401 `enrollment_required`;
   - E4 with no token, a wrong one, an expired one (`FakeClock` past
     `ENROLLMENT_TOKEN_TTL_SECONDS`) or a used one → 401 `invalid_code`, with the lockout failure
     count +1 each time;
   - a valid token → 200 with `secret` and `otpauth_uri`, and the same token again → 401;
   - E5 with the right code → 200 with 10 `recovery_codes`, a rotated cookie and proof, the user's
     other sessions in the realm → 401, and one `auth.login.success` event.
7. **Voluntary enrollment** (AC-22 part), from a FULL session:
   - E4 without `current_password` → 400; with a wrong one → 401 `invalid_credentials` (counted);
     with the right one → 200;
   - E5 → 10 codes; the absolute deadline is unchanged (`FakeClock`); epoch +1.
8. **Sticky TOTP** (AC-9b, S9): a user enrolled under `optional`, then a new app with policy `off`:
   - E2 → `second_factor_required`;
   - `GET /api/runs` with the proof → 401 `second_factor_required`;
   - E4 from a FULL session under `off` → 403 `totp_disabled_by_policy`.
9. **Disable (E6):**
   - under `required`, and for a `totp_required` user → 403 `totp_required`;
   - not enrolled → 409 `totp_not_enrolled`;
   - a wrong password → 401 `invalid_credentials`; a wrong code → 401 `invalid_code`;
   - success with a TOTP code, and separately with a recovery code → `user.totp_enrolled == false`,
     a rotated pair, and other sessions → 401.
10. **Regenerate (E7):** 10 new codes; an old code → 401 at the next E3; a rotated pair; other
    sessions → 401.
11. **`insecure_transport`** (AC-37 route part, S23).
    `TestClient(app, base_url="http://testserver", client=("10.0.0.5", 1))` → E4, E5 and E7 → 403
    `insecure_transport`. The same calls from client `127.0.0.1` are allowed, and so are calls over
    `https://testserver` from `10.0.0.5`. E2 login over remote http still works.
    **v2.1 (AC-43 part, security M2, S28):** from client `127.0.0.1` over http **with**
    `X-Forwarded-For: 203.0.113.9` (or `Forwarded: for=203.0.113.9`, or a non-loopback allow-listed
    `Host`) and no trusted proxies, E4, E5 and E7 → 403 `insecure_transport` (the request counts as
    remote); with `trusted_proxies` configured and an https `X-Forwarded-Proto` from the proxy, they
    pass.
12. **E4 hygiene:** the response is `no-store`. The pending secret is held only in the session (the
    record's `pending_totp_secret` is set, and `users.json` has no `totp` until E5).
13. **Provider seam** (AC-10, `test_provider_seam.py`):
    - start → 302 to the external URL;
    - a callback sent **cross-site** (`Sec-Fetch-Site: cross-site`, top-level navigation headers) →
      303 `/` plus a session cookie;
    - the protected probe then returns 200 with a `Principal` whose `provider == "test-redirect"`.
      The test sends the `IssuedSession.proof` that the fake provider captured in its test dict; see
      the second OPEN_QUESTION.

    An AST check confirms that `auth/sessions.py`, `auth/http/middleware.py` and
    `auth/principal.py` do not import `local_provider`.
14. **Full-configuration enumeration** (AC-17, HLD §13.2 configuration rule). With a real runtime
    whose `totp` is set, the dashboard (built) and the hub enumerations include the three
    `/api/auth/totp/*` rows. The computed non-AUTHENTICATED sets equal the full §13.2 rows exactly,
    and the no-stale check covers every table entry. (`test_route_enumeration_full_config.py`)
15. **Registration:** after `install_auth_routes` with a TOTP-enabled runtime, the five E3–E7
    routes are direct members of `app.router.routes` (flat), registered once even if
    `install_auth_routes` runs for two apps in one process; with `runtime.totp is None` none of them
    exists.
16. ruff and mypy are clean. Coverage of `routes_second_factor.py` is ≥ 90 %.

## Risks
- **OPEN_QUESTION, recorded as HLD OQ-10 (§25.3, §10.7). Non-blocking; a follow-up for real
  OIDC.** A real redirect-based provider needs PUBLIC policy
  entries for its routes. Today these exist only in the static per-app tables in `policy.py`, and
  `create_app` and `build_hub_app` hard-code those tables. AC-10 is therefore met with an explicitly
  composed test app. A production provider would need an additive merge mechanism, such as
  provider-declared policies restricted to `/api/auth/*`. The HLD §10.7 claim "a provider module plus
  a route builder" leaves this out.
- **OPEN_QUESTION (second), recorded as HLD OQ-10.** The follow-up design is a single-use, 60 s,
  session-bound proof ticket in the redirect URL fragment. A cookie-only exchange would defeat
  D25. Under D25 the session proof is delivered
  only in JSON bodies. A redirect callback (a 303 navigation) has no way to hand the proof to the
  SPA's `localStorage`. A real redirect provider would need a proof-delivery step, for example a
  single-use exchange endpoint the SPA calls after the redirect. The seam test works around it with
  the captured proof.
- **Attempt-counter vs lockout interplay** (both 5 by default): AC-4 pins the observable behaviour.
- **Co-critical (v2.1).** Through the merge edge to T-jVqH8w this task has zero slack (HLD §24.3).

## Dependencies
- **Upstream:** T-yfrfxv, T-rpKCjP (and transitively T-XchniS, T-G7qByZ, T-QJ1vyQ).
- **Downstream:**
  - **T-jVqH8w (merge edge, security M4):** `ao ui --auth` merges to the integration branch only
    after this task has merged, so no state offers `--auth` without the TOTP routes;
  - T-U2ERMo (e2e, scrub sweep, browser smoke). The frontend lanes are verified against these
    routes in T-U2ERMo.

## Pseudocode / Algorithm
The HLD §11.18 handler table (E3–E7 rows) and sequences §14.2–§14.4. Rotation uses T-rpKCjP's
`rotate(...)` helper, which builds the identity from the post-change record.

## Schemas / Interface Notes
- HLD §2.4 (E3–E7 bodies), §2.2 (`invalid_code` with `reason` and `attempts_remaining`), §2.5
  (`AuthStepResponse`, `TotpEnrollment`).
- The route paths are the `AUTH_*_PATH` constants (§12.6).

## Handoff Boundary
- **Upstream:** the TOTP service and the core routes.
- **Downstream:** the complete `/api/auth/*` surface for the SPA (T-pQ73eO, T-vCgsU6) and the hub
  page (T-R7JhTL).

## Verification

```
python -m pytest -q tests/auth/test_routes_second_factor.py tests/auth/test_provider_seam.py tests/auth/test_route_enumeration_full_config.py
python -m pytest -q tests/auth tests/ui
ruff check src tests && ruff format --check src tests && mypy src
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-KQ6ZrY-auth-routes-second-factor/`
