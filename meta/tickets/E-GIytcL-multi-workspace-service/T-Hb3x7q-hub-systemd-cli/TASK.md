# TASK: T-Hb3x7q-hub-systemd-cli

## Metadata
- Task ID: `T-Hb3x7q-hub-systemd-cli`
- Epic ID: `E-GIytcL-multi-workspace-service`
- Owner: developer agent
- Created: 2026-08-28
- Last Updated: 2026-08-28
- Status: Done
- Estimate: < 2 days

## Requirements Mapping
- Requirement IDs: FR-5, FR-6, NFR-2 (see `../EPIC.md`)

## Description
The user-facing surface: the hub HTTP server, systemd unit generation, and the
`ao service` CLI sub-app that ties `T-Gr8s2a`'s registry/ports and `T-Sv9d4k`'s
`Supervisor` together into runnable commands. Depends on both prior tasks — read their
actual merged code (`service/registry.py`, `service/ports.py`, `service/supervisor.py`,
`service/boot_resume.py`) before starting; do not re-derive their APIs from the HLD alone.

Files you own (create/edit freely):
- `src/agent_orchestrator/service/hub.py` (new)
- `src/agent_orchestrator/service/systemd.py` (new)
- `src/agent_orchestrator/service/cli.py` (new)
- `tests/service/test_hub.py`, `tests/service/test_systemd.py`,
  `tests/service/test_cli_e2e.py` (new)

Do NOT touch: `service/registry.py`, `service/ports.py`, `service/paths.py`,
`service/supervisor.py`, `service/boot_resume.py` (read-only — import, do not edit).
In `cli.py` (top-level), make ONLY this edit: one import
(`from .service.cli import app as service_app`) and one line
(`app.add_typer(service_app, name="service")`), placed near the other command
registrations; do not reformat or touch anything else in that file. Do NOT touch
`models.py`, `executors/`, `spec.py`, `validate.py`, `specs/*.schema.json`, `templates/`.

## Acceptance Criteria

### Hub (`service/hub.py`)
1. `fastapi`/`uvicorn` imported lazily (inside functions, not module scope) — mirrors
   `ui/app.py`'s own `[ui]`-extra-optionality rule; a core `ao` install without `[ui]`
   must still be able to import `service/hub.py` itself (e.g. from `service/cli.py`'s
   module scope) without raising `ImportError` at import time.
2. `build_hub_app(status_provider: Callable[[], dict]) -> FastAPI`: `GET /` returns an
   HTML index (workspace root, resolved port, a clickable `http://127.0.0.1:<port>/`
   link, coarse run-count summary) rendered from `status_provider()`'s payload — do not
   hand-roll a second run-count computation; if a run summary needs `RunRepository`,
   that call belongs in the `status_provider` this module is handed, not inside
   `hub.py` itself (keep `hub.py` a thin adapter, same split ADR-0010 established for
   `ui/app.py` vs `ui/service.py`). `GET /api/service/status` returns
   `status_provider()`'s dict verbatim as JSON.
3. Bind loopback-only by default; reuse `ui.security.DEFAULT_ALLOWED_HOSTS`/
   `resolve_allowed_hosts` for the Host-header allowlist rather than re-deriving a
   second one — import from `ui.security`, do not copy its constants.

