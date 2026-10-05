# STATUS

- ID: `T-rpKCjP-auth-http-routes`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (lane Q; v2.1, was lane A)
- Scope: `MVP` · Sprint: `S2` · Estimate: `3 d` (v2.1; was 2.5 d)

## This update
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented end to end (v2.1 core
  routes only). `auth/http/routes.py`: callable list-valued builder registry
  (`register_route_builder`, `ROUTE_BUILDERS`), `install_auth_routes` enabled branch, core routes
  E1/E9/E10, local-password builder E2/E8, `user_payload`, `rotate`, `read_json_object`,
  `require_str`, `client_info`, `auth_error_handler`, `assert_flat_auth_routes`.
  `auth/http/routes_second_factor.py` is the registered no-op stub (T-KQ6ZrY owns it now). Routes
  are flat (`app.add_api_route`); handler annotations use module-scope `Request` (R1a).
  - **Deviation 1 (additive, HLD 11.18 note added):** `assert_flat_auth_routes(app, *, optional=())`.
    The three TOTP routes are an `optional` GROUP (all or none), keyed on the routes actually
    present instead of `runtime.totp is None`: a runtime whose TOTP service is wired (T-yfrfxv)
    before T-KQ6ZrY adds the routes would otherwise make `create_app` fail at startup.
  - **Deviation 2:** E2 warns "not encrypted" after a SUCCESSFUL login (any next state); a failed
    login does not use up the once-per-process warning.
  - **Deviation 3:** only the codes `not_authenticated`, `second_factor_required` and
    `enrollment_required` carry `WWW-Authenticate` (HLD 2.2 table); `invalid_credentials` does not.
  - **Observation (not changed, outside this task):** `SessionManager.lookup` does not compare
    `record.realm` with its own realm; cross-realm isolation (S3) rests on the cookie name plus a
    per-process session table, which holds for the in-memory store. A shared `SessionStore`
    (the future handoff seam) would need that check.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  - **Estimate 2.5 → 3 d; lane A → Q** (HLD §24.2/§24.3 rebalance, design-review minor 3).
  - **Security M2:** `client_info(scope, runtime)` with the strict loopback rule (loopback peer +
    loopback `Host` + no forwarding headers), `proxy_suspected`, one WARNING per process, and the
    additive E1 field `transport.proxy_suspected` (manager-approved contract change). New AC 13 =
    AC-43 (`test_client_info.py`, the security review's test gate 3).
  - **Design-review M2:** list-valued `register_route_builder`; this task creates
    `auth/http/routes_second_factor.py` as a registered no-op stub that T-KQ6ZrY then owns; the hub
    routes go to T-KOv2qD's `hub_routes.py`; real-route enumeration in its own
    `test_route_enumeration_real_routes.py` (T-G7qByZ's files are no longer edited here).
  - **Design-review minor 6:** rule R1a + `test_routes_annotations.py`.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: **v2 rescope.**
  - **Estimate and sprint:** 3 d / S2→S3 → 2.5 d / S2.
  - **Split** (developer D-4): the second-factor routes (E3–E7) and the provider-seam test moved to
    the new `T-KQ6ZrY-auth-routes-second-factor`. This task keeps the core routes (E1, E2, E8, E9,
    E10), the callable route-builder registry (reviewer R-5), `assert_flat_auth_routes` (developer
    D-1 BLOCKER: flat routes only), and the error handler with `cause_for_log` (reviewer R-10).
  - **New in v2:**
    - `session_proof` in the issuing bodies;
    - status and logout honour the proof (D25, dev-security #2);
    - `Clear-Site-Data` on logout (dev-security #7);
    - realm ids stable across ports (dev-critic C-2);
    - route-level revocation races (dev-security #1).
  - **Precisions, reported to the architect:**
    - E2 replaces the presented session only when `proof_ok`, so that a missing proof never
      destroys anything;
    - `read_json_object` gains an additive `optional_body` keyword for the optional E10 body.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the E-Da5Tn9
  design package (design and tickets only; **no code written**). It implements the frozen HLD §2
  contract exactly. It may start on a fake provider before T-XchniS merges.

## Evidence
Commands run in the worktree, all with `.venv/bin/`:
- `python -m pytest -q tests/auth/test_routes_core.py tests/auth/test_cookie_isolation.py tests/auth/test_revocation.py tests/auth/test_client_info.py tests/auth/test_routes_annotations.py tests/auth/test_route_enumeration_real_routes.py tests/auth/test_routes_registry.py`
  -> all pass (core 56, cookie isolation 8, revocation 4, client_info 27, annotations 7,
  real-routes enumeration 15, registry 20 = 137 tests).
- `python -m pytest -q tests/auth tests/ui` -> 2539 passed, 2 skipped, 0 failed (T-G7qByZ's
  stub-based `test_route_enumeration_dashboard.py` / `test_partial_confinement.py` unchanged, green).
- `python -m pytest -q tests/auth --cov=agent_orchestrator.auth.http.routes` -> `routes.py` 97 %
  (9 of 287 statements missed: defensive branches).
- `ruff check src/agent_orchestrator/auth/http tests/auth` -> clean; `ruff format --check` on every
  touched file -> clean.
- `mypy src` -> only the 4 pre-existing `_version.py` errors (none in `auth/`).

AC map: 1 contract table (`test_routes_core.py`: every status/code of E1/E2/E8/E9/E10, exact key
sets/order, 43-char proof), 2 login states, 3 uniform failure (identical body bytes + header-name
set), 4 status (partial/enroll/auth, 12-row policy x enrolled x required table, proof-less and
wrong-proof -> anonymous, idle deadline unmoved on `FakeClock`), 5 rotation/replay, 6 logout
without proof, 7 logout everywhere (full: epoch +1, other instance 401, one `auth.logout_all`;
partial: no bump, one `auth.logout`), 8 password change, 9 invalid requests (key named, sentinel
value never echoed; 10 000-deep JSON), 10 cookie isolation (Set-Cookie byte pins, `__Host-`
https, port-stable realm id, duplicate cookie), 11 revocation, 12 insecure-login warning, 13
`client_info` (a)-(g), 14 handler + registry (`test_routes_registry.py`), 15 real-routes
enumeration (allowlist minus the 3 TOTP rows, no stale entries, partial confinement), 16
annotation AST rule with negative control and the live 422 trap.

## Risks / Blockers
- None blocking. Follow-ups for T-KQ6ZrY: fill `add_totp_routes` (it is the only edit to
  `routes_second_factor.py` needed; `assert_flat_auth_routes` already demands all three TOTP rows
  once any is present), and reuse `user_payload`, `rotate`, `read_json_object`, `client_info`. It runs on lane Q in parallel with T-QJ1vyQ (lane C) after T-G7qByZ. Its proof cases
  depend only on the `proof_ok` value that T-G7qByZ computes. It is on the critical path (HLD
  §24.3). OQ-8 and OQ-9 are DECIDED.

## Next actions
1. reviewer: review; T-KQ6ZrY: implement E3-E7 in `routes_second_factor.py`.
