# TASK: T-jVqH8w-ao-ui-auth-wiring

## Metadata
- Task ID: `T-jVqH8w-ao-ui-auth-wiring`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane B)
- Created: `2026-10-04`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `3 days` · Sprint `S3`

## Requirements Mapping
- Requirement IDs:
  - FR-1, FR-2;
  - FR-10/FR-24 (file-browser denial of the store **and** state directories);
  - FR-16 (uvicorn proxy-header kwargs);
  - FR-21 (startup warnings);
  - FR-25 (`child_env` computation, consumed by T-PDGw9p);
  - NFR-1.
- ACs:
  - AC-2 (`prepare_auth` and `ao ui` parts);
  - AC-3 (`ao ui` part);
  - AC-13 (denial part);
  - AC-25 (`prepare_auth` warnings part);
  - AC-4c: §11.3.4 row 9 (auth disabled while accounts exist → the `Note:` warning), through
    `count_store_users` returning >0, in `test_launch.py`.

  The precedence checks through the CLI (AC-5) support AC-1, which is owned by T-PlEROT and
  T-j9dfsw.
- Invariants: S12, S14, S17, S19.
- Design: HLD §11.20 (`launch.py`), §11.3.4 (fail-closed table), §11.3.5 (adapter rules), §11.4
  (denial), §14.8, §16 rows 1e, 2, 3, 4b, 4c, §18 #2; ADR-0021 D3, D8.

## Description
1. **NEW `src/agent_orchestrator/auth/launch.py`** (L3, framework-free; reviewer R-3). It contains
   `AuthLaunch`, `prepare_auth(...)` and `exit_code_for(exc) -> int` (`EXIT_CONFIG` for any
   `AuthConfigError`), exactly as in HLD §11.20, in this order:
   1. `settings = resolve_auth_settings(...)`. An `AuthConfigError` propagates.
   2. **Enabled:**
      - `runtime = build_auth_runtime(settings, Realm(realm_kind, port, workspace_root), clock=clock)`
        → `runtime.provider.check_ready()`.
      - On `AuthNotReadyError`, write a **best-effort** `auth.startup.refused` audit event to the
        state directory. Any exception from that write is swallowed and logged at DEBUG. Then
        re-raise the **original** error.
      - Then add `provider.startup_warnings()`.
      - If `not is_loopback_bind(bind_host)` and there are no `settings.trusted_proxies`, add
        `PLAIN_HTTP_WARNING`.
      - Call `install_log_redaction()`.
   3. **Disabled:**
      - a non-loopback bind adds `DEPRECATION_NOTICE`;
      - if `count_store_users(settings.store_dir)` is not in `(0, None)`, add the §11.3.4 row-9
        `Note: …` text.
   4. **Adapters** (§11.3.5):
      - `uvicorn_kwargs`: `{}` when off; `{"proxy_headers": False}` when on without proxies;
        `{"proxy_headers": True, "forwarded_allow_ips": "<csv>"}` with proxies.
      - `child_env`: only the **CLI-sourced** `enabled`, `totp` and `store_dir`, as `AO_UI_AUTH`
        (`"1"`/`"0"`), `AO_UI_AUTH_TOTP` and `AO_AUTH_DIR`.
      - `denied_paths`: resolved and de-duplicated; the XDG store and state defaults,
        `$AO_AUTH_DIR` and `$AO_AUTH_STATE_DIR` when set, plus the effective store and state
        directories.
   - **Warning order:** `settings.warnings`, then provider, then transport (or deprecation and
     accounts notes).
   - **`is_loopback_bind(host)`** is local to `launch.py`: `"localhost"` → True; otherwise
     `ipaddress.ip_address(host).is_loopback`; unparseable → False. It does **not** import
     `ui.security` (R2).
   - **Message constants** in `launch.py`:
     - `PLAIN_HTTP_WARNING` must contain the phrase `not encrypted`, name the host, and point to
       `AO_UI_AUTH_TRUSTED_PROXIES` / a TLS proxy.
     - `DEPRECATION_NOTICE` must contain `deprecated`.
