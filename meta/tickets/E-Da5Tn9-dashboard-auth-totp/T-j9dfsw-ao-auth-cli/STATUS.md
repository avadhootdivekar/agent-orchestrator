# STATUS

- ID: `T-j9dfsw-ao-auth-cli`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (lane B)
- Scope: `MVP` · Sprint: `S2` · Estimate: `3 d`

## This update
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented `auth/cli.py` (11
  commands incl. `enrollment-token`; M3/M6/L1/L6 folded) plus the one-hunk registration in
  `cli.py` (import + `add_typer` + docstring line). All ACs 1-8 and 10 met; AC 9 stays with
  T-U2ERMo (the CLI maps `StoreLockTimeoutError` to exit 1 naming the lock file and "busy"; a
  single-process lock-held test covers the mapping). Decisions to know:
  - **Header:** line 1 is exactly `store: <dir>`, line 2 `state: <dir>`. With `status --json` the
    header goes to **stderr** so stdout is one parseable JSON document.
  - **LOCKED column** shows the `locked_until` timestamp, `no`, or `?` when `lockouts.json` is
    unreadable. `list-users --json` (HLD table) is not in this ticket's scope and was not built.
  - **Corrupt `lockouts.json`** does not block unrelated mutating commands: `ensure_name_key`
    failure is a stderr WARNING; `unlock` repairs the file (`reset(repair_corrupt=True)`), and
    `remove-user` warns if `forget` fails after the user is removed.
  - **Policy notes** (not refusals) are also printed by `add-user --require-totp` and `reset-2fa`
    when the resolved policy is `off` (blocked user), reusing the `enrollment-token` helper.
  - **Test seams:** hashing reads `passwords.CURRENT_PARAMS` at call time (tests swap in
    `TEST_PARAMS`); `enable-2fa` tests pin `totp.new_totp_secret` to the RFC seed so the code can
    be computed before the single invocation (the secret is generated inside it).
  - **Read-only commands** (`list-users`) only warn on unsafe permissions; `status` reports every
    check (`ok` / `missing` / `unsafe`) and never exits 78 for a `ConfigRisk`.
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2.1 gates folded (HLD §28.9).
  Estimate, lane and sprint unchanged (3 d, lane B, S2). Changes:
  - **Security M6:** a config-sourced `store_dir` is never created or chmod-ed; it must exist and be
    private, or the operator passes `--auth-dir`. New AC 5c (= AC-46).
  - **Security M3 / design-review M3:** `status` reports the `ConfigRisk` flags (never exit 78 for
    them), the recent `auth.startup.*` events, and the required/off policy notes. New AC 5b (AC-45
    status part).
  - **Security L1 / L6:** mutating commands call `lockouts.ensure_name_key()`; a group-writable
    parent owned by the user now warns (AC 4 adjusted).
  - **Rebalance:** the store-busy multiprocess test (old AC 9) moved to T-U2ERMo; AC 8 reduced to
    the CLI invocations (T-kzEzwy's skeleton covers module imports).
- By: architect · Role: agent · Date: 2026-10-05 · Comment: v2 rescope after the Phase-4
  consultations (HLD §28).
  - **Estimate and lane:** 2.5 d → 3 d; lane Q → B (HLD §24.2 #12).
  - **New command `enrollment-token`.** `add-user --require-totp` and `reset-2fa` now print a
    one-time enrollment token, because forced enrollment requires it (dev-security #5, ADR-0021 D5).
  - **Lockouts.** `unlock` and `remove-user` act on `lockouts.json` in the **state** directory,
    keyed by the immutable `user_id` (reviewer R-7b, dev-security #1/#3, ADR-0021 D9/D10).
  - **`status`** adds the state directory, `lockouts.json` health, the pending-token count and the
    parent-directory checks (dev-security #12).
  - **Imports and prompts:** lazy imports inside command bodies, mismatch re-prompt and EOF → exit 1
    (developer D-8).
  - **Clarifications:**
    - `disable-2fa` clears a non-enrolled user's `totp_required` (the §19.1 fix path).
    - `enrollment-token` prints notes rather than refusing when the CLI's resolved policy says the
      token looks unnecessary, because the server may read a different env.
- By: architect · Role: agent · Date: 2026-10-04 · Comment: Task created as part of the
  E-Da5Tn9 design package (design and tickets only; **no code written**). The CLI is the only
  account-administration and bootstrap surface (no web sign-up).

## Evidence
- By: developer · Role: developer · Date: 2026-10-05 (worktree `agent-a0e6c5c5f87392fca`, branch
  `ad/dashboard-auth-totp`; commands run from the worktree root with `.venv/bin/`):
  - `python -m pytest -q tests/auth/test_cli_e2e.py` -> 78 passed.
  - `python -m pytest -q tests/auth/test_cli_e2e.py --cov=agent_orchestrator.auth.cli
    --cov-report=term-missing` -> `auth/cli.py` 582 stmts, 9 missed, **98 %** (AC 10 >= 90 %).
  - `python -m pytest -q tests/auth/test_import_boundary.py` -> 24 passed (incl. the two new CLI
    cases: `ao auth --help` and `ao auth status --auth-dir <tmp>` exit 0 with
    fastapi/starlette/uvicorn blocked; after `import agent_orchestrator.cli` none of
    `auth.{store,passwords,totp,lockouts,audit,settings}` is in `sys.modules`).
  - `python -m pytest -q tests/auth tests/test_cli.py tests/service tests/test_e2e_cli.py
    tests/test_cli_isolation_flags.py` -> 1791 passed, 0 failed (regression check of everything the
    `cli.py` hunk and the shared auth tests could touch).
  - `ruff check` and `ruff format --check` on `auth/cli.py`, `cli.py`, `test_cli_e2e.py`,
    `test_import_boundary.py` -> clean. `mypy src` -> only the 4 pre-existing `_version.py` errors.
  - `ao auth --help` lists all 11 commands.
  - AC map: 1 lifecycle (13 steps) + 7 audit sequence = `test_lifecycle_and_audit_trail`; 2/3 =
    password/stdin/no-secret-option tests; 4 = config/permission tests (real other-writable and
    group-writable-by-you parents; group-writable-by-another-user via a patched `_check_parent`);
    5/5b = `test_status_*`; 5c = `test_config_store_dir_*` (+ `os.chmod`/`os.fchmod` spy) and
    `test_explicit_auth_dir_restores_create_and_fix` (flag and env); 5d = lifecycle step 1;
    6 = `test_enrollment_token_*`; 8 = `test_import_boundary.py`.

## Risks / Blockers
- None. A-9 verified: `typer.prompt(hide_input=True, confirmation_prompt=True)` reads CliRunner
  `input`; a mismatch re-prompts and EOF exits 1. Estimate held (no overrun).
- Residual: the multi-process store-busy test is T-U2ERMo's (AC 9 moved there in v2.1).

## Next actions
1. T-U2ERMo: scrub sweep over every CLI command and the store-busy multiprocess test.
2. T-jVqH8w / T-PDGw9p: refusal messages may name `ao auth add-user`.
