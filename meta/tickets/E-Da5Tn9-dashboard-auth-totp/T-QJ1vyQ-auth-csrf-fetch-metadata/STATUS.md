# STATUS

- ID: `T-QJ1vyQ-auth-csrf-fetch-metadata`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (lane C; v2.1, was lane B)
- Scope: `MVP` · Sprint: `S2` · Estimate: `2.5 d` (v2.1; was 2 d)

## This update
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented end to end; State Done.
  - `auth/http/origin.py` (new, pure): `origin_matches_host` per HLD 11.18. Unparseable IPv6 in the
    `Host` header (`[::1`) is rejected, not raised.
  - `auth/http/middleware.py`: filled `_check_csrf` (step 2), `_cap_auth_body` (step 3, buffers up
    to `MAX_AUTH_BODY_BYTES` and replays one message; a disconnect while buffering is passed on)
    and the step-5 enforcement branch (`proof_required(...) and session and not attested` -> the
    session is `None` for this request only; no `destroy`, no clear-cookie). `AUTH_API_PREFIX`
    imported from `constants`. Hook docstrings updated. `ui/app.py` and `ui/security.py` untouched.
  - Tests: `tests/auth/{test_origin,test_csrf,test_proof,test_body_cap,test_cookie_only_principal}.py`
    plus the probe-app helper `tests/auth/helpers/edge.py` (owner T-QJ1vyQ; the helpers `__init__`
    table was not edited to avoid a clash with the T-CsT5gk edit; add one row when convenient).
  - Parity list (AC 2; observed `(ui.security._is_origin_allowed, origin_matches_host)`, allowlist
    `{127.0.0.1, localhost, testserver}`): 14 agreeing cases; documented differences:
    allowlisted host != Host `http://localhost:8765` vs `127.0.0.1:8765` -> (True, False);
    `allowed=None` (`http://evil.example:1`, `null`) -> (True, False); default-port spellings
    `http://testserver:80`/`Host testserver`, `http://testserver`/`Host testserver:80`,
    `https://testserver:443` -> (False, True); `[::1]` not in the allowlist -> (False, True);
    path / query / fragment on the origin -> (True, False).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  - **Estimate 2 → 2.5 d; lane B → C** (HLD §24.2 rebalance, design-review minor 3).
  - **Security M1:** the enforcement branch now uses `attested = proof_ok or cookie_only`; the proof
    is required on every non-PUBLIC route (API or not) except the app's `COOKIE_ONLY_NAVIGATION`
    routes. AC 7 rewritten; new AC 8 = AC-44 dashboard part (`test_cookie_only_principal.py`, the
    security review's test gate 1).
  - **Security M4:** T-jVqH8w merges only after this task (merge edge).
  - **Design-review minor 2:** the T-QJ1vyQ → T-KOv2qD edge is now drawn in HLD §24.3.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: TASK.md aligned with the HLD v2 cross-check (HLD §28.8): `AUTH_API_PREFIX` is now an HLD §12.6 constant owned by T-kzEzwy; import it.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Task **created in v2**. It is split out
  of T-G7qByZ, which was over the 3-day cap (developer D-4).
  - It owns HLD §13.3 step 2 (Origin required on every mutation; Fetch Metadata with the navigation
    exemption), step 3 (the auth body cap) and step 5 (session-proof **enforcement**, D25,
    dev-security #2), plus the pure `http/origin.py` with a parity test against `ui/security.py`
    (reviewer R-2, which was partly adopted).
  - T-G7qByZ's hook already *computes* `proof_ok` for the E1/E10 routes. This task adds only the
    enforcement branch.
  - **Gap found:** `AUTH_API_PREFIX`, used in §13.3, is missing from §12.6. This task defines it.
    Reported to the architect.
  - Design only; no code written.

## Evidence
- By: developer · Role: developer · Date: 2026-10-05 · Comment: commands from the worktree root:
  - `.venv/bin/python -m pytest -q tests/auth/test_origin.py tests/auth/test_csrf.py
    tests/auth/test_proof.py tests/auth/test_body_cap.py tests/auth/test_cookie_only_principal.py
    -p no:warnings` -> 61 + 81 + 8 + 18 + 14 passed.
  - `.venv/bin/python -m pytest -q tests/auth tests/ui -p no:warnings --cov=agent_orchestrator.auth.http`
    -> `1924 passed, 2 skipped` (T-G7qByZ suites and `tests/ui` unmodified, enforcement on).
    Coverage: `origin.py` 100 %, `middleware.py` 100 %, `responses.py` 100 %.
  - `.venv/bin/ruff check` and `ruff format --check` on every touched file: clean.
  - `.venv/bin/mypy src`: only the 4 pre-existing `_version.py` errors.
  - Mutation check: turning the step-5 enforcement into a no-op made 7 tests fail
    (`test_proof.py`, `test_cookie_only_principal.py`); restored.

## Risks / Blockers
- None. OQ-9 is DECIDED (D25 in the MVP), so the enforcement branch ships. It starts when
  T-G7qByZ's skeleton lands, and runs on lane C after T-yfrfxv.

## Next actions
1. Downstream: T-KOv2qD reuses the complete middleware; T-jVqH8w may merge `--auth` now (security M4); T-U2ERMo browser smoke AC-34.
