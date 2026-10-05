# STATUS

- ID: `T-PlEROT-auth-settings-layering`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane A)
- Scope: `MVP` · Sprint: `S1` · Estimate: `3 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate 2.5 → 3 d.
  - **`ConfigRisk` detection (security M3; HLD §11.3.2 step 5, §11.3.4 rows 14–15):**
    `AuthSettings.config_risks` records `DISABLED_BY_CONFIG` (config-only `enabled: false` with
    accounts or an unknown count) and `TOTP_DOWNGRADED_BY_CONFIG` (auth on, config-only `totp`
    below `required`, accounts or unknown; plus a pin-it warning). `resolve_auth_settings` never
    raises for them; T-jVqH8w enforces and T-j9dfsw reports. New AC 12 (AC-4d / AC-45 detection);
    AC 11 widened (`count_users` also runs in the two step-5 cases).
  - Path helpers use the v2.1 `xdg` signature (`environ=`; design-review M1).
  - Lane A in S1 holds this task plus T-s6sJmB (5.5 dev-days), an overload the HLD accepts
    explicitly (§24.3 capacity note).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: TASK.md aligned with the HLD v2 cross-check (HLD §28.8): AC label aligned to HLD AC-4a (rows 1–5, 10, 12).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope; the estimate rises from
  2 to 2.5 d. Changes:
  - **Hermetic fixture removed.** The root fixture `_hermetic_auth_env` moved to T-kzEzwy, so it
    exists before any auth code (developer finding D-3, tester finding T-1).
  - **Launch adapters removed.** `cli_env_overrides`, `uvicorn_kwargs` and `protected_paths` moved
    to `auth/launch.py` (T-jVqH8w), leaving `AuthSettings` as pure data (reviewer finding R-3).
  - **Added:**
    - tighten-only workspace rules, with `trusted_proxies` accepted from CLI/env only
      (dev-security #10, A12);
    - `state_dir` resolution (D5);
    - the pure `decide()` (reviewer finding R-7);
    - a tri-state user count, where unknown fails closed (dev-security #10).
  - **AC-4 scope:** rows 1–5, 10 and 12 of HLD §11.3.4. Rows 6–8 and 11–13 are tested by their
    owners (T-XchniS, T-jVqH8w).
  - Design and tickets only; **no code written**.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the E-Da5Tn9
  design package (design and tickets only; **no code written**). v1 note: this task also owned the
  root hermetic test fixture (HLD §16 #10). That ownership is superseded by v2 above.

## Evidence
- None yet.

## Risks / Blockers
- None.

## Next actions
1. developer: implement once T-kzEzwy and T-8NQP8J land, run the TASK.md verification, and record
   the results here.
