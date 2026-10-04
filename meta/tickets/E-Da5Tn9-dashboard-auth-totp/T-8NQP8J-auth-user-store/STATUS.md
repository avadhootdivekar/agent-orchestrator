# STATUS

- ID: `T-8NQP8J-auth-user-store`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane B)
- Scope: `MVP` · Sprint: `S1` · Estimate: `3 d`

## This update
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
- None yet.

## Risks / Blockers
- None. `fsutil` must stay neutral (no `agent_orchestrator.auth` import), so it raises
  `UnsafePathError`, which `auth.paths.check_private_paths` maps to `UnsafePermissionsError`.

## Next actions
1. developer: implement once T-kzEzwy lands (`fsutil` and `xdg` first), run the TASK.md
   verification, and record the results, the coverage and the final `fsutil`/`xdg` signatures here.
