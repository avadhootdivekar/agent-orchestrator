# TASK: T-XchniS-auth-local-provider-runtime

## Metadata
- Task ID: `T-XchniS-auth-local-provider-runtime`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane A)
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `3 days` · Sprint `S2`

## Requirements Mapping
- Requirement IDs: FR-2 (`check_ready`), FR-3 (CAS rehash), FR-7, FR-9 (`next_state`), FR-16, FR-17,
  FR-18 (password re-auth), FR-31, NFR-3, NFR-5, NFR-9
- ACs:
  - owned: AC-4b (§11.3.4 rows 6–8, 11 and 13 via `check_ready` / `startup_warnings`), AC-20 (guard
    part), AC-21 (uniformity part), AC-22 (password re-auth part), AC-39 (provider part), AC-40
    (harness + login part)
  - supports: AC-3 (`check_ready` behaviour), AC-16 (`Realm.id`)
- Invariants: S6, S9, S15, S16, S25
- Design: HLD §11.11 (`guard.py`), §11.15.1–§11.15.4, §11.15.6 (provider parts), §11.15.7, §11.16,
  §20.3 #6/#7/#10; §1 D6, D9, D10, D11; ADR-0021 D1 (realm ids), D7, D9, D10

## Description
This task holds the core credential logic, apart from the second factor (T-yfrfxv). It contains no
FastAPI imports (layer L3, HLD §11.1 R1/R4).
- **`auth/guard.py`:** `AttemptSubject`, `AttemptResult`, and `AttemptGuard.attempt(...)`. This is
  the **single** throttle → username gate → lockout → verify → record → audit sequence (HLD §11.11
  pseudocode, verbatim).
  - A `BusyError` raised by `verify()` records an **address** failure, writes **no** lockout entry,
    and is re-raised.
  - Exactly one `lockouts.record_failure` per failed attempt, for known users and phantoms alike.
  - `auth.lockout` is emitted when the failure count reaches the threshold.
- **`auth/provider.py`:** `ClientInfo`, `VerifiedIdentity` (`credential_epoch` **from the same
  snapshot** as the verified hash; `next_state: SessionState`), `UserView`, `Revalidation`, and the
  `AuthProvider` ABC (HLD §11.15.1).
- **`auth/local_provider.py`:** `LocalPasswordProvider(AuthProvider)`, `provider_id =
  LOCAL_PROVIDER_ID`, constructed with `store, lockouts, guard, hasher, settings, audit, clock`.
  - `check_ready()`: checks the store directory and `users.json`, **and** the state directory,
    including the parent-directory checks. A missing state directory is created 0700 through
    `fsutil.ensure_private_dir`. The bootstrap message text follows HLD §11.15.3.
  - `startup_warnings()`: enrolled users under `off`, and users whose `totp_requirement` is
    `BLOCKED`.
  - `authenticate()`: HLD §11.15.4 verbatim — one snapshot, a dummy verify for unknown or
    invalid names, CAS rehash, `totp_requirement()` → `next_state` or 403 `TOTP_REQUIRED`;
    `lockouts.reset` + `cas_mark_login` **only** for FULL.
  - `verify_current_password()`, `change_password()`, `logout_everywhere()`, `revalidate()`
    (tri-state through `store.user_by_id`) and `user_view()`.
- **Identity-guarded web writes** (task-level precision, see Pseudocode). `set_password_hash` and
  `bump_epoch` run inside one `store.mutate` that first re-checks the record's `user_id` and
  `credential_epoch` against the session. A mismatch raises `StaleIdentityError` →
  `AuthError(NOT_AUTHENTICATED)`.
- **`auth/runtime.py`:**
  - `Realm`: `id` = `HUB_REALM_ID` or `"ui:" + sha256(str(workspace_root.resolve()))[:WORKSPACE_ID_HEX_CHARS]`;
    `cookie_name(secure)`; `login_path`.
  - `AuthRuntime` (the exact §11.16 fields).
  - `build_auth_runtime(settings, realm, *, clock, entropy, hasher, audit, session_store, provider=None)`.
    It raises `ValueError` if `settings.enabled` is false, and does **not** call `check_ready`.
  - `runtime_of(app)`.
  - T-G7qByZ builds against a duck-typed `StubRuntime`/`StubRealm` in `tests/auth/helpers.py`,
    which uses the exact §11.16 attribute names. When this file lands, its "final wiring" switches
    to the real types. Keep the attribute names identical.
  - `AuthRuntime.totp` stays `None` here. T-yfrfxv wires `LocalTotpService` into
    `build_auth_runtime`.
- **Async rule:** every `UserStore.mutate`, `LockoutStore.*` and `AuditLog.record` call from an
  `async` function goes through `seams.run_sync`. `snapshot()` and `revalidate()` stay synchronous,
  because they are cheap on a cache hit (HLD §11.15.7).
- **Not in scope:** `launch.py` / `prepare_auth` (T-jVqH8w), `totp_service.py` (T-yfrfxv), HTTP
  (T-G7qByZ, T-rpKCjP).

