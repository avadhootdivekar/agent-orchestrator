# TASK: T-8NQP8J-auth-user-store

## Metadata
- Task ID: `T-8NQP8J-auth-user-store`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane B)
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `3 days` · Sprint `S1`

## Requirements Mapping
- Requirement IDs: FR-4 (replay state), FR-5 (consumption), FR-10, FR-28 (enrollment-token storage), FR-31 (`user_id`, CAS), NFR-9
- ACs: AC-5 (CAS rehash part), AC-7 (store part), AC-13 (filesystem part), AC-36 (store part), AC-42; supports AC-39 (store part)
- Design: HLD §11.4 (`paths.py`, `xdg.py`), §11.5 (`fsutil.py`), §11.9 (`store.py`), §12.1, §12.2, §16 rows 20 and 21; HLD D5, D7, D10; ADR-0021 D3, D5, D10

## Description
Build the persistence layer: the neutral file primitives, path resolution, and `users.json`.
Lockouts are **not** stored here; they live in `lockouts.json` (T-CsT5gk).

- **`src/agent_orchestrator/fsutil.py`** (NEW, neutral, shared; ledger row 21; HLD §11.5):
  - `FileLock` (sidecar flock, timeout, poll, `O_NOFOLLOW`) and `LockTimeoutError(OSError)`;
  - `atomic_write_bytes`: an `O_EXCL|O_NOFOLLOW` temp file, fsync, `os.replace`, then a directory
    fsync;
  - `remove_stale_temp_files`, `ensure_private_dir`, `check_private_file`.
  - It **must not import `agent_orchestrator.auth`**. Permission problems raise a neutral
    `UnsafePathError(OSError)` whose message contains the exact `chmod` fix. The auth layer maps it
    to `UnsafePermissionsError` (see `check_private_paths` below).
  - The existing call sites (`isolation/locks.py`, `service/registry.py`, `feedback.py`) are not
    migrated.
- **`src/agent_orchestrator/xdg.py`** (shared, additive; ledger row 20):
  - add `resolve_config_dir(override_env: str | None, subpath: str, *, env: Mapping[str, str] |
    None = None) -> Path` with the precedence `$override` > `$XDG_CONFIG_HOME/<sub>` >
    `~/.config/<sub>`;
  - widen `resolve_state_dir` additively: `override_env: str | None` (None skips the override) and a
    keyword-only `env` (None means `os.environ`).
  - Existing callers and `tests/test_xdg.py` stay unchanged.
- **`auth/paths.py`** (L1; HLD §11.4):
  - `AO_AUTH_DIR_ENV`, `AO_AUTH_STATE_DIR_ENV`;
  - `xdg_default_store_dir(env)`, which ignores `AO_AUTH_DIR`;
  - `xdg_default_state_dir(env)`, which ignores `AO_AUTH_STATE_DIR`;
  - `default_denied_paths(env)`;
  - `StorePaths.at(store_dir, state_dir)`, holding the users, lockouts and audit files and locks;
  - `is_within(path, root)`;
  - `entry_is_denied(parent_resolved, entry, denied)`: resolves **only** symlink entries. T-jVqH8w
    uses it in `FileBrowser.list_dir`;
  - `check_private_paths(paths, *, create, fix) -> list[str]`: checks the store directory,
    `users.json` (if present), the state directory and both parents. It maps `UnsafePathError` to
    `UnsafePermissionsError`.
