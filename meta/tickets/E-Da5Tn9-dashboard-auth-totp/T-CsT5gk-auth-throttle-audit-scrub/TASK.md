# TASK: T-CsT5gk-auth-throttle-audit-scrub

## Metadata
- Task ID: `T-CsT5gk-auth-throttle-audit-scrub`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane Q)
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `3 days` · Sprint `S1`

## Requirements Mapping
- Requirement IDs: FR-16, FR-17 (one lockout write per attempt, phantoms), FR-20, FR-23, NFR-4, NFR-8, NFR-9 (lockouts fail closed)
- ACs: AC-20 (throttle and lockout part), AC-21 (phantom part), AC-28; unit support for AC-24 (redaction)
- Design: HLD §11.11 (`lockouts.py`, `throttle.py`; `guard.py` is T-XchniS), §11.12 (`audit.py`), §11.13 (`scrub.py`), §12.1b, §12.4, §12.6; HLD D9, D18; ADR-0021 D3 (state directory), D9

## Description
Implement the mutable security state that lives outside the credential file, plus the audit log and
log redaction. Everything here writes **only** to the **state** directory (`StorePaths.state_dir`),
never to `users.json`.

- **`auth/lockouts.py`** (L2; HLD §11.11):
  - The pydantic models `LockoutState` and `LockoutFile` (`extra="allow"`,
    `hide_input_in_errors=True`).
  - The pure `LockoutPolicy`: `effective_failures`, `retry_after`, `register_failure`,
    `register_success`.
  - `LockoutKey(user_id | phantom)` and `phantom_key(normalized_username) -> str`: the 64-hex
    SHA-256.
  - `LockoutStore(paths, *, clock, lock_timeout)` with `state`, `record_failure`,
    `reset(user_id, *, repair_corrupt=False)`, `forget` and `check_readable()`. `check_readable`
    parses `lockouts.json` if present and raises `StoreCorruptError` if it is corrupt; T-XchniS's
    `check_ready` calls it (HLD §11.11, §11.15.3). It uses the same stat-cached snapshot,
    `fsutil.FileLock` and `atomic_write_bytes` pattern as `UserStore`.
  - Phantom entries are capped at `PHANTOM_LOCKOUT_MAX_ENTRIES` (4096), evicting the oldest
    `last_failure_at`. `accounts` entries are never evicted.
  - A corrupt `lockouts.json` raises `StoreUnavailableError`, which fails closed (§12.1b).
    `reset(..., repair_corrupt=True)`, used by `ao auth unlock`, rewrites it as an empty valid file
    and logs one WARNING.
