# TASK: T-PDGw9p-service-cli-supervisor-auth

## Metadata
- Task ID: `T-PDGw9p-service-cli-supervisor-auth`
- Epic ID: `E-Da5Tn9-dashboard-auth-totp`
- Owner: `developer` (lane Q)
- Created: `2026-10-05`
- Last Updated: `2026-10-05`
- Status: `Draft`
- Estimate: `2.5 days` · Sprint `S3`

**Origin:** this scope was split out of v1 `T-KOv2qD-hub-service-auth` (developer D-4: the 3-day
cap).

## Requirements Mapping
- Requirement IDs: FR-2 (the hub refuses before spawning), FR-14, FR-21 (hub startup warnings via
  `prepare_auth`), FR-25, FR-26, NFR-1
- ACs: AC-3 (`ao service run` part), AC-18, AC-31
- Invariants: S14
- Design: HLD §11.20 (`prepare_auth`), §14.8, §15 rows 1–3, §16 rows 6, 7 and 8, §18 #3, §19.1;
  HLD D14, D15; ADR-0021 D8

## Description
1. **`src/agent_orchestrator/service/cli.py::run`** (§16 row 6).
   - **New options:** `--auth/--no-auth` (`bool | None`), `--auth-totp` (`off|optional|required`),
     `--auth-dir PATH`.
   - **Call `prepare_auth` first.** Right after the existing uvicorn `ImportError` check, and
     **before** `Supervisor(...)` is constructed (no singleton lock is taken and no child is
     spawned):
     `launch = prepare_auth(cli=AuthCliOverrides(...), env=os.environ, workspace_root=None, realm_kind="hub", port=hub_port, bind_host=hub_host)`.
     On `AuthConfigError`, print `ERROR: <msg>` to stderr and `raise typer.Exit(EXIT_CONFIG)`.
   - Print `launch.warnings` to stderr.
   - **Wiring:**
     - `Supervisor(registry, state_dir=..., hub_port=hub_port, child_env=launch.child_env)`;
     - `build_hub_app(status_provider, auth=launch.runtime)`;
     - `uvicorn.Config(hub_app, host=hub_host, port=hub_port, log_level="warning", **launch.uvicorn_kwargs)`.
   - **v2 vocabulary.** Use `launch.child_env` and `launch.uvicorn_kwargs`. Ledger row 6 (a) still
     names the v1 `settings.cli_env_overrides()` / `settings.uvicorn_kwargs()`; §11.20 supersedes
     it.
   - **Help texts:** `--hub-host` (on both `run` and `install`) says "unauthenticated unless
     --auth / AO_UI_AUTH" (row 6 d). Update the module docstring's "no test invokes `run`" note,
     since AC-1 and AC-2 below now invoke it with stubs.
2. **`_probe_hub_status(hub_port, *, timeout=...)`.** Keep the **single positional argument**,
   because tests monkeypatch it with `lambda port: ...`.
   - Add `except urllib.error.HTTPError as exc:` **before** the existing broad except:
     `exc.code == 401` → `raise HubLoginRequired(hub_port) from None`; any other code →
     `return None`.
   - Define `class HubLoginRequired(Exception)` in `service/cli.py`. It carries only `hub_port`.
3. **`list` / `status`** catch `HubLoginRequired`. The exact texts come from HLD §15 row 1, and
   both exit 0:
   - **`list`** prints the registry and persisted ports (the `live=None` path), then
     `Note: hub running with dashboard authentication enabled — live state is shown in the browser after login`
     **instead of** the "not confirmed running" note.
   - **`status`** prints
     `Service daemon running on port N (login required for the live view); persisted state:`, then
     `_read_fallback_status(...)` as JSON, or the existing "No persisted service state found" line.
4. **`install`'s "Next steps"** gains one hint (row 8): add `AO_UI_AUTH=1` (and optionally
   `AO_UI_AUTH_TOTP=required`) to `~/.config/ao/service.env`, create an account with
   `ao auth add-user <name>`, then `systemctl --user restart ao`.
