# STATUS

- ID: `T-jVqH8w-ao-ui-auth-wiring`
- Updated At: `2026-10-05`
- State: `Draft`
- Owner: `developer` (lane B)
- Scope: `MVP` · Sprint: `S3` · Estimate: `3 d`

## This update
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate, lane and sprint unchanged (3 d, lane B, S3). Changes:
  - **Moved out:** the file-browser denial (`ui/files.py`, `ui/service.py`, old AC 8–9) →
    `T-Hd4wQ2-auth-browse-denial-log-scrub` (lands in S1 for the approvals epic, HLD §16 X2). This
    task still computes and passes `AuthLaunch.denied_paths` (now incl. `service.env`, L7).
  - **Security M3:** `prepare_auth` enforces `ConfigRisk.DISABLED_BY_CONFIG` (exit 78 + audit
    `auth.startup.disabled_by_config`) and audits `TOTP_DOWNGRADED_BY_CONFIG`; new AC 3 and AC 9
    (= AC-45 incl. the config `env:` block isolation test).
  - **Security L3 / L4:** the `--reload` call gets `**launch.uvicorn_kwargs` (tested with and
    without trusted proxies); port 0 with auth on → exit 78.
  - **Security M4:** merge edges — merges only after T-QJ1vyQ and T-KQ6ZrY; new upstream T-Hd4wQ2.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: TASK.md aligned with the HLD v2 cross-check (HLD §28.8): added AC-4c (row 9 via `prepare_auth`); `AO_UI_BOUND_PORT_ENV` is now an HLD §12.6 constant.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope after the Phase-4
  consultations (HLD §28).
  - **Estimate:** 2 d → 3 d.
  - **`auth/launch.py`.** The task now owns `prepare_auth` / `AuthLaunch` / `exit_code_for`
    (reviewer R-3). It is the one startup sequence shared by `ao ui`, `create_app_from_env` and
    `ao service run` (T-PDGw9p). The adapter methods that v1 had on `AuthSettings`
    (`uvicorn_kwargs()`, `cli_env_overrides()`, `protected_paths()`) are gone, and the CLI reads
    `launch.*` instead.
  - **Trusted proxies** come from env or CLI only; a workspace config value is refused with exit 78
    (dev-security #10).
  - **Denial** now also covers the state directory (D5).
  - **`create_app_from_env`** requires `AO_UI_BOUND_PORT` only when auth is enabled.
  - **Hermetic fixture:** it moved from T-PlEROT to T-kzEzwy (developer D-3, tester T-1).
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the
  E-Da5Tn9 design package (design and tickets only; **no code written**). The store-directory denial
  applies with auth on **and** off. It is the one deliberate behaviour change with auth off (NFR-1).

## Evidence
- None yet.

## Risks / Blockers
- None. `AuthLaunch` field names are frozen once T-PDGw9p starts.
- Merge gate (v2.1, security M4): do not merge before T-QJ1vyQ and T-KQ6ZrY have merged. On the
  critical path, with T-KQ6ZrY co-critical (HLD §24.3).

## Next actions
1. developer: implement once T-XchniS, T-G7qByZ, T-rpKCjP and T-Hd4wQ2 land; rebase onto
   T-QJ1vyQ and T-KQ6ZrY before merging. Run the verification and the full suite, and record the
   results and the `launch.py` coverage here.
