# STATUS

- ID: `T-XchniS-auth-local-provider-runtime`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane A)
- Scope: `MVP` · Sprint: `S2` · Estimate: `3 d`

## This update
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
- None yet.

## Risks / Blockers
- None. The S1 modules (T-kzEzwy, T-s6sJmB, T-8NQP8J, T-kwwJ82, T-PlEROT, T-CsT5gk) must land
  first. On the critical path (HLD §24.3).

## Next actions
1. developer: implement once the S1 tasks land. Keep the §11.16 attribute names identical to
   T-G7qByZ's `StubRuntime`, so its final wiring is a type swap. Run the verification and record
   coverage here.