2. **`cli.py::ui_cmd`** (§16 rows 4b and 4c).
   - **New options:** `--auth/--no-auth` (`bool | None`, default `None`), `--auth-totp`
     (`off|optional|required`, default `None`), `--auth-dir PATH`.
   - **Flow:**
     1. Make one call:
        `launch = prepare_auth(cli=AuthCliOverrides(...), env=os.environ, workspace_root=Path(ws), realm_kind="ui", port=port, bind_host=host)`.
        On `AuthConfigError`, print `ERROR: <msg>` to stderr and `raise typer.Exit(EXIT_CONFIG)`.
     2. Print every `launch.warnings` line to stderr.
     3. Print the existing UNAUTHENTICATED warning **only when `launch.runtime is None`**, with its
        text unchanged.
     4. Build the service as
        `DashboardService(ws, denied_paths=[str(p) for p in launch.denied_paths])`.
   - **Non-reload:** `create_app(service, allowed_hosts=..., auth=launch.runtime)`, then
     `uvicorn.run(app, host=host, port=port, **launch.uvicorn_kwargs)`.
   - **Reload:** keep the existing `AO_UI_WORKSPACE` / `AO_UI_BOUND_HOST` relay, then
     `os.environ[AO_UI_BOUND_PORT_ENV] = str(port)` and `os.environ.update(launch.child_env)`, then
     `uvicorn.run(..., **launch.uvicorn_kwargs)`.
   - **Help and docstring (row 4c):** the `--host` help text says "unauthenticated unless --auth /
     AO_UI_AUTH", and the module docstring lists `ao auth`.
3. **`ui/app.py::create_app_from_env`** (§16 row 1e; about 8 lines):
   - `prepare_auth(cli=AuthCliOverrides(), env=os.environ, workspace_root=Path(AO_UI_WORKSPACE or cwd), realm_kind="ui", port=<AO_UI_BOUND_PORT or UI_DEFAULT_PORT>, bind_host=AO_UI_BOUND_HOST or loopback)`.
   - If `launch.runtime is not None` and `AO_UI_BOUND_PORT` was absent or invalid → raise
     `AuthConfigError` naming `AO_UI_BOUND_PORT`. A guessed port would mis-name the realm cookie.
   - Pass `denied_paths` and `auth=launch.runtime`. `AuthConfigError` re-raises (§11.20).
   - **Auth off without `AO_UI_BOUND_PORT`:** behaviour is unchanged.
4. **File-browser denial** (§11.4 pseudocode, §16 rows 2 and 3):
   - `FileBrowser.denied_paths`, resolved in `__post_init__`.
   - In `resolve()`, after the existing containment check, a denied path raises the generic
     `PathNotAllowedError("path is not browsable: …")`. The message never says "auth".
   - `list_dir` skips denied entries, resolving **only** symlink entries (reviewer R-12).
   - `DashboardService(..., denied_paths=None)` defaults to `[str(p) for p in default_denied_paths()]`.
5. **`AO_UI_BOUND_PORT_ENV = "AO_UI_BOUND_PORT"`** is now listed in HLD §12.6, so T-kzEzwy's
   constants pin covers it. Use the constant; no string literal (NFR-7).

The root hermetic fixture is T-kzEzwy's (§16 row 10). Do not duplicate it.

## Inputs / Outputs
- **Inputs:**
  - T-PlEROT: `resolve_auth_settings`, `count_store_users`.
  - T-XchniS: `build_auth_runtime`, `Realm`, `check_ready`, `startup_warnings`.
  - T-CsT5gk: `install_log_redaction`, `AuditLog`.
  - T-G7qByZ: `create_app(auth=)`.
  - T-rpKCjP: `install_auth_routes`.
  - T-8NQP8J: `paths.default_denied_paths`, `is_within`.
- **Outputs:**
  - `src/agent_orchestrator/auth/launch.py`
  - edits to `src/agent_orchestrator/cli.py` (rows 4b, 4c), `ui/app.py` (row 1e), `ui/files.py`
    (row 2) and `ui/service.py` (row 3)
  - tests: `tests/auth/test_launch.py`, `tests/auth/test_ui_command_auth.py`,
    `tests/ui/test_file_browser_denial.py`