- **`auth/store.py`** (L2; HLD §11.9):
  - Models with `_STORE_MODEL_CONFIG = ConfigDict(extra="allow", hide_input_in_errors=True)`:
    `TotpEnrollment`, `RecoveryCodeHash`, `EnrollmentToken`, `UserRecord` (immutable `user_id`,
    `totp_required`, `enrollment_token`, **no lockout field**) and `UserStoreFile`
    (`required_features`).
  - `UserStore`: `exists`, `snapshot` (stat-key cache; `user_id` index), `user_by_id`,
    `mutate(fn, create)` (lock, stale-temp cleanup, fresh parse, validate, atomic write, cache
    refresh) and `count_users`.
  - `count_store_users(store_dir) -> int | None`, the tri-state probe used by T-PlEROT and
    T-jVqH8w:
    - a missing directory or file → 0;
    - an unreadable or corrupt file, or an unsupported schema or feature → `None`;
    - otherwise → the number of users.
  - **Every pure mutation in the HLD §11.9 table:** `add_user`, `remove_user`,
    `set_password_hash`, `cas_rehash_password`, `cas_mark_login`, `consume_totp`,
    `consume_recovery`, `issue_enrollment_token`, `consume_enrollment_token`, `enroll_totp`,
    `remove_totp`, `replace_recovery_codes`, `bump_epoch`.
  - **`with_identity(mutation, *, username, user_id, epoch)`** (HLD §11.9, D10): the single pure
    wrapper that re-checks `user_id` + `credential_epoch` inside the locked `mutate` and raises
    `StaleIdentityError` on mismatch, before applying `mutation`. T-XchniS and T-yfrfxv use it for
    every web-initiated credential write; nobody re-implements the check.
  - `paths.check_private_paths(store_dir, users_file)` and `paths.check_state_dir(state_dir)`
    (HLD §11.4) map `fsutil.UnsafePathError` to `UnsafePermissionsError`. `check_state_dir`
    creates a missing state directory (0700, parent checked) and never fixes permissions.
  - Outcome types: `TotpOutcome(kind: OK|INVALID|REPLAYED|STALE, step: int | None)` and
    `RecoveryOutcome(kind: OK|INVALID|STALE, remaining: int | None)`.
  - Domain errors: `StoreMissingError`, `UserExistsError`, `UserNotFoundError`,
    `TooManyUsersError`, `AlreadyEnrolledError` (also raised by `issue_enrollment_token`; the HLD's
    `TotpAlreadyEnrolledError` is this class), `NotEnrolledError`, `StaleIdentityError`.

## Inputs / Outputs
- **Inputs:**
  - HLD §11.4, §11.5, §11.9, §12.1, §12.2, §12.6;
  - T-kzEzwy (`constants`, `errors`, `seams`);
  - T-s6sJmB (`match_totp_step`, `find_unused_match`, recovery helpers). Stub them with the HLD
    signatures if they are not merged yet.
- **Outputs:**
  - `src/agent_orchestrator/fsutil.py`, `src/agent_orchestrator/xdg.py` (additive)
  - `src/agent_orchestrator/auth/paths.py`, `store.py`
  - `tests/test_fsutil.py`, `tests/test_xdg.py` (+cases)
  - `tests/auth/test_paths.py`, `tests/auth/test_store.py`
  - `tests/auth/schemas/auth-users-v1.json` (a verbatim copy of HLD §12.1)

## Acceptance Criteria
1. **`xdg`.**
   - `resolve_config_dir("AO_AUTH_DIR", "ao/auth", env={"AO_AUTH_DIR": "/a"})` → `/a`.
   - `env={"XDG_CONFIG_HOME": "/x"}` → `/x/ao/auth`; `env={}` → `~/.config/ao/auth`.
   - `resolve_state_dir(None, "ao/auth", "ao/auth", env={"XDG_STATE_HOME": "/s"})` →
     `/s/ao/auth`.
   - Every existing `tests/test_xdg.py` case passes unmodified. (`tests/test_xdg.py`)
2. **`paths`.**
   - `xdg_default_store_dir` and `xdg_default_state_dir` ignore `AO_AUTH_DIR` and
     `AO_AUTH_STATE_DIR` respectively.
   - `default_denied_paths(env)` is the resolved, de-duplicated set {XDG store, XDG state,
     `$AO_AUTH_DIR` if set, `$AO_AUTH_STATE_DIR` if set}.
   - `StorePaths.at(s, t)` uses the §12.6 file names.
   - `is_within` is True for the same path and for a child, and False for a sibling with a shared
     prefix (`/a/b` vs `/a/bc`).
   - `entry_is_denied` makes 0 `Path.resolve` calls for 100 regular entries (patched counter), and
     denies a symlink entry that points into the store. (`tests/auth/test_paths.py`)
3. **`FileLock`** (spawn context, `join(timeout=…)`, exit codes asserted).
   - A second process waiting with `timeout=0.2` raises `LockTimeoutError` within 1 s.
   - The lock is released when the holder exits.
   - A symlinked lock path fails with `OSError` (`O_NOFOLLOW`). (`tests/test_fsutil.py`)
4. **`atomic_write_bytes`.**
   - The file is 0600 under umask 022 and under umask 077.
   - With `os.replace` patched to raise, the original is byte-identical afterwards and no `.*.tmp`
     sibling remains.
   - A symlink pre-planted at the temp name (with a patched `token_hex`) makes the write fail and
     leaves the symlink target untouched.
   - `remove_stale_temp_files` removes only `.<name>.*.tmp` siblings and returns their count.
