# TASK: T-KOv2qD-hub-service-auth

## Metadata
- Task ID: `T-KOv2qD-hub-service-auth`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane A)
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `3 days` (v2.1; was 2.5) · Sprint `S2→S3` (v2.1; started late S2 on lane A)

**v2 scope:** the hub **app** only. The `ao service` CLI, supervisor and systemd edits moved to
`T-PDGw9p-service-cli-supervisor-auth` (developer D-4: v1 exceeded the 3-day cap).

## Requirements Mapping
- Requirement IDs: FR-8 (cookie-only principal on the hub index only), FR-13 (hub
  deny-by-default), FR-15, FR-27, NFR-1 (auth-off hub HTML byte-identical), packaging (wheel ships
  `auth/assets/**`)
- ACs: AC-17 (hub part, incl. the v2.1 L2 doc-URL check), AC-19, **AC-44 (hub part; v2.1)**
- Invariants: S1 (hub), S17, S20, S27
- Design: HLD §2.4 ("Hub-only routes"), §10.5, §10.6 #2, §11.14 (`HUB_ROUTE_POLICIES`,
  `HUB_COOKIE_ONLY_NAVIGATION`), §11.17, §11.18 (ownership table: `http/hub_routes.py`,
  `http/hub_page.py`), §13.2 (hub rows, the cookie-only row, framework routes, test-file
  ownership), §13.3 steps 5/8/9, §14.9, §16 rows 5 and 16, §17.6, §20.3 #15; HLD D13;
  §28.9 (security M1, L2; design-review M2, minor 2); ADR-0021 D6, D11

## Description
1. **NEW `src/agent_orchestrator/auth/http/hub_page.py`.** L4 but **pure** (stdlib only; R1).
   - `HUB_ASSET_TYPES = {"hub-auth.js": "text/javascript; charset=utf-8", "hub-auth.css": "text/css; charset=utf-8"}`.
   - `render_login_page() -> str`: the HLD §17.6 skeleton **verbatim**, including
     `#ao-enroll-token-form` and `#ao-transport-warning`. It has no inline script, no `on*`
     attributes and no `<style>`.
   - `hub_head_tags() -> str`:
     `<link rel="stylesheet" href="/auth-assets/hub-auth.css"><script src="/auth-assets/hub-auth.js" defer></script>`.
   - `render_signed_in_bar(username, auth_method) -> str`: `html.escape` on both values; it contains
     `Signed in as <strong>{username}</strong>` and `<button type="button" id="ao-logout">`.
   - `read_hub_asset(name) -> bytes`: `KeyError` unless `name in HUB_ASSET_TYPES`; otherwise
     `importlib.resources.files("agent_orchestrator.auth").joinpath("assets", name).read_bytes()`.
