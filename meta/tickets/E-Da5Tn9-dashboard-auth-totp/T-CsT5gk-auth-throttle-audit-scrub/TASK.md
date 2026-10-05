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
- Requirement IDs: FR-16, FR-17 (one lockout write per attempt, phantoms), FR-23, NFR-4, NFR-8, NFR-9 (lockouts fail closed)
- ACs: AC-20 (throttle and lockout part), AC-21 (phantom part, incl. the v2.1 keyed digests), AC-28
- Design: HLD §11.11 (`lockouts.py`, `throttle.py`; `guard.py` is T-XchniS), §11.12 (`audit.py`), §12.1b, §12.2, §12.4, §12.6, §24.1 (cut-lines), §28.9 (security L1, design-review M3); HLD D9, D18; ADR-0021 D3 (state directory), D9

**v2.1 scope note.** The task ID and slug are unchanged, but **`scrub.py` (log redaction) moved out**
to `T-Hd4wQ2-auth-browse-denial-log-scrub` (HLD §24.2, re-baseline to keep every task ≤ 3 d). The
freed half-day goes to the keyed username digests (security L1).

## Description
Implement the mutable security state that lives outside the credential file, plus the audit log.
Everything here writes **only** to the **state** directory (`StorePaths.state_dir`), never to
`users.json`.

- **`auth/lockouts.py`** (L2; HLD §11.11):
  - The pydantic models `LockoutState` and `LockoutFile` (`extra="allow"`,
    `hide_input_in_errors=True`).
  - The pure `LockoutPolicy`: `effective_failures`, `retry_after`, `register_failure`,
    `register_success`.
  - `LockoutKey(user_id | phantom)`.
  - **v2.1 (security L1; HLD §11.11, §12.1b, §12.2): keyed username digests.** `LockoutFile`
    gains an optional `name_key_hex: str | None = Field(None, repr=False)` (32 random bytes,
    `LOCKOUT_NAME_KEY_BYTES`). `LockoutStore` gains:
    - an `entropy: Entropy = SYSTEM_ENTROPY` constructor parameter;
    - `ensure_name_key()`: locked; creates `name_key_hex` if absent (one write); no write when it
      already exists. Called by `check_ready()` at startup (T-XchniS) and by mutating CLI
      commands (T-j9dfsw), so a login attempt never pays an extra write;
    - `name_digest(normalized_username) -> str` = `HMAC-SHA256(name_key, subject).hexdigest()`
      (64 hex), where `subject` is the name if it matches `USERNAME_RE`, else
      `INVALID_USERNAME_BUCKET` (`""`), so every malformed name shares **one** bucket. A missing key
      at runtime (file deleted) triggers `ensure_name_key()` once, logged once.
    - Phantom keys are `name_digest(...)`; the v2 unkeyed `phantom_key = sha256(...)` is removed.
    - Losing `lockouts.json` rotates the key (old phantoms and old audit hashes stop correlating;
      accepted, HLD §11.11).
  - `LockoutStore(paths, *, clock, entropy, lock_timeout)` with `state`, `record_failure`,
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
  - **v2.1 (security L1):** there is **no** `username_hash()` helper in `audit.py` and `AuditLog`
    never hashes names. Callers pass `username_hash=lockouts.name_digest(name)[:AUDIT_USERNAME_HASH_CHARS]`
    for unknown names (the guard, T-XchniS).
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
- **Cut-lines (HLD §24.1, design-review M3).** If the manager cuts scope, this task's stretch items
  go first: **#1** audit rotation + flood coalescing (AC 10 and AC 13 become stretch), then **#2**
  the phantom table (AC 3; the guard then writes no lockout entry for unknown names). Implement
  them last and record any cut here and in the epic STATUS.
- **Not here any more:** `auth/scrub.py` → T-Hd4wQ2-auth-browse-denial-log-scrub.

## Inputs / Outputs
- **Inputs:**
  - HLD §11.11–§11.13, §12.1b, §12.4, §12.6;
  - T-kzEzwy (`constants`, `errors`, `model.AuditEventName`, `seams`, `tests/auth/helpers/core.py`);
  - T-8NQP8J (`fsutil`, `StorePaths`). Stub them with the HLD signatures if they are not merged
    yet.
