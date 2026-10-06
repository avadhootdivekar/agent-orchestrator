# STATUS

- ID: `T-yfrfxv-auth-provider-second-factor`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (lane C)
- Scope: `MVP` · Sprint: `S2` · Estimate: `2 d`

## This update
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented
  `src/agent_orchestrator/auth/totp_service.py` (`LocalTotpService`, `classify_code`, the three result
  types with every secret-bearing field `repr=False`), wired `AuthRuntime.totp` (now typed
  `LocalTotpService | None`) in `runtime.py` via `provider.guard`, and added a read-only `guard`
  property on `LocalPasswordProvider` (so an injected provider's service shares its guard).
  Decisions: `regenerate_recovery_codes` returns `(epoch, codes)` per HLD 11.15.6; `confirm` with no
  pending secret is `INVALID_REQUEST`; `begin` on a PARTIAL_SECOND_FACTOR session raises that
  state's denial code; a malformed/missing code or token is a counted failure with no store write;
  `NotEnrolledError` raced into a consume maps to `NOT_AUTHENTICATED` (login) or
  `TOTP_NOT_ENROLLED` (disable/regenerate). The constructor takes an extra keyword-only
  `realm` (audit label only), like the provider.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate unchanged (2 d); no logic change. `ClientInfo.is_loopback` now means loopback peer
  **and** loopback `Host` **and** no forwarding headers (security M2), computed by T-rpKCjP's
  `client_info`; this service only reads it. Its unit tests keep constructing `ClientInfo`
  directly; the header matrix is AC-43 (T-rpKCjP, T-KQ6ZrY).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: TASK.md aligned with the HLD v2 cross-check (HLD §28.8): guarded web writes now use `store.with_identity`; a missing enrollment token is a counted failed attempt; the token is consumed by `begin`.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Task **created in v2**. It is split out
  of T-XchniS, which was over the 3-day cap (developer D-4) and too large (reviewer R-4).
  - It holds `LocalTotpService`: second-factor verify, enrollment begin/confirm, disable and
    regenerate. Every credential check goes through the shared `AttemptGuard`.
  - **New v2 behaviour:**
    - forced enrollment needs the CLI-issued enrollment token (dev-security #5, D7);
    - E4, E5 and E7 are refused over plain HTTP from non-loopback clients (dev-security #4);
    - consumes are keyed by `user_id` and epoch, and STALE → 401 (dev-security #1, D10).
  - **Task-level precisions,** reported to the architect: the `classify_code` rule for the single
    `code` field of E6/E7; the identity guard on web-path `remove_totp`/`replace_recovery_codes`; and
    `begin_enrollment` checking `result.ok`.
  - Design only; no code written.

## Evidence
- `.venv/bin/python -m pytest -q tests/auth/test_totp_service.py tests/auth/test_event_loop.py tests/auth/test_runtime.py --cov=agent_orchestrator.auth.totp_service --cov-report=term` -> 128 passed; `totp_service.py` 197 stmts, 0 missed, 100 % (gate >= 90 %).
- `.venv/bin/python -m pytest -q tests/auth tests/ui` -> 2402 passed, 2 skipped, 1 failed. The one failure is
  `tests/auth/test_smoke_tmp.py::test_smoke`, an uncommitted scratch file of the concurrent T-rpKCjP
  work (not mine); no failure in any file this task touches.
- `.venv/bin/ruff check` + `ruff format --check` on the 8 touched files -> clean (a repo-wide check also flags
  only T-rpKCjP's uncommitted `tests/auth/helpers/real_routes.py` / `test_smoke_tmp.py`).
- `.venv/bin/mypy src` -> only the 4 pre-existing `_version.py` errors.
- AC map: 1-2 verify/recovery, 3-5 begin (FULL, PARTIAL_ENROLL token, rejections), 6 confirm, 7 disable,
  8 regenerate, 9 sticky TOTP, 11 no-secrets (repr + audit-file sentinel grep) in
  `tests/auth/test_totp_service.py`; 10 event loop in `tests/auth/test_event_loop.py` (verify, begin-token +
  confirm, disable + regenerate); 12 wiring in `tests/auth/test_totp_service.py` (+ `test_runtime.py`
  default graph now asserts a `LocalTotpService`).
- Also covered: STALE not counted, replay across steps, lockout short-circuit, BusyError from the hasher (no lockout
  entry), store lock timeout -> 503, corrupt store -> 503, CAS-stale/AlreadyEnrolled races.

## Risks / Blockers
- None. By design the enrollment token is consumed by `begin`; an abandoned enrollment needs a fresh operator token (HLD 11.15.6).

## Next actions
1. T-KQ6ZrY: build the second-factor routes over this service (route counts the pending confirm attempts).
