# TASK: T-j9dfsw-ao-auth-cli

## Metadata
- Task ID: `T-j9dfsw-ao-auth-cli`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane B)
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `3 days` · Sprint `S2`

## Requirements Mapping
- Requirement IDs: FR-2 (CLI-only bootstrap), FR-5, FR-6 (CLI text enrollment), FR-22, FR-23 (CLI audit events), FR-28 (enrollment tokens are issued only by the CLI), NFR-3, NFR-7, NFR-8
- ACs: AC-1 (`ao auth status` source display), AC-26, AC-27 (CLI part; the AST/subprocess skeleton is T-kzEzwy's), AC-36 (CLI part), **AC-45 (`status` flags; v2.1)**, **AC-46 (config-sourced `store_dir`; v2.1)**
- Invariants: S18 (import boundary), S22 (forced enrollment needs a CLI-issued token)
- Design: HLD §11.19 (v2.1 command table and common behaviour), §11.9 (pure mutation functions), §11.11 (`LockoutStore`, `ensure_name_key`), §11.12 (audit), §11.5 ("Who may fix permissions", v2.1), §11.3.1 (`ConfigRisk`), §12.4, §12.5, §18 #8, §19.1; §28.9 (security M3, M6, L1; design-review M3); ADR-0021 D3, D5, D8, D9, D10

## Description
Create `src/agent_orchestrator/auth/cli.py` (layer L4), a Typer sub-app named `auth`, and register it
in `src/agent_orchestrator/cli.py` next to the `service` sub-app (HLD §16 row 4a **only**; row 4b is
T-jVqH8w's):

```python
from .auth.cli import app as auth_app
app.add_typer(auth_app, name="auth")
```

- **Import rules (R1, R4, developer D-8).** Module scope imports only `typer`, `auth.constants`,
  `auth.errors` and stdlib. Everything else (`store`, `passwords`, `totp`, `recovery`, `lockouts`,
  `audit`, `settings`, `paths`, `policy`) is imported **inside command bodies**. Never import
  `auth/http/*`, fastapi, starlette or uvicorn.
- **Common options** on every command:
  - `--auth-dir PATH`, which becomes `AuthCliOverrides(store_dir=...)`;
  - `--workspace/-w PATH`, defaulting to `$AO_WORKSPACE_ROOT` or the cwd, exactly like `ao ui`.

  Resolution is `resolve_auth_settings(cli=..., env=os.environ, workspace_root=ws)`, so the CLI
  operates on the same store and state directories that `ao ui` would use in that workspace. The
  first output line of every command is `store: <store_dir>` (state: `<state_dir>`).
- **Secrets in, secrets out.**
  - Passwords come only from `typer.prompt(hide_input=True, confirmation_prompt=True)` or from
    `--password-stdin`: the first stdin line, with exactly one trailing `\n`/`\r\n` stripped; empty
    is rejected.
  - A confirmation mismatch re-prompts (click behaviour). EOF exits 1.
  - **No `--password`, `--token` or `--code` option, and no env var, exists for any secret.**
  - Tokens and recovery codes are printed **once**, to stdout, under a
    `=== STORE THESE NOW — shown only once ===` banner.
- **Permissions (§11.5).**
  - Mutating commands call `ensure_private_dir(create=True, fix=True)` on the store **and** state
    directories, and `check_private_file(fix=True)`. Notices go to stderr.
  - **v2.1 (security M6): a config-sourced `store_dir` is never created or chmod-ed.** When
    `settings.sources["store_dir"]` starts with `config:` (a workspace `ui.auth.store_dir` reached
    through `--workspace`), mutating commands call `ensure_private_dir(store_dir, create=False,
    fix=False)` and `check_private_file(users.json, fix=False)`: a missing or non-private directory
    → exit 78 with `ui.auth.store_dir from <config> must already exist and be private (0700, owned
    by you); create it yourself, or pass --auth-dir explicitly`. Only `<store_dir>/state` may be
    created (0700, via `os.mkdir(mode=0o700)`) inside the verified directory; nothing is chmod-ed.
    An explicit `--auth-dir` (CLI) or `AO_AUTH_DIR` (env) restores the normal create/fix behaviour.
  - Mutating commands also call `lockouts.ensure_name_key()` (v2.1, security L1), so the
    phantom/audit HMAC key exists.
  - Read-only commands (`list-users`, `status`) use `fix=False` and only report.
  - An unsafe parent directory cannot be fixed: `UnsafePermissionsError` → exit 78 with the `chmod`
    hint.
- **Exit codes.**
  - 0 on success.
  - 1 for operational errors: user exists or not found, already enrolled, not enrolled, policy
    violation, EOF, store busy (`StoreLockTimeoutError`, message names the lock file and "busy").
  - 78 (`EXIT_CONFIG`) for any `AuthConfigError`, which includes invalid env/config, tighten-only
    violations and unsafe permissions.
- **Audit.** Every mutation writes one `AuditEvent` to the **state directory's** `audit.jsonl`:
  `realm=CLI_REALM`, `details.source=SOURCE_CLI`, and the affected account's `username` and
  `user_id`.

**Commands** (HLD §11.19). Every write is **one** `UserStore.mutate` call composing the pure
functions of §11.9.

| Command | Behaviour |
|---|---|
| `add-user USERNAME [--password-stdin] [--require-totp]` | Normalize (NFKC → strip → lower); reject a name not matching `USERNAME_PATTERN`; run `PasswordPolicy(settings.min_password_length)`. `mutate(create=True)`: `add_user(..., totp_required=require_totp)` plus, with `--require-totp`, `issue_enrollment_token` in the **same** mutate → print the token and its expiry (`ENROLLMENT_TOKEN_TTL_SECONDS`). Audit `auth.user.added` (+ `auth.enrollment_token.issued`). |
| `set-password USERNAME [--password-stdin]` | Policy check → `set_password_hash` (epoch +1) → print "existing sessions are revoked on their next request". Audit `auth.password.changed`. |
| `list-users` | Columns `USERNAME TOTP TOTP_REQUIRED RECOVERY_LEFT LOCKED LAST_LOGIN`. LOCKED is read from `lockouts.json` by `user_id`. Never prints hashes, secrets or tokens. |
| `remove-user USERNAME [--yes] [--force]` | Confirm unless `--yes`. **Last-user guard:** when it is the last account and the resolved settings have auth enabled → exit 1 explaining that `ao ui` / `ao service run` would then refuse (exit 78), unless `--force`. `remove_user` → `LockoutStore.forget(user_id)`. Audit `auth.user.removed`. |
| `enable-2fa USERNAME` | Interactive; works over any transport, and under any policy (prints a note when the resolved policy is `off`: enrollment is sticky). Refuse if enrolled (exit 1). Print the secret grouped in 4s and the `otpauth://` URI. Prompt for a code up to `CLI_TOTP_CONFIRM_ATTEMPTS` (3) times. `enroll_totp(user_id, epoch from the snapshot read at start, completes_login=False)`; `StaleIdentityError` → exit 1 "account changed during enrollment; retry". Print the 10 recovery codes once. Audit `auth.totp.enrolled`. |
| `disable-2fa USERNAME [--yes]` | Enrolled → `remove_totp(set_required=False)`. **Not enrolled but `totp_required`** → `remove_totp(set_required=False)` clears the requirement (the §19.1 fix for a BLOCKED user). Neither → exit 1 `not enrolled`. Audit `auth.totp.disabled`. |
| `reset-2fa USERNAME [--yes]` | Idempotent `remove_totp(set_required=True)` (epoch +1) + `issue_enrollment_token`, in one mutate → print the token; advise `ao auth set-password USERNAME` if the device may have been lost together with the password. Audit `auth.totp.reset` + `auth.enrollment_token.issued`. |
| `enrollment-token USERNAME` (**new**) | Enrolled → exit 1 `already enrolled`. Otherwise `issue_enrollment_token` (replaces any previous token) → print it. When `totp_requirement(resolved policy, rec.totp_required)` is `NONE` or `BLOCKED`, also print a NOTE naming the policy and its source. The server's effective policy may come from another env (e.g. `service.env`), so this is a note, not a refusal. Audit `auth.enrollment_token.issued`. |
| `unlock USERNAME` | Unknown → exit 1. `LockoutStore.reset(user_id)` (state directory). Audit `auth.user.unlocked`. |
| `revoke-sessions USERNAME` | `bump_epoch` → epoch +1. Audit `auth.sessions.revoked`. |
| `status [--json]` | See AC-5. **v2.1:** also the `ConfigRisk` flags (`disabled_by_config`, `totp_downgraded_by_config`) from `settings.config_risks` — reported, never an exit 78 — the most recent `auth.startup.*` audit events (up to 5), and the policy notes (design-review M3): under `required`, "users who are not enrolled need an operator-issued enrollment token (`ao auth enrollment-token <user>`) to finish their first login"; under `off` with enrolled users, "N enrolled user(s) are still asked for a code (enrollment is sticky)". |

## Inputs / Outputs
- **Inputs:**
  - T-s6sJmB: `PasswordHasher`, `PasswordPolicy`, totp, recovery.
  - T-8NQP8J: `UserStore`, mutations, `fsutil`, `paths`.
  - T-PlEROT: `resolve_auth_settings` (incl. `sources` and `config_risks`).
  - T-CsT5gk: `LockoutStore` (incl. `ensure_name_key`), `AuditLog`.
  - T-kzEzwy: constants, errors, `EXIT_CONFIG`, `tests/auth/helpers/core.py`, the import-boundary
    skeleton.
- **Outputs:**
  - `src/agent_orchestrator/auth/cli.py`
  - the row-4a edit to `src/agent_orchestrator/cli.py` (registration + module docstring lists
    `ao auth`)
  - `tests/auth/test_cli_e2e.py`
  - the CLI cases in `tests/auth/test_import_boundary.py`

## Acceptance Criteria
All tests use `typer.testing.CliRunner`, a tmp `--auth-dir`, and the hermetic env fixture.
1. **Lifecycle** (`test_cli_e2e.py`), run in this order:
   1. `add-user alice` (input `pw\npw\n`, password ≥ 12 characters) → exit 0. The store dir is
      0700, `users.json` is 0600 and the state dir is 0700. The record has a 32-hex `user_id`.
      The first line is `store: <dir>`.
   2. `add-user alice` again → exit 1, `already exists`.
   3. `add-user bob --password-stdin --require-totp` → exit 0. `bob.totp_required` is true.
      - stdout contains exactly one token matching `^[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){3}$`
        (call it T1).
      - `users.json` holds `enrollment_token.{salt_hex,hash_hex,expires_at}`, with `expires_at` =
        now + `ENROLLMENT_TOKEN_TTL_SECONDS` (±5 s).
      - The T1 text does **not** appear in `users.json`.
   4. `list-users` → both rows, with a `TOTP_REQUIRED` column. The output contains no `$scrypt$`,
      no `[A-Z2-7]{32}` run and no T1.
   5. `set-password alice` → epoch +1.
   6. `enable-2fa alice`, feeding the code computed from the printed secret (parse it from stdout;
      compute the code immediately before invoking) → exit 0; 10 recovery codes printed; epoch +1.
      Then `enable-2fa alice` → exit 1, and `enrollment-token alice` → exit 1 `already enrolled`.
   7. `disable-2fa alice --yes` → `totp` is null and `totp_required` is false.
   8. `reset-2fa bob --yes` (bob is not enrolled) → exit 0. `totp_required` is true. A new token T2
      is printed. `hash_hex` changed, so T1 no longer verifies against the stored record.
   9. `enrollment-token bob` → T3 is printed; T2 no longer verifies.
   10. Seed a lockout for alice via `LockoutStore.record_failure` × threshold → `unlock alice` → no
       active lock for alice's `user_id`.
   11. `revoke-sessions alice` → epoch +1.
   12. Seed a lockout entry for bob → `remove-user bob --yes` → exit 0, and `lockouts.json` no
       longer contains bob's `user_id`.
   13. With `AO_UI_AUTH=1`: `remove-user alice --yes` → exit 1 (last-user guard; the message
       mentions exit 78). With `--force` → exit 0.
2. **Password input:**
   - shorter than `min_password_length` → exit 1, and the output lists `too_short`;
   - input `pw1\npw2\n` and then EOF → exit 1;
   - `--password-stdin` with empty stdin → exit 1;
   - a trailing space is preserved, and only one trailing newline is stripped (verify the stored
     hash with the real hasher at `TEST_PARAMS`).
3. **No secret inputs:**
   - Introspecting every command's Typer params shows no `--password`, `--token` or `--code`
     option.
   - With `AO_AUTH_PASSWORD=x` set and no input, `set-password alice` → exit 1 (EOF), and the stored
     hash is unchanged.
4. **Configuration errors → 78:**
   - `AO_UI_AUTH=maybe` + `status` → exit 78; stderr names `AO_UI_AUTH`.
   - `--workspace W` with `ui.auth.trusted_proxies: ["127.0.0.1"]` → 78, naming
     `AO_UI_AUTH_TRUSTED_PROXIES`.
   - An other-writable parent of the store, or a group-writable parent owned by another user
     (patched `stat`), → `add-user` exits 78 with the `chmod` hint. **v2.1 (security L6):** a
     group-writable parent owned by the euid → exit 0 with a warning on stderr naming `chmod g-w`.
5. **`status`** (AC-1 CLI part):
   - It prints each setting with its source: `(env:AO_UI_AUTH)` when set, `(default)` otherwise,
     and `(config:<path>)` for `--workspace W` with `ui.auth.totp: required`.
   - It prints the store and state directories with the permission results, including the parent
     check.
   - It prints `lockouts.json` health: `ok`, `missing` or `corrupt`. Writing invalid JSON gives
     `corrupt` with exit 0.
   - It prints the counts `{users, enrolled, totp_required_unenrolled, locked,
     enrollment_tokens_pending}`, any stray `.users.json.*.tmp` files, the server UTC time, and
     warnings.
   - **(v2.1, design-review M3)** With the resolved policy `required` it prints the
     enrollment-token note; with `off` and one enrolled user it prints the "still asked for a code"
     note with the count.
   - `--json` parses and carries the same keys (incl. `config_risks` and `recent_startup_events`).
   - A sentinel grep finds no password, secret, code, token or hash in either output.
5b. **`status` config-risk flags** (AC-45 status part; security M3): with `--workspace W` whose
    `.ao/config.yaml` has `ui.auth.enabled: false` and one account in the store → exit **0** and the
    flag `disabled_by_config` with the text `ao ui in this workspace will refuse to start (exit 78);
    use --no-auth / AO_UI_AUTH=0 to disable on purpose`; with `ui.auth: {enabled: true, totp:
    optional}` → the flag `totp_downgraded_by_config`; with zero accounts → neither flag. After an
    `auth.startup.disabled_by_config` line is written to the state dir's `audit.jsonl`, `status`
    lists it under recent startup events.
5c. **Config-sourced `store_dir`** (AC-46; security M6). `--workspace W` with `ui.auth.store_dir:
    <abs path outside W>`:
    - the directory does **not** exist → `add-user` exits 78 naming the config file and
      `--auth-dir`; afterwards the path still does not exist;
    - it exists with mode 0755 → exit 78; the mode is still 0755 afterwards (no chmod);
    - it exists, owned by the user, mode 0700 → exit 0; only `state/` (0700) and the store files
      are created inside it, and the directory mode is unchanged;
    - the same missing path passed as `--auth-dir` (or `AO_AUTH_DIR`) → exit 0, created 0700
      (normal behaviour).
    - A spy on `os.chmod` sees no call in any config-sourced case.
5d. **Name key** (security L1): after `add-user`, `lockouts.json` in the state dir holds a 64-hex
    `name_key_hex`.
6. **`enrollment-token` notes:**
   - With the resolved policy `optional` and `totp_required=false` → exit 0, a token, and a NOTE
     containing `not currently required`.
   - With `AO_UI_AUTH_TOTP=off` and `totp_required=true` → a NOTE containing `cannot log in until`.
   - An unknown user → exit 1.
7. **Audit** (state dir `audit.jsonl`):
   - After AC-1, the event sequence is exactly: `user.added`(alice), `user.added`(bob),
     `enrollment_token.issued`(bob), `password.changed`, `totp.enrolled`, `totp.disabled`,
     `totp.reset`, `enrollment_token.issued`, `enrollment_token.issued`, `user.unlocked`,
     `sessions.revoked`, `user.removed`(bob), `user.removed`(alice). Each name is prefixed `auth.`.
   - Every line has `realm:"cli"`, `details.source:"cli"` and a 32-hex `user_id`.
   - A sentinel grep finds no password, secret, recovery code or token.
8. **Import boundary** (AC-27 CLI part; v2.1: reduced to the CLI invocations, because T-kzEzwy's
   skeleton already imports every non-http module, `auth/cli.py` included, with the frameworks
   blocked and runs the AST layer check):
   - A subprocess with `sys.modules['fastapi'|'starlette'|'uvicorn'] = None` runs `ao auth --help`
     and `ao auth status --auth-dir <tmp>` through `CliRunner` → both exit 0.
   - After `import agent_orchestrator.cli`, none of `agent_orchestrator.auth.{store,passwords,totp,lockouts,audit,settings}`
     is in `sys.modules`.
9. *(Moved to T-U2ERMo in v2.1: the store-busy multiprocess test — another process holds
   `users.lock`, `set-password` exits 1 within 2 s naming the lock file. The CLI still maps
   `StoreLockTimeoutError` to exit 1 with that message.)*
10. ruff and mypy are clean. Coverage of `auth/cli.py` is ≥ 90 %.

## Risks
- **CliRunner and hidden prompts** (A-9). `typer.prompt(hide_input=True)` reads CliRunner `input`.
  Verify on day 1.
- **The real clock in `enable-2fa`.** Compute the code just before invoking; the ±1 window absorbs
  a step boundary.
- **Policy visible to the CLI ≠ the server's** (`service.env`). Notes, not refusals (AC-6), and
  `status` prints the sources.
- **Estimate is tight (v2.1).** The M3/M6 additions (~0.5 d) are offset by moving the store-busy
  test to T-U2ERMo and reducing AC 8. If it still runs over, report it in STATUS rather than
  dropping ACs.

## Dependencies
- **Upstream:**
  - direct (HLD §24.2 #12): T-s6sJmB, T-8NQP8J, T-PlEROT, T-CsT5gk;
  - transitive: T-kzEzwy.
- **Downstream:**
  - T-jVqH8w and T-PDGw9p: their refusal messages name `ao auth add-user`.
  - T-KQ6ZrY: its forced-enrollment tests consume tokens issued through the store API.
  - T-U2ERMo: the scrub sweep runs every CLI command; it also owns the store-busy multiprocess test
    (moved from AC 9 in v2.1).

## Pseudocode / Algorithm

```
FUNCTION run_command(ctx, body):
  settings = resolve_auth_settings(cli=AuthCliOverrides(store_dir=auth_dir), env=os.environ, workspace_root=ws)  # AuthConfigError -> 78
  echo(f"store: {settings.store_dir} (state: {settings.state_dir})")
  paths = StorePaths.at(settings.store_dir, settings.state_dir)
  IF mutating:
     IF settings.sources["store_dir"].startswith("config:"):          # v2.1, security M6
        ensure_private_dir(store, create=False, fix=False)              # missing / unsafe -> AuthConfigError -> 78 (names config + --auth-dir)
        check_private_file(users.json, fix=False) IF it exists
        ensure_private_dir(state, create=True, fix=False)               # only inside the verified store; never chmod
     ELSE:
        ensure_private_dir(store, create=True, fix=True); ensure_private_dir(state, create=True, fix=True)   # notices -> stderr
     lockouts.ensure_name_key()                                         # v2.1, security L1
  TRY result = body(UserStore(paths), LockoutStore(paths), AuditLog(paths), settings)
  EXCEPT AuthConfigError -> 78 | (UserExistsError, UserNotFoundError, AlreadyEnrolledError, NotEnrolledError,
         PasswordPolicyError, StaleIdentityError, StoreLockTimeoutError, click.Abort/EOF) -> 1
```

The command semantics are the table above; the pure functions are in HLD §11.9.

## Schemas / Interface Notes
- CLI surface: the table above (HLD §11.19). Env and config: HLD §12.5.
- Audit events: HLD §12.4 (`AuditEventName`; no string literals).
- `remove_totp`: the CLI relies on the §11.9 rule that it raises `NotEnrolledError` **only** when
  `set_required is None` (reset-2fa and disable-2fa are idempotent). T-8NQP8J implements that rule.

## Handoff Boundary
- **Upstream:** the core modules (no HTTP).
- **Downstream:** the only account-administration and token-issuing surface (ADR-0021 D3, D5).

## Verification

```
python -m pytest -q tests/auth/test_cli_e2e.py tests/auth/test_import_boundary.py
python -m pytest -q tests/auth/test_cli_e2e.py --cov=agent_orchestrator.auth.cli --cov-report=term-missing
python -m agent_orchestrator.cli auth --help
ruff check src tests && ruff format --check src tests && mypy src
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-j9dfsw-ao-auth-cli/`