- **Outputs:**
  - `src/agent_orchestrator/auth/lockouts.py`, `throttle.py`, `audit.py`
  - `tests/auth/test_lockouts.py`, `test_throttle.py`, `test_audit.py`
  - `tests/auth/schemas/auth-lockouts-v1.json` (a verbatim copy of the v2.1 §12.1b, including the
    optional `name_key_hex`)
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
3. **Phantoms (AC-21 part; cut-line #2).**
   - `LockoutKey(phantom=store.name_digest("ghost"))` is stored under `phantoms`.
   - With `PHANTOM_LOCKOUT_MAX_ENTRIES` patched to 3, a 4th distinct phantom evicts the one with the
     oldest `last_failure_at`, so the size stays 3.
   - `accounts` entries are never evicted.
   - The default value is 4096 (constants pin).
3b. **Keyed digests (v2.1, security L1; AC-21 part).** (`SeededEntropy`)
   - `ensure_name_key()` on a missing file writes one valid file with a 64-hex `name_key_hex`; a
     second call writes nothing (inode and mtime unchanged).
   - `name_digest("ghost")` is 64 hex, equals `hmac.new(key, b"ghost", sha256).hexdigest()`, and
     is **not** equal to the unkeyed `sha256(b"ghost").hexdigest()`.
   - Two stores with different keys give different digests for the same name.
   - `"Bad Name!"`, `"x" * 100` and `"a b"` (all failing `USERNAME_RE`) give the **same** digest:
     the `INVALID_USERNAME_BUCKET` digest.
   - With the key missing at runtime, `name_digest` creates it once and logs exactly once.
   - `name_key_hex` never appears in `repr()` of the model, nor in any log record (sentinel).
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
   - An event built with `username_hash=lockouts.name_digest("ghost")[:16]` validates (16 hex);
     `audit.py` exports no `username_hash` function (v2.1).
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
10. **Rotation** (cut-line #1 stretch). With `max_bytes=200`, ten events produce `audit.jsonl` plus `.1` up to
    `.AUDIT_BACKUP_COUNT`, never more. Every line parses.
11. **Fail-open.** With `os.open` patched to raise `PermissionError`, `record()` returns normally,
    and exactly one ERROR record names the event, not its payload.
12. **Concurrency.** Four spawn processes × 25 events give exactly 100 valid lines.
13. **Coalescing** (`FakeClock`; cut-line #1 stretch).
    - 61 failure events within one minute → 60 lines written.
    - The first `record()` in the next minute first writes `auth.failure.burst` (outcome `info`,
      `details == {"suppressed": 1}`).
    - `auth.lockout` events beyond the cap are still written.
    - `flush_suppressed()` writes a pending burst immediately.
14. *(v2 ACs 14–15 for `redact()` and the logging filters moved with `scrub.py` to
    T-Hd4wQ2-auth-browse-denial-log-scrub.)*
15. ruff and mypy are clean. Coverage of `lockouts.py`, `throttle.py` and `audit.py` is ≥ 90 %.

## Risks
- **The name key must exist before the first login attempt**, or S6's "exactly one lockout write"
  breaks for that one attempt. `check_ready()` (T-XchniS) calls `ensure_name_key()`; AC 3b covers
  the runtime fallback.
- **Fsync cost.** Audit and lockout writes happen on failures and admin actions only, never per
  request. The address throttle runs before any write.
- **Lost bursts at a crash.** A pending `auth.failure.burst` is lost if the process dies before the
  next event or `flush_suppressed()`. The count is advisory, so this is accepted.

## Dependencies
- **Upstream:** T-kzEzwy; T-8NQP8J (`fsutil`, `StorePaths`).
- **Downstream:**
  - T-XchniS (`AttemptGuard` composes the lockouts, the throttle, the gates and the audit;
    `audit_log_for` lives in `runtime.py`; `check_ready` calls `ensure_name_key`, `authenticate`
    calls `name_digest`);
  - T-yfrfxv;
  - T-j9dfsw (`unlock` → `reset(repair_corrupt=True)`; `remove-user` → `forget`; CLI audit;
    `ensure_name_key` on mutations);
  - T-U2ERMo (the audit file in the scrub sweep).
  - (`install_log_redaction` now comes from T-Hd4wQ2.)

## Pseudocode / Algorithm
HLD §11.11 (the worked example, v2.1 `name_digest`) and §11.12. Task-level steps:

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

LockoutStore.ensure_name_key():                         # v2.1, security L1
  IF cached key present: RETURN
  WITH fsutil.FileLock(paths.lockouts_lock, timeout=lock_timeout):
     f = load_fresh() OR LockoutFile()
     IF f.name_key_hex IS None: f.name_key_hex = entropy.token_bytes(LOCKOUT_NAME_KEY_BYTES).hex(); atomic write
     cache the key

LockoutStore.name_digest(normalized):
  key = cached key OR (ensure_name_key(); log_once("lockout name key was missing; created"))
  subject = normalized IF USERNAME_RE.fullmatch(normalized) ELSE INVALID_USERNAME_BUCKET
  RETURN hmac.new(bytes.fromhex(key), subject.encode("utf-8"), hashlib.sha256).hexdigest()

AuditLog.record(event):
  TRY validate(event)                                   # strict -> ValueError propagates
  EXCEPT ValueError IF NOT strict: log_error_once(event.event); event = reduced(event)
  IF is_failure(event) AND event.event != AUTH_LOCKOUT AND over_minute_cap(): suppressed += 1; RETURN
  IF suppressed AND minute_changed(): write_line(burst(suppressed)); suppressed = 0
  write_line(event)                                     # flock, rotate, append, fsync; OSError/lock -> ERROR, RETURN
```

## Schemas / Interface Notes
- `lockouts.json`: HLD §12.1b (v2.1: optional `name_key_hex`, 64 hex).
- Unknown-username digests: HLD §12.2 (v2.1, keyed HMAC).
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
  - `LockoutStore` (incl. `ensure_name_key`, `name_digest`), `LockoutPolicy` and `LockoutKey`;
  - `canonical_client_key`, `AddressThrottle` and `UsernameGates`;
  - `AuditLog` and `AuditEvent`.

  `AttemptGuard` (T-XchniS) is the only production composer of the lockout and throttle pieces.

## Verification

```
python -m pytest -q tests/auth/test_lockouts.py tests/auth/test_throttle.py tests/auth/test_audit.py tests/auth/test_import_boundary.py
python -m pytest -q tests/auth --cov=agent_orchestrator.auth.lockouts --cov=agent_orchestrator.auth.throttle --cov=agent_orchestrator.auth.audit --cov-report=term-missing
ruff check src/agent_orchestrator/auth tests/auth && mypy src/agent_orchestrator/auth
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-CsT5gk-auth-throttle-audit-scrub/`