## Inputs / Outputs
- **Inputs:**
  - T-kzEzwy: constants, errors, seams, model and test helpers.
  - T-s6sJmB: `PasswordHasher`, `PasswordPolicy`.
  - T-8NQP8J: `UserStore`, mutations, `StorePaths`, `fsutil`.
  - T-kwwJ82: `SessionState`, `totp_requirement`.
  - T-PlEROT: `AuthSettings`.
  - T-CsT5gk: `LockoutStore`, `LockoutPolicy`, `AddressThrottle`, `UsernameGates`,
    `canonical_client_key`, `AuditLog`.
- **Outputs:**
  - `src/agent_orchestrator/auth/guard.py`, `provider.py`, `local_provider.py`, `runtime.py`
  - `tests/auth/test_guard.py`, `test_local_provider.py`, `test_runtime.py`, `test_event_loop.py`
    (harness plus login cases; T-yfrfxv appends)

## Acceptance Criteria
1. **`check_ready`** (supports AC-3). It raises `AuthNotReadyError` for:
   - a missing store directory or a missing `users.json`;
   - zero users. The message contains `ao auth add-user`, plus `--auth-dir <dir>` when the store is
     not the default.
   - a corrupt or newer-schema store;
   - a corrupt `lockouts.json` (via `LockoutStore.check_readable()`). The message names
     `ao auth unlock`.

   It raises `UnsafePermissionsError` for:
   - a store directory, state directory or file with group/other access;
   - a group-writable parent;
   - a symlinked `users.json`.

   It passes with one user and correct modes, and creates a missing state directory with 0700
   through `paths.check_state_dir`. Together with AC 2, this covers AC-4b: §11.3.4 rows 6, 7, 8
   and 13. (`test_local_provider.py`)
2. **`startup_warnings`** (AC-4b, row 11), using the exact HLD §11.15.3 strings:
   - under `off` with enrolled users, one warning contains `will still be asked for a code` and
     the count;
   - for BLOCKED users, one warning contains `cannot log in` and `ao auth disable-2fa`;
   - under `optional` with no BLOCKED users, the result is `[]`.
   (`test_local_provider.py`)
3. **Guard** (AC-20 part, `test_guard.py`, using fakes):
   - When the address throttle is active, `TooManyAttemptsError` is raised, `verify` is **not**
     called and there is no lockout write.
   - When the lockout is active, the result is the same.
   - A `BusyError` from `verify` → one address failure, zero lockout writes, re-raised.
   - A failure → exactly one `record_failure`, one address failure, and one audit event:
     `username` set and `username_hash` null for a known user, and the reverse for a phantom.
   - `auth.lockout` is emitted exactly once, on the threshold-th failure.
   - `reset_on_success=True` with a known user → `lockouts.reset(user_id)` is called once.
4. **Uniformity** (AC-21 part, S6), with `FastFakeHasher` and spies. `authenticate("ghost", "x")`
   and `authenticate("alice", "wrong")`:
   - both raise `AuthError(INVALID_CREDENTIALS)` with an identical `detail`;
   - each makes exactly **1** `hasher.verify` call, **1** `LockoutStore.record_failure` call and
     **0** `UserStore.mutate` calls.

   The phantom key is `sha256("ghost").hexdigest()`. `"Bad Name!"` takes the phantom path.
5. **Shared lockout** (AC-20 part): two `LocalPasswordProvider` instances on one state directory
   share the account lockout. After `threshold` failures through instance A, instance B raises
   `TooManyAttemptsError` without calling `verify`.
6. **`next_state` matrix** (S9), `test_local_provider.py`:
   - enrolled → `PARTIAL_SECOND_FACTOR` under `off`, `optional` and `required`;
   - not enrolled, `optional`, not required → `FULL`, with `lockouts.reset` and `cas_mark_login`
     each called once;
   - not enrolled and (`required` or `totp_required`) under a policy other than `off` →
     `PARTIAL_ENROLL`;
   - not enrolled, `totp_required`, `off` → `AuthError(TOTP_REQUIRED)` after a **correct** password.
     A wrong password still gives `INVALID_CREDENTIALS`.
   - For both partial states, `lockouts.reset` is **not** called, and one
     `auth.login.second_factor_pending` event is written.
7. **Snapshot epoch** (AC-39 part, S25 straddle). The hasher's `verify` is patched to run
   `store.mutate(set_password_hash(...))` (epoch 3 → 4) before returning `True`.
   `authenticate` returns `credential_epoch == 3`, and `revalidate(user_id, 3) == REVOKED`.
8. **CAS rehash** (AC-39 part, AC-5). With `needs_rehash=True`, a successful login rewrites
   `password_hash` and leaves the epoch unchanged. If a `set_password_hash` lands between the
   verify and the rehash (patched), `users.json` keeps the newer hash and the epoch is 4.
9. **Revival** (AC-39 part): after `remove_user` + `add_user` with the same name,
   `revalidate(old_user_id, old_epoch) == REVOKED`.