2. **NEW `src/agent_orchestrator/auth/http/hub_routes.py`** (v2.1, design-review M2; owned only by
   this task — `auth/http/routes.py` is T-rpKCjP's and is **not** edited here):
   `register_hub_auth_routes(app, runtime, *, status_provider, render_index)`. All routes are
   **flat** (`add_api_route`, never `include_router`; developer D-1), and handler parameters are
   annotated with names imported at module scope (rule R1a):
   - `GET /` (AUTHENTICATED **by omission** from `HUB_ROUTE_POLICIES`; the **one**
     `HUB_COOKIE_ONLY_NAVIGATION` route, security M1):
     `HTMLResponse(render_index(status_provider(), current_principal(request)), headers={"Cache-Control": "no-store"})`.
     The anonymous and partial 303 → `/login` comes from the middleware (§13.3 step 7).
   - `GET /login` (PUBLIC): `HTMLResponse(render_login_page())` with `Cache-Control: no-store`.
   - `GET /auth-assets/{name}` (PUBLIC): `read_hub_asset(name)` with the exact
     `HUB_ASSET_TYPES[name]` content type. `KeyError` → 404 with no body detail.
   - This lives in the http layer, not in `service/hub.py`: the developer D-2 annotation trap
     means a nested `def index(request: Request)` under `from __future__ import annotations` plus a
     lazy import gets 422.
3. **`src/agent_orchestrator/service/hub.py`** (§16 row 5):
   - `build_hub_app(status_provider, *, auth: AuthRuntime | None = None)`. Every auth import is
     **lazy inside** the function (the existing AC1 at `tests/service/test_hub.py:130` keeps
     passing).
   - **(v2.1, security L2)** When `auth is not None`, construct
     `FastAPI(title=..., redoc_url=None, swagger_ui_oauth2_redirect_url=None)`; with `auth=None` the
     call is unchanged (byte-identical auth-off app).
   - Insert
     `app.add_middleware(AuthMiddleware, runtime=auth, policies=HUB_ROUTE_POLICIES, cookie_only_navigation=HUB_COOKIE_ONLY_NAVIGATION)`
     **immediately before** the existing `SecurityMiddleware` line, so Security stays outermost.
   - Then call `install_auth_routes(app, auth)`.
   - **Auth off (`auth is None`):** the existing `index()` and `api_status()` are registered exactly
     as today.
   - **Auth on:** `register_hub_auth_routes(app, auth, status_provider=status_provider, render_index=_render_index_html)`
     replaces the `index()` registration. `GET /api/service/status` stays as today; it is
     AUTHENTICATED (+ proof) by omission. The hub's default `/docs` and `/openapi.json` stay
     registered; they are AUTHENTICATED and, not being flagged cookie-only, need the proof
     (HLD §13.2).
   - `_render_index_html(payload, principal=None)`: with `principal is None`, the output is
     **byte-identical** to today's. Otherwise it inserts `hub_head_tags()` before `</head>` and
     `render_signed_in_bar(principal.username, principal.auth_method)` at the start of `<body>`.
4. **`pyproject.toml`** (§16 row 16): append `"src/agent_orchestrator/auth/assets/**"` to
   `[tool.hatch.build.targets.wheel].artifacts` **unconditionally**, next to `ui/static/**`
   (reviewer R-12). No dependency changes.
5. **Hub route enumeration** in its own file `tests/auth/test_route_enumeration_hub.py` (v2.1,
   design-review M2), using `tests/auth/helpers/enumeration.py` (T-G7qByZ). No other task edits it.
6. **Hub cookie-only check** (AC-44 hub part) in `tests/service/test_hub_auth.py`.

## Inputs / Outputs
- **Inputs:**
  - T-rpKCjP: `install_auth_routes`, `auth_error_handler`, and login/logout/status/keepalive.
  - T-QJ1vyQ (v2.1, design-review minor 2): the complete middleware (proof enforcement on non-API
    routes; the cookie-only exemption).
  - T-R7JhTL: the built `src/agent_orchestrator/auth/assets/hub-auth.js` and `hub-auth.css`.
  - T-G7qByZ: `AuthMiddleware`, `tests/auth/helpers/enumeration.py`.
  - T-kwwJ82: `HUB_ROUTE_POLICIES`, `HUB_COOKIE_ONLY_NAVIGATION`, `current_principal`.
- **Outputs:**
  - `src/agent_orchestrator/auth/http/hub_page.py`
  - `src/agent_orchestrator/auth/http/hub_routes.py` (v2.1)
  - edits to `src/agent_orchestrator/service/hub.py` and `pyproject.toml`
  - `tests/service/test_hub_auth.py`
  - `tests/auth/test_route_enumeration_hub.py` (v2.1)

## Acceptance Criteria
Tests build the runtime with
`build_auth_runtime(settings, Realm("hub", 8770), clock=FakeClock(...), entropy=SeededEntropy(1), hasher=FastFakeHasher())`
over a tmp store holding `alice`, and use `make_client` and `same_origin_headers`
(`tests/auth/helpers/core.py`).
1. **Auth off** (NFR-1):
   - `tests/service/test_hub.py` passes **unmodified**.
   - `_render_index_html(FIXED_PAYLOAD)` equals a golden string captured from the **pre-change**
     implementation and committed in `test_hub_auth.py`.
   - `build_hub_app(provider)` serves exactly that HTML at `GET /`, and still registers `/redoc`
     and `/docs/oauth2-redirect`.
2. **Anonymous with auth on** (AC-19):
   - `GET /` with `Accept: text/html` → 303 `Location: /login`;
   - `GET /` with `Accept: application/json` → 401 `not_authenticated`;
   - `GET /api/service/status` → 401.
3. **Login page:**
   - `GET /login` → 200 `text/html` with `Cache-Control: no-store`.
   - Parsed with `html.parser`: every `<script>` has a `src`, no attribute name starts with `on`,
     there is no `<style>` element, and `#ao-login-form`, `#ao-totp-form` and
     `#ao-enroll-token-form` are present.
4. **Assets:**
   - `/auth-assets/hub-auth.js` → 200, `content-type == "text/javascript; charset=utf-8"`, with a
     non-empty body;
   - `/auth-assets/hub-auth.css` → `text/css; charset=utf-8`;
   - `/auth-assets/x.js`, `/auth-assets/..%2Fusers.json` and `/auth-assets/` → 404.
5. **Signed in:**
   - After `POST /api/auth/login` with same-origin headers (E2 `authenticated`), a **cookie-only**
     `GET /` (no proof header) → 200, containing `Signed in as <strong>alice</strong>`,
     `id="ao-logout"` and the `hub-auth.js` script tag, with `Cache-Control: no-store`.
   - `GET /api/service/status` with the cookie and **no** proof → 401. With the proof → 200 and the
     unchanged status JSON.
6. **Cookie-only principal, hub part** (AC-44, S20, S27; security M1 and test gate 1), with a valid
   FULL cookie and **no** proof:
   - `GET /` → a principal is set (scope-capturing shim around `app.router`); with
     `Sec-Fetch-Mode: navigate`, `Sec-Fetch-Dest: document` and `Sec-Fetch-Site: same-origin`
     (or `none`) `last_activity_mono` advances; without those headers, or with `same-site`, it does
     not;
   - every other hub route context (enumerated) → `principal is None` and `last_activity_mono`
     unchanged;
   - `GET /docs` and `GET /openapi.json` → 303 `/login` with `Accept: text/html`, otherwise 401;
     with the proof → 200.
7. **Doc URLs (security L2):** with auth on, `/redoc` and `/docs/oauth2-redirect` are absent from
   `app.router.routes` (requests → 401/303 like any unknown route).
8. **Escaping:** `render_signed_in_bar("<b>x</b>", "password")` contains `&lt;b&gt;` and no
   `<b>`.
9. **Hub enumeration** (AC-17 hub part, `test_route_enumeration_hub.py`): with auth on, the hub's
   non-AUTHENTICATED `(method, template)` set equals the hub rows of HLD §13.2 **exactly**:
   - auth status, login and logout (PUBLIC);
   - `totp/verify` (PARTIAL_SECOND_FACTOR);
   - `enroll/begin` and `enroll/confirm` (ENROLLMENT);
   - `GET /login` and `GET /auth-assets/{name}` (PUBLIC).

   `GET /`, `GET /api/service/status`, `/docs` and `/openapi.json` are AUTHENTICATED; `GET /` is
   the only cookie-only route. This test builds the hub runtime with `totp=None`, so by the HLD
   §13.2 configuration rule the expected set **excludes** the three `/api/auth/totp/*` rows and the
   no-stale check skips them. This keeps the test green in either merge order. T-KQ6ZrY's
   `test_route_enumeration_full_config.py` covers the hub with them. The file skips with a reason
   when `iter_route_contexts` is unavailable.
10. **Packaging:**
    - `tests/service/test_hub_auth.py::test_wheel_artifacts_include_auth_assets` parses
      `pyproject.toml` with `tomllib` and asserts that the glob is in `artifacts`.
    - `read_hub_asset("hub-auth.js")` returns non-empty bytes.
    - The implementer records the wheel listing (Verification) in STATUS.
11. ruff and mypy are clean. `tests/service`, `tests/service/test_hub_auth.py` and
    `tests/auth/test_route_enumeration_hub.py` are green.

## Risks
- **Hub HTML regressions with auth off.** AC-1 (golden string) is the gate.
- **The annotation trap** (developer D-2). The auth-on `GET /` lives in `auth/http/hub_routes.py`.
  Never add a `Request`-typed nested handler inside `hub.py` (rule R1a).
- **Merge order with T-KQ6ZrY** (both S3). The §13.2 `totp=None` rule in AC-9 keeps either order
  green.
- **Cookie-only residual (A4).** A harvested hub cookie can still render the index (read-only
  HTML) until expiry; documented in HLD §6.3 A4. Do not flag any other hub route.

## Dependencies
- **Upstream:**
  - direct (HLD §24.2 #14): T-rpKCjP, T-R7JhTL, **T-QJ1vyQ** (v2.1, design-review minor 2);
  - transitive: T-G7qByZ, T-kwwJ82.
- **Downstream:** T-PDGw9p (`build_hub_app(auth=)`), T-U2ERMo (browser smoke and hub e2e).

## Pseudocode / Algorithm

```
build_hub_app(status_provider, *, auth=None):
  app = FastAPI(title=...) IF auth IS None
        ELSE FastAPI(title=..., redoc_url=None, swagger_ui_oauth2_redirect_url=None)          # v2.1, L2
  lazy-import AuthMiddleware, HUB_ROUTE_POLICIES, HUB_COOKIE_ONLY_NAVIGATION, install_auth_routes,
              register_hub_auth_routes (from auth/http/hub_routes.py)
  app.add_middleware(AuthMiddleware, runtime=auth, policies=HUB_ROUTE_POLICIES,
                     cookie_only_navigation=HUB_COOKIE_ONLY_NAVIGATION)          # added first = inner
  app.add_middleware(SecurityMiddleware, allowed_hosts=resolve_allowed_hosts())   # unchanged line = outer
  install_auth_routes(app, auth)                    # off: GET /api/auth/status (disabled body) only
  IF auth IS None: register today's index() and api_status() unchanged
  ELSE: register_hub_auth_routes(app, auth, status_provider=..., render_index=_render_index_html); register api_status() unchanged
  RETURN app
```

Hub login flow: HLD §14.9.

## Schemas / Interface Notes
- Hub routes and policies: HLD §2.4 and §13.2. Content types: `HUB_ASSET_TYPES`.
- `register_hub_auth_routes` signature: HLD §11.18 (frozen); module `auth/http/hub_routes.py`.

## Handoff Boundary
- **Upstream:** the auth routes, the complete middleware and the built hub asset.
- **Downstream:** a protected hub app. The service CLI wiring is T-PDGw9p's.

## Verification

```
python -m pytest -q tests/service tests/auth/test_route_enumeration_hub.py
ruff check src tests && ruff format --check src tests && mypy src
# Wheel listing. Run by the implementer outside the agent harness (agents must not run uv), or by CI:
uv build --wheel && unzip -l dist/agent_orchestrator-*.whl | grep -E 'agent_orchestrator/auth/assets/hub-auth\.(js|css)'
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-KOv2qD-hub-service-auth/`