- **`auth/throttle.py`** (L3; HLD §11.11):
  - `canonical_client_key(raw)`: unwraps IPv4-mapped IPv6; aggregates IPv6 to
    `/IPV6_THROTTLE_PREFIX_LEN`; maps anything else to `UNKNOWN_CLIENT_KEY` (dev-security #3).
  - `AddressThrottle(threshold, *, clock, window, base, max, max_entries)`: in memory, an LRU over
    per-key deques. History per key is bounded at `threshold + ADDRESS_HISTORY_SLACK`.
  - `UsernameGates(max_entries)`: `async with gates.hold(name)`; a held lock is never evicted.
- **`auth/audit.py`** (L2; HLD §11.12):
  - `AuditOutcome` (`success` / `failure` / `info`) and the frozen `AuditEvent`. Fields: `event`
    (`AuditEventName`, or a namespaced string for other epics), `outcome`, `username`,
    `username_hash`, `user_id`, `realm`, `session_id`, `auth_method`, `client_addr`, `details`.
  - `username_hash(normalized) -> str`: the first `AUDIT_USERNAME_HASH_CHARS` hex characters of its
    SHA-256.
  - `AUTH_DETAIL_KEYS` applies to `auth.*` events **only**. There is no `register_detail_keys`
    (reviewer finding R-7c).
  - `AuditLog(paths, *, clock, strict=False)` and
    `AuditLog.for_state_dir(state_dir, *, clock=SYSTEM_CLOCK, strict=False)`. The latter is the
    constructor the approvals epic uses when auth is off; OQ-5's `for_store_dir` refers to it.
  - `record(event)`:
    - validation (strict mode raises `ValueError`; non-strict writes a reduced event and logs ERROR
      once per event name);
    - the `audit.lock` flock;
    - rotation at `AUDIT_MAX_BYTES` with `AUDIT_BACKUP_COUNT` backups;
    - one compact line, then fsync;
    - mode 0600; the state directory is created at 0700 if missing.
  - **Fail-open:** an OS or lock failure gives one ERROR log line naming the event, never the
    payload.
  - **Flood coalescing:** above `AUDIT_FAILURE_EVENTS_PER_MINUTE` failure-outcome events per
    wall-clock minute per process, further failure events are counted, not written. The next
    `record()` in a later minute first writes `auth.failure.burst` with
    `details={"suppressed": n}`. `flush_suppressed()` writes a pending burst immediately; the
    server calls it at shutdown. `auth.lockout` is never suppressed.
  - **`audit_log_for(request)` is not in this module.** It needs `runtime_of(app)` (L3), so it
    lives in `runtime.py` (T-XchniS) to keep rules R4 and R5.
- **`auth/scrub.py`** (L2; HLD §11.13):
  - `REDACTED`, `SECRET_PATTERNS` (the v2 list, including the `X-AO-Session-Proof` value,
    enrollment tokens and `session_proof` / `enrollment_token` keys), `redact()`;
  - `SecretRedactingFilter` and `auth_logger(name)` (idempotent);
  - `install_log_redaction()`: a process-wide `logging.setLogRecordFactory` wrapper that redacts
    `record.getMessage()` once per record. It is idempotent through a marker attribute and chains
    to the previous factory (dev-security #11).

## Inputs / Outputs
- **Inputs:**
  - HLD §11.11–§11.13, §12.1b, §12.4, §12.6;
  - T-kzEzwy (`constants`, `errors`, `model.AuditEventName`, `seams`);
  - T-8NQP8J (`fsutil`, `StorePaths`). Stub them with the HLD signatures if they are not merged
    yet.
- **Outputs:**
  - `src/agent_orchestrator/auth/lockouts.py`, `throttle.py`, `audit.py`, `scrub.py`
  - `tests/auth/test_lockouts.py`, `test_throttle.py`, `test_audit.py`, `test_scrub.py`
  - `tests/auth/schemas/auth-lockouts-v1.json` (a verbatim copy of §12.1b)
  - `tests/auth/schemas/auth-audit-line-v1.json` (the §12.4 line, with the field list under
    Schemas / Interface Notes below)

## Acceptance Criteria
1. **`LockoutPolicy`** (defaults `threshold=5, base=30, max=900`; `FakeClock`).
   - Failures 1–4: no lock. Failure 5: `retry_after == 30`.
   - Failures 6, 7, 8 and 9, each after waiting out the previous lock: 60, 120, 240, 480.
   - Failure 10 and later: 900.
   - `register_success()` resets to zero.
   - A failure more than `LOCKOUT_RESET_AFTER_SECONDS` after the previous one counts as failure #1.
   (`test_lockouts.py`)
2. **`LockoutStore` (AC-20 part).**
   - `state()` on a missing file → `LockoutState()`.
   - `record_failure(LockoutKey(user_id=u))` persists to `<state_dir>/lockouts.json`, with the file
     at 0600 and the directory at 0700. The `users.json` bytes and mtime are unchanged.
   - **Two `LockoutStore` instances (two realms) sharing one state directory share counts:** three
     failures through A plus two through B → locked.
   - `reset(u)` clears the entry, and writes nothing when the entry is already clear (inode and
     mtime unchanged). `forget(u)` removes the key.
   - The written file validates against `tests/auth/schemas/auth-lockouts-v1.json`. Unknown fields
     survive a rewrite.
   - Four spawn processes × 25 `record_failure` calls on one user give `failures == 100` (no lost
     updates; join timeouts; exit codes asserted).
3. **Phantoms (AC-21 part).**
   - `LockoutKey(phantom=phantom_key("ghost"))` is stored under `phantoms`.
   - With `PHANTOM_LOCKOUT_MAX_ENTRIES` patched to 3, a 4th distinct phantom evicts the one with the
     oldest `last_failure_at`, so the size stays 3.
   - `accounts` entries are never evicted.
   - The default value is 4096 (constants pin).
4. **Corruption fails closed.**
   - A corrupt `lockouts.json` makes `state()` and `record_failure()` raise
     `StoreUnavailableError`.
   - `reset(u, repair_corrupt=True)` rewrites a valid empty file and logs exactly one WARNING.
   - `reset(u)` without the flag raises.
5. **`canonical_client_key`** (`test_throttle.py`):
   - `"127.0.0.1"` → itself; `"::ffff:127.0.0.1"` → `"127.0.0.1"`;
   - `"2001:db8::1"` and `"2001:db8::ffff"` → `"2001:db8::/64"`;
   - `"2001:db8:0:1::1"` → `"2001:db8:0:1::/64"`; `"::1"` → `"::/64"`;
   - `None`, `""`, `"testclient"` and `"not-an-ip"` → `"unknown"`.
6. **`AddressThrottle`** (`threshold=3`, `FakeClock`).
   - 2 failures → `retry_after is None`; 3 → 1 s; 4 → 2 s.
   - Failures older than `ADDRESS_WINDOW_SECONDS` are pruned (→ `None`).
   - With `max_entries=2`, a third key evicts the least recently used one.
   - The per-key history never exceeds `threshold + ADDRESS_HISTORY_SLACK` entries.
7. **`UsernameGates`** (through `run_async`).
   - Two tasks holding the same name never overlap (recorded enter and exit order); different names
     do overlap.
   - When over capacity, a held entry is never evicted.
8. **Audit lines (AC-28).**
   - Every line validates against `tests/auth/schemas/auth-audit-line-v1.json`.
   - The file is `<state_dir>/audit.jsonl` (**not** the store directory), mode 0600.
   - `ts` has millisecond precision with a `Z` suffix. Keys are sorted and `ensure_ascii` is on.
   - `user_id` is present when given and `null` otherwise.
   - `username_hash("ghost")` is 16 hex characters.
9. **Validation.**
   - `strict=True` raises `ValueError` for:
     - a non-namespaced event (`"login"`);
     - an `auth.*` name that is not in `AuditEventName`;
     - an `auth.*` detail key outside `AUTH_DETAIL_KEYS`;
     - in any namespace, a dict value or a string longer than `AUDIT_MAX_DETAIL_CHARS`.
   - `approval.requested` with the detail key `approval_id` is accepted, with no registration.
   - `strict=False`, with the same invalid `auth.*` event, writes one line with the same event,
     outcome and identity fields and `details == {}`. It logs exactly one ERROR per event name: a
     second invalid event with the same name logs nothing more.
10. **Rotation.** With `max_bytes=200`, ten events produce `audit.jsonl` plus `.1` up to
    `.AUDIT_BACKUP_COUNT`, never more. Every line parses.
11. **Fail-open.** With `os.open` patched to raise `PermissionError`, `record()` returns normally,
    and exactly one ERROR record names the event, not its payload.
12. **Concurrency.** Four spawn processes × 25 events give exactly 100 valid lines.
13. **Coalescing** (`FakeClock`).
    - 61 failure events within one minute → 60 lines written.
    - The first `record()` in the next minute first writes `auth.failure.burst` (outcome `info`,
      `details == {"suppressed": 1}`).
    - `auth.lockout` events beyond the cap are still written.
    - `flush_suppressed()` writes a pending burst immediately.
14. **`redact()`** (`test_scrub.py`). Each sentinel is replaced with `[REDACTED]`, keeping its
    prefix:
    - `ao_sid_8765=<43>` and `__Host-ao_sid_8765=<43>`;
    - `X-AO-Session-Proof: <43>`;
    - an `otpauth://…` URI; a `$scrypt$…` hash; a 32-character Base32 secret;
    - a recovery code and an enrollment token (`XXXX-XXXX-XXXX-XXXX`);
    - `"password": "…"`, `password=…`, `current_password=…`, `new_password=…`, `code=123456`,
      `"session_proof": "…"`, `"enrollment_token": "…"`.

    Ordinary text (run ids such as `run-20261004-abc`, file paths) is unchanged.
15. **Filters.**
    - `SecretRedactingFilter`, attached through `auth_logger(__name__)`, redacts a record logged
      with arguments (`logger.info("x %s", secret)`).
    - Calling `auth_logger` twice attaches exactly one filter.
    - After `install_log_redaction()`, a record from `logging.getLogger("uvicorn.error")` (with
      `propagate=False`) has a message containing `[REDACTED]` and not the sentinel.
    - A second `install_log_redaction()` does not double-wrap (marker), and the previous factory is
      still called (spy).
16. ruff and mypy are clean. Coverage of `lockouts.py`, `throttle.py`, `audit.py` and `scrub.py` is
    ≥ 90 %.

## Risks
- **Regex false positives in `redact()`**, for example a 32-character upper-case run id. This is
  acceptable: redaction is defence in depth. Document the false-positive classes in the module
  docstring.
- **Fsync cost.** Audit and lockout writes happen on failures and admin actions only, never per
  request. The address throttle runs before any write.
- **Lost bursts at a crash.** A pending `auth.failure.burst` is lost if the process dies before the
  next event or `flush_suppressed()`. The count is advisory, so this is accepted.

## Dependencies
- **Upstream:** T-kzEzwy; T-8NQP8J (`fsutil`, `StorePaths`).
- **Downstream:**
  - T-XchniS (`AttemptGuard` composes the lockouts, the throttle, the gates and the audit;
    `audit_log_for` lives in `runtime.py`);
  - T-yfrfxv;
  - T-j9dfsw (`unlock` → `reset(repair_corrupt=True)`; `remove-user` → `forget`; CLI audit);
  - T-jVqH8w and T-PDGw9p (`install_log_redaction`);
  - T-U2ERMo (the scrub sweep).

## Pseudocode / Algorithm
HLD §11.11 (the worked example), §11.12 and §11.13. Task-level steps:

```text
LockoutStore.record_failure(key, policy):
  WITH fsutil.FileLock(paths.lockouts_lock, timeout=lock_timeout):
     f = load_fresh()                                   # corrupt -> StoreUnavailableError
     table = f.accounts IF key.user_id ELSE f.phantoms; k = key.user_id OR key.phantom
     new = policy.register_failure(table.get(k, LockoutState()), clock.now_utc()); table[k] = new
     IF key.phantom AND len(f.phantoms) > PHANTOM_LOCKOUT_MAX_ENTRIES:
        DELETE f.phantoms[argmin(last_failure_at)]      # the oldest goes; accounts are never evicted
     fsutil.atomic_write_bytes(paths.lockouts_file, canonical_json(f)); refresh cache
  RETURN new

AuditLog.record(event):
  TRY validate(event)                                   # strict -> ValueError propagates
  EXCEPT ValueError IF NOT strict: log_error_once(event.event); event = reduced(event)
  IF is_failure(event) AND event.event != AUTH_LOCKOUT AND over_minute_cap(): suppressed += 1; RETURN
  IF suppressed AND minute_changed(): write_line(burst(suppressed)); suppressed = 0
  write_line(event)                                     # flock, rotate, append, fsync; OSError/lock -> ERROR, RETURN
```

## Schemas / Interface Notes
- `lockouts.json`: HLD §12.1b.
- Audit line, as in HLD §12.4 plus its example. The fields are:
  - `v` (int, `AUDIT_SCHEMA_VERSION`), `ts` (string, ms, `Z`), `event`, `outcome`
    (`success|failure|info`), `pid` (int);
  - `realm` (string | null; `"cli"` for CLI events);
  - `username` (string | null), `username_hash` (16 hex | null), `user_id` (32 hex | null);
  - `session_id` (32 hex | null), `auth_method` (string | null), `client_addr` (string | null);
  - `details` (object).

  Readers tolerate unknown keys.
- Event names come only from `AuditEventName`; there are no string literals for `auth.*` events.

## Handoff Boundary
- **Upstream:** the HLD, T-kzEzwy and T-8NQP8J.
- **Downstream:**
  - `LockoutStore`, `LockoutPolicy` and `LockoutKey`;
  - `canonical_client_key`, `AddressThrottle` and `UsernameGates`;
  - `AuditLog`, `AuditEvent` and `username_hash`;
  - `redact`, `auth_logger` and `install_log_redaction`.

  `AttemptGuard` (T-XchniS) is the only production composer of the lockout and throttle pieces.

## Verification

```
python -m pytest -q tests/auth/test_lockouts.py tests/auth/test_throttle.py tests/auth/test_audit.py tests/auth/test_scrub.py tests/auth/test_import_boundary.py
python -m pytest -q tests/auth --cov=agent_orchestrator.auth.lockouts --cov=agent_orchestrator.auth.throttle --cov=agent_orchestrator.auth.audit --cov=agent_orchestrator.auth.scrub --cov-report=term-missing
ruff check src/agent_orchestrator/auth tests/auth && mypy src/agent_orchestrator/auth
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-CsT5gk-auth-throttle-audit-scrub/`