## Acceptance Criteria
1. **Unchanged suites:**
   - `tests/ui/test_ui_command.py`, `tests/ui/test_files.py` and `tests/ui/test_service.py` pass
     **unmodified**.
   - With no auth settings, the `fake_uvicorn.calls[0]` kwargs are exactly `{"host", "port"}`.
   - `--host 0.0.0.0` prints the existing UNAUTHENTICATED warning byte-identically, plus a
     `deprecated` line.
2. **`prepare_auth`** (`test_launch.py`; AC-2, AC-25):
   - **(a) No settings, loopback:** `runtime is None`, `uvicorn_kwargs == {}`, `child_env == {}`,
     `warnings == ()`, and `denied_paths` contains the XDG store and state defaults.
   - **(b) Enabled, empty store:** raises `AuthNotReadyError` whose text contains
     `ao auth add-user`, and `--auth-dir <dir>` for a non-default store. The state-dir `audit.jsonl`
     holds one `auth.startup.refused`. With `AuditLog.record` patched to raise, the **same**
     `AuthNotReadyError` still propagates.
   - **(c) Enabled with one user, loopback:** `runtime.realm.id == "ui:" + 12 hex`;
     `uvicorn_kwargs == {"proxy_headers": False}`; no `not encrypted` text;
     `install_log_redaction` called once (spy). It is not called when disabled.
   - **(d) Enabled, bind `0.0.0.0`, no proxies:** a warning contains `not encrypted`.
   - **(e) `AO_UI_AUTH_TRUSTED_PROXIES=127.0.0.1`:** `{"proxy_headers": True, "forwarded_allow_ips": "127.0.0.1"}`
     and no transport warning.
   - **(f) Disabled, bind `0.0.0.0`:** the `deprecated` notice.
   - **(g) Disabled with one user:** the `Note:` text with the account count.
   - **(h) `child_env`:**
     - CLI `enabled=True, totp=required, store_dir=D` → exactly
       `{"AO_UI_AUTH": "1", "AO_UI_AUTH_TOTP": "required", "AO_AUTH_DIR": D}`;
     - env-only `AO_UI_AUTH=1` → `{}`;
     - defaults → `{}`.
   - **(i) `denied_paths`** contains the custom store dir, its derived `<store>/state`, and
     `$AO_AUTH_STATE_DIR` when set. All entries are resolved, with no duplicates.
   - **(j)** `exit_code_for(AuthConfigError("x")) == EXIT_CONFIG == 78`.
3. **Refuse to start** (AC-3 `ao ui` part; S14):
   - `ao ui --auth --auth-dir <empty>` → exit 78; stderr contains `ao auth add-user`;
     `fake_uvicorn.calls == []`.
   - The same through `AO_UI_AUTH=1`, and through `ui.auth.enabled: true` in `W/.ao/config.yaml`.
4. **Enabled path:**
   - One user → exit 0. `uvicorn.run` receives an app with `runtime_of(app)` not `None`, and
     `proxy_headers=False`.
   - `AO_UI_AUTH_TRUSTED_PROXIES=127.0.0.1` → `proxy_headers=True` and
     `forwarded_allow_ips="127.0.0.1"`.
   - `--host 0.0.0.0` → stderr contains `not encrypted` and does **not** contain the
     UNAUTHENTICATED warning.
5. **Precedence through the CLI:**
   - `--no-auth` beats `AO_UI_AUTH=1`, and `AO_UI_AUTH=0` beats `ui.auth.enabled: true`. Both start
     unauthenticated.
   - Disabled with users present → the `Note:` line is printed.
