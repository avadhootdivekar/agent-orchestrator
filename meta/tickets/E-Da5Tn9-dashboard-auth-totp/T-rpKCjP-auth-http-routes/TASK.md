# TASK: T-rpKCjP-auth-http-routes

## Metadata
- Task ID: `T-rpKCjP-auth-http-routes`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane A)
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `2.5 days` · Sprint `S2`

## Requirements Mapping
- Requirement IDs: FR-11, FR-12, FR-17 (HTTP-level uniformity), FR-18 (password change), FR-21
  (first-login warning), FR-23 (route-side audit), FR-27 (status/logout), FR-31
- ACs:
  - owned: AC-14 (route part), AC-16, AC-22 (password-change part), AC-25 (first-login warning
    part), AC-35 (status/logout part), AC-39 (route part)
  - supports: AC-17 (`assert_flat_auth_routes`)
- Invariants: S3, S5, S6, S15, S21, S25, S26
- Design: HLD §2 (E1, E2, E8, E9, E10, §2.2, §2.3; **the contract: implement it exactly**), §11.18
  (routes and the handler table), §11.15.2 (audit responsibility), §14.1, §14.5–§14.7, §14.10, §20.3
  #3/#4/#7; §1 D3, D4, D10, D25; ADR-0021 D1, D2, D10, D11

## Description
Extend `src/agent_orchestrator/auth/http/routes.py` (FastAPI allowed; flat routes only).
- **`install_auth_routes(app, runtime)`**, enabled branch:
  - `add_core_auth_routes(app, runtime)`: E1 status, E10 logout, E9 keepalive.
  - Then `ROUTE_BUILDERS[runtime.provider.provider_id](app, runtime)`. A missing builder →
    `AuthConfigError("no route builder registered for provider '<id>'")`.
  - `app.add_exception_handler(AuthError, auth_error_handler)`.
  - `assert_flat_auth_routes(app)`: every `AUTH_ROUTE_POLICIES` key must be a **direct** member of
    `app.router.routes`; otherwise `RuntimeError` naming the key.
- **`RouteBuilder`, `ROUTE_BUILDERS`, `register_route_builder(provider_id, fn)`** (callables, not
  strings). The local builder `add_local_password_routes(app, runtime)` registers E2 login and E8
  password. It also calls T-KQ6ZrY's TOTP builder when `runtime.totp` is set; that builder is
  initially a no-op stub.
- **Handlers** (`async def`), exactly as in the HLD §11.18 handler table, reading
  `SCOPE_SESSION_KEY` and `SCOPE_PROOF_OK_KEY` from `request.state`:
  - **E1:** the body as in §2.4. It reports non-anonymous **only if `proof_ok`**. It never slides
    the session. `transport` comes from `client_info`. A shared `user_payload(runtime, session) ->
    dict` builds the `user` object, including the `can_enroll_totp` / `can_disable_totp` rules; it
    is reused by T-KQ6ZrY.
  - **E2:**
    - `provider.authenticate`;
    - `sessions.issue(identity, identity.next_state, client_key=…, auth_method="password" if FULL
      else None, replacing=session if proof_ok else None)`. This is a precision on §11.18: a
      presented cookie **without** its proof is never destroyed (D25: a missing proof never
      destroys anything);
    - FULL → audit `auth.login.success` with the new `session_id`;
    - the body for the three states, with `session_proof`, plus `Set-Cookie` from
      `responses.session_cookie_header`;
    - the first plain-HTTP login from a non-loopback client logs one WARNING per process containing
      `not encrypted` (`runtime.first_insecure_login_warned`).
  - **E8:** `provider.change_password`, then rotate: a `VerifiedIdentity` from the post-change
    record (the same `user_id`, the new epoch), `issue(FULL, replacing=session,
    keep_absolute_deadline=True, auth_method=session.auth_method, second_factor=session.second_factor)`.
    Then `destroy_user_sessions(user_id, except_session_id=new)`, and the body with a new proof.
  - **E9:** `sessions.times(session)`. The middleware has already slid the session.
  - **E10:** the body is optional (`{}` or `{"everywhere": true}`).
    - Only if a session exists **and** `proof_ok`: `sessions.destroy`. If `everywhere` and FULL,
      also `provider.logout_everywhere` + audit `auth.logout_all`; otherwise audit `auth.logout`.
    - Always: `{"state": "anonymous"}`, the clear-cookie header and `Clear-Site-Data: "cache"`.
