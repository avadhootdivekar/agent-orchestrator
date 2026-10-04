# TASK: T-kwwJ82-auth-sessions-policy-principal

## Metadata
- Task ID: `T-kwwJ82-auth-sessions-policy-principal`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane C)
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `2 days` · Sprint `S1`

## Requirements Mapping
- Requirement IDs: FR-8, FR-9 (the policy table), FR-11, FR-27 (proof storage and check), FR-31 (sessions keyed by `user_id`), NFR-4, NFR-7
- ACs: AC-14 (session part), AC-15 (expiry part, `FakeClock`), AC-11 (`Principal` shape part); supports AC-12 (via `policy_allows`)
- Design: HLD §2.6, §11.10 (`sessions.py`), §11.14 (`principal.py`, `policy.py`), §11.15.1 (`VerifiedIdentity` fields), §12.3; HLD D3, D12, D23, D25; ADR-0021 D1, D6, D11

## Description
Build the framework-free session, identity and policy core that the middleware and routes depend
on. Constants, errors, `model.py` and the seams come from T-kzEzwy.

- **`auth/sessions.py`** (L3; HLD §11.10):
  - `SessionRecord` with the v2 fields: `proof_hash`, `user_id`, `provider`, `roles` (tuple),
    `amr`, `auth_time`, `client_key`, the single monotonic timeline (`created_mono`,
    `last_activity_mono`, `absolute_deadline_mono`), and `pending_*`. Secrets use `repr=False`.
  - The `SessionStore` ABC (`get`, `put`, `delete`, `records`) and `InMemorySessionStore` (a dict
    and a `threading.Lock`; `get()` returns a **copy**).
  - `IssuedSession(token, proof, record)` and `SessionTimes(idle_timeout_seconds, idle_expires_at,
    absolute_expires_at)`.
  - `SessionManager(store, *, realm, idle_seconds, absolute_seconds, clock, entropy)` with:
    `issue`, `lookup`, `proof_matches`, `touch`, `record_second_factor_failure`,
    `set_pending_secret`, `record_confirm_failure`, `clear_pending`, `destroy`,
    `destroy_user_sessions(user_id, *, except_session_id)`, `principal_for` and `times`.
  - **Every mutation ends in `store.put()`** (reviewer finding R-5).
  - The module helper `amr_for(auth_method, second_factor)`.
  - Expiry runs on `Clock.monotonic()` **only** (`CLOCK_BOOTTIME` in production). There is no
    dual-clock logic.
- **`auth/provider.py` (seed only):** create the module with **only** the frozen `VerifiedIdentity`
  dataclass, with exactly the HLD §11.15.1 fields: `user_id`, `username`, `roles`,
  `credential_epoch`, `store_id`, `next_state`, `provider = LOCAL_PROVIDER_ID`. `issue()` needs it
  in S1. T-XchniS later adds `ClientInfo`, `UserView`, `Revalidation` and the `AuthProvider` ABC
  to the same file and must not redefine this dataclass.
- **`auth/principal.py`** (L0; HLD §2.6 and §11.14):
  - `@dataclass(frozen=True, slots=True) class Principal` with exactly these fields, in this order:
    `username`, `auth_method`, `roles: tuple[str, ...]`, `user_id`, `realm`, `session_id`,
    `amr: tuple[str, ...]`, `auth_time`, `provider`;
  - `current_principal(request)`, `auth_enabled(request)` and `require_principal(request)`, which
    read `request.state` through the `SCOPE_*` constant names.
- **`auth/policy.py`** (L1; HLD §11.14):
  - `RoutePolicy`, `RouteKey`;
  - `AUTH_ROUTE_POLICIES`, `DASHBOARD_ROUTE_POLICIES`, `HUB_ROUTE_POLICIES`, with paths from
    `constants.py`;
  - `proof_required(policy, route_path_is_api)`, `policy_allows(policy, state)`, and
    `totp_requirement(policy, user_totp_required) -> TotpRequirement`. This is the **only** place
    that decides "TOTP is required for this user".
  - Denial codes come from `model.denial_code(state)` (T-kzEzwy); `policy.py` does not duplicate
    them.

## Inputs / Outputs
- **Inputs:** HLD §2.6, §11.10, §11.14, §11.15.1, §12.6; T-kzEzwy (`constants`, `errors`, `model`,
  `seams`, `tests/auth/helpers.py`).
- **Outputs:**
  - `src/agent_orchestrator/auth/sessions.py`, `principal.py`, `policy.py`
  - `src/agent_orchestrator/auth/provider.py` (`VerifiedIdentity` only)
  - `tests/auth/test_sessions.py`, `tests/auth/test_policy.py`, `tests/auth/test_principal.py`
  - `tests/auth/helpers.py` (+`make_identity(**overrides) -> VerifiedIdentity`)