6. **Reload:**
   - `--reload --auth --auth-totp required --auth-dir D` (one user) → `uvicorn.run` is called with
     the factory import string, `reload=True`, `factory=True` and `proxy_headers=False`.
   - `os.environ` (monkeypatched) holds `AO_UI_AUTH=1`, `AO_UI_AUTH_TOTP=required`,
     `AO_AUTH_DIR=D` and `AO_UI_BOUND_PORT=<port>`.
   - `create_app_from_env()` then builds an app where an anonymous `GET /api/runs` → 401 and the
     realm cookie name is `ao_sid_<port>`.
   - `AO_UI_AUTH=1` without `AO_UI_BOUND_PORT` → `AuthConfigError` naming `AO_UI_BOUND_PORT`.
   - Auth off without it → the app builds, and `runtime_of(app) is None`.
7. **Invalid config through `ao ui`** (each must exit 78 unless stated otherwise):
   - `ui: {auth: {enable: true}}` → 78, naming `ui.auth.enable`;
   - `ui.auth.trusted_proxies: [...]` → 78, naming `AO_UI_AUTH_TRUSTED_PROXIES`;
   - `ui.auth.session_idle_minutes: 600` → 78 with `would weaken`;
   - unparseable YAML with users → 78;
   - unparseable YAML with no users → exit 0 plus a warning.
8. **Denial** (AC-13 part; S12), with auth **off and on**. The workspace contains the store and
   state directories (`AO_AUTH_DIR` inside the workspace; state = `<store>/state`):
   - listing the store → 403 (the existing `PathNotAllowedError` mapping);
   - reading `users.json`, `state/lockouts.json` or `state/audit.jsonl` → 403;
   - an absolute path → 403;
   - a symlink `ws/link → store`, then `ws/link/users.json` → 403;
   - an HTML preview of a markup file inside the store → 403;
   - listing the parent omits both directories;
   - no error detail contains `auth`.
9. **`DashboardService(ws)` defaults:** with no `denied_paths`, it denies the hermetic
   `AO_AUTH_DIR` and its `state` subdirectory, plus the XDG defaults. Point `XDG_CONFIG_HOME` and
   `XDG_STATE_HOME` into the workspace to observe this.
10. ruff and mypy are clean. `python -m pytest -q` (full suite) is green. Coverage of
    `auth/launch.py` is ≥ 90 %.

## Risks
- **Regressing `ao ui` for users without auth.** AC-1 and T-G7qByZ's header snapshot are the gates.
- **Double `prepare_auth` under `--reload`.** The parent and the reloader child each build a
  runtime; the parent's exists only to fail fast before uvicorn starts. This is documented, and
  sessions live in the child.
- **The `$HOME` workspace case.** The store is inside the workspace; this is allowed for
  default-sourced stores, and denial makes it safe (AC-8).

## Dependencies
- **Upstream:**
  - direct (HLD §24.2 #13): T-PlEROT, T-XchniS, T-G7qByZ, T-rpKCjP;
  - transitive: T-kzEzwy, T-8NQP8J, T-CsT5gk.
- **Downstream:**
  - T-PDGw9p: reuses `prepare_auth` for the hub.
  - T-U2ERMo: e2e and scrub sweep.

## Pseudocode / Algorithm
- `prepare_auth`: HLD §11.20 plus §11.3.5, in the order given in Description 1.
- The fail-closed outcomes: HLD §11.3.4.
- The denial: HLD §11.4.
- The `ui_cmd` flow: Description 2 (row 4b order).

## Schemas / Interface Notes
- `AuthLaunch` and `prepare_auth` signatures: HLD §11.20. Field names are frozen, because T-PDGw9p
  consumes them.
- CLI flags and env: HLD §12.5. Exit code: root `errors.EXIT_CONFIG` (78).

## Handoff Boundary
- **Upstream:** settings, runtime and the HTTP layer.
- **Downstream:** a shippable `ao ui --auth`, plus the shared `prepare_auth` for `ao service run`.

## Verification

```
python -m pytest -q tests/auth/test_launch.py tests/auth/test_ui_command_auth.py tests/ui/test_file_browser_denial.py tests/ui
python -m pytest -q tests/auth/test_launch.py --cov=agent_orchestrator.auth.launch --cov-report=term-missing
python -m pytest -q
ruff check src tests && ruff format --check src tests && mypy src
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-jVqH8w-ao-ui-auth-wiring/`
