# STATUS

- ID: `T-kwwJ82-auth-sessions-policy-principal`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane C)
- Scope: `MVP` · Sprint: `S1` · Estimate: `2 d`

## This update
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
- None yet.

## Risks / Blockers
- None. OQ-8 is DECIDED (v2.1, owner decision): `roles: list[str]` with `hash=False` and
  keyword-only additive fields. `principal.py` may be merged as specified.

## Next actions
1. developer: implement once T-kzEzwy lands, run the TASK.md verification (including the S30
   aliasing tests), and record the results here.