## Acceptance Criteria
1. **Token and proof.**
   - `issue()` returns an `IssuedSession` whose `token` and `proof` are each 43 base64url
     characters, decode to 32 bytes, and differ from each other.
   - The record stores `sha256` of each.
   - Neither raw value appears in `repr(record)`, in `repr(issued)`, or in any record field.
   (`test_sessions.py`)
2. **`lookup`** returns `None` for: `None`; the wrong length; non-base64url characters; an unknown
   token; the **proof** passed as a cookie; an expired session (which it also deletes).
3. **`proof_matches`.**
   - True for the issued proof.
   - False for `None`, the wrong length, non-base64url input, another session's proof, and the
     session's own **token**.
   - The comparison goes through `hmac.compare_digest` (spy).
4. **Expiry (AC-15 part; `FakeClock`, single timeline).**
   - FULL: still found at `idle_seconds - 1` with no activity, and gone at `idle_seconds`.
     `touch()` extends idle expiry. The absolute deadline always applies.
   - PARTIAL: expires at `PARTIAL_SESSION_TTL_SECONDS`, and `touch()` does not extend it.
   - `FakeClock.advance_wall(10 * idle_seconds)` alone never expires a session.
   - `FakeClock.advance_mono(idle_seconds)` alone does: the monotonic clock is the timeline, which
     covers suspend.
5. **Rotation and identity (AC-14 part).**
   - `issue(..., replacing=old)` makes the old token and the old proof unusable.
   - FULL → FULL with `keep_absolute_deadline=True` keeps `absolute_deadline_mono` and `auth_time`.
   - PARTIAL → FULL gets a fresh absolute deadline, and `auth_time == clock.now_utc()`.
   - `amr_for`: `password` → `("pwd",)`; TOTP → `("pwd", "otp", "mfa")`; recovery code →
     `("pwd", "rcv", "mfa")`. Partial sessions have `amr == ()` and `auth_time is None`.
   - `credential_epoch`, `user_id` and `roles` are copied from the `VerifiedIdentity`.
6. **Mutations end in `put()`.** A spying `SessionStore` subclass sees exactly one `put()` for each
   of `touch`, `record_second_factor_failure`, `set_pending_secret`, `record_confirm_failure` and
   `clear_pending`. Mutating the object returned by `InMemorySessionStore.get()` does not change
   the stored record.
7. **Attempt counters.**
   - With `MAX_SECOND_FACTOR_ATTEMPTS = 5`, `record_second_factor_failure` returns 4, 3, 2, 1, 0
     and destroys the session at 0.
   - `record_confirm_failure` clears `pending_totp_secret` at `MAX_ENROLL_CONFIRM_ATTEMPTS`.
   - `set_pending_secret` resets `pending_confirm_failures` to 0.
8. **Bounds** (constants patched small where needed).
   - The 33rd session for one `user_id` evicts that user's oldest session.
   - The partial count never exceeds `MAX_PARTIAL_SESSIONS`, and the total never exceeds
     `MAX_SESSIONS_TOTAL`; the session with the oldest `last_activity_mono` is evicted.
   - Expired sessions are garbage-collected on `issue`.
9. **`destroy_user_sessions(user_id, except_session_id=s)`** removes every session of that
   `user_id` except `s` and returns the count. Sessions of a **different `user_id` with the same
   username** (the remove-then-re-add case) are untouched.
10. **`principal_for`.** A FULL record yields a `Principal` with exactly the record's values. A
    non-FULL record raises `ValueError`.
11. **`times()`.** With `FakeClock`, `idle_expires_at` and `absolute_expires_at` are aware UTC
    datetimes equal to `now_utc + (deadline - monotonic_now)`. For a FULL session, `idle_expires_at`
    is the earlier of the idle and absolute deadlines. For a partial session, both equal the
    partial deadline. `idle_timeout_seconds` equals `idle_seconds` (FULL) or
    `PARTIAL_SESSION_TTL_SECONDS` (partial).
12. **`Principal` contract (AC-11 part).** (`test_principal.py`)
    - `[f.name for f in dataclasses.fields(Principal)]` equals the §2.6 order exactly.
    - Assigning an attribute raises `FrozenInstanceError`; there is no `__dict__` (slots);
      `hash(p)` works; `roles` and `amr` are tuples.
    - On `SimpleNamespace` stand-ins:
      - no `state` → `current_principal` is `None`, `auth_enabled` is False, and
        `require_principal` returns `None`;
      - `auth_enabled=False` → `require_principal` returns `None`;
      - `auth_enabled=True` with a principal → that principal;
      - `auth_enabled=True` without one → `AuthError(NOT_AUTHENTICATED)`.