- **Helpers:**
  - `auth_error_handler` builds the body and headers through `responses.py`. A 5xx logs ERROR once
    with `cause_for_log`, which is never put in the body.
  - `read_json_object(request, *, allowed, required=frozenset(), optional_body=False)`. It catches
    `ValueError` and `RecursionError`, and names the key, never the value. `optional_body` is an
    additive keyword, needed because the E10 body is optional.
  - `require_str`.
  - `client_info(scope)`: the canonical key through `throttle.canonical_client_key`, plus
    `is_loopback` and `secure`.
- **Tests:** replace `install_stub_auth_routes` in `test_route_enumeration.py` and
  `test_partial_confinement.py` (T-G7qByZ) with the real `install_auth_routes`. The suites must stay
  green.

## Inputs / Outputs
- **Inputs:**
  - T-XchniS: provider, runtime, `VerifiedIdentity`.
  - T-G7qByZ: middleware, `responses.py`, the minimal `routes.py`, the scope keys including
    `proof_ok`.
  - T-kwwJ82: `SessionManager`.
- **Outputs:**
  - `src/agent_orchestrator/auth/http/routes.py`, enabled branch
  - `tests/auth/test_routes_core.py`, `test_cookie_isolation.py`, `test_revocation.py`
  - an `assert_flat_auth_routes` case in `test_route_enumeration.py`

## Acceptance Criteria
1. **Contract conformance** (`test_routes_core.py`, table-driven). For E1, E2, E8, E9 and E10,
   every status and error `code` listed in HLD §2.2 and §2.4 is produced by at least one case, and
   the JSON key sets and value types match exactly. That covers 400, 401 `invalid_credentials`, 403
   `totp_required`, 429 with `Retry-After` and `retry_after_seconds`, 503 `busy`, 503
   `store_unavailable`, and 400 `password_policy` with `violations`.
2. **Login states:**
   - password-only → `authenticated`, plus the `user` object, a 43-character `session_proof` and a
     cookie;
   - an enrolled user → `second_factor_required` with `second_factors == ["totp", "recovery_code"]`;
   - `required` and not enrolled → `enrollment_required` with `enrollment_token_required: true`;
   - policy `off` with a `totp_required` user who is not enrolled → 403 `totp_required`.
3. **Uniform failure** (S6, HTTP level): an unknown user and a wrong password give an identical
   status, identical body **bytes** and the same header-name set (excluding `Date`).
4. **Status** (E1):
   - anonymous;
   - second-factor pending (`pending_username`, `second_factors`);
   - enrollment pending (`enrollment_token_required: true`);
   - authenticated, with a parametrized table of policy × `totp_enrolled` × `totp_required` for
     `can_enroll_totp` / `can_disable_totp`;
   - `session` times and `transport` (`secure`, `client_is_loopback`).

   A valid cookie with **no** proof, or a **wrong** one, → `state: "anonymous"` (AC-35 route part).
   A status call does not move the idle deadline (`FakeClock`).
5. **Rotation and replay** (AC-14 route part):
   - a login that presents a proof-verified session destroys it, and the old cookie and proof → 401;
   - a login that presents a cookie **without** a proof leaves that server session intact;
   - E8 → a new cookie and proof, the old pair → 401, the user's other sessions in this realm → 401,
     the absolute deadline is unchanged (`FakeClock`), and the epoch +1;
   - after E10 with a proof, replaying the old pair → 401.
6. **Logout without a proof** (AC-35 route part): the response is still 200 with the clear-cookie
   header and `Clear-Site-Data: "cache"`, but the server session **remains valid**: the same cookie
   and proof → 200 on a protected route.
7. **Logout everywhere:**
   - FULL session plus proof with `everywhere: true` → epoch +1. A session for the same user in a
     second app instance sharing the store → 401. One `auth.logout_all` event.
   - a partial session with `everywhere: true` → only that session is destroyed, there is no epoch
     bump, and one `auth.logout` event.
8. **Password change** (AC-22 part):
   - a wrong `current_password` → 401 `invalid_credentials` and the lockout failure count +1;
   - a policy violation → 400 `password_policy`;
   - a missing field → 400 `invalid_request`.
9. **Invalid requests:**
   - an unknown key → 400 whose message names the key and does not contain the sent value
     (sentinel);
   - a non-object body, malformed JSON, or JSON nested 10 000 levels deep → 400;
   - a username over 64 characters or a password over 1024 → 400.