5. **`src/agent_orchestrator/service/supervisor.py`** (row 7):
   - `Supervisor.__init__(..., child_env: Mapping[str, str] | None = None)`, passed only to
     `default_child_spawner(self._ao_executable, extra_env=child_env)`. An injected
     `child_spawner` ignores it.
   - `default_child_spawner(ao_executable, extra_env=None)`: `Popen(..., env={**os.environ, **extra_env})`
     **only if** `extra_env` is truthy. Otherwise make **no** `env` kwarg, i.e. the exact current
     call, so an empty `{}` behaves as `None`.
   - `_on_child_exit`: `if exit_code == EXIT_CONFIG` (imported from root `agent_orchestrator.errors`):
     - set `popen=None` and `stable_since=None`;
     - set `last_error = f"child exited with code {EXIT_CONFIG} (configuration error: see {log_path})"`;
     - set `next_retry_at=None`;
     - write the snapshot and log a WARNING;
     - **return without `_register_failure`**: no restart, no port reassignment, and the state
       reads `stopped`.
6. **`src/agent_orchestrator/service/systemd.py`** (row 8): add `RestartPreventExitStatus={exit_config}`
   on the line after `Restart=on-failure` in `_UNIT_TEMPLATE`, and pass `exit_config=EXIT_CONFIG`
   to `.format()`. There is no literal `78` in the source.

## Inputs / Outputs
- **Inputs:**
  - T-jVqH8w: `auth/launch.py` (`prepare_auth`, `AuthLaunch`).
  - T-KOv2qD: `build_hub_app(..., auth=)`.
  - T-kzEzwy: root `errors.EXIT_CONFIG`.
  - T-j9dfsw: the bootstrap command named in messages.
- **Outputs:**
  - edits to `src/agent_orchestrator/service/cli.py`, `service/supervisor.py` and
    `service/systemd.py`
  - tests: `tests/auth/test_service_run_auth.py`, `tests/service/test_cli_probe_auth.py`,
    `tests/service/test_supervisor_auth.py`, plus one added case in
    `tests/service/test_systemd.py`

## Acceptance Criteria
`run` tests use:
- `CliRunner`;
- isolated `AO_SERVICE_CONFIG` / `AO_SERVICE_STATE_DIR` (the existing `_isolated_service_paths`
  pattern);
- a stub `uvicorn` module in `sys.modules` (`Config` records kwargs; `Server.run()` is a no-op);
- a recording `Supervisor` stub (`start`/`tick`/`shutdown` recorded);
- `service.cli.threading.Event` patched so that `is_set()` returns True and the loop exits at once;
- `signal.signal` patched to a no-op.

1. **Refusal** (AC-3 service part; S14): `ao service run --auth --auth-dir <empty>` → exit 78;
   stderr contains `ao auth add-user`; the `Supervisor` stub is **never constructed**;
   `uvicorn.Config` is never called; no supervisor lock or state file is created.
2. **Wiring:**
   - With one user and `--auth --auth-totp required --auth-dir D`: `Supervisor` gets
     `child_env == {"AO_UI_AUTH": "1", "AO_UI_AUTH_TOTP": "required", "AO_AUTH_DIR": D}`.
     `build_hub_app` gets a runtime whose realm id is `"hub"` and whose cookie name is
     `ao_sid_<hub_port>`. `uvicorn.Config` gets `proxy_headers=False`.
   - With `AO_UI_AUTH=1` from env only: `child_env == {}`.
   - With auth off: the `Config` kwargs are exactly `{host, port, log_level}`, and
     `build_hub_app(..., auth=None)`.
   - In every case `shutdown()` is called once.
3. **Probe** (AC-18):
   - a fake `urlopen` raising `HTTPError(url, 401, ...)` → `HubLoginRequired` with
     `.hub_port == port`;
   - `HTTPError(500)` → `None`;
   - `URLError` → `None`;
   - a `TimeoutError` → `None`.
4. **CLI fallbacks** (AC-18):
   - With `_probe_hub_status` patched to raise `HubLoginRequired`, `ao service status` exits 0. It
     prints `login required for the live view` and the persisted JSON keys (`supervisor`,
     `port_resolution`), or the "No persisted service state found" line when there are no files.
   - `ao service list` exits 0 and prints the `hub running with dashboard authentication enabled`
     note and the registry rows.
   - `tests/service/test_cli_e2e.py` passes **unmodified**.
