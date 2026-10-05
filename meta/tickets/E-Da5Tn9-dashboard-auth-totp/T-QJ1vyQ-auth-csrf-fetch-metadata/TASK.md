# TASK: T-QJ1vyQ-auth-csrf-fetch-metadata

## Metadata
- Task ID: `T-QJ1vyQ-auth-csrf-fetch-metadata`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane B)
- Created: `2026-10-05`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `2 days` · Sprint `S2`

## Requirements Mapping
- Requirement IDs: FR-19, FR-27, NFR-1
- ACs: AC-23 (CSRF matrix), AC-35 (middleware part), AC-41 (origin-parity part)
- Invariants: S4, S21
- Design: HLD §13.3 steps 2, 3 and 5; §11.17 (split); §11.18 (`http/origin.py`); §2.1; §2.3;
  §10.4; §20.3 #3/#5; §1 D4, D25; ADR-0021 D2, D11

## Description
This task fills the three hooks that T-G7qByZ left in `auth/http/middleware.py`, and adds the pure
`http/origin.py`.
- **`auth/http/origin.py`:** `origin_matches_host(origin, *, scheme, host_header)`, exactly as in
  HLD §11.18. It rejects `@` and `null`, needs a scheme in {http, https} equal to the request
  scheme, allows only an empty or `/` path with no query or fragment, and compares the host
  (lower-cased, brackets stripped) and the port (default ports normalized through
  `http.client.HTTP_PORT` / `HTTPS_PORT`).
  - `ui/security.py` is **not** switched to it in this epic (NFR-1). The parity test documents the
    differences.
- **Step 2, `_check_csrf`** (every route, PUBLIC included):
  - for `MUTATING_METHODS` (imported from `ui.security`): a missing `Origin` → 403
    `origin_required`, and an `Origin` other than the request `Host` → 403 `origin_mismatch`;
  - Fetch Metadata: `Sec-Fetch-Site` present and not in {`same-origin`, `none`}, on a non-PUBLIC
    route or any mutation → 403 `cross_site_request`, **except** a top-level GET/HEAD navigation
    (`Sec-Fetch-Mode: navigate` + `Sec-Fetch-Dest: document`).
- **Step 3, `_cap_auth_body`:** mutating requests to `/api/auth/*`.
  - `Content-Length > MAX_AUTH_BODY_BYTES` → 413 `body_too_large`.
  - Otherwise buffer up to the cap; a chunked body over the cap → 413.
  - Replay the buffered body to the app unchanged.
  - Other paths are not touched.
- **Step 5, `_check_proof` enforcement:** T-G7qByZ already sets `proof_ok`. Add: if
  `proof_required(policy, is_api_path(rpath))` and a session exists without a matching proof, the
  session is `None` **for this request only**.
  - It **never** calls `sessions.destroy`, and **never** sets `clear_cookie`.
  - The policy decision then yields 401 `not_authenticated`.
  - Non-API routes (navigations) never require the proof.
- **Constant:** HLD §13.3 step 3 uses `AUTH_API_PREFIX`. It is now listed in §12.6
  (`API_PREFIX + "/auth"`) and defined by T-kzEzwy. Import it; do not redefine it.

## Inputs / Outputs
- **Inputs:** T-G7qByZ (middleware skeleton and hooks, `responses.py`, `StubRuntime`,
  `install_stub_auth_routes`); T-kwwJ82 (`proof_required`, `SessionManager.proof_matches`).
- **Outputs:**
  - `src/agent_orchestrator/auth/http/origin.py`
  - the step 2/3/5 code in `auth/http/middleware.py`
  - `tests/auth/test_origin.py`, `test_csrf.py`, `test_proof.py`, `test_body_cap.py`

## Acceptance Criteria
1. **`origin_matches_host`, unit table** (`test_origin.py`):
   - True for same scheme + host + port;
   - True for `http://h` vs `Host: h:80`, and `https://h:443` vs `Host: h` (scheme https);
   - True for `http://[::1]:8765` vs `Host: [::1]:8765`, and for different host case;
   - False for another port, scheme or host;
   - False for `null`, `http://u@h:8765`, `http://h:8765/x`, `http://h:8765?q`, an empty string and
     garbage.
2. **Origin parity** (AC-41 part). A fixed list of at least 12 same-origin and cross-origin cases,
   with the allowlist `{"127.0.0.1", "localhost", "testserver"}`: `origin_matches_host` agrees with
   `ui.security._is_origin_allowed` on every case **not** in the documented-differences list. Each
   documented difference is asserted explicitly with its observed value from each function. These
   include:
   - `allowed=None`, where `_is_origin_allowed` is always True;
   - an allowlisted host that differs from the `Host` header, e.g. `http://localhost:8765` vs
     `Host: 127.0.0.1:8765`;
   - default-port spellings.
