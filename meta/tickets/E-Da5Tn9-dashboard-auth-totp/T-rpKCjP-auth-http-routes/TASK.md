# TASK: T-rpKCjP-auth-http-routes

## Metadata
- Task ID: `T-rpKCjP-auth-http-routes`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane Q; v2.1, was lane A)
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Done`
- Estimate: `3 days` (v2.1; was 2.5) · Sprint `S2`

## Requirements Mapping
- Requirement IDs: FR-11, FR-12, FR-17 (HTTP-level uniformity), FR-18 (password change), FR-21
  (first-login warning; v2.1 unconfigured-proxy warning), FR-23 (route-side audit), FR-27
  (status/logout), FR-29 (loopback definition), FR-31
- ACs:
  - owned: AC-14 (route part), AC-16, AC-22 (password-change part), AC-25 (first-login warning
    part), AC-35 (status/logout part), AC-39 (route part), **AC-43** (`client_info`, E1 transport,
    warning; v2.1)
  - supports: AC-17 (real-routes enumeration, `assert_flat_auth_routes`)
- Invariants: S3, S5, S6, S15, S21, S25, S26, S28
- Design: HLD §2 (E1, E2, E8, E9, E10, §2.2, §2.3; **the contract: implement it exactly**; v2.1
  adds `transport.proxy_suspected`), §11.1 R1/R1a, §11.15.1 (`ClientInfo`), §11.16
  (`proxy_suspected_warned`), §11.18 (ownership table, list-valued registry, `client_info`, the
  handler table), §11.15.2 (audit responsibility), §13.2 (test-file ownership), §14.1,
  §14.5–§14.7, §14.10, §20.3 #3/#4/#7/#17; §1 D3, D4, D7, D10, D11, D17, D25; §28.9 (security M2;
  design-review M2, minor 6); ADR-0021 D1, D2, D5, D7, D10, D11

## Description
Extend `src/agent_orchestrator/auth/http/routes.py` (FastAPI allowed; flat routes only). After
T-G7qByZ's minimal version, this task is the file's **only** editor (HLD §11.18 ownership table,
design-review M2).
- **Builder registry (v2.1, design-review M2):** `RouteBuilder`,
  `ROUTE_BUILDERS: dict[str, list[RouteBuilder]]` and `register_route_builder(provider_id, fn)`
  (callables, not strings). Registration **appends**; registering the same `fn` twice is a no-op;
  builders run in registration order. `routes.py` registers `add_local_password_routes` for
  `LOCAL_PROVIDER_ID` at import.
- **`install_auth_routes(app, runtime)`**, enabled branch (HLD §11.18 pseudocode):
  - import every module in `_BUILTIN_ROUTE_MODULES = ("agent_orchestrator.auth.http.routes_second_factor",)`
    (idempotent; registers `add_totp_routes`);
  - `add_core_auth_routes(app, runtime)`: E1 status, E10 logout, E9 keepalive;
  - then every builder in `ROUTE_BUILDERS[runtime.provider.provider_id]`, in order. No builder →
    `AuthConfigError("no route builder registered for provider '<id>'")`;
  - `app.add_exception_handler(AuthError, auth_error_handler)`;
  - `assert_flat_auth_routes(app)`: every `AUTH_ROUTE_POLICIES` key must be a **direct** member of
    `app.router.routes`; otherwise `RuntimeError` naming the key.
- **Local builder** `add_local_password_routes(app, runtime)` registers E2 login and E8 password.
  It does **not** call the TOTP builder; that is registered separately (above).
- **`auth/http/routes_second_factor.py` stub (created here, v2.1):** contains `add_totp_routes(app,
  runtime)` that returns at once when `runtime.totp is None` (and, in this stub, always), and the
  import-time `register_route_builder(LOCAL_PROVIDER_ID, add_totp_routes)`. **T-KQ6ZrY owns the
  file afterwards** and fills in E3–E7; this task never edits it again.
- **Handlers** (`async def`), exactly as in the HLD §11.18 handler table, reading
  `SCOPE_SESSION_KEY` and `SCOPE_PROOF_OK_KEY` from `request.state`:
  - **E1:** the body as in §2.4. It reports non-anonymous **only if `proof_ok`**. It never slides
    the session. `transport` = `{secure, client_is_loopback: is_loopback, proxy_suspected}` from
    `client_info(scope, runtime)` (v2.1). A shared `user_payload(runtime, session) -> dict` builds
    the `user` object, including the `can_enroll_totp` / `can_disable_totp` rules; it is reused by
    T-KQ6ZrY.
  - **E2:**
    - `provider.authenticate`;
    - `sessions.issue(identity, identity.next_state, client_key=…, auth_method="password" if FULL
      else None, replacing=session if proof_ok else None)` (D25: a missing proof never destroys
      anything);
    - FULL → audit `auth.login.success` with the new `session_id`;
    - the body for the three states, with `session_proof`, plus `Set-Cookie` from
      `responses.session_cookie_header`;
    - the first plain-HTTP login from a non-loopback client logs one WARNING per process containing
      `not encrypted` (`runtime.first_insecure_login_warned`).
  - **E8:** `provider.change_password`, then `rotate(...)` (below) with the new epoch,
    `keep_absolute=True`, the session's `auth_method` and `second_factor`, and the body with a new
    proof.
  - **E9:** `sessions.times(session)`. The middleware has already slid the session (only with the
    proof, v2.1).
  - **E10:** the body is optional (`{}` or `{"everywhere": true}`).
    - Only if a session exists **and** `proof_ok`: `sessions.destroy`. If `everywhere` and FULL,
      also `provider.logout_everywhere` + audit `auth.logout_all`; otherwise audit `auth.logout`.
    - Always: `{"state": "anonymous"}`, the clear-cookie header and `Clear-Site-Data: "cache"`.
- **Helpers:**
  - `auth_error_handler` builds the body and headers through `responses.py`. A 5xx logs ERROR once
    with `cause_for_log`, which is never put in the body.
  - `read_json_object(request, *, allowed, required=frozenset(), optional_body=False)`. It catches
    `ValueError` and `RecursionError`, and names the key, never the value.
  - `require_str`.
  - **`client_info(scope, runtime) -> ClientInfo` (v2.1, security M2; HLD §11.18 pseudocode):**
    - `key = canonical_client_key(peer)`; `peer_loopback` after the `::ffff:` unwrap;
    - `host` = the `Host` header's hostname (lower-case, brackets and port stripped);
    - `forwarded` = any header named `forwarded` or starting with `x-forwarded-`
      (`FORWARDED_HEADER`, `FORWARDING_HEADER_PREFIX`);
    - `is_loopback = peer_loopback and host in LOOPBACK_HOSTNAMES and not forwarded`;
    - `proxy_suspected = not runtime.settings.trusted_proxies and peer_loopback and (forwarded or
      host not in LOOPBACK_HOSTNAMES)`;
    - on the first `proxy_suspected` per process: one WARNING naming `AO_UI_AUTH_TRUSTED_PROXIES`,
      then `runtime.proxy_suspected_warned = True`;
    - `secure = scope["scheme"] == "https"`.
  - `rotate(...)` (Pseudocode), shared with T-KQ6ZrY.
- **Rule R1a (v2.1, design-review minor 6):** every name used in a route-handler parameter
  annotation in `http/routes*.py` and `http/hub_routes.py` is imported at module scope, never only
  under `if TYPE_CHECKING:`. This task adds the AST test.
- **Tests (v2.1 ownership, design-review M2):** do **not** edit T-G7qByZ's
  `test_route_enumeration_dashboard.py` or `test_partial_confinement.py` (they keep using the stub
  routes and stay merge-order independent). Add `tests/auth/test_route_enumeration_real_routes.py`
  instead, using `tests/auth/helpers/enumeration.py`.

## Inputs / Outputs
- **Inputs:**
  - T-XchniS: provider, runtime (incl. `proxy_suspected_warned`), `VerifiedIdentity`, `ClientInfo`.
  - T-G7qByZ: middleware, `responses.py`, the minimal `routes.py`, the scope keys including
    `proof_ok`, `tests/auth/helpers/enumeration.py`.
  - T-kwwJ82: `SessionManager`.
  - T-kzEzwy: `LOOPBACK_HOSTNAMES`, `FORWARDED_HEADER`, `FORWARDING_HEADER_PREFIX`.
- **Outputs:**
  - `src/agent_orchestrator/auth/http/routes.py`, enabled branch
  - `src/agent_orchestrator/auth/http/routes_second_factor.py` (registered no-op stub; then T-KQ6ZrY's)
  - `tests/auth/test_routes_core.py`, `test_cookie_isolation.py`, `test_revocation.py`,
    `test_client_info.py` (v2.1), `test_routes_annotations.py` (v2.1),
    `test_route_enumeration_real_routes.py` (v2.1)

## Acceptance Criteria
1. **Contract conformance** (`test_routes_core.py`, table-driven). For E1, E2, E8, E9 and E10,
   every status and error `code` listed in HLD §2.2 and §2.4 is produced by at least one case, and
   the JSON key sets and value types match exactly (E1 `transport` has exactly `secure`,
   `client_is_loopback`, `proxy_suspected`). That covers 400, 401 `invalid_credentials`, 403
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
   - `session` times and `transport` (`secure`, `client_is_loopback`, `proxy_suspected`).

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
13. **`client_info` and the unconfigured-proxy warning** (AC-43, S28; security M2 and test gate 3;
    `test_client_info.py`, HLD §20.3 #17), TestClient with `client=("127.0.0.1", 1)` and no trusted
    proxies:
    - (a) no forwarding header and `Host: 127.0.0.1:8765` (also `localhost:8765`, `[::1]:8765`) →
      E1 `client_is_loopback: true`, `proxy_suspected: false`;
    - (b) `X-Forwarded-For: 203.0.113.9` → `client_is_loopback: false`, `proxy_suspected: true`;
    - (c) `Forwarded: for=203.0.113.9` → the same; (c2) `X-Forwarded-Proto: https` alone → the same;
    - (d) `Host: dash.example.com` (allow-listed via `AO_UI_ALLOWED_HOSTS`) → the same;
    - (e) exactly **one** WARNING across (b)–(d) in one process, naming
      `AO_UI_AUTH_TRUSTED_PROXIES`;
    - (f) a peer `10.0.0.5` with no headers → `client_is_loopback: false`, `proxy_suspected: false`
      (not a proxy case);
    - (g) a runtime whose settings carry `trusted_proxies=("127.0.0.1",)` → `proxy_suspected` is
      always false and no WARNING is logged.
    The E4/E5/E7 `insecure_transport` consequence of (b) is asserted by T-KQ6ZrY.
14. **Error handler and registry:**
    - a `StoreUnavailableError(cause_for_log="x")` → 503 with `Retry-After: 5`; exactly one ERROR
      log containing `x`; the body does not contain `x`;
    - a provider id with no builder → `AuthConfigError`;
    - `register_route_builder` appends; registering the same function twice adds it once; two
      builders for one provider both run, in registration order;
    - after `install_auth_routes`, `routes_second_factor` is imported and its `add_totp_routes` is
      registered for `LOCAL_PROVIDER_ID`; with `runtime.totp is None` it adds no route;
    - `assert_flat_auth_routes` raises when the login route is added through `include_router`, and
      passes for the output of `install_auth_routes`.
15. **Real-routes enumeration** (AC-17 part, `test_route_enumeration_real_routes.py`): the dashboard
    (built) with the real core routes and `totp=None`: the non-AUTHENTICATED set equals §13.2 minus
    the three `/api/auth/totp/*` rows (§13.2 configuration rule); no stale entries; partial
    confinement holds against the real routes. T-G7qByZ's stub-based files are unchanged and green.
16. **Annotation rule** (R1a, design-review minor 6, `test_routes_annotations.py`): an AST walk over
    `auth/http/routes*.py` and `auth/http/hub_routes.py` (when present) finds no route-handler
    parameter annotation whose name is bound only under `if TYPE_CHECKING:`; a synthetic module
    with such an annotation makes the check fail (negative control).
17. ruff and mypy are clean. Coverage of `http/routes.py` (the parts in this task) is ≥ 90 %.

## Risks
- **Contract drift from HLD §2.** The table-driven conformance test is the guard. The frontend lanes
  build against §2 in parallel. `proxy_suspected` is the one v2.1 additive field
  (manager-approved).
- **Soft ordering with T-QJ1vyQ.** The proof cases (AC-4, AC-6) depend only on `proof_ok`, which
  T-G7qByZ computes. They do not need T-QJ1vyQ's enforcement.
- **Circular import between `routes.py` and `routes_second_factor.py`.** The second module imports
  helpers from `routes.py`; `routes.py` imports it lazily inside `install_auth_routes` (never at
  module top), so the cycle cannot bite.
- **Rotation races with in-flight requests:** a 401 and a status re-fetch, as documented.

## Dependencies
- **Upstream:** T-XchniS, T-G7qByZ.
- **Downstream:**
  - T-KQ6ZrY (owns `routes_second_factor.py` after this task; reuses `user_payload`, `rotate`,
    `read_json_object`, `client_info`);
  - T-jVqH8w;
  - T-KOv2qD (writes its own `auth/http/hub_routes.py`, **not** this file);
  - T-U2ERMo;
  - the frontend lanes, verified against the real routes in T-U2ERMo.

## Pseudocode / Algorithm
- The HLD §11.18 handler table, the `install_auth_routes` / `client_info` pseudocode and the
  "Rotation identity" rule; the sequences in §14.1 and §14.5–§14.7.
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
- HLD §2 (E1, E2, E8, E9, E10; §2.2 codes; §2.3 cookie and proof; §2.5 `transport` type).
- `client_info(scope, runtime)`: HLD §11.18. `ClientInfo` fields: `key`, `is_loopback`, `secure`,
  `proxy_suspected` (HLD §11.15.1, T-XchniS defines the dataclass).

## Handoff Boundary
- **Upstream:** the runtime, and the middleware's scope keys.
- **Downstream:** the core `/api/auth/*` API, frozen to HLD §2. The TOTP routes plug in through the
  registry in their own module (T-KQ6ZrY); the hub routes live in `hub_routes.py` (T-KOv2qD).

## Verification

```
python -m pytest -q tests/auth/test_routes_core.py tests/auth/test_cookie_isolation.py tests/auth/test_revocation.py tests/auth/test_client_info.py tests/auth/test_routes_annotations.py tests/auth/test_route_enumeration_real_routes.py
python -m pytest -q tests/auth tests/ui
ruff check src tests && ruff format --check src tests && mypy src
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-rpKCjP-auth-http-routes/`
