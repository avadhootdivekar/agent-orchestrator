# TASK: T-G7qByZ-auth-middleware-app-integration

## Metadata
- Task ID: `T-G7qByZ-auth-middleware-app-integration`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane C)
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Done` (S1 stub phase 2026-10-05; S2 final wiring done 2026-10-05 with T-XchniS)
- Estimate: `3 days` · Sprint `S1→S2`

## Requirements Mapping
- Requirement IDs: FR-8, FR-9, FR-11 (sliding), FR-13, FR-19 (headers), FR-27 (principal and
  sliding gated on the proof), NFR-1
- ACs: AC-2 (auth-off part), AC-11, AC-12, AC-15 (sliding part), AC-17 (dashboard part + harness),
  AC-23 (headers part), AC-41 (byte-pin part), **AC-44** (steps 8–9 gating, named cases,
  construction checks; v2.1)
- Invariants: S1, S2, S5, S17, S20, S26, S27, S30
- Design: HLD §13.1–§13.3, §10.3 (ordering), §11.14 (`*_COOKIE_ONLY_NAVIGATION`,
  `proof_required`), §11.17 (signature and split), §11.18 (`responses.py`), §2.2–§2.3, §2.6,
  §16 #1 (a, b, c, f, g, h), §20.2 (helpers package), §20.3 #1/#2/#12/#14/#15/#16; §1 D4, D12,
  D19, D23, D24, D25; §28.9 (security M1, M5, L2; design-review B1, M2, minors 1, 4, 5);
  ADR-0021 D1, D2, D6, D11

## Description
1. **`auth/http/middleware.py`: `AuthMiddleware(app, *, runtime, policies,
   cookie_only_navigation=frozenset())`.** A pure ASGI middleware (HLD §11.17).
   - **Construction checks (v2.1, security M1):** every key of `cookie_only_navigation` is
     `("GET", <path not under /api>)` and is **not** a key of `policies` (so it is AUTHENTICATED by
     omission). Otherwise `ValueError` naming the key.
   - This task owns the parts of HLD §13.3 below (numbering as in §13.3):
   - **The preamble:**
     - lifespan pass-through;
     - initialise the scope-state keys (`SCOPE_PRINCIPAL_KEY`, `SCOPE_SESSION_KEY`,
       `SCOPE_PROOF_OK_KEY`, `SCOPE_AUTH_ENABLED_KEY`);
     - full pass-through when `runtime is None`;
     - websocket deny (`WS_POLICY_VIOLATION`);
     - read `sfs` / `navigation` from the headers. Steps 2, 5 and 9 use them.
   - **Step 1:** `route_path` (`root_path`-safe), `is_api_path`, and
     `classify(scope, policies, cookie_only_navigation) -> (route, policy, cookie_only)` with Mount
     handling, opaque routers → AUTHENTICATED, and the rule that the SPA fallback never opens
     `/api/*`. **v2.1 (design-review minor 1):** for a PARTIAL match the key is the first method of
     `sorted(chosen.methods)` that has a table entry (else AUTHENTICATED); never `first(...)` over a
     set. `cookie_only` is true only for a FULL match whose key is in `cookie_only_navigation`.
   - **Step 4:** `parse_realm_cookie` → a duplicate means **no session** and a WARNING logged once;
     then `sessions.lookup`, and `clear_cookie` for stale values.
   - **Step 5 (computing half):** `proof_ok = session and sessions.proof_matches(...)`, stored in
     `SCOPE_PROOF_OK_KEY`; `attested = proof_ok or cookie_only`; `browser_nav = navigation and sfs in
     {"same-origin", "none"}`. The enforcement branch (session → `None` when proof-required and not
     attested) is T-QJ1vyQ's, behind the `_check_proof` hook.
   - **Steps 6–10:** tri-state revalidation (UNAVAILABLE → 503 and the session is kept); the policy
     decision (401 + `WWW-Authenticate`, or a 303 to `realm.login_path` for HTML navigation);
     **step 8 (v2.1, security M1): principal injection through `sessions.principal_for` ONLY when
     `session.state == FULL and attested`**; **step 9 (v2.1): sliding ONLY when
     `proof_ok and (method in MUTATING_METHODS or rpath == AUTH_KEEPALIVE_PATH)`, or when
     `cookie_only and browser_nav`** — nothing else slides (no GET poll, no request without the
     proof, PUBLIC routes included, no navigation to an unflagged route); and the send wrapper that
     injects `X-Frame-Options`, `no-store` and the clear-cookie header.
   - **`deny()`**, built on `http/responses.py`.
   - **Hooks for T-QJ1vyQ.** Clearly marked functions `_check_csrf` (step 2) and `_cap_auth_body`
     (step 3) are no-ops here. `_check_proof` (step 5) here **only computes** `proof_ok`, `attested`
     and `browser_nav`. The E1/E10 routes (T-rpKCjP) need `proof_ok`. The enforcement branch is
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
   - The enabled branch is left for T-rpKCjP, marked with a clear TODO. After this task, T-rpKCjP
     is the file's only editor (HLD §11.18 ownership table).
4. **`ui/app.py`** (HLD §16 #1):
   - (a) `create_app(..., auth: AuthRuntime | None = None)`, keyword-only;
   - (b) `app.add_middleware(AuthMiddleware, runtime=auth, policies=DASHBOARD_ROUTE_POLICIES,
     cookie_only_navigation=DASHBOARD_COOKIE_ONLY_NAVIGATION)` **immediately before** the existing
     `SecurityMiddleware` line;
   - (c) `install_auth_routes(app, auth)` straight after the middleware lines, before every route
     and before `_mount_frontend`;
   - (f) the docstring update;
   - (g) a test pinning `auth.constants.API_PREFIX == ui.app.API_PREFIX`;
   - **(h) v2.1 (security L2):** when `auth is not None`, the `FastAPI(...)` call also gets
     `redoc_url=None, swagger_ui_oauth2_redirect_url=None`. With `auth=None` the call is
     byte-identical to today, so `/redoc` and `/docs/oauth2-redirect` still exist with auth off.

   Item (e), `create_app_from_env`, belongs to T-jVqH8w. There are **no** decorator edits.
5. **Staging.**
   - **S1:** build against a duck-typed `StubRuntime`/`StubRealm` with the exact HLD §11.16
     attribute names. They use the real `SessionManager` (T-kwwJ82) and a fake provider with
     `revalidate`.
   - **S2 final wiring (about 0.5 d, after T-XchniS):** switch the constructor annotations to the
     real `AuthRuntime`/`Realm` (a `TYPE_CHECKING` import is fine for the middleware constructor;
     rule R1a applies only to route-handler parameters). Parametrize the integration fixtures over
     the stub and `build_auth_runtime(...)`. Run mypy clean.
   - **Helpers (v2.1, design-review M2: `tests/auth/helpers/` is a package, one module per owner):**
     - `tests/auth/helpers/stub_runtime.py` (this task): `StubRuntime`, `StubRealm`,
       `install_stub_auth_routes(app)` (trivial flat handlers at all ten `/api/auth/*` paths, so
       the enumeration sees them before T-rpKCjP lands);
     - `tests/auth/helpers/enumeration.py` (this task): the route-enumeration harness shared by
       all four `test_route_enumeration_*.py` files. It guards
       `fastapi.routing.iter_route_contexts` with an importorskip-style check (skip with a clear
       reason when missing). The FastAPI floor in `pyproject.toml` is **not** bumped
       (design-review minor 5, NFR-2).
   - Add a `tests/auth/conftest.py` fixture `dashboard_service`, following the `workspace` +
     `StubSupervisor` pattern of `tests/ui/conftest.py`, and set `AO_UI_ALLOWED_HOSTS=testserver`.
   - **Every test sends the session proof** (`same_origin_headers(client, proof)`), so it keeps
     passing once T-QJ1vyQ enables enforcement.
   - **Moved out (v2.1, design-review minor 4):** the informational p95 benchmark (NFR-5 is a
     target, not an AC) is T-U2ERMo's.

## Inputs / Outputs
- **Inputs:**
  - T-kzEzwy: constants, errors, model, `tests/auth/helpers/core.py`.
  - T-kwwJ82: `SessionManager`, `principal` (v2.1 `Principal` with `roles: list[str]`),
    `policy` (tables, `*_COOKIE_ONLY_NAVIGATION`, `proof_required`).
  - T-XchniS: the real runtime, for the final wiring only.
- **Outputs:**
  - `src/agent_orchestrator/auth/http/__init__.py` (empty), `http/middleware.py`,
    `http/responses.py`, `http/routes.py` (minimal)
  - the edits to `src/agent_orchestrator/ui/app.py`
  - `tests/auth/test_middleware.py`, `test_route_enumeration_dashboard.py` (owned only by this
    task; T-rpKCjP no longer edits it), `test_partial_confinement.py`,
    `test_auth_off_regression.py`, `test_responses.py`
  - `tests/auth/helpers/stub_runtime.py`, `tests/auth/helpers/enumeration.py`,
    `tests/auth/conftest.py`

## Acceptance Criteria
1. **Ordering** (AC-17 part): `[m.cls for m in create_app(service).user_middleware] == [SecurityMiddleware, AuthMiddleware]`,
   with and without `auth`.
2. **Auth off** (AC-2 part, S17), `test_auth_off_regression.py`:
   - `python -m pytest -q tests/ui` passes **unmodified**;
   - the response **header names** of `GET /api/health`, `GET /api/runs` and `GET /` equal a
     hard-coded pre-change set (taken against the integration baseline at merge time, HLD §20.3 #14
     and §16 X4), and their bodies are unchanged;
   - `GET /api/auth/status` equals the E1 disabled body byte-for-byte;
   - **(v2.1, design-review minor 9)** the set of `/api/openapi.json` path keys differs from the
     pre-change set by exactly `/api/auth/status`; `GET /redoc` and `GET /docs/oauth2-redirect`
     are still registered with auth off;
   - a probe route sees `principal is None` and `auth_enabled is False`, and `require_principal`
     returns `None`.
3. **Route enumeration** (AC-17 dashboard part, S1), `test_route_enumeration_dashboard.py`. The app
   is built with a temp `STATIC_DIR` that contains `index.html` and `assets/`, and again with it
   absent (monkeypatched), each time with `install_stub_auth_routes`.
   - Contexts come from `fastapi.routing.iter_route_contexts` through the
     `helpers/enumeration.py` harness; the file skips with a reason when it is unavailable.
   - Each of GET, POST, PUT, PATCH and DELETE is sent anonymously with a same-origin `Origin`, plus
     `{}` for mutations. PUBLIC pairs → not 401. Every other pair → 401 `not_authenticated`.
   - The non-AUTHENTICATED set, computed by calling `classify` on each context, equals a literal: 10
     entries for the built frontend, and 8 for the unbuilt one (no `spa_fallback`, no `assets`
     mount).
   - Every `DASHBOARD_ROUTE_POLICIES` key matches a route of the built app, so there are no stale
     entries.
   - A probe route added through `include_router` classifies as AUTHENTICATED (anonymous → 401).
   - **(v2.1, security L2)** with auth on, `/redoc` and `/docs/oauth2-redirect` are absent from
     `app.router.routes`.
4. **`classify` determinism (v2.1, design-review minor 1)**, unit tests:
   - a PARTIAL match picks the first method of `sorted(chosen.methods)` that has a table entry
     (asserted with a route whose methods are `{"POST", "GET"}` and a table entry for `GET` only);
     the result is the same over 50 calls (no set-order dependence; the source contains no
     `first(` over `chosen.methods`);
   - `HEAD /api/health` (APIRoute, GET only) → PARTIAL → PUBLIC, then 405;
   - with auth off, `HEAD /redoc` and `GET /docs/oauth2-redirect` (Starlette `Route`, FULL match,
     no table key) → AUTHENTICATED; with auth on they match nothing (`chosen is None`);
   - `cookie_only` is true only for a FULL match whose key is in `cookie_only_navigation`.
5. **`/api` guard and path probes:**
   - anonymous `GET /api/does-not-exist` → 401; authenticated → the unchanged 404 body;
   - anonymous `GET /some/spa/route` (built) → 200 `index.html`;
   - `/api/x/`, `//api/x`, `/API/x` and a `root_path="/prefix"` app each return either 401 or the
     static `index.html` bytes, never API JSON (dev-security #8).
6. **Partial confinement** (AC-12, S2), `test_partial_confinement.py`. A PARTIAL_SECOND_FACTOR
   session plus its proof: every pair except PUBLIC and `POST totp/verify` → 401
   `second_factor_required`. A PARTIAL_ENROLL session plus its proof: every pair except PUBLIC and
   the two enroll routes → 401 `enrollment_required`.
7. **Principal** (AC-11, S30; owner decision v2.1):
   - full session plus proof → a probe sees a `Principal` whose `dataclasses.fields` names, in order,
     equal HLD §2.6;
   - `isinstance(p.roles, list)` and `p.roles == []`; `hash(p)` works; `amr` is a tuple;
     `auth_time` is an aware UTC datetime; `realm` matches `^ui:[0-9a-f]{12}$` (the stub provides it
     until the final wiring);
   - **two requests on the same session never share the list** (`p1.roles is not p2.roles`), and
     `p1.roles.append("x")` leaves `p2.roles` and the stored `SessionRecord.roles` unchanged;
   - `current_principal(request)` is that object, and `auth_enabled` is `True`;
   - a partial session → `None`, and `require_principal` raises `AuthError(NOT_AUTHENTICATED)` (the
     probe catches it and returns the code).
8. **Revalidation** (tri-state):
   - after `bump_epoch`, the next request → 401 `not_authenticated`, with a clear `Set-Cookie`, and
     the session is gone from the table;
   - with an UNAVAILABLE revalidation (corrupt `users.json`) → 503 `store_unavailable` with
     `Retry-After: 5`. The session is **kept**: after the file is restored, the same cookie and
     proof work.
9. **Cookies** (S26):
   - a duplicated realm cookie → treated as no session (401 on a protected probe), **no**
     clear-cookie header, and exactly one WARNING per process;
   - an unknown token → 401 plus the clear-cookie header;
   - an anonymous HTML navigation (`Accept: text/html`) to an AUTHENTICATED non-API probe page →
     303 `Location: /`.
10. **Sliding** (AC-15 part, S20; v2.1, security M1), with `FakeClock`:
    - 10 `GET /api/runs` requests (with the proof) spaced `idle - 1` s apart do **not** keep the
      session alive past `idle`;
    - a POST to the keepalive path (stub) and a mutating probe, **each with the proof**, do slide
      it; the same mutating probe **without** the proof does not;
    - on a test app whose `cookie_only_navigation` flags a probe page `("GET", "/nav")`: a GET
      navigation (`Sec-Fetch-Mode: navigate`, `Sec-Fetch-Dest: document`) with `Sec-Fetch-Site` of
      `same-origin` or `none` slides it; `same-site`, `cross-site` or **absent** `Sec-Fetch-*`
      headers do **not**;
    - a navigation to an **unflagged** page never slides;
    - partial sessions never slide.
11. **No principal or slide without the proof on PUBLIC routes** (AC-44 part, S27; security M1):
    `POST /api/auth/logout` (stub) and `GET /api/health`, each with a valid FULL cookie and **no**
    proof → `principal is None` (scope-capturing shim around `app.router` plus a
    `SessionManager.principal_for` spy that is never called), `last_activity_mono` unchanged, and
    the session is still valid for a following request with the proof.
12. **Construction checks** (AC-44 part): `AuthMiddleware(..., cookie_only_navigation=...)` raises
    `ValueError` for `("GET", "/api/x")`, for `("POST", "/nav")`, and for a key present in
    `policies`; `DASHBOARD_COOKIE_ONLY_NAVIGATION` (empty) and `HUB_COOKIE_ONLY_NAVIGATION` with
    `HUB_ROUTE_POLICIES` construct fine.
13. **Headers** (AC-23 part), with auth on:
    - `X-Frame-Options: DENY` on every response, including 401, 503 and the SPA shell;
    - `Cache-Control: no-store` on `/api/*` and on non-PUBLIC responses, and **not** on the PUBLIC
      SPA shell;
    - `WWW-Authenticate: AO-Session realm="<realm.id>"` on every 401;
    - deny bodies are byte-equal to `error_body(code)`.

    With auth off, neither header is added by auth.
14. **Byte pins** (AC-41 part, `test_responses.py`):
    - `Set-Cookie` and clear-cookie bytes over http and https exactly as in §2.3;
    - `parse_realm_cookie` returns `(value, False)`, `(None, False)` and `(value, True)` for the
      single, absent and duplicated cases, and ignores other names;
    - `error_body` bytes for every `ErrorCode`;
    - `error_headers` include `Retry-After` for 429 and 503.
15. **Proof observation:** with the right proof header, `request.state.auth_proof_ok` is `True`;
    with a wrong or absent one, it is `False`. Enforcement is T-QJ1vyQ's and is not tested here.
16. **Websocket:** with auth on, a websocket connect is closed with code 1008.
17. The `API_PREFIX` pin test passes. ruff and mypy are clean (after the final wiring). Coverage of
    `http/middleware.py` and `http/responses.py` is ≥ 90 %.

## Risks
- **Starlette routing internals** (`Route.matches`, `Mount`, `_IncludedRouter`): pinned by AC-3 to
  AC-5, not by comments. They were verified on FastAPI 0.139.2 / Starlette 1.3.1.
- **Breaking `tests/ui`.** AC-2 is the gate. Do not edit existing tests.
- **Stub drift:** the stub attribute names must equal HLD §11.16. The final wiring parametrizes over
  both runtimes.
- **Cookie-only flag misuse.** The construction checks (AC-12) keep the flag on read-only GET
  navigations outside `/api`; never add a key for another epic's route (HLD §2.6 guarantee 6).

## Dependencies
- **Upstream:** T-kzEzwy, T-kwwJ82 (hard); T-XchniS (final wiring only, about 0.5 d).
- **Downstream:**
  - T-QJ1vyQ (fills steps 2, 3 and the enforcement half of 5);
  - T-rpKCjP (extends `install_auth_routes`, reads the session and `proof_ok` scope keys, and adds
    its own `test_route_enumeration_real_routes.py` using `helpers/enumeration.py`);
  - T-jVqH8w (§16 #1 e, same file, after this task);
  - T-KOv2qD (the hub reuses `AuthMiddleware` with `HUB_ROUTE_POLICIES` and
    `HUB_COOKIE_ONLY_NAVIGATION`; its own `test_route_enumeration_hub.py`);
  - T-KQ6ZrY (its own `test_route_enumeration_full_config.py`);
  - T-U2ERMo (also takes the moved p95 benchmark).

## Pseudocode / Algorithm
HLD §13.1 (`classify`, v2.1) and §13.3 (steps 1, 4, 5-computing, 6–10, v2.1), verbatim, for the
steps this task owns. `deny()` is in §13.3.

## Schemas / Interface Notes
- The error envelope and headers are in HLD §2.2–§2.3. The allowlist is §13.2. The policy table is
  `policy.DASHBOARD_ROUTE_POLICIES` and the cookie-only set `policy.DASHBOARD_COOKIE_ONLY_NAVIGATION`
  (T-kwwJ82).
- The scope keys come only from `constants.py` (R5).

## Handoff Boundary
- **Upstream:** sessions, policy and principal.
- **Downstream:** a deny-by-default app shell with hook points for CSRF, the body cap and proof
  enforcement. Routes plug in through `install_auth_routes`.

## Verification

```
python -m pytest -q tests/auth/test_middleware.py tests/auth/test_route_enumeration_dashboard.py tests/auth/test_partial_confinement.py tests/auth/test_auth_off_regression.py tests/auth/test_responses.py
python -m pytest -q tests/ui            # must pass unmodified
ruff check src tests && ruff format --check src tests && mypy src
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-G7qByZ-auth-middleware-app-integration/`
