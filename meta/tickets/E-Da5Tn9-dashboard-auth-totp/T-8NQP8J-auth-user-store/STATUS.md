# STATUS

- ID: `T-8NQP8J-auth-user-store`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane B)
- Scope: `MVP` · Sprint: `S1` · Estimate: `3 d`

## This update
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
- None yet.

## Risks / Blockers
- None. `fsutil` must stay neutral (no `agent_orchestrator.auth` import), so it raises
  `UnsafePathError`, which `auth.paths.check_private_paths` maps to `UnsafePermissionsError`.

- Coordinate `xdg.py` with the approvals epic (HLD §16 X1): the manager confirms the defaults
  (`environ=None` → `os.environ`, `home=None` → `Path.home()`) with the approvals owner.

## Next actions
1. developer: implement once T-kzEzwy lands (`fsutil` and `xdg` first; the `consume_*` mutations
   last, after T-s6sJmB's `totp.py`/`recovery.py`), run the TASK.md verification, and record the
   results, the coverage and the final `fsutil`/`xdg` signatures here.
