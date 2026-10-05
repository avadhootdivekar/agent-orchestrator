# TASK: T-8NQP8J-auth-user-store

## Metadata
- Task ID: `T-8NQP8J-auth-user-store`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane B)
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Done`
- Estimate: `3 days` · Sprint `S1`

## Requirements Mapping
- Requirement IDs: FR-4 (replay state), FR-5 (consumption), FR-10, FR-28 (enrollment-token storage), FR-31 (`user_id`, CAS), NFR-9
- ACs: AC-5 (CAS rehash part), AC-7 (store part), AC-13 (filesystem part, incl. v2.1 umask 002 / parent warning / fd-based fixes), AC-36 (store part), AC-42; supports AC-39 (store part) and AC-46 (fd-based primitives)
- Design: HLD §11.4 (`paths.py`, `xdg.py`), §11.5 (`fsutil.py`), §11.9 (`store.py`), §12.1, §12.2, §16 rows 20, 21 and cross-epic row X1, §28.9 (design-review M1, minor 2; security M6, L6); HLD D5, D7, D10; ADR-0021 D3, D5, D10

## Description
Build the persistence layer: the neutral file primitives, path resolution, and `users.json`.
Lockouts are **not** stored here; they live in `lockouts.json` (T-CsT5gk).

- **`src/agent_orchestrator/fsutil.py`** (NEW, neutral, shared; ledger row 21; HLD §11.5):
  - `FileLock` (sidecar flock, timeout, poll, `O_NOFOLLOW`) and `LockTimeoutError(OSError)`;
  - `atomic_write_bytes`: an `O_EXCL|O_NOFOLLOW` temp file, fsync, `os.replace`, then a directory
    fsync;
  - `remove_stale_temp_files`, `ensure_private_dir`, `check_private_file`.
  - **v2.1 (security M6; HLD §11.5):** `ensure_private_dir(create=True)` creates each missing path
    component with `os.mkdir(p, mode=0o700)` (never `Path.mkdir(parents=True)`, whose parents
    follow the umask). Verification and fixing go through an
    `os.open(path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW)` fd: `os.fstat(fd)` for owner and mode,
    `os.fchmod(fd, 0o700)` to fix (and once after creation). `check_private_file(fix=True)` fixes
    through an `O_RDONLY | O_NOFOLLOW` fd + `os.fchmod(fd, 0o600)`. **No path-based `chmod`
    anywhere** (no symlink race).
  - **v2.1 parent rule (security L6):** the parent is judged with `os.stat` of the **resolved**
    parent (a symlinked `~/.config` is judged at its target): other-writable → `UnsafePathError`;
    group-writable and owned by another user (and not root-owned with the sticky bit) →
    `UnsafePathError`; group-writable **and owned by the euid** (`umask 002` hosts, HLD A-18) → a
    returned **WARNING notice** (`<parent> is group-writable; run chmod g-w <parent>`), not an
    error; root-owned with the sticky bit → accepted.
  - It **must not import `agent_orchestrator.auth`**. Permission problems raise a neutral
    `UnsafePathError(OSError)` whose message contains the exact `chmod` fix. The auth layer maps it
    to `UnsafePermissionsError` (see `check_private_paths` below).
  - The existing call sites (`isolation/locks.py`, `service/registry.py`, `feedback.py`) are not
    migrated.
- **`src/agent_orchestrator/xdg.py`** (shared, additive; ledger row 20; **v2.1 signature aligned
  with the approvals epic, design-review M1, cross-epic row X1**):
  - add `resolve_config_dir(override_env: str | None, xdg_subdir: str, default_subdir: str, *,
    environ: Mapping[str, str] | None = None, home: Path | None = None) -> Path` with the
    precedence `$<override_env>` (skipped when `None`) > `$XDG_CONFIG_HOME/<xdg_subdir>` >
    `<home>/.config/<default_subdir>`; `environ=None` → `os.environ` read at call time;
    `home=None` → `Path.home()`. This is the exact shape of the approvals epic's `T-drPIif`
    definition, so **one implementation serves both epics**: whichever epic merges first adds it
    with this signature plus both epics' test cases; the second only rebases and adds call sites;
  - widen `resolve_state_dir` additively: `override_env: str | None` (None skips the override) and
    keyword-only `environ` and `home` with the same defaults.
  - Existing callers and `tests/test_xdg.py` stay unchanged.
- **`auth/paths.py`** (L1; HLD §11.4):
  - `AO_AUTH_DIR_ENV`, `AO_AUTH_STATE_DIR_ENV`;
  - `xdg_default_store_dir(env)` = `xdg.resolve_config_dir(None, "ao/auth", "ao/auth",
    environ=env)`, which ignores `AO_AUTH_DIR`;
  - `xdg_default_state_dir(env)` = `xdg.resolve_state_dir(None, "ao/auth", "ao/auth",
    environ=env)`, which ignores `AO_AUTH_STATE_DIR`;
  - `StorePaths.at(store_dir, state_dir)`, holding the users, lockouts and audit files and locks;
  - `is_within(path, root)`;
  - `check_private_paths(store_dir, users_file) -> list[str]` and `check_state_dir(state_dir) ->
    list[str]` (below): they map `UnsafePathError` to `UnsafePermissionsError` and **return** the
    non-fatal parent-directory warnings (v2.1, L6).
  - **Moved out in v2.1:** `default_denied_paths` (now including `service.env`, security L7) and
    `entry_is_denied` belong to **T-Hd4wQ2-auth-browse-denial-log-scrub**, which appends them to
    this `paths.py` **after this task merges** (sequential ownership, HLD §16). Do not implement
    them here.
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
    (HLD §11.4) map `fsutil.UnsafePathError` to `UnsafePermissionsError` and return the parent
    warnings. `check_state_dir` creates a missing state directory (0700, parent checked) and never
    fixes permissions.
  - **Cut-line #4 (HLD §24.1, design-review M3):** `required_features` gating is the last stretch
    item. If the manager cuts it, readers refuse only on `schema_version > 1`; unknown fields are
    still preserved. Implement it last.
  - Outcome types: `TotpOutcome(kind: OK|INVALID|REPLAYED|STALE, step: int | None)` and
    `RecoveryOutcome(kind: OK|INVALID|STALE, remaining: int | None)`.
  - Domain errors: `StoreMissingError`, `UserExistsError`, `UserNotFoundError`,
    `TooManyUsersError`, `AlreadyEnrolledError` (also raised by `issue_enrollment_token`; the HLD's
    `TotpAlreadyEnrolledError` is this class), `NotEnrolledError`, `StaleIdentityError`.

## Inputs / Outputs
- **Inputs:**
  - HLD §11.4, §11.5, §11.9, §12.1, §12.2, §12.6;
  - T-kzEzwy (`constants`, `errors`, `seams`, `tests/auth/helpers/core.py`);
  - T-s6sJmB (`match_totp_step`, `find_unused_match`, recovery helpers). **v2.1:** a real
    (late-binding) edge, no stub: T-s6sJmB lands `totp.py` and `recovery.py` by S1 day 3, and this
    task writes the `consume_*` mutations last (design-review minor 2).
- **Outputs:**
  - `src/agent_orchestrator/fsutil.py`, `src/agent_orchestrator/xdg.py` (additive)
  - `src/agent_orchestrator/auth/paths.py` (without the denial helpers), `store.py`
  - `tests/test_fsutil.py`, `tests/test_xdg.py` (+cases)
  - `tests/auth/test_paths.py`, `tests/auth/test_store.py`
  - `tests/auth/helpers/store.py` (`make_store(tmp_path, users=...)`; this task owns the module)
  - `tests/auth/schemas/auth-users-v1.json` (a verbatim copy of HLD §12.1)

## Acceptance Criteria
1. **`xdg`** (v2.1 signature).
   - `resolve_config_dir("AO_AUTH_DIR", "ao/auth", "ao/auth", environ={"AO_AUTH_DIR": "/a"})` →
     `/a`.
   - `environ={"XDG_CONFIG_HOME": "/x"}` → `/x/ao/auth`; `environ={}, home=Path("/h")` →
     `/h/.config/ao/auth`; `environ={}` with no `home` → `Path.home() / ".config/ao/auth"`.
   - `override_env=None` ignores any override variable; distinct `xdg_subdir` and
     `default_subdir` values are each used in their own branch.
   - `resolve_state_dir(None, "ao/auth", "ao/auth", environ={"XDG_STATE_HOME": "/s"})` →
     `/s/ao/auth`; `home=` is honoured the same way.
   - The final signatures are recorded in STATUS (cross-epic row X1).
   - Every existing `tests/test_xdg.py` case passes unmodified. (`tests/test_xdg.py`)
2. **`paths`.**
   - `xdg_default_store_dir` and `xdg_default_state_dir` ignore `AO_AUTH_DIR` and
     `AO_AUTH_STATE_DIR` respectively, and read only the passed `env` mapping.
   - `StorePaths.at(s, t)` uses the §12.6 file names.
   - `is_within` is True for the same path and for a child, and False for a sibling with a shared
     prefix (`/a/b` vs `/a/bc`).
   - (`default_denied_paths` and `entry_is_denied` are tested by T-Hd4wQ2.)
   (`tests/auth/test_paths.py`)
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
5. **Private directories and files** (AC-13; v2.1 security M6 and L6).
   - `ensure_private_dir(create=True)` creates the directory, and any missing parents, with mode
     0700 under umasks 022, 077 **and 002**; spies show `os.mkdir(..., mode=0o700)` per component
     and no `Path.mkdir(parents=True)`.
   - An existing 0755 directory with `fix=False` raises `UnsafePathError` naming `chmod 700 <path>`.
     With `fix=True` it is tightened through `os.fchmod` on an `O_NOFOLLOW` fd, and a notice is
     returned. A spy on `os.chmod` / `Path.chmod` records **zero** calls in every fsutil path
     (AC-46 support).
   - Each of these raises: a symlinked directory; a foreign owner (patched `fstat` uid); an
     other-writable parent; a group-writable parent owned by another user (named in the message).
   - **A group-writable parent owned by the euid → no exception; one returned warning notice**
     containing `chmod g-w` (run under `umask 002`, Debian/Ubuntu-style home).
   - **A symlinked parent** (e.g. `cfg -> real_cfg`): the check judges `real_cfg`'s mode and owner.
     (This covers the manager's "check ln" note, interpreted as "verify on a `umask 002` host,
     including a symlinked parent"; HLD §28.9 L6.)
   - A parent with mode 1777 owned by root (patched) is accepted.
   - `check_private_file`: mode 0644 raises with `chmod 600`; a symlink raises; `fix=True` uses an
     fd-based `fchmod`.
   - `check_private_paths` / `check_state_dir` raise `UnsafePermissionsError` (an `AuthConfigError`)
     for each fatal condition on the store **or** the state directory, and return the warning
     notices otherwise.
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
- **Scope pressure (3 days).** `fsutil` and `xdg` come first (day 1), then `paths`, then the store,
  with the `consume_*` mutations last (they need T-s6sJmB's helpers). If time runs short, raise it
  in STATUS rather than dropping ACs; `required_features` is cut-line #4 (HLD §24.1).
- **Cross-epic `xdg.py` (row X1).** The approvals epic adds the same function. Keep the exact
  signature; if the approvals change merges first, rebase and only add this epic's call sites and
  test cases.

## Dependencies
- **Upstream:** T-kzEzwy; T-s6sJmB (hard edge since v2.1; late-binding, needed on day 3; no stub).
- **Downstream:**
  - T-PlEROT (`count_store_users`, `xdg_default_*`);
  - T-CsT5gk (`fsutil.FileLock`, `atomic_write_bytes`, `StorePaths`);
  - T-XchniS (`UserStore`, CAS, `check_private_paths`);
  - T-yfrfxv (consume and enroll mutations, tokens);
  - T-j9dfsw (every mutation; the fd-based fsutil primitives for the config-sourced `store_dir`
    rule, AC-46);
  - T-Hd4wQ2-auth-browse-denial-log-scrub (appends `default_denied_paths` and `entry_is_denied` to
    `paths.py`; uses `is_within`, `xdg_default_*`).

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
  opens `users.json`. `paths.py` is handed over to T-Hd4wQ2 for the append-only denial helpers
  after this task merges.

## Verification

```
python -m pytest -q tests/test_fsutil.py tests/test_xdg.py tests/auth/test_paths.py tests/auth/test_store.py tests/auth/test_import_boundary.py
python -m pytest -q tests/test_fsutil.py tests/auth --cov=agent_orchestrator.fsutil --cov=agent_orchestrator.auth.paths --cov=agent_orchestrator.auth.store --cov-report=term-missing
ruff check src/agent_orchestrator tests && mypy src/agent_orchestrator/fsutil.py src/agent_orchestrator/xdg.py src/agent_orchestrator/auth
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-8NQP8J-auth-user-store/`
