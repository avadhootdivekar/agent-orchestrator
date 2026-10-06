# STATUS

- ID: `T-8NQP8J-auth-user-store`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (lane B)
- Scope: `MVP` · Sprint: `S1` · Estimate: `3 d`

## This update
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented end to end; ACs 1-16
  verified (evidence below). **Final signatures (cross-epic row X1; for T-CsT5gk, T-PlEROT):**
  - `xdg.resolve_config_dir(override_env: str | None, xdg_subdir: str, default_subdir: str, *,
    environ: Mapping[str, str] | None = None, home: Path | None = None) -> Path`
  - `xdg.resolve_state_dir(override_env: str | None, xdg_subdir: str, default_subdir: str, *,
    environ: Mapping[str, str] | None = None, home: Path | None = None) -> Path` (additive;
    `tests/test_xdg.py` existing cases unchanged). Both share one private `_resolve`.
  - `fsutil`: `FileLock(lock_path, *, timeout, poll=DEFAULT_LOCK_POLL_SECONDS, monotonic, sleep)`
    (not reentrant), `LockTimeoutError(OSError)`, `UnsafePathError(OSError)`,
    `atomic_write_bytes(path, data, *, mode=0o600)`, `remove_stale_temp_files(path) -> int`,
    `ensure_private_dir(path, *, create, fix) -> list[str]`,
    `check_private_file(path, *, fix) -> list[str]`; neutral constants `PRIVATE_DIR_MODE`,
    `PRIVATE_FILE_MODE`, `DEFAULT_LOCK_POLL_SECONDS` (a test pins equality with `auth.constants`).
    A missing directory/file with `create=False` raises `FileNotFoundError` (not `UnsafePathError`).
  - `auth/paths.py`: as in HLD 11.4. `check_private_paths` / `check_state_dir` return the warnings;
    `check_private_paths` **skips** a missing store dir / `users.json` (returns `[]`; "no users" is
    the count probe's job) rather than raising.
  - `auth/store.py` decisions the HLD left open (downstream tasks rely on these):
    - Mutations are `(f: UserStoreFile, ...)`; callers pass `lambda f: consume_totp(f, ...)` to
      `mutate` (HLD 11.15 shows them partially applied for readability). `with_identity(mutation,
      *, username, user_id, epoch)` takes and returns such an `f -> result` callable.
    - Return values: `add_user -> UserRecord`; `remove_user -> user_id`; `set_password_hash`,
      `bump_epoch`, `remove_totp`, `replace_recovery_codes`, `enroll_totp -> int` (the epoch);
      `cas_* -> bool`; `issue_enrollment_token -> str` (plaintext); `consume_enrollment_token -> bool`.
    - `TotpOutcome` / `RecoveryOutcome` share one `OutcomeKind` StrEnum (`OK`, `INVALID`,
      `REPLAYED`, `STALE`; recovery never yields `REPLAYED`).
    - `consume_totp` / `consume_recovery` accept `code: str | None` (the result of
      `normalize_*`; `None` -> `INVALID`). An unknown user is `STALE`.
    - `enroll_totp(records=...)` and `replace_recovery_codes(records, ...)` accept
      `new_recovery_records(...)` output (`(salt_hex, hash_hex)` pairs) or `RecoveryCodeHash`.
    - `mutate` writes nothing when `fn` left the content unchanged (stale CAS, rejected code,
      stale identity: file byte-identical, same inode). It does not create the store directory
      (`StoreMissingError`): that is the CLI/launch's `ensure_private_dir` job.
    - Error mapping in `mutate`: `LockTimeoutError` -> `auth.errors.StoreLockTimeoutError`; other
      `OSError` (lock open, read, write) -> `auth.errors.StoreUnavailableError(cause_for_log=...)`;
      parse/validation -> `StoreCorruptError` (field paths only, never values).
    - `snapshot()` returns the shared cached model: callers must treat it as read-only.
    - `required_features` gating (cut-line #4) is implemented, not cut.
  - Crypto integration: `totp.py` / `recovery.py` of T-s6sJmB landed (`1ce99bf`; there is no
    `crypto.py`) and all store tests ran against the real `match_totp_step`,
    `find_unused_match` and recovery helpers: integration check done, no stub.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate unchanged (3 d); scope rebalanced.
  - **`xdg` signature (design-review M1, cross-epic row X1):**
    `resolve_config_dir(override_env, xdg_subdir, default_subdir, *, environ=None, home=None)`,
    identical to the approvals epic's `T-drPIif` definition; `resolve_state_dir` gains the same
    keyword-only `environ`/`home` and accepts `override_env=None`. One implementation for both
    epics; the final signature is recorded here when implemented.
  - **fsutil (security M6):** creation with `os.mkdir(mode=0o700)` per component; verification and
    fixes through `O_NOFOLLOW` fds + `os.fchmod`; no path-based `chmod`.
  - **Parent rule (security L6):** a group-writable parent owned by the euid now **warns**; tests
    under `umask 002` and with a symlinked parent ("check ln").
  - **Moved out:** `default_denied_paths` and `entry_is_denied` go to
    T-Hd4wQ2-auth-browse-denial-log-scrub (which appends them to `paths.py` after this task).
  - **New edge:** T-s6sJmB → this task (design-review minor 2), late-binding, no stub.
  - `make_store` lives in `tests/auth/helpers/store.py` (helpers package, design-review M2).
  - `required_features` is cut-line #4 (HLD §24.1).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: TASK.md aligned with the HLD v2 cross-check (HLD §28.8): added the single `with_identity` wrapper (HLD §11.9) plus `check_private_paths`/`check_state_dir`, and the precise `remove_totp` NotEnrolledError rule (only when `set_required is None`).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope; the estimate rises from
  2.5 to 3 d. Changes:
  - **File primitives.** `fileio.py` is replaced by the neutral shared `fsutil.py`, plus the
    additive `xdg.resolve_config_dir` (reviewer finding R-8).
  - **Store hygiene:** stale-temp cleanup and parent-directory checks (dev-security #12).
  - **Forward compatibility:** `extra="allow"` and `required_features` (dev-critic C-1).
  - **Revocation:** an immutable `user_id`, CAS writes and STALE outcomes (dev-security #1,
    dev-critic C-2).
  - **New:** enrollment-token storage (dev-security #5); the tri-state `count_store_users`
    (dev-security #10).
  - **Moved out:** lockouts and phantoms now live in `lockouts.json` (T-CsT5gk; reviewer finding
    R-7b), so the v1 `LockoutPolicy` coordination note is obsolete.
  - Design and tickets only; **no code written**.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the E-Da5Tn9
  design package (design and tickets only; **no code written**). The specification is in HLD §11.4,
  §11.5, §11.9 and §12.1–§12.2.

## Evidence
Run in the worktree on branch `ad/dashboard-auth-totp`, 2026-10-05:
- `.venv/bin/python -m pytest -q tests/test_fsutil.py tests/test_xdg.py tests/auth/test_paths.py tests/auth/test_store.py tests/auth/test_import_boundary.py tests/auth/test_foundation.py --cov=agent_orchestrator.fsutil --cov=agent_orchestrator.xdg --cov=agent_orchestrator.auth.paths --cov=agent_orchestrator.auth.store --cov-report=term-missing`
  -> **384 passed**. Coverage: `fsutil` 99 %, `xdg` 100 %, `auth/paths` 100 %, `auth/store` 99 % (AC-16 >= 90 %).
  Before this task the same files did not exist (`test_xdg.py` had 9 cases, all still passing).
- The spawn tests (20-process `add_user`, two-process `consume_totp`, `FileLock` contention and
  kill) were run 3 times in a row: 30 passed each time (no flakes).
- `.venv/bin/ruff check` and `ruff format --check` on every touched file: clean.
- `.venv/bin/mypy src`: only the 4 pre-existing `_version.py` errors (`_build_info`/`_version` excluded).
- AC mapping: AC1 `tests/test_xdg.py`; AC2 `tests/auth/test_paths.py`; AC3-5 `tests/test_fsutil.py`
  (+ `TestCheckPrivatePaths`/`TestCheckStateDir` for the `UnsafePermissionsError` mapping);
  AC6-15 `tests/auth/test_store.py`. `tests/auth/schemas/auth-users-v1.json` is the verbatim HLD
  12.1 schema, asserted after every mutation kind.
- Not run: the full suite (ticket instruction: targeted tests only).

## Risks / Blockers
- None open. Residual: `ensure_private_dir` judges only the parent's mode and (for group-write) owner, not a foreign-owned 0755 parent; HLD 11.5 does not ask for more.
- `fsutil` must stay neutral (no `agent_orchestrator.auth` import), so it raises
  `UnsafePathError`, which `auth.paths.check_private_paths` maps to `UnsafePermissionsError`.

- Coordinate `xdg.py` with the approvals epic (HLD §16 X1): the manager confirms the defaults
  (`environ=None` → `os.environ`, `home=None` → `Path.home()`) with the approvals owner.

## Next actions
0. Done (2026-10-05). Hand-over: T-Hd4wQ2 may append `default_denied_paths` / `entry_is_denied` to `paths.py`.
1. (historical) developer: implement once T-kzEzwy lands (`fsutil` and `xdg` first; the `consume_*` mutations
   last, after T-s6sJmB's `totp.py`/`recovery.py`), run the TASK.md verification, and record the
   results, the coverage and the final `fsutil`/`xdg` signatures here.
