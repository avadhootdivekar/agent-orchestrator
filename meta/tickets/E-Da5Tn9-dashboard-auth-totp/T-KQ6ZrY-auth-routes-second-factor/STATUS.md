# STATUS

- ID: `T-KQ6ZrY-auth-routes-second-factor`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (lane C)
- Scope: `MVP` · Sprint: `S3` · Estimate: `3 d`

## This update
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented end to end.
  `auth/http/routes_second_factor.py` now holds `add_totp_routes` (flat `async def` E3-E7,
  registered through `register_route_builder`, R1a-clean; `routes.py` untouched).
  - **E3** (FULL -> 409; exactly one of `code`/`recovery_code`; session attempts counted by the route;
    clear-cookie at 0 attempts and on a stale identity), **E4** (FULL needs `current_password`,
    PARTIAL_ENROLL takes the optional token; pending secret only in the session), **E5**
    (transport first, then 409 `no_pending_enrollment`; the route counts wrong confirm codes via
    `record_confirm_failure`, as T-yfrfxv's STATUS requires; `rotate(...)` with the new epoch,
    `keep_absolute` only for a voluntary enrollment; `auth.login.success` when it completed a login),
    **E6**/**E7** (`rotate` keep-absolute; E6 -> `password`/`none`, E7 keeps the login method).
  - Decisions: (1) `LocalTotpService._require_transport` made public as `require_secure_transport`
    (docstring only otherwise) so E5 can apply it before its own 409; (2) `add_totp_routes` is
    idempotent per app (a module re-import registers a second builder; `test_routes_registry`
    pops the module from `sys.modules`); (3) test harness `real_routes.build_dash` gained
    `with_totp=True` (additive; default unchanged, so every existing test keeps `totp=None`);
    (4) the seam test's fake provider must also add `POST /api/auth/login`, because
    `assert_flat_auth_routes` demands every `AUTH_ROUTE_POLICIES` row -- another OQ-10 datum;
    (5) the hub half of `test_route_enumeration_full_config.py` uses the bare auth-only hub app (the
    hub-only rows are T-KOv2qD's `test_route_enumeration_hub.py`), cut-line #3 NOT taken.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate, lane and sprint unchanged (3 d, lane C, S3). Changes:
  - **Design-review M2:** the routes live in their own module `auth/http/routes_second_factor.py`
    (T-rpKCjP creates the registered stub; this task owns it), registered through the list-valued
    `register_route_builder`; the full-configuration enumeration is its own file.
  - **Security M2:** AC 11 adds the forwarded-headers case (AC-43 part): a loopback peer with
    `X-Forwarded-*` over http → 403 `insecure_transport`.
  - **Security M4:** merge edge to T-jVqH8w; this task is now co-critical.
  - **Design-review M3:** the redirect-shaped AC-10 test is cut-line #3, with a credential-shaped
    fallback.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: TASK.md aligned with the HLD v2 cross-check (HLD §28.8): the two provider-seam OPEN_QUESTIONs are recorded as HLD OQ-10 (non-blocking); added AC 14, the full-configuration route enumeration (AC-17 part).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Task **created in v2**. It is split out
  of T-rpKCjP, which was over the 3-day cap (developer D-4).
  - It owns the second-factor routes (E3 verify, E4/E5 enrollment, E6 disable, E7 regenerate) and
    the **redirect-shaped** provider-seam test (dev-critic C-5).
  - **New v2 behaviour covered:**
    - token-gated forced enrollment (dev-security #5);
    - `insecure_transport` for E4/E5/E7 (dev-security #4);
    - cookie **and** proof rotation (D25);
    - cross-realm replay via the global TOTP step (S7).
  - **OPEN_QUESTIONs, reported to the architect:**
    1. A production redirect provider has no way to add PUBLIC policy entries without editing
       `policy.py`.
    2. Under D25 a redirect callback cannot hand the session proof to the SPA.

    AC-10 works around both with an explicitly composed test app and a captured proof.
  - Design only; no code written.

## Evidence
By: developer · Role: developer · Date: 2026-10-05. Commands in the worktree, all with `.venv/bin/`:
- `python -m pytest -q tests/auth/test_routes_second_factor.py tests/auth/test_provider_seam.py tests/auth/test_route_enumeration_full_config.py` -> 142 passed (second-factor 122 incl. the 42-case
  contract table, seam 6, full-config enumeration 14).
- `python -m pytest -q tests/auth tests/ui --cov=agent_orchestrator.auth.http.routes_second_factor --cov-report=term-missing`
  -> 2689 passed, 2 skipped, 0 failed (before: 2539 passed in T-rpKCjP's evidence; no regression);
  `routes_second_factor.py` 139 statements, 0 missed, **100 %** (gate >= 90 %).
- `ruff check src tests` -> clean; `ruff format --check src tests` -> clean except the pre-existing generated
  `src/agent_orchestrator/_build_info.py`; `mypy src` -> only the 4 pre-existing `_version.py` errors.
- AC map (`tests/auth/test_routes_second_factor.py` unless noted): 1 `test_contract_conformance`
  (table-driven, one scenario per documented status/code of E3-E7 + `test_the_table_covers_...` pins
  the HLD matrix, key sets and order); 2 TOTP login rotation, 409 from FULL, fresh absolute deadline;
  3 cross-realm replay (`reason: replayed`, `attempts_remaining: 4`, next step OK); 4 five wrong E3
  codes (clear-cookie at 0, then 401, then login 429) and five wrong E5 codes (then 409); 5 recovery
  login (`used_recovery_code`, 9 remaining, reuse -> 401, one `auth.recovery_code.used`, amr
  `pwd,rcv,mfa`); 6 forced enrollment (missing/wrong/expired/used token -> uniform 401 bytes with
  lockout +1 via the guard, valid token once, E5 rotation + other sessions revoked + one login event);
  7 voluntary enrollment (400/401 counted/200, deadline kept, epoch +1); 8 sticky TOTP under `off`;
  9 disable (required policy, `totp_required` user, not enrolled, wrong password/code, TOTP and
  recovery code success with rotation); 10 regenerate; 11 `insecure_transport` (remote http 403 for E4/E5/E7,
  loopback / https / E2 login allowed, `X-Forwarded-For` / `Forwarded` / `X-Forwarded-Proto` /
  foreign `Host` from a loopback peer 403, trusted proxy + https passes); 12 E4 hygiene (no-store, secret
  only in the session record, absent from `users.json`, audit and log); 13
  `test_provider_seam.py` (302 -> cross-site 303 -> probe 200 with `provider == "test-redirect"`,
  AST import check); 14 `test_route_enumeration_full_config.py`; 15 registration (flat, once per app,
  none without a service, idempotent); 16 above.
- Also covered: store outage keeps the partial session and logs ERROR once (503), stale identity and
  a revocation race destroy the partial session with a clear cookie, handler-level state/proof
  re-checks (planted-session wrapper), recovery codes never in audit/users/log.

## Risks / Blockers
- None blocking. The OPEN_QUESTION above (HLD OQ-10) affects only future providers, not the MVP
  (new datum: every provider must register `POST /api/auth/login`, since `assert_flat_auth_routes`
  requires all `AUTH_ROUTE_POLICIES` rows). OQ-8 and OQ-9 are DECIDED. Zero slack: T-jVqH8w waits on
  this task's merge (HLD §24.3).
- The hub half of the full-configuration enumeration is the bare auth-only hub app; T-KOv2qD's hub
  file owns the hub-only rows (`/login`, `/auth-assets/{name}`, the index).
- Shared edits (additive): `LocalTotpService.require_secure_transport` (rename of a private method,
  no behaviour change) and `tests/auth/helpers/real_routes.py` (`with_totp` option).

## Next actions
1. reviewer: review. T-jVqH8w may merge `ao ui --auth` after this task has merged (HLD §24.3).