13. **Policy tables** (`test_policy.py`).
    - `AUTH_ROUTE_POLICIES` has exactly the 6 entries of §11.14. `DASHBOARD_ROUTE_POLICIES` adds
      exactly 4; `HUB_ROUTE_POLICIES` adds exactly 2.
    - The hub table contains neither `("GET", "/")` nor `/api/service/status`.
    - `policy_allows` matches the §11.14 4×4 table in 16 parametrized cells. For every denied cell,
      `model.denial_code(state)` equals the code in the table.
    - `proof_required` is True for every non-PUBLIC policy on an API path, and False for PUBLIC and
      for any non-API path.
    - `totp_requirement`:
      - (`off`, False) and (`optional`, False) → `NONE`;
      - (`required`, False), (`required`, True) and (`optional`, True) → `ENROLL_ALLOWED`;
      - (`off`, True) → `BLOCKED`.
14. **Layering.** T-kzEzwy's boundary test passes with the new modules. `principal.py` and
    `policy.py` import only L0 and L1 modules. Nothing imports fastapi or starlette.
15. ruff and mypy are clean. Coverage of `sessions.py`, `principal.py` and `policy.py` is ≥ 95 %.

## Risks
- **OQ-8 (blocker for freezing `principal.py`).** v2 changes `roles` from the brief's `list[str]` to
  `tuple[str, ...]` and adds `user_id`, `amr`, `auth_time` and `provider`. **The manager must
  confirm this with the approval-gates epic before `principal.py` is merged.** Until then, implement
  it as specified but do not merge.
- **Contract stability.** `Principal` and the policy tables are consumed by T-G7qByZ and by the
  sibling epic. Do not rename them without manager sign-off.
- **Copy semantics.** Code that mutates a `get()` copy without `put()` silently loses updates. AC-6
  guards this.

## Dependencies
- **Upstream:** T-kzEzwy.
- **Downstream:**
  - T-XchniS (`VerifiedIdentity`, `SessionManager`);
  - T-yfrfxv;
  - T-G7qByZ (policy tables, `principal_for`, `lookup`);
  - T-QJ1vyQ (`proof_matches`, `proof_required`);
  - T-rpKCjP and T-KQ6ZrY (`issue`, rotation, counters).

## Pseudocode / Algorithm
HLD §11.10 (`issue`, `lookup`, `proof_matches`, `amr_for`) and §11.14 (helpers, tables,
`totp_requirement`). Task-level steps:

```text
enforce_bounds(new_record):                       # called inside issue(), before store.put(new_record)
  now = clock.monotonic()
  FOR rec IN store.records(): IF expired(rec, now): store.delete(rec.token_hash)
  same_user = [r FOR r IN store.records() IF r.user_id == new_record.user_id]
  WHILE len(same_user) >= MAX_SESSIONS_PER_USER: evict(oldest by last_activity_mono IN same_user)
  IF new_record.state != FULL: WHILE count(partial) >= MAX_PARTIAL_SESSIONS: evict(oldest partial)
  WHILE len(store.records()) >= MAX_SESSIONS_TOTAL: evict(oldest overall)

times(rec):
  mono = clock.monotonic(); wall = clock.now_utc()
  abs_at = wall + (rec.absolute_deadline_mono - mono)
  idle_at = min(wall + (rec.last_activity_mono + idle_seconds - mono), abs_at) IF rec.state == FULL ELSE abs_at
  RETURN SessionTimes(idle_seconds IF FULL ELSE PARTIAL_SESSION_TTL_SECONDS, idle_at, abs_at)
```

## Schemas / Interface Notes
- `SessionRecord`: HLD §11.10.
- `Principal`: HLD §2.6.
- `VerifiedIdentity`: HLD §11.15.1. It is seeded here and extended by T-XchniS.
- Constants: HLD §12.6.

## Handoff Boundary
- **Upstream:** the HLD and T-kzEzwy.
- **Downstream:** `SessionManager`, `Principal` and the policy helpers and tables are consumed by
  the provider and runtime (T-XchniS), the middleware (T-G7qByZ, T-QJ1vyQ) and the routes
  (T-rpKCjP, T-KQ6ZrY). `principal_for` is the **only** `Principal` builder.

## Verification

```
python -m pytest -q tests/auth/test_sessions.py tests/auth/test_policy.py tests/auth/test_principal.py tests/auth/test_import_boundary.py
python -m pytest -q tests/auth --cov=agent_orchestrator.auth.sessions --cov=agent_orchestrator.auth.principal --cov=agent_orchestrator.auth.policy --cov-report=term-missing
ruff check src/agent_orchestrator/auth tests/auth && mypy src/agent_orchestrator/auth
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-kwwJ82-auth-sessions-policy-principal/`