3. **CSRF matrix** (AC-23, S4, `test_csrf.py`). With a full session and proof, against a probe
   `POST /api/__probe` (AUTHENTICATED, returns 204), PUT/PATCH/DELETE probes, and the PUBLIC
   `POST /api/auth/login` (stub):
   - no `Origin` → 403 `origin_required`, for public routes as well;
   - a same-origin `Origin` → passes the middleware (probe 204);
   - another port, scheme or host, `null`, or userinfo:
     - with `allowed_hosts=frozenset({"testserver"})`, `SecurityMiddleware` answers first: 403
       plain text `cross-origin request rejected`;
     - with `allowed_hosts=None`, `AuthMiddleware` answers 403 `origin_mismatch` JSON.

     **Both configurations are tested.**
4. **Fetch Metadata** (AC-23):
   - `GET /api/runs` with `Sec-Fetch-Site: same-site` or `cross-site` → 403 `cross_site_request`;
   - the same on an AUTHENTICATED non-API probe page with `Sec-Fetch-Mode: navigate` and
     `Sec-Fetch-Dest: document` → allowed;
   - `same-origin`, `none` or absent → allowed;
   - `GET /api/health` (PUBLIC) with `cross-site` → allowed;
   - a mutating request with `cross-site` and a same-origin `Origin` → 403 `cross_site_request`.
5. **Body cap** (`test_body_cap.py`). The test app uses a custom policy table that adds a PUBLIC
   echo route `POST /api/auth/__echo`:
   - `Content-Length: 20000` → 413 `body_too_large`, and the route is not invoked;
   - a chunked body of 16 KiB + 1 → 413;
   - exactly 16 KiB → passes;
   - a 1 KiB body is echoed byte-identical;
   - `POST /api/runs` with 20 KiB is **not** capped by this rule.
6. **Proof** (AC-35 middleware part, S21, `test_proof.py`). For **every** AUTHENTICATED `/api` route
   context of the dashboard app (with stub or real auth routes), with a FULL session:
   - (a) no proof, (b) a wrong proof of valid length, or (c) a malformed proof (wrong length or bad
     base64) → 401 `not_authenticated`. The session is **still in the table** afterwards. There is
     **no** clear-cookie header, and `sessions.destroy` (spied) is never called.
   - (d) the correct proof → passes the middleware, so the status is not 401.
7. **Proof, other policies:**
   - a PARTIAL_SECOND_FACTOR session without a proof on `POST /api/auth/totp/verify` → 401
     `not_authenticated`;
   - PUBLIC routes (health, status, login, logout) are reached without a proof;
   - an AUTHENTICATED **non-API** navigation probe with a FULL cookie and no proof → passes. This is
     the rule the hub index relies on; the real hub check is AC-19 in T-KOv2qD.
8. **No regressions:** `tests/ui` passes unmodified. The T-G7qByZ suites (which already send proofs
   and same-origin headers) pass with enforcement on.
9. ruff and mypy are clean. Coverage of `http/origin.py` is 100 % and the step 2/3/5 code is ≥ 90 %.

## Risks
- **Double 403 sources** (`SecurityMiddleware` vs `AuthMiddleware`). AC-3 pins which layer answers
  in each configuration.
- **A body replay bug breaks routes.** AC-5 includes a byte-identical echo.

## Dependencies
- **Upstream:** T-G7qByZ (hard), T-kwwJ82.
- **Downstream:** T-KOv2qD (the hub reuses the full middleware), T-U2ERMo (browser smoke AC-34:
  cross-port proof check). The frontend lanes rely on the §2.1 rules but do not depend on this code.

## Pseudocode / Algorithm
HLD §13.3 steps 2, 3 and 5, verbatim. `origin_matches_host` follows the §11.18 comment exactly.

## Schemas / Interface Notes
- Error codes: `origin_required`, `origin_mismatch`, `cross_site_request`, `body_too_large`,
  `not_authenticated` (HLD §2.2).
- The header name comes from `SESSION_PROOF_HEADER` (§12.6).

## Handoff Boundary
- **Upstream:** the middleware skeleton with hooks.
- **Downstream:** the complete §13.3 middleware. After this task and T-G7qByZ, no further middleware
  steps are planned in the MVP.

## Verification

```
python -m pytest -q tests/auth/test_origin.py tests/auth/test_csrf.py tests/auth/test_proof.py tests/auth/test_body_cap.py
python -m pytest -q tests/auth tests/ui
ruff check src tests && ruff format --check src tests && mypy src
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-QJ1vyQ-auth-csrf-fetch-metadata/`