10. **Cookie isolation** (AC-16, S3/S5/S26, `test_cookie_isolation.py`). Two dashboard apps (ports
    8765 and 8766, different workspaces) and a hub-realm test app (`Realm("hub", 8770)`) share one
    store:
    - A's token under B's cookie name → 401;
    - a new instance on port 8765 → 401 and the cookie cleared;
    - the flags `HttpOnly`, `SameSite=Strict` and `Path=/`; no `Domain`; no `Max-Age` except on
      clears;
    - `TestClient(base_url="https://testserver")` → `__Host-ao_sid_8765` plus `Secure`;
    - the realm id `ui:<12 hex>` is unchanged when the same workspace is served on port 9000;
    - a duplicated realm cookie → 401.
11. **Revocation** (AC-39 route part, S15/S25, `test_revocation.py`). Two apps; log in on both;
    then:
    - `store.mutate(set_password_hash(...))`, as the CLI would → both get 401 on their next request;
    - `remove_user` then `add_user` with the same name → the old cookie and proof get 401 on both.
12. **First insecure login** (AC-25 part):
    - two successful logins from client `10.0.0.5` over http → exactly **one** WARNING containing
      `not encrypted` (`caplog`);
    - from `127.0.0.1` → none;
    - over https from `10.0.0.5` → none.
13. **Error handler and registry:**
    - a `StoreUnavailableError(cause_for_log="x")` → 503 with `Retry-After: 5`; exactly one ERROR
      log containing `x`; the body does not contain `x`;
    - a missing route builder → `AuthConfigError`;
    - `assert_flat_auth_routes` raises when the login route is added through `include_router`, and
      passes for the output of `install_auth_routes`;
    - `test_route_enumeration.py` and `test_partial_confinement.py` pass against the real routes.
14. ruff and mypy are clean. Coverage of `http/routes.py` (the parts in this task) is ≥ 90 %.

## Risks
- **Contract drift from HLD §2.** The table-driven conformance test is the guard. The frontend lanes
  build against §2 in parallel.
- **Soft ordering with T-QJ1vyQ.** The proof cases (AC-4, AC-6) depend only on `proof_ok`, which
  T-G7qByZ computes. They do not need T-QJ1vyQ's enforcement.
- **Rotation races with in-flight requests:** a 401 and a status re-fetch, as documented.

## Dependencies
- **Upstream:** T-XchniS, T-G7qByZ.
- **Downstream:**
  - T-KQ6ZrY (TOTP routes, `user_payload`, rotation helpers);
  - T-jVqH8w, T-KOv2qD (`register_hub_auth_routes` lives in this file and is added there), T-U2ERMo;
  - the frontend lanes, verified against the real routes in T-U2ERMo.

## Pseudocode / Algorithm
- The HLD §11.18 handler table and the "Rotation identity" rule; the sequences in §14.1 and
  §14.5–§14.7.
- Rotation helper, shared with T-KQ6ZrY:

```text
FUNCTION rotate(runtime, session, *, epoch, auth_method, second_factor, keep_absolute) -> IssuedSession:
  identity = VerifiedIdentity(session.user_id, session.username, session.roles, epoch, session.store_id,
                              next_state=FULL, provider=session.provider)
  new = runtime.sessions.issue(identity, FULL, client_key=session.client_key, auth_method=auth_method,
                               second_factor=second_factor, replacing=session, keep_absolute_deadline=keep_absolute)
  runtime.sessions.destroy_user_sessions(session.user_id, except_session_id=new.record.session_id)
  RETURN new
```

## Schemas / Interface Notes
- HLD §2 (E1, E2, E8, E9, E10; §2.2 codes; §2.3 cookie and proof).
- `client_info()`: the address comes from `scope["client"][0]` (or `None` → `UNKNOWN_CLIENT_KEY`),
  canonicalized through `canonical_client_key`; `is_loopback` via `ipaddress`; `secure` means
  `scope["scheme"] == "https"`.

## Handoff Boundary
- **Upstream:** the runtime, and the middleware's scope keys.
- **Downstream:** the core `/api/auth/*` API, frozen to HLD §2. The TOTP routes plug into the local
  builder (T-KQ6ZrY), and the hub routes into this file (T-KOv2qD).

## Verification

```
python -m pytest -q tests/auth/test_routes_core.py tests/auth/test_cookie_isolation.py tests/auth/test_revocation.py tests/auth/test_route_enumeration.py tests/auth/test_partial_confinement.py
python -m pytest -q tests/auth tests/ui
ruff check src tests && ruff format --check src tests && mypy src
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-rpKCjP-auth-http-routes/`
