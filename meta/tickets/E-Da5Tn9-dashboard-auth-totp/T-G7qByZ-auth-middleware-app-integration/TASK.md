# TASK: T-G7qByZ-auth-middleware-app-integration

## Metadata
- Task ID: `T-G7qByZ-auth-middleware-app-integration`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane C)
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `3 days` · Sprint `S1→S2`

## Requirements Mapping
- Requirement IDs: FR-8, FR-9, FR-11 (sliding), FR-13, FR-19 (headers), NFR-1, NFR-5
- ACs: AC-2 (auth-off part), AC-11, AC-12, AC-15 (sliding part), AC-17 (dashboard part), AC-23
  (headers part), AC-41 (byte-pin part)
- Invariants: S1, S2, S5, S17, S20, S26
- Design: HLD §13.1–§13.3, §10.3 (ordering), §11.17 (split), §11.18 (`responses.py`), §2.2–§2.3,
  §16 #1 (a, b, c, f, g), §20.3 #1/#2/#12/#14; §1 D4, D12, D19, D23, D24; ADR-0021 D2, D6

## Description
1. **`auth/http/middleware.py`: `AuthMiddleware(app, *, runtime, policies)`.** A pure ASGI
   middleware. This task owns the parts of HLD §13.3 below (numbering as in §13.3):
   - **The preamble:**
     - lifespan pass-through;
     - initialise the scope-state keys (`SCOPE_PRINCIPAL_KEY`, `SCOPE_SESSION_KEY`,
       `SCOPE_PROOF_OK_KEY`, `SCOPE_AUTH_ENABLED_KEY`);
     - full pass-through when `runtime is None`;
     - websocket deny (`WS_POLICY_VIOLATION`);
     - read `sfs` / `navigation` from the headers. Both step 2 and step 9 use them.
   - **Step 1:** `route_path` (`root_path`-safe), `is_api_path`, and `classify(scope, policies)`
     with Mount handling, opaque routers → AUTHENTICATED, and the rule that the SPA fallback never
     opens `/api/*`.
   - **Step 4:** `parse_realm_cookie` → a duplicate means **no session** and a WARNING logged once;
     then `sessions.lookup`, and `clear_cookie` for stale values.
   - **Steps 6–10:** tri-state revalidation (UNAVAILABLE → 503 and the session is kept); the policy
     decision (401 + `WWW-Authenticate`, or a 303 to `realm.login_path` for HTML navigation);
     principal injection through `sessions.principal_for`; sliding for FULL sessions only, on
     mutations, keepalive, and same-origin or typed navigations; and the send wrapper that injects
     `X-Frame-Options`, `no-store` and the clear-cookie header.
   - **`deny()`**, built on `http/responses.py`.
   - **Hooks for T-QJ1vyQ.** Clearly marked functions `_check_csrf` (step 2) and `_cap_auth_body`
     (step 3) are no-ops here. `_check_proof` (step 5) here **only computes** `proof_ok` through
     `sessions.proof_matches` and stores it in `SCOPE_PROOF_OK_KEY`. The E1/E10 routes (T-rpKCjP)
     need that value. The enforcement branch (proof required but not matching → anonymous) is
     T-QJ1vyQ's.
2. **`auth/http/responses.py`** (pure, byte-exact; HLD §11.18 and §2.3):
   - `session_cookie_header`, `clear_cookie_header`;
   - `parse_realm_cookie → (value, duplicated)`;
   - `error_body`, `error_headers`.

   The routes reuse them.
3. **`auth/http/routes.py`, minimal version:** `install_auth_routes(app, runtime)`.
   - It sets `app.state.<APP_STATE_AUTH_KEY>`.
   - With `runtime is None` it adds **only** the disabled `GET /api/auth/status` (the E1 disabled
     body, flat `add_api_route`).
   - The enabled branch is left for T-rpKCjP, marked with a clear TODO.
