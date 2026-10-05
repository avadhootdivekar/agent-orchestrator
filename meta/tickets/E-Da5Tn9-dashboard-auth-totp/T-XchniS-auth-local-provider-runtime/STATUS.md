# STATUS

- ID: `T-XchniS-auth-local-provider-runtime`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (lane A)
- Scope: `MVP` · Sprint: `S2` · Estimate: `3 d`

## This update
- By: developer · Role: developer · Date: 2026-10-05 · Comment: **Done.** Implemented
  `auth/guard.py` (`AttemptSubject`, `AttemptResult`, `AttemptGuard.attempt`, the `ok_if` /
  `from_totp_outcome` / `from_recovery_outcome` adapters, `subject_for`, `lockout_call`),
  `auth/provider.py` additions (`ClientInfo` with `proxy_suspected`, `UserView`, `AuthProvider`
  ABC; `VerifiedIdentity` and `Revalidation` untouched), `auth/local_provider.py`
  (`LocalPasswordProvider`, `normalize_username`) and `auth/runtime.py` (`Realm`, `AuthRuntime`,
  `build_auth_runtime`, `runtime_of`, `audit_log_for`). Also did T-G7qByZ's S2 remainder (real
  `AuthRuntime`/`Realm` types in the middleware, `create_app` and `responses.py`; HTTP-edge fixture
  parametrized over stub and real). Decisions and notes for reviewers and downstream tasks:
  - **`normalize_username`** (NFKC, strip, lower) did not exist anywhere; it lives in
    `local_provider.py`. The CLI (T-j9dfsw) and routes should import it from there.
  - **`LocalPasswordProvider(..., *, realm=None)`**: one additive keyword (the realm label for the
    provider's own audit events: `second_factor_pending`, `password.changed`). `build_auth_runtime`
    passes `realm.id`.
  - **`Realm("ui", ...)` without `workspace_root` raises `ValueError`** (its id derives from it).
  - **Phantom gate key is capped** at `MAX_USERNAME_CHARS` so a huge junk name cannot pin memory in
    the 4096-entry gate table; the lockout bucket for malformed names is unchanged
    (`INVALID_USERNAME_BUCKET` digest).
  - **`name_digest` / `ensure_name_key` run through `run_sync`** (they can write when the key file
    was deleted at runtime), in addition to the calls the ticket lists.
  - **Lock timeouts and corrupt state map to `StoreUnavailableError`** (503, `cause_for_log` set)
    at the guard/provider boundary, so the HTTP layer logs once and never leaks a path.
  - **`startup_warnings` BLOCKED count excludes already-enrolled users**: an enrolled user is
    challenged under every policy (S9) and can log in, so "cannot log in" would be wrong for them.
  - `AuthRuntime.totp` is typed `Any | None` until T-yfrfxv lands `LocalTotpService` (L3 sibling
    module; avoids a forward import). T-yfrfxv should tighten the annotation.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate unchanged (3 d).
  - **Keyed phantom digests (security L1):** `authenticate` uses `lockouts.name_digest(uname)`
    (malformed names share the `INVALID_USERNAME_BUCKET` digest); the guard's `username_hash` is the
    digest prefix; `check_ready` ends with `lockouts.ensure_name_key()` so S6's one-write rule
    holds. AC 1, AC 3 and AC 4 updated.
  - **Parent warnings (security L6):** `startup_warnings()` includes the group-writable,
    euid-owned parent notices returned by `check_private_paths` / `check_state_dir`.
  - **Proxy detection plumbing (security M2):** `ClientInfo.proxy_suspected` (default `False`) and
    the stricter `is_loopback` meaning (computed by T-rpKCjP); `AuthRuntime.proxy_suspected_warned`.
  - `VerifiedIdentity` / `UserView` keep tuple roles (Principal decision A).
  - Cut-line #2 (phantom table) noted (HLD §24.1).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: TASK.md aligned with the HLD v2 cross-check (HLD §28.8): added `audit_log_for(request)` in `runtime.py`, `LockoutStore.check_readable()` in `check_ready`, AC-4b ownership (rows 6–8, 11, 13) and the exact HLD §11.15.3 warning strings; identity-guarded writes now use the single `store.with_identity` helper.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: **v2 rescope** after the Phase-4
  consultations.
  - **Split** (developer D-4, reviewer R-4): the second factor (verify, enrollment, disable,
    regenerate) moved to the new `T-yfrfxv-auth-provider-second-factor`. This task keeps the login
    side.
  - **New `guard.py`:** the single throttle → gate → lockout → verify → record → audit sequence.
    `BusyError` counts as an address failure (dev-security #3).
  - **Revocation** (dev-security #1, D10): an immutable `user_id`; the epoch is taken from the same
    snapshot as the verified hash; CAS rehash and `cas_mark_login`; tri-state `revalidate`.
  - **Policy** (dev-security #5): `totp_requirement()` decides `next_state`, and BLOCKED users get
    403 `totp_required`.
  - **Runtime:** stable realm ids (dev-critic C-2); `build_auth_runtime(provider=)` (reviewer R-5).
  - **Lockouts** live in `lockouts.json` in the state directory (reviewer R-7b), so failed logins no
    longer write `users.json`.
  - **Moved out:** `launch.py` / `prepare_auth` → T-jVqH8w. The capability protocols and the
    `"module:function"` registry were dropped (reviewer R-5).
  - **Task-level precision:** web credential writes (`set_password_hash`, `bump_epoch`) are guarded
    by `user_id` and epoch inside the mutate. HLD §11.15.6 keys them by username only. This is
    reported to the architect.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the E-Da5Tn9
  design package (design and tickets only; **no code written**). This task holds the core security
  logic (uniformity, lockout, replay, sticky enrollment). The verbatim pseudocode is in HLD §11.15.

## Evidence
- By: developer · Role: developer · Date: 2026-10-05 · Comment: commands run in the worktree
  (`.venv/bin/python`):
  - `python -m pytest -q tests/auth tests/ui -p no:warnings` -> `2299 passed, 2 skipped` (zero
    failures; `tests/ui` unmodified and green).
  - New suites: `test_guard.py` (25), `test_local_provider.py` (70), `test_runtime.py` (21),
    `test_event_loop.py` (4); the HTTP-edge tests now also run `[real]` (`-k real`: 36 in
    `test_middleware.py` alone).
  - Coverage (`--cov=agent_orchestrator.auth`): `guard.py` 100 %, `local_provider.py` 100 %,
    `provider.py` 100 %, `runtime.py` 100 % (AC 15 target 90 %).
  - `ruff check src tests` -> all checks passed; `ruff format --check src tests` -> 349 files
    already formatted; `mypy src` -> only the 4 pre-existing `_version.py` errors.
  - AST layer test (`test_import_boundary.py`) passes for the four modules.
  - Event loop (AC 13): `ThreadRecorder` shows zero watched calls on the loop thread for login
    success/failure/phantom/rehash/partial, re-auth, change password and logout-everywhere; a
    self-check test proves the harness flags a direct call on the loop.

## Risks / Blockers
- None. Residual (documented, dev-security #12): dummy-hash timing for users still on legacy scrypt
  parameters is not equalized.

## Next actions
1. T-yfrfxv: wire `LocalTotpService` into `build_auth_runtime` (`totp=`), reuse `guard.subject_for`,
   `ok_if`, `from_*_outcome`; append its scenarios to `tests/auth/test_event_loop.py`.
2. T-rpKCjP / T-jVqH8w: build on `runtime.py` (`build_auth_runtime` + `check_ready`).