### systemd (`service/systemd.py`)
4. `resolve_ao_executable() -> str` — `sys.argv[0]` if `Path(sys.argv[0]).name == "ao"`,
   else `shutil.which("ao")`, else raise a clear `ServiceError` (or similar) naming the
   problem ("no absolute `ao` executable path found — installed via `uv tool install` or
   on PATH required for a systemd unit; a `python -m` dev invocation cannot be handed to
   systemd"). This function's only job is resolving the path; it does not touch disk.
5. `render_unit(ao_executable: str) -> str` — the exact unit text from HLD §7,
   including `KillMode=process` (ADR-0012 D2 — do not omit or default this; a reviewer
   will specifically check for its presence) and `ExecStart=<ao_executable> service run`.
6. `install_unit(*, print_only: bool, unit_dir: Path | None = None) -> Path | str` —
   `print_only=True` returns the rendered text without touching disk (no file I/O at
   all — this is what the e2e test exercises, and what CI can safely call). Otherwise
   writes to `unit_dir or (XDG_CONFIG_HOME or ~/.config)/systemd/user/ao.service`
   (creating parent dirs), returns the written path. Respect an env override for
   `unit_dir` resolution consistent with how `service/paths.py` does its XDG lookups
   (reuse that module's env-reading pattern; do not invent a third).
7. `systemctl_available() -> bool` — `shutil.which("systemctl") is not None`, injectable
   via a parameter default so tests can force both branches without touching the real
   PATH. `start_via_systemctl()`/`stop_via_systemctl()` — `subprocess.run(["systemctl",
   "--user", "start"/"stop", "ao"], ...)`, only ever called when
   `systemctl_available()` is true; the CLI command (AC10) prints guidance instead when
   it's false. **No test calls these functions for real** — test them by injecting a
   fake `runner: Callable[[list[str]], CompletedProcess]` and asserting the argv built,
   not by shelling out.

### CLI (`service/cli.py`)
8. `typer.Typer(name="service", help=...)` sub-app with commands:
   - `add <dir> [--port N] [--no-autoresume]` — resolve `dir` to an absolute path (must
     exist and be a directory, else a clear error), load/mutate/save the registry via
     `service.registry.ServiceRegistry`.
   - `remove <dir>` — same path resolution; error clearly if not registered.
   - `list` — print a table (root, pinned/resolved port if known, autoresume). If the
     hub is reachable (best-effort short-timeout HTTP GET to
     `http://127.0.0.1:<hub_port>/api/service/status`), annotate live state; otherwise
     print from the registry alone with a note that the daemon isn't confirmed running.
   - `status` — same live-vs-fallback logic as `list`, but the full status payload
     (workspaces, conflicts, boot-resume history, supervisor pid/uptime); fallback path
     reads `<state_dir>/supervisor.json`/`port_resolution.json`/`boot_resume.json`
     directly when the hub is unreachable.
   - `install [--print] [--hub-port N]` — calls `systemd.install_unit`; on success (not
     `--print`) echo the next-step guidance verbatim from HLD §7 (`systemctl --user
     daemon-reload && systemctl --user enable --now ao`, plus the `loginctl
     enable-linger` note for headless boxes).
   - `start` / `stop` — `systemctl_available()` branch to real `systemctl --user
     start/stop ao`, else print guidance to run `ao service run` directly.
   - `run [--hub-port N]` — the foreground entrypoint: build a `Supervisor` (from
     `T-Sv9d4k`), call `.start()`, register `SIGTERM`/`SIGINT` handlers that call
     `.shutdown()` and set a stop flag, start the hub server (`uvicorn.run` or
     `uvicorn.Server(...).run()`) on a background thread bound loopback with
     `hub_port` (default 8770, a **named constant**, not `UI_DEFAULT_PORT` — this port
     is deliberately distinct per the locked decisions), and loop calling
     `Supervisor.tick()` at a fixed interval (e.g. 2s) until the stop flag is set, then
     join the hub thread and exit. This command is the ONLY place in the epic that
     imports `signal` and blocks — everything it calls (`Supervisor.start/tick/shutdown`,
     `hub.build_hub_app`) is independently unit-tested already; keep this function thin.
9. Register in the top-level `cli.py`: `app.add_typer(service_app, name="service")` —
   confirm with a CliRunner e2e test that `ao service --help` lists all seven
   subcommands, proving the wiring actually took.

### Tests
10. `test_hub.py`: `build_hub_app` with a fake `status_provider` — `GET /` returns HTML
    containing each workspace's root/port; `GET /api/service/status` returns the fake
    payload verbatim as JSON. Use `fastapi.testclient.TestClient` (already a transitive
    dep via `httpx`, same as existing `tests/ui/` tests — check their setup for the
    exact pattern before inventing a new one).
11. `test_systemd.py`: `render_unit` text contains `KillMode=process`, the right
    `ExecStart` line, `Restart=on-failure`, `WantedBy=default.target`;
    `resolve_ao_executable` branches (argv[0] named `ao`, `shutil.which` fallback,
    neither → raises) each independently asserted via monkeypatching `sys.argv`/
    `shutil.which`; `install_unit(print_only=True)` performs zero filesystem writes
    (assert via a `tmp_path`-scoped check that nothing new appears) and returns the same
    text `render_unit` would; `install_unit(print_only=False, unit_dir=tmp_path/...)`
    writes the file and returns its path.
12. `test_cli_e2e.py` (CliRunner, `AO_SERVICE_CONFIG`/`AO_SERVICE_STATE_DIR` pointed at
    `tmp_path` — never the real `~`): `add` → `list` shows it; `add` again with
    `--port` → registry reflects the pin; `remove` → `list` no longer shows it;
    `status` with no daemon running falls back cleanly (no traceback, a clear "not
    running" message) when the registry is empty and when it has entries;
    `install --print` prints unit text containing `KillMode=process` and writes nothing
    to disk (assert `~/.config/systemd` — actually a tmp-overridden equivalent — has no
    new file); `ao service --help` lists `add remove list status install start stop
    run`. No test invokes `ao service run` itself (it blocks/binds a real port) or real
    `systemctl` — cover `run`'s internals through `Supervisor`'s own tests
    (`T-Sv9d4k`) and this task's `hub`/`systemd` unit tests instead.
13. `uv run pytest -q tests/service/` green (report count delta vs. epic baseline in
    your handoff). `uv run ruff check src/agent_orchestrator/service/
    src/agent_orchestrator/cli.py` + `ruff format --check` clean. `uv run mypy src` —
    report whole-tree error count. Confirm the one-line `cli.py` diff is exactly what
    AC9 describes (`git diff -- src/agent_orchestrator/cli.py` in your handoff, or
    equivalent evidence) — this is a hard boundary another epic is relying on staying
    minimal.

## Acceptance Criteria — early-gate additions (2026-08-28, see HLD §7/§8/§9, ADR-0012 "Early-gate corrections")
A `reviewer`+`architect` early-gate pass (run before this task started) found the original
AC3 as drafted ("reuse `ui.security` constants") was import-only and would enforce nothing;
plus several systemd-unit gaps. All now required:

14. **AC3 correction: the hub app must actually MOUNT `SecurityMiddleware`.**
    `build_hub_app` (or `create_hub_app`, name TBD by you) must call
    `app.add_middleware(SecurityMiddleware, allowed_hosts=resolve_allowed_hosts(...))` —
    identical pattern to `ui/app.py::create_app`. Importing `DEFAULT_ALLOWED_HOSTS`/
    `resolve_allowed_hosts` without attaching the middleware is NOT sufficient and will be
    treated as not meeting this criterion. Test it by asserting a disallowed `Host` header
    gets HTTP 421 from a real `TestClient` request, not by inspecting the app's import
    statements.
15. **Hub `GET /`'s run-summary must go through the same `status_provider` callable as
    `GET /api/service/status`**, not a second independent `RunRepository.aggregate()` call
    path — and that call should sit behind a short TTL cache (named constant, e.g. 5s,
    injectable clock) inside whatever provides `status_provider` to avoid re-parsing every
    `state.json` in every workspace on every poll from every open browser tab.
16. **`render_unit` gains**: `EnvironmentFile=-%h/.config/ao/service.env` (leading `-` =
    optional — a missing file must not fail startup), `RestartSec=5`,
    `StartLimitIntervalSec=120`, `StartLimitBurst=5`, `TimeoutStopSec=30`, and an explicit
    `--hub-port <N>` on the `ExecStart` line (never rely on `service run`'s own default —
    render the value `install` was actually called with, default 8770). All six are new
    text-assertion targets for `test_systemd.py` alongside the existing `KillMode=process`
    check.
17. **`resolve_ao_executable` must reject a non-absolute `sys.argv[0]`** even when its
    basename is `ao` (`Path(sys.argv[0]).is_absolute()` check) before falling through to
    `shutil.which("ao")` — systemd requires an absolute `ExecStart`; a relative argv[0]
    silently written into the unit would fail at systemd-parse time, not at `ao service
    install` time, which is a much worse place to discover it. Add this as its own test
    case alongside the two already-specified branches.
18. **`install`'s printed guidance additionally mentions** `~/.config/ao/service.env` for
    credentials boot-resumed runs need, and the existing "stale global `ao` snapshot" risk
    (`meta/ROADMAP.md` §4) with `install.sh --force` as the fix — both are one-line
    additions to the guidance text, asserted by the e2e test's output-contains checks.
19. **`status`/`list`'s hub-reachability probe reads the port to check from the persisted
    `<state_dir>/supervisor.json`'s `hub_port` field**, never from the invoking CLI
    invocation's own `--hub-port` flag/default — the running daemon may have been started
    with a different `--hub-port` than whatever this particular `ao service status`
    invocation happens to default to. When `supervisor.json` doesn't exist (never run, or
    cleanly shut down), fall back to the module constant default (8770) purely as a
    last-resort probe target, and be clear in the "not running" message that this is a
    guess, not a confirmed port.

## Risks
- The `[ui]` extra (fastapi/uvicorn) must stay optional for every non-`hub`/non-`run`
  command — verify `ao service add/remove/list/status/install/start/stop --help` all
  work in an environment that imports `service/cli.py` without `[ui]` installed (this
  repo's dev env has `[ui]` installed already, so this needs an explicit lazy-import
  check by reading the import statements, not just "tests passed here").
- `ao service status`'s fallback path must not crash when `<state_dir>` doesn't exist
  yet (fresh install, never `run` once) — treat "no state files" the same as "daemon not
  running," not as an error.

## Dependencies
- `T-Gr8s2a-registry-ports-config` and `T-Sv9d4k-supervisor-boot-resume` must have landed.

## Schemas / Interface Notes
- Interface: CLI surface per HLD §9 exactly (command names/flags) — do not rename
  without updating the HLD and this ticket.
- Artifacts: `~/.config/systemd/user/ao.service` (only on non-`--print` `install`).

## Handoff Boundary
- Upstream: `T-Gr8s2a-registry-ports-config`, `T-Sv9d4k-supervisor-boot-resume`;
  HLD §7, §8, §9; ADR-0012 D2 (`KillMode=process`).
- Downstream: `T-Rv5m1t-test-review-e2e` (full-suite verification, late-gate e2e,
  reviewer pass).

## Artifacts
- Docs/comments: `meta/tickets/E-GIytcL-multi-workspace-service/T-Hb3x7q-hub-systemd-cli/`
- Large outputs: N/A

## Comments

By: agent
Role: developer
Date: 2026-08-28
Comment: Implemented and verified AC1-19, including the three early-gate corrections
(AC14 hub `SecurityMiddleware` actually mounted via `app.add_middleware`, tested by a real
`TestClient` request with a disallowed `Host` header getting HTTP 421; AC16 the six new
systemd hardening lines — `RestartSec=5`, `StartLimitIntervalSec=120`, `StartLimitBurst=5`,
`TimeoutStopSec=30`, `EnvironmentFile=-%h/.config/ao/service.env`, explicit `--hub-port <N>`
on `ExecStart` — alongside the existing `KillMode=process` check; AC17
`resolve_ao_executable` rejecting a non-absolute `sys.argv[0]` even when its basename is
`ao`, via `Path(sys.argv[0]).is_absolute()`, before falling through to `shutil.which`).
AC15's run-summary TTL cache landed as `service/cli.py::build_status_provider` — a
clock-injectable wrapper around `Supervisor.status_snapshot()` that adds a per-workspace
`run_summary` via `RunRepository.aggregate()`, so `hub.py` itself never imports
`RunRepository` (kept a thin adapter per ADR-0010's split). AC19's `list`/`status`
hub-reachability probe reads the hub port from the persisted
`<state_dir>/supervisor.json.hub_port`, never from the invoking CLI's own `--hub-port`
flag/default, falling back to the module constant `DEFAULT_HUB_PORT` (8770) as an
explicitly-labeled guess when no snapshot exists yet.

Files created: `src/agent_orchestrator/service/hub.py`,
`src/agent_orchestrator/service/systemd.py`, `src/agent_orchestrator/service/cli.py`,
`tests/service/test_hub.py`, `tests/service/test_systemd.py`,
`tests/service/test_cli_e2e.py`. Top-level `src/agent_orchestrator/cli.py` changed by
exactly the two lines AC9 specifies (`from .service.cli import app as service_app` +
`app.add_typer(service_app, name="service")`) — confirmed via `git diff -- cli.py`, no
reformatting or other edits. `service/registry.py`/`ports.py`/`paths.py`/`supervisor.py`/
`boot_resume.py` untouched (read-only imports only).

`uv run pytest -q tests/service/`: 116 passed, 0 failed (66 pre-existing from
`T-Gr8s2a`/`T-Sv9d4k` + 50 new from this task's three test files). `uv run pytest -q` full
suite: 1933 passed, 7 skipped, 0 failed (baseline after `T-Sv9d4k` was 1883/7/0 — exactly
+50, zero regressions). `uv run ruff check`/`ruff format --check` on
`src/agent_orchestrator/service/` and `src/agent_orchestrator/cli.py`: clean (after one
auto-fix round for `UP037` quoted-annotation redundancy, safe under this module's
`from __future__ import annotations`). `uv run mypy src`: 4 errors, all pre-existing in
`_version.py` (unchanged baseline — zero new errors from this task's files). Full detail
also reported to the requesting agent in this session's final handoff message.
