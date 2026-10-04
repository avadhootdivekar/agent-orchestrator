# STATUS

- ID: `T-yfrfxv-auth-provider-second-factor`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane C)
- Scope: `MVP` · Sprint: `S2` · Estimate: `2 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: TASK.md aligned with the HLD v2 cross-check (HLD §28.8): guarded web writes now use `store.with_identity`; a missing enrollment token is a counted failed attempt; the token is consumed by `begin`.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: Task **created in v2**. It is split out
  of T-XchniS, which was over the 3-day cap (developer D-4) and too large (reviewer R-4).
  - It holds `LocalTotpService`: second-factor verify, enrollment begin/confirm, disable and
    regenerate. Every credential check goes through the shared `AttemptGuard`.
  - **New v2 behaviour:**
    - forced enrollment needs the CLI-issued enrollment token (dev-security #5, D7);
    - E4, E5 and E7 are refused over plain HTTP from non-loopback clients (dev-security #4);
    - consumes are keyed by `user_id` and epoch, and STALE → 401 (dev-security #1, D10).
  - **Task-level precisions,** reported to the architect: the `classify_code` rule for the single
    `code` field of E6/E7; the identity guard on web-path `remove_totp`/`replace_recovery_codes`; and
    `begin_enrollment` checking `result.ok`.
  - Design only; no code written.

## Evidence
- None yet.

## Risks / Blockers
- None. It starts when T-XchniS lands.

## Next actions
1. developer: implement after T-XchniS, run the verification, and record coverage here.
