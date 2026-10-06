# STATUS

- ID: `T-CsT5gk-auth-throttle-audit-scrub`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (lane Q)
- Scope: `MVP` · Sprint: `S1` · Estimate: `3 d`

## This update
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented `auth/lockouts.py`,
  `auth/throttle.py`, `auth/audit.py` with tests and both schema copies; all ACs (1-13, 15) met,
  no cut-line applied (rotation, coalescing and the phantom table are implemented).
  Decisions and deviations to know:
  - `AuditLog(paths: StorePaths, *, clock, strict, max_bytes, backups, failures_per_minute,
    lock_timeout)` follows this ticket (the HLD sketch shows `audit_file, audit_lock`);
    `AuditLog.for_state_dir` builds a `StorePaths` whose store dir is the state dir (only the
    audit paths are used). `NullAuditLog` is included (HLD 11.12).
  - `lockouts.enter_state_lock` (public) is the one helper that takes a state-dir sidecar flock and
    creates the state dir at 0700; `audit.py` reuses it (no duplicated lock code).
  - Lockout timestamps have 1 s resolution; `locked_until` is rounded **up**, so a lock is never
    shortened. A corrupt file raises `StoreUnavailableError` from `state`/`record_failure`/`reset`/
    `forget`/`name_digest`; `check_readable` raises `StoreCorruptError`; lock timeout raises
    `StoreLockTimeoutError` (same as `UserStore`).
  - Phantom eviction never evicts the entry just written (timestamp ties).
  - Flood coalescing counts events with `outcome == failure` or in `FAILURE_CLASS_EVENTS`
    (excluding `auth.lockout`); a non-strict malformed event name is written as
    `audit.invalid_event` so lines stay schema-valid.
  - `USERNAME_RE` (compiled `USERNAME_PATTERN`) lives in `lockouts.py`.
  - `lockouts.py` imports `store._field_paths` (private helper, same layer) to avoid duplicating
    the no-value validation-error renderer.
  - Test helper module `tests/auth/helpers/state.py` (+ row in `helpers/__init__.py`).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate unchanged (3 d); scope rebalanced.
  - **Moved out:** `scrub.py` (redaction, filters, record factory; v2 ACs 14–15) now belongs to
    T-Hd4wQ2-auth-browse-denial-log-scrub. The ID and slug of this task are unchanged.
  - **Keyed username digests (security L1):** `lockouts.json` gains an optional `name_key_hex`;
    `LockoutStore` gains `entropy`, `ensure_name_key()` and `name_digest()` (HMAC-SHA256; all
    names failing `USERNAME_RE` share one `INVALID_USERNAME_BUCKET` digest). Phantom keys use it;
    `audit.username_hash()` is removed (callers pass the digest prefix). New AC 3b; AC 3 and AC 8
    updated; the schema copy includes `name_key_hex`.
  - **Cut-lines (HLD §24.1):** #1 audit rotation + coalescing (AC 10, AC 13) and #2 the phantom
    table (AC 3) are this task's stretch items; implement them last.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: TASK.md aligned with the HLD v2 cross-check (HLD §28.8): added `LockoutStore.check_readable()` (used by `check_ready`); HLD §11.11–§11.13 now carry the full v2 module specs this ticket implements.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope; the estimate rises from
  2 to 3 d and the sprint is now S1 only. Changes:
  - **New `lockouts.py`.** Lockouts and phantoms move out of `users.json` into `lockouts.json` in
    the state directory, keyed by `user_id` (reviewer findings R-1 and R-7b, dev-security #1). The
    phantom cap rises to 4096 (dev-security #9).
  - **`canonical_client_key`** added (dev-security #3).
  - **Audit:**
    - moves to the state directory;
    - the `auth.*`-only detail allowlist replaces `register_detail_keys` (reviewer finding R-7c);
    - a strict mode for tests, and fail-open in production (reviewer finding R-6);
    - flood coalescing into `auth.failure.burst` (dev-critic C-3).
  - **Scrub:** a LogRecord-factory redaction, plus the proof and token patterns (dev-security #11).
  - **Moved out:** `audit_log_for` moves to `runtime.py` (T-XchniS) to respect layering rules R4
    and R5. `guard.py` is T-XchniS.
  - Design and tickets only; **no code written**.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the E-Da5Tn9
  design package (design and tickets only; **no code written**). The audit seam (`AuditLog.record`)
  is consumed by the approval-gates epic (HLD §2.6).

## Evidence
- By: developer · Role: developer · Date: 2026-10-05 · Comment: results below (worktree
  `ad/dashboard-auth-totp`).
- `.venv/bin/python -m pytest -q tests/auth/test_lockouts.py tests/auth/test_throttle.py tests/auth/test_audit.py tests/auth/test_import_boundary.py` -> 153 passed
  (64 lockouts, 28 throttle, 39 audit).
- `.venv/bin/python -m pytest -q tests/auth --cov=agent_orchestrator.auth.lockouts --cov=agent_orchestrator.auth.throttle --cov=agent_orchestrator.auth.audit --cov-report=term-missing`
  -> 1113 passed; coverage audit.py 100%, throttle.py 100%, lockouts.py 98% (uncovered: refuse-to-write
  validation guard and a stat failure after write).
- `.venv/bin/ruff check` + `ruff format --check` on the 3 modules, 3 test files and `helpers/state.py` -> clean.
- `.venv/bin/mypy src` -> only the 4 pre-existing `_version.py` errors; `MYPYPATH=src mypy` on the new tests -> clean.

## Risks / Blockers
- None. The audit-line JSON Schema is derived from the HLD §12.4 field list and example (see
  TASK.md, Schemas / Interface Notes).
- If the schedule slips, the manager may apply cut-lines #1 and #2 (HLD §24.1); record any cut
  here and in the epic STATUS.

## Next actions
1. Downstream: T-XchniS (`AttemptGuard`, `check_ready` -> `check_readable` + `ensure_name_key`,
   `audit_log_for` in `runtime.py`); T-j9dfsw (`reset(repair_corrupt=True)`, `forget`,
   `ensure_name_key` on mutations).