4. **`ui/app.py`** (HLD §16 #1):
   - (a) `create_app(..., auth: AuthRuntime | None = None)`, keyword-only;
   - (b) `app.add_middleware(AuthMiddleware, runtime=auth, policies=DASHBOARD_ROUTE_POLICIES)`
     **immediately before** the existing `SecurityMiddleware` line;
   - (c) `install_auth_routes(app, auth)` straight after the middleware lines, before every route
     and before `_mount_frontend`;
   - (f) the docstring update;
   - (g) a test pinning `auth.constants.API_PREFIX == ui.app.API_PREFIX`.

   Item (e), `create_app_from_env`, belongs to T-jVqH8w. There are **no** decorator edits.
5. **Staging.**
   - **S1:** build against a duck-typed `StubRuntime`/`StubRealm`, added to
     `tests/auth/helpers.py` with the exact HLD §11.16 attribute names. They use the real
     `SessionManager` (T-kwwJ82) and a fake provider with `revalidate`.
   - **S2 final wiring (about 0.5 d, after T-XchniS):** switch the annotations to the real
     `AuthRuntime`/`Realm` (a `TYPE_CHECKING` import). Parametrize the integration fixtures over the
     stub and `build_auth_runtime(...)`. Run mypy clean.
   - Add `install_stub_auth_routes(app)` to `tests/auth/helpers.py`. It registers trivial flat
     handlers at all ten `/api/auth/*` paths, so the enumeration can see them before T-rpKCjP lands.
     T-rpKCjP removes its use.
   - Add a `tests/auth/conftest.py` fixture `dashboard_service`, following the `workspace` +
     `StubSupervisor` pattern of `tests/ui/conftest.py`, and set `AO_UI_ALLOWED_HOSTS=testserver`.
   - **Every test sends the session proof** (`same_origin_headers(client, proof)`), so it keeps
     passing once T-QJ1vyQ enables enforcement.

## Inputs / Outputs
- **Inputs:**
  - T-kzEzwy: constants, errors, model, test helpers.
  - T-kwwJ82: `SessionManager`, `principal`, `policy`.
  - T-XchniS: the real runtime, for the final wiring only.
- **Outputs:**
  - `src/agent_orchestrator/auth/http/__init__.py`, `http/middleware.py`, `http/responses.py`,
    `http/routes.py` (minimal)
  - the edits to `src/agent_orchestrator/ui/app.py`
  - `tests/auth/test_middleware.py`, `test_route_enumeration.py`, `test_partial_confinement.py`,
    `test_auth_off_regression.py`, `test_responses.py`
  - additions to `tests/auth/helpers.py` and `tests/auth/conftest.py`

## Acceptance Criteria
1. **Ordering** (AC-17 part): `[m.cls for m in create_app(service).user_middleware] == [SecurityMiddleware, AuthMiddleware]`,
   with and without `auth`.
2. **Auth off** (AC-2 part, S17), `test_auth_off_regression.py`:
   - `python -m pytest -q tests/ui` passes **unmodified**;
   - the response **header names** of `GET /api/health`, `GET /api/runs` and `GET /` equal a
     hard-coded pre-change set, and their bodies are unchanged;
   - `GET /api/auth/status` equals the E1 disabled body byte-for-byte;
   - a probe route sees `principal is None` and `auth_enabled is False`, and `require_principal`
     returns `None`.
3. **Route enumeration** (AC-17 dashboard part, S1), `test_route_enumeration.py`. The app is built
   with a temp `STATIC_DIR` that contains `index.html` and `assets/`, and again with it absent
   (monkeypatched), each time with `install_stub_auth_routes`.
   - Contexts come from `fastapi.routing.iter_route_contexts`.
   - Each of GET, POST, PUT, PATCH and DELETE is sent anonymously with a same-origin `Origin`, plus
     `{}` for mutations. PUBLIC pairs → not 401. Every other pair → 401 `not_authenticated`.
   - The non-AUTHENTICATED set, computed by calling `classify` on each context, equals a literal: 10
     entries for the built frontend, and 8 for the unbuilt one (no `spa_fallback`, no `assets`
     mount).
   - Every `DASHBOARD_ROUTE_POLICIES` key matches a route of the built app, so there are no stale
     entries.
   - A probe route added through `include_router` classifies as AUTHENTICATED (anonymous → 401).
4. **`/api` guard and path probes:**
   - anonymous `GET /api/does-not-exist` → 401; authenticated → the unchanged 404 body;
   - anonymous `GET /some/spa/route` (built) → 200 `index.html`;
   - `/api/x/`, `//api/x`, `/API/x` and a `root_path="/prefix"` app each return either 401 or the
     static `index.html` bytes, never API JSON (dev-security #8).
5. **Partial confinement** (AC-12, S2), `test_partial_confinement.py`. A PARTIAL_SECOND_FACTOR
   session plus its proof: every pair except PUBLIC and `POST totp/verify` → 401
   `second_factor_required`. A PARTIAL_ENROLL session plus its proof: every pair except PUBLIC and
   the two enroll routes → 401 `enrollment_required`.
6. **Principal** (AC-11):
   - full session plus proof → a probe sees a `Principal` whose `dataclasses.fields` names, in order,
     equal HLD §2.6;
   - `roles` and `amr` are tuples, `auth_time` is an aware UTC datetime, and `realm` matches
     `^ui:[0-9a-f]{12}$` (the stub provides it until the final wiring);
   - `current_principal(request)` is that object, and `auth_enabled` is `True`;
   - a partial session → `None`, and `require_principal` raises `AuthError(NOT_AUTHENTICATED)` (the
     probe catches it and returns the code).
7. **Revalidation** (tri-state):
   - after `bump_epoch`, the next request → 401 `not_authenticated`, with a clear `Set-Cookie`, and
     the session is gone from the table;
   - with an UNAVAILABLE revalidation (corrupt `users.json`) → 503 `store_unavailable` with
     `Retry-After: 5`. The session is **kept**: after the file is restored, the same cookie and
     proof work.
8. **Cookies** (S26):
   - a duplicated realm cookie → treated as no session (401 on a protected probe), **no**
     clear-cookie header, and exactly one WARNING per process;
   - an unknown token → 401 plus the clear-cookie header;
   - an anonymous HTML navigation (`Accept: text/html`) to an AUTHENTICATED non-API probe page →
     303 `Location: /`.
9. **Sliding** (AC-15 part, S20), with `FakeClock`:
   - 10 `GET /api/runs` requests spaced `idle - 1` s apart do **not** keep the session alive past
     `idle`;
   - a POST to the keepalive path (stub) and a mutating probe **do** slide it;
   - a GET navigation with `Sec-Fetch-Site` of `same-origin`, `none` or absent slides it;
   - `same-site` does **not** slide it;
   - partial sessions never slide.
10. **Headers** (AC-23 part), with auth on:
    - `X-Frame-Options: DENY` on every response, including 401, 503 and the SPA shell;
    - `Cache-Control: no-store` on `/api/*` and on non-PUBLIC responses, and **not** on the PUBLIC
      SPA shell;
    - `WWW-Authenticate: AO-Session realm="<realm.id>"` on every 401;
    - deny bodies are byte-equal to `error_body(code)`.

    With auth off, neither header is added by auth.
11. **Byte pins** (AC-41 part, `test_responses.py`):
    - `Set-Cookie` and clear-cookie bytes over http and https exactly as in §2.3;
    - `parse_realm_cookie` returns `(value, False)`, `(None, False)` and `(value, True)` for the
      single, absent and duplicated cases, and ignores other names;
    - `error_body` bytes for every `ErrorCode`;
    - `error_headers` include `Retry-After` for 429 and 503.
12. **Proof observation:** with the right proof header, `request.state.auth_proof_ok` is `True`;
    with a wrong or absent one, it is `False`. Enforcement is T-QJ1vyQ's and is not tested here.
13. **Websocket:** with auth on, a websocket connect is closed with code 1008.
14. **Performance** (NFR-5, `-m slow`, informational): p95 of classification plus lookup ≤ 1 ms
    over 1000 requests. The number is recorded in STATUS.
15. The `API_PREFIX` pin test passes. ruff and mypy are clean (after the final wiring). Coverage of
    `http/middleware.py` and `http/responses.py` is ≥ 90 %.

## Risks
- **Starlette routing internals** (`Route.matches`, `Mount`, `_IncludedRouter`): pinned by AC-3 and
  AC-4, not by comments. They were verified on FastAPI 0.139.2 / Starlette 1.3.1.
- **Breaking `tests/ui`.** AC-2 is the gate. Do not edit existing tests.
- **Stub drift:** the stub attribute names must equal HLD §11.16. The final wiring parametrizes over
  both runtimes.

## Dependencies
- **Upstream:** T-kzEzwy, T-kwwJ82 (hard); T-XchniS (final wiring only, about 0.5 d).
- **Downstream:**
  - T-QJ1vyQ (fills steps 2, 3 and 5);
  - T-rpKCjP (extends `install_auth_routes` and reads the session and `proof_ok` scope keys);
  - T-jVqH8w (§16 #1 e, same file);
  - T-KOv2qD (the hub reuses `AuthMiddleware` with `HUB_ROUTE_POLICIES`);
  - T-U2ERMo.

## Pseudocode / Algorithm
HLD §13.1 and §13.3, verbatim, for the steps this task owns. `deny()` is in §13.3.

## Schemas / Interface Notes
- The error envelope and headers are in HLD §2.2–§2.3. The allowlist is §13.2. The policy table is
  `policy.DASHBOARD_ROUTE_POLICIES` (T-kwwJ82).
- The scope keys come only from `constants.py` (R5).

## Handoff Boundary
- **Upstream:** sessions, policy and principal.
- **Downstream:** a deny-by-default app shell with hook points for CSRF, the body cap and the proof.
  Routes plug in through `install_auth_routes`.

## Verification

```
python -m pytest -q tests/auth/test_middleware.py tests/auth/test_route_enumeration.py tests/auth/test_partial_confinement.py tests/auth/test_auth_off_regression.py tests/auth/test_responses.py
python -m pytest -q tests/ui            # must pass unmodified
ruff check src tests && ruff format --check src tests && mypy src
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-G7qByZ-auth-middleware-app-integration/`