5. **Private directories and files.**
   - `ensure_private_dir(create=True)` creates the directory with mode 0700 under both umasks.
   - An existing 0755 directory with `fix=False` raises `UnsafePathError` naming `chmod 700 <path>`.
     With `fix=True` it is tightened, and a notice is returned.
   - Each of these raises: a symlinked directory; a foreign owner (patched `lstat` uid); a
     group-writable parent (named in the message).
   - A parent with mode 1777 owned by root (patched) is accepted.
   - `check_private_file`: mode 0644 raises with `chmod 600`; a symlink raises.
   - `check_private_paths` raises `UnsafePermissionsError` (an `AuthConfigError`) for each of those
     conditions on the store **or** the state directory.
6. **Snapshot and forward compatibility (AC-42).**
   - A missing file → no users and `store_id == ""`.
   - Invalid JSON, or JSON nested 10 000 deep → `StoreCorruptError`.
   - `schema_version: 2`, or `required_features: ["x"]` → `StoreCorruptError` whose message
     contains `upgrade ao`.
   - An **unknown top-level key and an unknown user key are accepted**, and both survive a later
     `mutate(set_password_hash)` rewrite byte-for-byte.
   - An unchanged file is not re-read (`read_bytes` counter). After an external atomic replace, the
     next snapshot returns the new content.
   - A read `OSError` → `StoreUnavailableError`.
   - Validation-error messages contain field paths but never the input value (sentinel).
7. **`mutate` (AC-13).**
   - 20 spawn processes each run `add_user(f"u{i}")` through `mutate`. The final store holds
     exactly 20 users with 20 distinct `user_id`s.
   - `create=True` writes `schema_version` 1, a 32-hex `store_id` and `created_at`.
     `create=False` on a missing store raises `StoreMissingError`.
   - Every written file is 0600 and validates against `tests/auth/schemas/auth-users-v1.json`
     (`jsonschema`).
   - `remove_stale_temp_files` is called under the lock (spy).
8. **User mutations.**
   - `add_user` gives a 32-hex `user_id`, epoch 1 and `totp_required` from its argument. A
     duplicate → `UserExistsError`; more than `MAX_USERS` users → `TooManyUsersError`.
   - `remove_user` returns the removed `user_id`. Remove then re-add the same name → a **different**
     `user_id`.
   - `set_password_hash`, `bump_epoch`, `enroll_totp`, `remove_totp` and `replace_recovery_codes`
     each bump the epoch by exactly 1.
   - `remove_totp(set_required=True|False|None)` sets, clears or keeps `totp_required`. For a user
     who is **not** enrolled it raises `NotEnrolledError` **only** when `set_required is None`.
     With `True` or `False` it succeeds: `disable-2fa` clears the flag and `reset-2fa` is
     idempotent. The epoch is bumped only when something changed.
   - **`with_identity`:** applied with a matching `user_id` and epoch, the inner mutation runs.
     After a `set_password_hash` (epoch +1) or a remove + re-add (new `user_id`), the same wrapped
     call raises `StaleIdentityError` and the file is byte-identical to before.
9. **CAS (AC-5 and AC-39 store part).**
   - `cas_rehash_password` applies only while `user_id`, epoch and `password_hash` all equal the
     verified values. It returns True and leaves the epoch unchanged.
   - After `set_password_hash`, the same stale CAS call returns False, and the file keeps the new
     hash.
   - `cas_mark_login` with a stale epoch or a foreign `user_id` returns False and leaves
     `last_login_at` unchanged.
10. **`consume_totp`** (fixed `now_unix`, RFC key).
    - Valid at step s with `last_used_step = s-1` → `OK(s)`, and `last_used_step` becomes s.
    - The same code again → `REPLAYED`. A wrong code → `INVALID`.
    - A stale epoch or `user_id` → `STALE`, with no write.
    - Not enrolled → `NotEnrolledError`.
    - Two spawn processes consuming the same valid code → exactly one `OK` (AC-7).
11. **`consume_recovery`.** `OK` with `remaining == 9`; the same code again → `INVALID`; a stale
    epoch → `STALE` with no write.
12. **Enrollment tokens (AC-36 store part; `FakeClock`).**
    - `issue_enrollment_token` returns `XXXX-XXXX-XXXX-XXXX` once. The plaintext never appears in
      `users.json` (grep).
    - `expires_at == now + ENROLLMENT_TOKEN_TTL_SECONDS`.
    - Re-issuing replaces the old token (the old one → False).
    - A valid consume → True and the token is cleared (a second consume → False).
    - An expired token → False and cleared. A wrong `user_id` → False.
    - Issuing for an enrolled user → `AlreadyEnrolledError`. `enroll_totp` clears any token.
13. **`enroll_totp`.** A stale epoch → `StaleIdentityError`; already enrolled →
    `AlreadyEnrolledError`; `completes_login=True` sets `last_login_at`.
