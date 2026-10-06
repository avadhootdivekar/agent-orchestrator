# STATUS

- ID: `T-kwwJ82-auth-sessions-policy-principal`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (lane C)
- Scope: `MVP` · Sprint: `S1` · Estimate: `2 d`

## This update
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented and verified; State
  Done. Delivered `auth/sessions.py`, `principal.py`, `policy.py`, `provider.py`
  (`VerifiedIdentity` only), `tests/auth/test_{sessions,policy,principal}.py` and
  `tests/auth/helpers/sessions.py` (`make_identity`, plus a `make_manager` convenience).
  Implementation decisions (all within the HLD, none change a contract):
  - `session_id` is `entropy.token_bytes(16).hex()` (32 hex, audit-schema shape) instead of
    `uuid4().hex`, so it is deterministic under `SeededEntropy`.
  - `lookup`/`proof_matches` decode strictly: length, alphabet (`fullmatch`, so a trailing
    newline fails) and canonical re-encode (a non-canonical alias of a token does not match).
  - `amr_for` raises `ValueError` for an inconsistent `(auth_method, second_factor)` pair;
    `issue` raises for FULL without `auth_method`; a partial session never records a method.
  - `touch()` on a non-FULL record is a no-op (no put). Mutations of a record destroyed
    meanwhile are dropped instead of resurrecting it (`_save` re-checks the store); a live
    record still gets exactly one `put()`.
  - `Principal` and `principal_for` follow decision A exactly: positional minimum, `list`
    roles with `hash=False`, kw-only additive fields, fresh `list(record.roles)` per call.
  - Not touched: `auth/__init__.py` `_LAZY_EXPORTS` (shared with sibling lanes; later task may add).

- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate unchanged (2 d).
  - **Principal (owner decision, final; design-review B1, security M5; OQ-8 DECIDED):**
    `roles: list[str] = field(default_factory=list, hash=False)`; `user_id`, `realm`,
    `session_id`, `amr` (tuple), `auth_time`, `provider` are keyword-only; positional
    `Principal(username, auth_method, roles)` works. `SessionRecord`, `VerifiedIdentity` and
    `UserView` keep tuples; `principal_for` is the only converter and returns a fresh
    `list(record.roles)` per call. AC-10 compares `list(record.roles)`; AC-12 asserts a list,
    `hash(p)`, no shared lists, no mutation leak (S30), positional construction. RBAC forward note:
    role changes must bump `credential_epoch`.
  - **Policy (security M1):** `DASHBOARD_COOKIE_ONLY_NAVIGATION` (empty) and
    `HUB_COOKIE_ONLY_NAVIGATION` (`("GET", "/")`); `proof_required(policy, *,
    cookie_only_navigation)` is True for every non-PUBLIC policy unless flagged.
  - `make_identity` lives in `tests/auth/helpers/sessions.py` (design-review M2).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope; lane B → C. Changes:
  - **Moved to T-kzEzwy:** `constants`, `errors` and `seams` (developer finding D-3).
  - **Single timeline.** Sessions use one `CLOCK_BOOTTIME` monotonic clock instead of dual-clock
    expiry (reviewer finding R-7).
  - **Session proof** (D25): `proof_hash` and `proof_matches` (dev-security #2).
  - **Revocation:** sessions are keyed by `user_id` (dev-security #1).
  - **Mutation discipline:** every mutation ends in `put()`, and `principal_for` is the only
    `Principal` builder (reviewer findings R-1, R-5).
  - **Principal v2:** `user_id`, `amr`, `auth_time`, `provider`; `require_principal` and
    `auth_enabled` (dev-security #6, dev-critic C-4). (v2's `roles` tuple is superseded by v2.1's
    `list[str]`, above.)
  - **Policy:** per-app policy tables replace decorators (developer D-1, reviewer R-7), and
    `totp_requirement` is the single requirement predicate (reviewer R-4).
  - **`VerifiedIdentity`** is seeded in `provider.py` here.
  - Design and tickets only; **no code written**.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the E-Da5Tn9
  design package (design and tickets only; **no code written**). The `Principal` contract (HLD §2.6)
  is frozen for the approval-gates epic.

## Evidence
- `.venv/bin/python -m pytest -q tests/auth/test_sessions.py tests/auth/test_policy.py tests/auth/test_principal.py tests/auth/test_import_boundary.py tests/auth/test_foundation.py --cov=agent_orchestrator.auth.sessions --cov=agent_orchestrator.auth.principal --cov=agent_orchestrator.auth.policy --cov-report=term-missing`
  -> 326 passed; coverage sessions 100 %, principal 100 %, policy 100 % (AC-15 needs >= 95 %).
  Foundation tests (test_foundation, test_import_boundary) still pass: zero regressions.
- Layering: `tests/auth/test_import_boundary.py` passes with the new modules (principal L0,
  policy L1, sessions/provider L3; no fastapi/starlette import).
- `.venv/bin/ruff check` and `ruff format --check` on the 4 src files + 4 test files: clean.
- `.venv/bin/mypy src`: only the 4 pre-existing `_version.py` errors; the 4 new test files
  also type-check clean.
- AC mapping: AC-1..3 token/proof/lookup/proof_matches (incl. `compare_digest` spy); AC-4
  expiry with `FakeClock` single timeline; AC-5 rotation + `amr_for`; AC-6 spy store one put per
  mutation + copy semantics; AC-7 counters 4..0; AC-8 bounds (33rd session, partial cap, total
  cap, GC on issue); AC-9 `destroy_user_sessions` keyed by `user_id`; AC-10 `principal_for`
  aliasing (S30); AC-11 `times()`; AC-12 `Principal` contract; AC-13 policy tables and 16-cell
  matrix against `model.denial_code`; AC-14 layering; AC-15 lint/types/coverage.

## Risks / Blockers
- None. OQ-8 is DECIDED (v2.1, owner decision): `roles: list[str]` with `hash=False` and
  keyword-only additive fields. `principal.py` may be merged as specified.

## Next actions
None. Downstream: T-XchniS (extends provider.py), T-G7qByZ, T-KOv2qD, T-QJ1vyQ, T-rpKCjP, T-KQ6ZrY.