10. **Re-authentication** (AC-22 part):
    - `verify_current_password` with a wrong password → `INVALID_CREDENTIALS`, with one
      `record_failure` and one `auth.reauth.failure` event;
    - a stale session (unknown `user_id` or an epoch mismatch) → `NOT_AUTHENTICATED`, with no
      verify call.
11. **`change_password`:**
    - wrong current password → `INVALID_CREDENTIALS`;
    - policy violation → `PASSWORD_POLICY` with `violations`;
    - success → epoch +1, the new password verifies, and one `auth.password.changed` event
      (`source=web`) is written;
    - a record whose `user_id` or epoch changed inside the mutate → `NOT_AUTHENTICATED`, and nothing
      is written.

    `logout_everywhere` → epoch +1 under the same identity guard.
12. **`revalidate`:** VALID; REVOKED (unknown user, or an epoch mismatch); UNAVAILABLE (invalid
    JSON, or lock-timeout/`OSError`; logged once). `user_view` returns `user_id`, the counts and
    `totp_required`. Its `repr` contains no hash or seed.
13. **Event loop** (AC-40 harness + login, `test_event_loop.py`). `UserStore.mutate`,
    `LockoutStore.state/record_failure/reset/forget` and `AuditLog.record` are patched to record
    `threading.current_thread()`. A successful and a failed `authenticate` run through
    `run_async(...)`. None of the recorded threads is the loop thread.
14. **Runtime** (supports AC-16, `test_runtime.py`):
    - `Realm("ui", 8765, root).id == "ui:" + sha256(str(root.resolve()))[:12]`, and it is the same
      for port 8766;
    - `Realm("hub", 8770).id == "hub"`;
    - `cookie_name(secure=False) == "ao_sid_8765"` and `cookie_name(secure=True) == "__Host-ao_sid_8765"`;
    - hub `login_path == "/login"` and ui `login_path == "/"`;
    - `build_auth_runtime(disabled settings)` raises `ValueError`;
    - `build_auth_runtime(..., provider=fake)` uses the fake;
    - `runtime_of(app)` returns the app-state value, or `None`;
    - `audit_log_for(request)` (HLD §11.16 and §2.6, the approvals-epic seam) returns
      `runtime.audit` when auth is on, and `None` when `runtime_of(request.app)` is `None`.
15. The AST layer test (T-kzEzwy) passes for the four modules. ruff and mypy are clean. Coverage of
    `guard.py`, `provider.py`, `local_provider.py` and `runtime.py` is ≥ 90 %.

## Risks
- **Async/thread mix-ups.** Gates are `asyncio.Lock`s confined to one loop. Store, lockout and audit
  I/O go through `run_sync`. Tests use `run_async` (there is no pytest-asyncio).
- **Audit responsibility drift between the guard, the provider and the routes.** HLD §11.15.2 is
  the contract.
- **The dummy-hash timing for users still on legacy parameters** is a documented residual
  (dev-security #12). Do not try to "fix" it here.

## Dependencies
- **Upstream:** T-kzEzwy, T-s6sJmB, T-8NQP8J, T-kwwJ82, T-PlEROT, T-CsT5gk.
- **Downstream:**
  - T-yfrfxv (guard, provider);
  - T-G7qByZ (final wiring onto the real `AuthRuntime`);
  - T-rpKCjP;
  - T-jVqH8w (`prepare_auth` calls `build_auth_runtime` + `check_ready`);
  - T-KOv2qD, T-U2ERMo.

## Pseudocode / Algorithm
- HLD §11.11 (`AttemptGuard.attempt`), §11.15.4 (`authenticate`), §11.15.6 (provider parts) and
  §11.15.7 (`revalidate`), verbatim.
- **Identity-guarded writes** use the single `store.with_identity(...)` wrapper (HLD §11.9, built
  by T-8NQP8J). Do **not** re-implement the check here:

```text
change_password:   epoch = AWAIT run_sync(store.mutate, with_identity(set_password_hash(..), username=session.username,
                                                     user_id=session.user_id, epoch=session.credential_epoch))
logout_everywhere: epoch = AWAIT run_sync(store.mutate, with_identity(bump_epoch(..), username=.., user_id=.., epoch=..))
StaleIdentityError (from store.py) -> AuthError(NOT_AUTHENTICATED)
```

## Schemas / Interface Notes
- The signatures in HLD §11.15.1 and §11.16 are binding. The field names of `VerifiedIdentity` and
  `UserView` are consumed by the routes and the E1/E2 bodies. Do not rename them.
- The error vocabulary comes from `auth/errors.py` (T-kzEzwy).

## Handoff Boundary
- **Upstream:** the S1 modules (pure logic plus storage).
- **Downstream:** `build_auth_runtime()`, `AuthProvider`, `LocalPasswordProvider` and
  `AttemptGuard`. The HTTP layer never touches the store directly.

## Verification

```
python -m pytest -q tests/auth/test_guard.py tests/auth/test_local_provider.py tests/auth/test_runtime.py tests/auth/test_event_loop.py
python -m pytest -q tests/auth --cov=agent_orchestrator.auth --cov-report=term-missing
ruff check src tests && ruff format --check src tests && mypy src
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-XchniS-auth-local-provider-runtime/`