14. **`count_store_users`.** A missing directory → 0; an empty store → 0; two users → 2; corrupt →
    `None`; `schema_version: 2` → `None`; unreadable (mode 000, skipped when running as root) →
    `None`.
15. **No secrets in `repr`.** No sentinel value appears in `repr()` or `str()` of any model
    (`password_hash`, `secret_b32`, `salt_hex` and `hash_hex` use `Field(repr=False)`).
16. ruff and mypy are clean. Coverage of `fsutil`, the new `xdg` code, `paths` and `store` is
    ≥ 90 %.

## Risks
- **Flaky multiprocess tests.** Use `multiprocessing.get_context("spawn")`, module-level targets,
  `join(timeout=…)` and a small patched `STORE_LOCK_TIMEOUT_SECONDS`.
- **Coarse mtime granularity.** The cache key includes `st_ino`, which changes on every replace.
- **Case-insensitive filesystems.** `is_within` uses `os.path.normcase` on resolved strings.
- **Scope pressure (3 days).** `fsutil` and `xdg` come first (day 1), then `paths`, then the store.
  If time runs short, raise it in STATUS rather than dropping ACs.

## Dependencies
- **Upstream:** T-kzEzwy; T-s6sJmB (stub if needed).
- **Downstream:**
  - T-PlEROT (`count_store_users`, `xdg_default_*`);
  - T-CsT5gk (`fsutil.FileLock`, `atomic_write_bytes`, `StorePaths`);
  - T-XchniS (`UserStore`, CAS, `check_private_paths`);
  - T-yfrfxv (consume and enroll mutations, tokens);
  - T-j9dfsw (every mutation);
  - T-jVqH8w (`default_denied_paths`, `entry_is_denied`).

## Pseudocode / Algorithm
HLD §11.5 (`FileLock`, `atomic_write_bytes`, the private checks) and §11.9 (`snapshot`, `mutate`,
the mutation table). Task-level steps:

```text
consume_enrollment_token(f, username, normalized, *, user_id, now):
  rec = f.users.get(username)
  IF rec IS None OR rec.user_id != user_id OR rec.enrollment_token IS None OR normalized IS None: RETURN False
  tok = rec.enrollment_token
  IF parse(tok.expires_at) <= now: rec.enrollment_token = None; RETURN False        # expired -> cleared
  ok = hmac.compare_digest(hash_recovery_code(normalized, bytes.fromhex(tok.salt_hex)), tok.hash_hex)
  IF ok: rec.enrollment_token = None
  RETURN ok

load_store_file(path) -> UserStoreFile:     # ONE parse + validation routine, shared by snapshot(), mutate() and the probe
  raw = read_bytes(path)                       (OSError -> StoreUnavailableError)
  data = json.loads(raw)                       (ValueError, RecursionError -> StoreCorruptError)
  schema_version / required_features checks   (-> StoreCorruptError "... upgrade ao")
  RETURN UserStoreFile.model_validate(data)    (ValidationError -> StoreCorruptError with field paths only)

count_store_users(store_dir):
  f = store_dir / USERS_FILENAME
  IF NOT f.exists(): RETURN 0
  TRY RETURN len(load_store_file(f).users)
  EXCEPT (StoreCorruptError, StoreUnavailableError): RETURN None
```

## Schemas / Interface Notes
- `users.json`: HLD §12.1, a forward-compatible JSON Schema. Timestamps are
  `YYYY-MM-DDTHH:MM:SSZ`. `canonical_json` uses `sort_keys=True`, `indent=2`, `ensure_ascii=True`
  and a trailing newline.
- The `fsutil` and `xdg` signatures are additive. Record the exact final signatures in STATUS for
  T-CsT5gk and T-PlEROT.

## Handoff Boundary
- **Upstream:** the HLD, T-kzEzwy and T-s6sJmB.
- **Downstream:** `UserStore`, the mutation functions, `StorePaths`, `count_store_users` and the
  `fsutil` primitives are the only way any module touches the credential files. No other module
  opens `users.json`.

## Verification

```
python -m pytest -q tests/test_fsutil.py tests/test_xdg.py tests/auth/test_paths.py tests/auth/test_store.py tests/auth/test_import_boundary.py
python -m pytest -q tests/test_fsutil.py tests/auth --cov=agent_orchestrator.fsutil --cov=agent_orchestrator.auth.paths --cov=agent_orchestrator.auth.store --cov-report=term-missing
ruff check src/agent_orchestrator tests && mypy src/agent_orchestrator/fsutil.py src/agent_orchestrator/xdg.py src/agent_orchestrator/auth
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-8NQP8J-auth-user-store/`
