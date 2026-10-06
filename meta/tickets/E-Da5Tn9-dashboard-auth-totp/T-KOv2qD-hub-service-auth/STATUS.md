# STATUS

- ID: `T-KOv2qD-hub-service-auth`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (lane A)
- Scope: `MVP` · Sprint: `S2→S3` (v2.1; was S3) · Estimate: `3 d` (v2.1; was 2.5 d)

## This update
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented end to end (hub app only).
  - New `auth/http/hub_page.py` (pure) and `auth/http/hub_routes.py` (`register_hub_auth_routes`:
    flat `GET /`, `GET /login`, `GET /auth-assets/{name}`); `service/hub.py` gets
    `build_hub_app(..., *, auth=None)` with lazy auth imports, AuthMiddleware inserted just before
    SecurityMiddleware, `install_auth_routes`, L2 `redoc_url=None, swagger_ui_oauth2_redirect_url=None`
    only with auth on, and `_render_index_html(payload, principal=None)`; `pyproject.toml` wheel
    `artifacts` gains `auth/assets/**`.
  - Tests: `tests/service/test_hub_auth.py` (26), `tests/auth/test_route_enumeration_hub.py` (7),
    harness `tests/auth/helpers/hub.py` (`make_hub`, reuses `real_routes.build_dash(kind="hub")`).
  - **Deviation 1 (AC 4):** `/auth-assets/..%2Fusers.json` and `/auth-assets/` match no route
    (`{name}` is one segment), so deny-by-default answers an anonymous caller 401, not 404. Never
    served; the test asserts 401-or-404. `/auth-assets/x.js` is a real 404 with an empty body.
  - **Deviation 2 (pre-existing hub bug, fixed):** `GET /openapi.json` returned 500 on the hub (even
    auth off): the lazily imported `JSONResponse` return annotation of `api_status` is unresolvable
    under `from __future__ import annotations`. Fixed with `response_model=None` (served body
    unchanged); required for AC 6 ("`/openapi.json` with the proof -> 200").
  - **Deferred:** wheel listing (`uv build`) - agents must not run `uv`; CI/implementer runs the
    Verification command. The pyproject glob and `read_hub_asset` are asserted by tests.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  - **Estimate 2.5 → 3 d; sprint S3 → S2→S3** (lane A starts it late in S2, HLD §24.3).
  - **Design-review M2:** `register_hub_auth_routes` moves to a new `auth/http/hub_routes.py`
    owned by this task (`routes.py` is T-rpKCjP's alone); the hub enumeration is its own
    `test_route_enumeration_hub.py`.
  - **Security M1:** the hub index is the only `HUB_COOKIE_ONLY_NAVIGATION` route; new AC 6
    (= AC-44 hub part): cookie-only principal and browser-attested sliding on `GET /` only; every
    other hub route without the proof → no principal; `/docs` and `/openapi.json` need the proof.
  - **Security L2:** with auth on, `/redoc` and `/docs/oauth2-redirect` are not registered (AC 7);
    auth off unchanged (AC 1).
  - **Design-review minor 2:** T-QJ1vyQ is now a direct upstream.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: TASK.md aligned with the HLD v2 cross-check (HLD §28.8): hub enumeration now uses the HLD §13.2 `totp=None` configuration rule instead of `xfail`; T-KQ6ZrY owns the full-configuration check.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope after the Phase-4
  consultations (HLD §28).
  - **Narrowed to the hub app** (estimate 3 d → 2.5 d). The `ao service run` / probe / list /
    status / install hint, supervisor `child_env` + exit-78 handling and systemd edits moved to the
    new `T-PDGw9p-service-cli-supervisor-auth` (developer D-4: the v1 task exceeded the 3-day cap).
  - **`hub_page.py`** now lives in `auth/http/` (layering R4).
  - **`register_hub_auth_routes`** also registers the auth-on `GET /`, so `hub.py` keeps only lazy
    imports and avoids the `Request` annotation trap (developer D-2).
  - **Policies** come from `HUB_ROUTE_POLICIES` instead of decorators (reviewer R-7).
  - **The login page** gains the enrollment-token form (dev-security #5).
  - **`pyproject.toml` artifacts** are added unconditionally (reviewer R-12).
  - **Wheel listing:** agents must not run `uv`, so the implementer or CI produces it.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the
  E-Da5Tn9 design package (design and tickets only; **no code written**). It originally covered the
  hub, the `ao service` CLI, the supervisor and systemd edits (HLD §16 #5–#8); v2 keeps #5 and #16.

## Evidence
- By: developer · Role: developer · Date: 2026-10-05
  - `.venv/bin/python -m pytest -q tests/auth tests/ui tests/service` -> 2764 passed, 2 skipped.
  - `.venv/bin/python -m pytest -q tests/service/test_hub.py` (unmodified) -> 14 passed;
    `tests/service/test_hub_auth.py` -> 26 passed; `tests/auth/test_route_enumeration_hub.py` -> 7 passed.
  - `.venv/bin/ruff check src tests/...` clean; `ruff format --check` clean; `.venv/bin/mypy src` ->
    only the 4 pre-existing `_version.py` errors.
  - Wheel listing (`uv build --wheel && unzip -l ...`): NOT run (agents must not run uv); open for CI.

## Risks / Blockers
- None. Merge-order note: the hub enumeration uses the HLD §13.2 `totp=None` rule, so it is green
  whether or not T-KQ6ZrY has landed (AC 9). OQ-8 and OQ-9 are DECIDED.

## Next actions
1. (done 2026-10-05; only the CI wheel listing remains) developer: implement once T-rpKCjP, T-QJ1vyQ and T-R7JhTL land. Capture the auth-off golden
   HTML **before** editing `hub.py`, then run the verification.
