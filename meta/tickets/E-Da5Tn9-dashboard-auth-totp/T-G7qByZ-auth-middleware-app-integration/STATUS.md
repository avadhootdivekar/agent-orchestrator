# STATUS

- ID: `T-G7qByZ-auth-middleware-app-integration`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (lane C)
- Scope: `MVP` · Sprint: `S1→S2` · Estimate: `3 d`

## This update
- By: developer · Role: developer · Date: 2026-10-05 · Comment: **S2 final wiring done** (with
  T-XchniS). `AuthRuntimeLike` / `_ProviderLike` (middleware) and `RealmLike` (responses) are gone;
  `AuthMiddleware`, `create_app(auth=...)` and the response builders are annotated with the real
  `runtime.AuthRuntime` / `runtime.Realm` through `TYPE_CHECKING` imports (no new import-time
  edge, auth-off dashboards never import `runtime`). `tests/auth/conftest.py::stub_runtime` is
  parametrized over `stub` and `real` (`tests/auth/helpers/real_runtime.py`: `RealRuntime` is an
  `AuthRuntime` subclass over `build_auth_runtime` with a real `LocalPasswordProvider` and a real
  `users.json`; `issue_session` was factored out of `StubRuntime.issue` so both share it). Every
  HTTP-edge test (middleware, partial confinement, route enumeration, auth-off) now runs against
  both. Only the `[real]`/`[stub]` ids differ from before; no test body changed.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: **S1 (stub-runtime phase) done;
  the S2 final wiring is the explicit remainder.** Implemented `auth/http/{__init__,responses,
  middleware,routes}.py`, the `ui/app.py` edits (§16 #1 a, b, c, f, h; g is a test), and the
  tests and helpers listed in TASK.md Outputs. Notes for reviewers and downstream tasks:
  - `Revalidation` (HLD 11.15.1, exactly as specified) was added to `auth/provider.py` because
    step 6 branches on it; T-XchniS extends that module and must not redefine it.
  - Until T-XchniS lands, the middleware and `create_app(auth=...)` are typed against a local
    `AuthRuntimeLike` / `RealmLike` Protocol (same attribute names as HLD 11.16).
  - `install_auth_routes` with an enabled runtime raises `NotImplementedError` (fail closed, marked
    `TODO(T-rpKCjP)`); tests swap in `stub_install_auth_routes` by monkeypatching
    `ui.app.install_auth_routes` (fixture `build_dashboard`).
  - Hooks for T-QJ1vyQ in `middleware.py`: `_check_csrf` (step 2), `_cap_auth_body` (step 3) and
    the marked enforcement line in `_check_proof` (step 5, returns `ProofCheck`).
  - Duplicate-cookie WARNING is once per middleware instance (one per app).
  - **Remainder (about 0.5 d, after T-XchniS):** switch the constructor/`create_app` annotations
    to the real `AuthRuntime`/`Realm` (TYPE_CHECKING import), parametrize `tests/auth/conftest.py`
    fixtures over `StubRuntime` and `build_auth_runtime(...)`, rerun mypy/ruff. Coverage target
    met already.

- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate, lane and sprint unchanged (3 d, lane C, S1→S2). Changes:
  - **Principal (owner decision, design-review B1 / security M5):** AC-11 now asserts
    `isinstance(p.roles, list)`, `hash(p)` works, and two requests never share the list.
  - **Security M1:** step 5 computes `proof_ok`, `cookie_only` and `attested`; step 8 sets the
    principal only when attested; step 9 slides only with the proof (mutations, keepalive) or for a
    browser-attested navigation to a `COOKIE_ONLY_NAVIGATION` route. New
    `AuthMiddleware(..., cookie_only_navigation=...)` with construction checks; new ACs for
    logout/health without the proof (AC-44 part).
  - **Design-review minor 1:** deterministic `classify` for PARTIAL matches (`sorted` methods) +
    unit tests on HEAD, `/redoc`, `/docs/oauth2-redirect`.
  - **Security L2:** `create_app` drops `/redoc` and `/docs/oauth2-redirect` only when auth is on;
    AC-2 adds the OpenAPI path-key diff.
  - **Design-review M2 / minor 5:** helpers in `tests/auth/helpers/stub_runtime.py` and
    `helpers/enumeration.py` (importorskip-style guard, no FastAPI floor bump); enumeration file is
    `test_route_enumeration_dashboard.py`, owned only by this task.
  - **Design-review minor 4:** the p95 benchmark (NFR-5, now a target) moved to T-U2ERMo.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: **v2 rescope.**
  - **Lane and sprint:** lane B / S2 → lane C / S1→S2. The task builds against a duck-typed stub
    runtime in S1. Only the final wiring (about 0.5 d) waits for T-XchniS; this is the critical-path
    assumption in HLD §24.3.
  - **Classification** uses the per-app **policy table** instead of `@public_route` decorators.
    Auth routes are flat (developer D-1 BLOCKER: `include_router` hides route objects; reviewer R-7).
    `ui/app.py` gets no decorator edits.
  - **Split** (developer D-4): CSRF/Fetch Metadata, the body cap and proof enforcement moved to the
    new `T-QJ1vyQ-auth-csrf-fetch-metadata`. This task leaves marked hooks for them.
  - **New here:**
    - byte-exact `http/responses.py` shared with the routes (reviewer R-9);
    - duplicate-cookie rejection (dev-security #2);
    - tri-state revalidation, where UNAVAILABLE → 503 and the session is kept (developer D-7);
    - `root_path`-safe paths (dev-security #8);
    - `no-store` on every non-PUBLIC response (dev-security #7);
    - navigation sliding restricted to same-origin or typed navigations;
    - the Principal v2 shape (OQ-8).
  - **Precision, reported to the architect:** this task's step-5 hook *computes* `proof_ok` (which
    the E1/E10 routes need), and T-QJ1vyQ adds only the enforcement branch. This removes a hidden
    T-rpKCjP → T-QJ1vyQ dependency.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the E-Da5Tn9
  design package (design and tickets only; **no code written**). This task enforces deny by
  default. The exact allowlist is in HLD §13.2.

## Evidence
- By: developer · Role: developer · Date: 2026-10-05 · Comment: S2 remainder, worktree
  `.venv/bin/python`: `python -m pytest -q tests/auth tests/ui -p no:warnings` -> `2299 passed,
  2 skipped` (tests/ui unmodified: 629 passed, 2 skipped); `-k real` on `test_middleware.py` ->
  36 passed; `ruff check src tests` clean, `ruff format --check src tests` clean, `mypy src` only
  the 4 pre-existing `_version.py` errors.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: commands run in the worktree
  (all from the repo root with `.venv/bin/python`):
  - `python -m pytest -q tests/ui -p no:warnings` -> `593 passed, 2 skipped` (unmodified; same
    count as before the change).
  - `python -m pytest -q tests/auth/test_middleware.py tests/auth/test_route_enumeration_dashboard.py
    tests/auth/test_partial_confinement.py tests/auth/test_auth_off_regression.py
    tests/auth/test_responses.py` -> all pass (47 middleware, 24 enumeration, 6 partial confinement, 13 auth-off, 40 responses);
    together with `tests/ui`, import-boundary, foundation, policy, principal and sessions suites:
    `1085 passed, 2 skipped`.
  - Coverage (`--cov=agent_orchestrator.auth.http`): `middleware.py` 98 %, `responses.py` 100 %,
    `routes.py` 94 %.
  - `ruff check` / `ruff format --check` on all touched src/tests files: clean.
  - `mypy src`: only the 4 pre-existing `_version.py` errors.
  - Mutation checks (temporarily broke step 8 `attested` gating, step 9 `proof_ok` gating, and
    `browser_nav` gating): each made the named tests fail; sources restored.
  - Auth-off byte-parity: header-name sets for `/api/health`, `/api/runs`, `/` captured from
    pre-change `create_app` and hard-coded in `test_auth_off_regression.py`; OpenAPI path keys
    differ by exactly `/api/auth/status`; `/redoc` and `/docs/oauth2-redirect` still 200.

## Risks / Blockers
- None. OQ-8 is DECIDED (`roles: list[str]`, `hash=False`, keyword-only additive fields) and OQ-9
  is DECIDED (D25 in the MVP).

## Next actions
1. T-rpKCjP: replace the `NotImplementedError` branch of `install_auth_routes` (the T-QJ1vyQ hooks
   landed in c941cc0).