5. **Supervisor** (AC-31):
   - **(a)** Use the **default** spawner with `ao_executable=[sys.executable, <tmp script>]`. The
     script dumps `os.environ` as JSON to stdout, which is the child log. With
     `child_env={"AO_TEST_FLAG": "x"}`, the log shows `AO_TEST_FLAG == "x"` and an inherited
     sentinel variable set by the test.
   - **(b)** With `child_env=None` and with `child_env={}`, a patched `subprocess.Popen` receives no
     `env` kwarg.
   - **(c)** A child running `exit 78` (`_sleep_child_spawner("exit 78")`) gives, after the exit
     plus 5 `tick()` calls:
     - `status_snapshot()` state `stopped`;
     - the spawner called exactly once;
     - `restart_count == 0` and `fast_fail_count == 0`;
     - the port unchanged;
     - `last_error` containing `configuration error`;
     - the snapshot file showing the same.
   - **(d)** `tests/service/test_supervisor.py` passes **unmodified**.
6. **systemd** (AC-31):
   - The rendered unit has `RestartPreventExitStatus=78` on the line right after
     `Restart=on-failure`.
   - `systemd.py`'s source contains no `RestartPreventExitStatus=78` literal (grep).
   - The existing `test_systemd.py` assertions are unchanged.
7. **Install hint:** the existing non-`--print` `install` test path shows `AO_UI_AUTH=1` and
   `ao auth add-user` in the output.
8. ruff and mypy are clean. `tests/service` and `tests/auth/test_service_run_auth.py` are green.

## Risks
- **Restart loops** if exit 78 is not terminal. AC-5c is the gate. Existing systemd units without
  the new line still stop after `StartLimitBurst` (§18 #3).
- **The probe exception changes control flow.** Only `list` and `status` call it, and AC-4 covers
  both.
- **`run` was deliberately untested** (service-epic AC12). The stubs here are minimal; if the loop
  structure changes, update the `Event` stub rather than adding sleeps.

## Dependencies
- **Upstream:**
  - direct (HLD §24.2 #15): T-jVqH8w, T-KOv2qD;
  - transitive: T-kzEzwy, T-j9dfsw.
- **Downstream:** T-U2ERMo (subprocess e2e with the supervisor), T-otjIkJ (README service setup).

## Pseudocode / Algorithm

```
run(hub_port, hub_host, auth, auth_totp, auth_dir):
  import uvicorn (existing ImportError path -> exit 1)
  TRY launch = prepare_auth(cli=AuthCliOverrides(auth, auth_totp, auth_dir), env=os.environ,
                            workspace_root=None, realm_kind="hub", port=hub_port, bind_host=hub_host)
  EXCEPT AuthConfigError AS e: echo(f"ERROR: {e}", err=True); RAISE typer.Exit(EXIT_CONFIG)
  FOR w IN launch.warnings: echo(w, err=True)
  supervisor = Supervisor(registry, state_dir=..., hub_port=hub_port, child_env=launch.child_env)
  ... existing start / shutdown-on-failure logic unchanged ...
  hub_app = build_hub_app(build_status_provider(supervisor), auth=launch.runtime)
  server = uvicorn.Server(uvicorn.Config(hub_app, host=hub_host, port=hub_port, log_level="warning", **launch.uvicorn_kwargs))
  ... existing signal / tick loop unchanged ...
```

Refusal and terminal-child sequence: HLD §14.8.

## Schemas / Interface Notes
- `HubLoginRequired(Exception)`: attribute `hub_port: int`; no secret content.
- `Supervisor(child_env=...)` and `default_child_spawner(..., extra_env=...)` are keyword-only and
  additive. The `ChildSpawner` signature `(root, port, host, log_path)` is unchanged.

## Handoff Boundary
- **Upstream:** `prepare_auth` and `build_hub_app(auth=)`.
- **Downstream:** an auth-aware `ao service`. The hub refuses before spawning; children inherit only
  CLI-sourced settings; exit 78 is terminal.

## Verification

```
python -m pytest -q tests/service tests/auth/test_service_run_auth.py
ruff check src tests && ruff format --check src tests && mypy src
```

## Artifacts
- `meta/tickets/E-Da5Tn9-dashboard-auth-totp/T-PDGw9p-service-cli-supervisor-auth/`
