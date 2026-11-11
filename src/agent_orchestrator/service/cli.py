"""`ao service` Typer sub-app (HLD §9): `add`/`remove`/`list`/`status`/`install`/`start`/
`stop`/`run` -- the user-facing surface tying `service/registry.py` + `service/ports.py`
(T-Gr8s2a) and `service/supervisor.py` (T-Sv9d4k) together into runnable commands.

Every command except `run` is fast, non-blocking, and safe to invoke repeatedly; none of
them import `fastapi`/`uvicorn` at module scope (the `[ui]` extra stays optional for
`add`/`remove`/`list`/`status`/`install`/`start`/`stop` -- only `run`, which actually serves
the hub, needs it, and imports it lazily inside its own function body).

`run` is the one command that blocks, binds a real port, and installs signal handlers -- it
is deliberately kept thin (HLD §9): everything it calls (`Supervisor.start/tick/shutdown`,
`hub.build_hub_app`) is independently unit-tested already. The service epic's own tests
(AC12) never invoke `run`; the dashboard-auth epic's `tests/auth/test_service_run_auth.py`
does, with stub `uvicorn`/`Supervisor` modules, to pin the auth startup sequence.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any

import typer

from ..auth.model import TotpPolicy
from ..errors import EXIT_CONFIG, OrchestratorError
from ..ui.runs import RunRepository
from .boot_resume import BOOT_RESUME_FILENAME
from .paths import default_registry_path, default_state_dir
from .registry import (
    DEFAULT_WEBHOOK_HOST,
    DEFAULT_WEBHOOK_PORT,
    ServiceRegistry,
    ServiceRegistryFile,
)
from .supervisor import (
    LOOPBACK_HOSTS,
    PORT_RESOLUTION_FILENAME,
    SUPERVISOR_SNAPSHOT_FILENAME,
    Supervisor,
    SupervisorLockHeldError,
)

if TYPE_CHECKING:
    from .schedule_engine import ScheduleEngine
from .systemd import (
    DEFAULT_HUB_HOST,
    DEFAULT_HUB_PORT,
    ServiceError,
    install_unit,
    start_via_systemctl,
    stop_via_systemctl,
    systemctl_available,
)

logger = logging.getLogger(__name__)

app = typer.Typer(
    name="service", help="Multi-workspace dashboard service (hub + supervisor daemon)."
)

# `run`'s monitor-loop tick interval (HLD §9) -- a named constant, not a magic literal in the
# `while` loop below.
HUB_TICK_INTERVAL_SECONDS = 2.0

# TTL for `build_status_provider`'s cache (AC15/early-gate correction): `RunRepository.
# aggregate()` fully parses every `state.json` in a workspace, so this avoids re-parsing
# it on every hub poll from every open browser tab plus `ao service status`/`list`.
HUB_STATUS_CACHE_TTL_SECONDS = 5.0

# Subdirectory of the state dir holding the scheduler's persisted state (HLD §12); `status`
# falls back to `<state_dir>/schedules/state.json` when the hub is unreachable.
SCHEDULES_STATE_SUBDIR = "schedules"
SCHEDULES_STATE_FILENAME = "state.json"

# Short-timeout best-effort probe for "is the hub actually up" (AC8 `list`/`status`) -- long
# enough for a loopback round trip, short enough that a dead/hung daemon doesn't stall the CLI.
HUB_PROBE_TIMEOUT_SECONDS = 0.5

# HTTP status the authenticated hub answers an anonymous probe with (HLD §15 row 1).
HTTP_UNAUTHORIZED = 401

# `list` wording when the hub answered 401 (HLD §15 row 1).
HUB_AUTH_NOTE = (
    "Note: hub running with dashboard authentication enabled — live state is shown in the "
    "browser after login"
)


class HubLoginRequired(OrchestratorError):
    """The hub answered the status probe with 401: it is up, with dashboard authentication
    on. Carries only the port (no secret content); `list`/`status` fall back to the
    persisted state files."""

    def __init__(self, hub_port: int) -> None:
        super().__init__(f"hub on port {hub_port} requires login")
        self.hub_port = hub_port


# -- shared helpers -----------------------------------------------------------------------


def _resolve_registered_dir(raw: str, *, must_exist: bool) -> str:
    """Absolute, resolved workspace root (mirrors `registry.py`'s own normalization)."""
    resolved = Path(raw).resolve()
    if must_exist and not resolved.is_dir():
        typer.echo(f"ERROR: not a directory: {resolved}", err=True)
        raise typer.Exit(1)
    return str(resolved)


def build_status_provider(
    supervisor: Supervisor,
    *,
    clock: Callable[[], float] = time.monotonic,
    run_repository_factory: Callable[[str], RunRepository] = RunRepository,
    ttl_seconds: float = HUB_STATUS_CACHE_TTL_SECONDS,
    schedule_engine: ScheduleEngine | None = None,
) -> Callable[[], dict[str, Any]]:
    """Build the `status_provider` callable handed to `hub.build_hub_app` (AC2/AC15).

    Wraps `Supervisor.status_snapshot()` with a per-workspace `run_summary` (via
    `RunRepository.aggregate()`) -- `hub.py` itself never calls `RunRepository` (AC2 keeps it
    a thin adapter); this is "whatever provides `status_provider`" that call is required to
    live in instead. Cached behind a short TTL (`clock`-injectable for deterministic tests)
    so the same, potentially expensive, `aggregate()` pass is not repeated on every request.

    With a *schedule_engine*, its `status_snapshot()` is merged under a top-level
    ``"schedules"`` key inside the same cache (HLD §12); `hub.py` renders unknown keys
    harmlessly, so it needs no edit (ADR-0010 D5). Without one, the payload is unchanged
    apart from ``"schedules": {"enabled": False}`` so consumers can tell "off" from "absent".
    """
    cached_payload: dict[str, Any] | None = None
    cached_at: float | None = None

    def _provider() -> dict[str, Any]:
        nonlocal cached_payload, cached_at
        now = clock()
        if cached_payload is not None and cached_at is not None and now - cached_at < ttl_seconds:
            return cached_payload

        payload = supervisor.status_snapshot()
        for workspace in payload.get("workspaces", []):
            root = workspace.get("root")
            if not root:
                continue
            try:
                stats = run_repository_factory(root).aggregate()
            except OSError as exc:
                logger.warning(
                    "hub status: could not aggregate run stats for workspace %r (%s) -- "
                    "omitting its run_summary this poll, rest of the status view unaffected",
                    root,
                    exc,
                )
                continue  # one bad workspace must not break the whole hub's status view
            workspace["run_summary"] = {
                "total_runs": stats.total_runs,
                "runs_by_status": dict(stats.runs_by_status),
            }

        payload["schedules"] = (
            schedule_engine.status_snapshot() if schedule_engine is not None else {"enabled": False}
        )

        cached_payload = payload
        cached_at = now
        return payload

    return _provider


def _probe_hub_status(
    hub_port: int, *, timeout: float = HUB_PROBE_TIMEOUT_SECONDS
) -> dict[str, Any] | None:
    """Best-effort `GET http://127.0.0.1:<hub_port>/api/service/status`. Returns `None` on
    any failure (daemon not running, extra not installed, timeout, bad JSON) -- every one of
    those collapses to the same "fall back to registry/state files" path for the caller.
    The one exception: an HTTP 401 means the hub is up with authentication on, so it raises
    `HubLoginRequired`.

    Deliberately stdlib-only (`urllib`, not `httpx`): `list`/`status` must keep working in a
    core install without the optional `[ui]` extra (`httpx` ships with it).
    """
    url = f"http://127.0.0.1:{hub_port}/api/service/status"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # before URLError: HTTPError subclasses it
        if exc.code == HTTP_UNAUTHORIZED:
            raise HubLoginRequired(hub_port) from None
        return None
    except (OSError, urllib.error.URLError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _persisted_hub_port(state_dir: Path) -> tuple[int, bool]:
    """The hub port to probe (AC19 / early-gate correction #4): read from the running
    daemon's own persisted `supervisor.json`, never from this invocation's own `--hub-port`
    flag/default -- the daemon may have been started with a different one. Returns
    `(port, confirmed)`; `confirmed=False` means `supervisor.json` was missing/unreadable and
    the module-constant default is a last-resort guess, not a confirmed running port.
    """
    snapshot_path = state_dir / SUPERVISOR_SNAPSHOT_FILENAME
    if snapshot_path.is_file():
        try:
            data = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict) and isinstance(data.get("hub_port"), int):
            return data["hub_port"], True
    return DEFAULT_HUB_PORT, False


def _read_fallback_status(state_dir: Path) -> dict[str, Any] | None:
    """`ao service status`'s fallback when the hub is unreachable: read the persisted state
    files directly. A fresh install with no state files yet is treated as "not running", not
    an error -- returns `None` rather than raising."""
    sections: dict[str, Any] = {}
    for filename, key in (
        (SUPERVISOR_SNAPSHOT_FILENAME, "supervisor"),
        (PORT_RESOLUTION_FILENAME, "port_resolution"),
        (BOOT_RESUME_FILENAME, "boot_resume"),
    ):
        path = state_dir / filename
        if not path.is_file():
            continue
        try:
            sections[key] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    schedules_path = state_dir / SCHEDULES_STATE_SUBDIR / SCHEDULES_STATE_FILENAME
    if schedules_path.is_file():
        try:
            sections["schedules"] = json.loads(schedules_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return sections or None


def _schedules_summary(schedules: Any) -> str | None:
    """One-line schedules summary for `ao service status` (live `schedules` payload from the
    hub). Tolerates the persisted-state shape (no counts) by returning ``None``."""
    if not isinstance(schedules, dict) or "totals" not in schedules:
        return None
    totals = schedules.get("totals") or {}
    webhook = schedules.get("webhook") or {}
    wh = "on" if webhook.get("enabled") else "off"
    return (
        f"Schedules: {totals.get('active', 0)} active, {totals.get('disabled', 0)} disabled, "
        f"{totals.get('quarantined', 0)} quarantined; "
        f"next fire {schedules.get('next_fire_at') or '-'}; "
        f"live scheduled runs {schedules.get('live_scheduled_runs', 0)}; webhook {wh}"
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)


class _DeferredLauncher:
    """Engine-launcher Protocol shim whose real target is wired right after the engine is
    constructed (the launcher needs the engine's stores; the engine needs a launcher)."""

    def __init__(self) -> None:
        self.target: Any = None

    def launch(self, decision: Any) -> Any:
        if self.target is None:
            raise RuntimeError("scheduled launcher used before the engine finished wiring")
        return self.target.launch(decision)

    def launch_capped_resume(self, rec: Any, new_cap_usd: float) -> Any:
        if self.target is None:
            raise RuntimeError("scheduled launcher used before the engine finished wiring")
        return self.target.launch_capped_resume(rec, new_cap_usd)


def build_schedule_engine(
    registry: ServiceRegistryFile,
    registry_path: Path,
    state_dir: Path,
    supervisor: Supervisor,
    *,
    no_schedules: bool = False,
    clock: Callable[[], datetime] = _utc_now,
    monotonic: Callable[[], float] = time.monotonic,
    engine_factory: Callable[..., ScheduleEngine] | None = None,
) -> ScheduleEngine | None:
    """Construct (and `start()`) the `ScheduleEngine`, or return ``None`` when a kill switch is
    set -- the CLI ``--no-schedules`` flag or registry ``schedules.enabled: false`` -- in which
    case no engine object exists at all (T-Sv5Hb3 AC6).

    Called AFTER ``supervisor.start()`` so orphan-fire reconciliation sees auto-resumed runs
    (AC3). The launcher is built with the engine's own shared stores and per-workspace
    `ProcessSupervisor` cache (F2: only the instance that spawned a child can reap it).

    F23 (pinned by test): the engine re-reads *registry_path* each pass rather than sharing the
    daemon's one-shot ``registry`` object, so a workspace opted in while the daemon runs is
    scheduled at once, but has no dashboard child until the service restarts (HLD §6.1).
    """
    cfg = registry.schedules
    if no_schedules or not cfg.enabled:
        return None
    from .schedule_engine import ScheduleEngine
    from .schedule_launch import ScheduledLauncher
    from .webhook import WebhookSource

    factory = engine_factory or ScheduleEngine
    deferred = _DeferredLauncher()
    engine = factory(
        registry_path,
        state_dir=state_dir,
        clock=clock,
        monotonic=monotonic,
        launcher=deferred,
        eval_interval_seconds=cfg.eval_interval_seconds,
        max_concurrent_global=cfg.max_concurrent_global,
    )
    # Event kind served by persisted webhook requests (T-Wh9Kv1): the engine drains them via
    # TriggerSource.observe/commit, so ``ao service run`` and a separate engine see the same queue.
    engine.register_trigger_source(
        "webhook", lambda root, binding: WebhookSource(state_dir, root, binding)
    )
    # The launcher must share the engine's own stores and per-workspace ProcessSupervisor
    # cache, which only exist once the engine does -- hence the deferred hand-over.
    deferred.target = ScheduledLauncher(
        state_dir,
        process_supervisor_factory=engine.process_supervisor,
        clock=clock,
        fire_store=engine.fire_store,
        state_store=engine.state_store,
        event_log=engine.event_log,
        usage_ledger=engine.usage_ledger,  # L2: one ledger + lock for pre-check and authority
        governance_lock_path=engine.governance_lock_path,
    )
    try:
        engine.start()
    except Exception:
        # start() may hold engine.lock already (e.g. reconcile raised): release it before the
        # caller degrades to engine=None, or the next restart would be locked out too.
        try:
            engine.shutdown()
        except Exception:  # noqa: BLE001 - best effort; the original error is what matters
            logger.warning("engine.shutdown() after a failed start() also failed", exc_info=True)
        raise
    return engine


def monitor_tick(supervisor: Supervisor, engine: ScheduleEngine | None) -> None:
    """One monitor-loop pass: ``supervisor.tick()`` then, if present, ``engine.tick()``.

    AC5: an exception escaping the engine is logged with its traceback under the named event
    and swallowed so the supervisor keeps ticking; the engine already isolates per-schedule
    failures, this is the outer backstop."""
    supervisor.tick()
    if engine is None:
        return
    try:
        engine.tick()
    except Exception:
        logger.exception("schedule.engine_tick_failed")


def shutdown_daemon(
    supervisor: Supervisor,
    engine: ScheduleEngine | None,
    servers: list[Any],
    threads: list[threading.Thread],
) -> None:
    """The `run` ``finally`` body: engine first (best effort, never blocks supervisor
    shutdown), then supervisor, then stop each uvicorn server (``should_exit`` + bounded join)."""
    try:
        if engine is not None:
            try:
                engine.shutdown()
            except Exception:
                logger.exception("schedule.engine_shutdown_failed")
    finally:
        supervisor.shutdown()
        for server in servers:
            server.should_exit = True
        for thread in threads:
            thread.join(timeout=10.0)


def build_service_webhook_app(host: str, registry_path: Path, state_dir: Path) -> Any:
    """The webhook listener app for ``ao service run --webhook`` (`T-Wh9Kv1`): HMAC-verified
    deliveries are persisted as ``requests/*.json`` for the engine to drain. NOT wrapped in the
    hub's session ``AuthMiddleware`` (F18) -- HMAC is its auth."""
    from .fire_store import ScheduleEventLog
    from .webhook import WebhookRequestQueue, build_webhook_app, make_binding_lookup

    events = ScheduleEventLog(state_dir)
    return build_webhook_app(
        make_binding_lookup(registry_path),
        WebhookRequestQueue(state_dir),
        emit=events.emit,
        bound_host=host,
    )


# -- registry commands --------------------------------------------------------------------


@app.command()
def add(
    directory: str = typer.Argument(..., metavar="DIR", help="Workspace root to register."),
    port: int | None = typer.Option(
        None, "--port", help="Pin a port (P2); omit to auto-resolve (P3) at daemon boot."
    ),
    host: str | None = typer.Option(
        None,
        "--host",
        help=(
            "Pin a bind host (P2), e.g. 0.0.0.0 to expose on all interfaces -- the"
            " dashboard is UNAUTHENTICATED, only do this on a trusted network. Omit for"
            " loopback (the workspace's own `ui.host` still takes precedence)."
        ),
    ),
    no_autoresume: bool = typer.Option(
        False, "--no-autoresume", help="Disable boot-resume for this workspace's runs."
    ),
    schedules: bool = typer.Option(
        False,
        "--schedules/--no-schedules",
        help=(
            "Opt this workspace in to clock-triggered runs from its .ao/schedules.yaml "
            "(default OFF: a repo-supplied schedule file is otherwise inert)."
        ),
    ),
) -> None:
    """Register a workspace with the service."""
    root = _resolve_registered_dir(directory, must_exist=True)
    ServiceRegistry().add(
        root, port=port, autoresume=not no_autoresume, host=host, schedules=schedules
    )
    suffix = f" (pinned port {port})" if port is not None else ""
    typer.echo(f"Registered {root}{suffix}")


@app.command(name="set")
def set_cmd(
    directory: str = typer.Argument(..., metavar="DIR", help="Registered workspace root."),
    schedules: bool = typer.Option(
        ..., "--schedules/--no-schedules", help="Opt the workspace in/out of scheduled runs."
    ),
) -> None:
    """Update a registered workspace's settings (currently the schedules opt-in)."""
    root = _resolve_registered_dir(directory, must_exist=False)
    try:
        ServiceRegistry().set_schedules(root, schedules)
    except KeyError:
        typer.echo(f"ERROR: not registered: {root}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"{root}: schedules {'enabled' if schedules else 'disabled'}")


@app.command()
def remove(
    directory: str = typer.Argument(..., metavar="DIR", help="Workspace root to deregister."),
) -> None:
    """Deregister a workspace from the service."""
    root = _resolve_registered_dir(directory, must_exist=False)
    store = ServiceRegistry()
    current = store.load()
    if not any(w.root == root for w in current.workspaces):
        typer.echo(f"ERROR: not registered: {root}", err=True)
        raise typer.Exit(1)
    store.remove(root)
    typer.echo(f"Removed {root}")


@app.command(name="list")
def list_cmd() -> None:
    """List registered workspaces, annotated with live state when the daemon is reachable."""
    registry = ServiceRegistry().load()
    state_dir = default_state_dir()
    hub_port, confirmed = _persisted_hub_port(state_dir)
    login_required = False
    try:
        live = _probe_hub_status(hub_port)
    except HubLoginRequired:
        live, login_required = None, True
    live_by_root = {w["root"]: w for w in live.get("workspaces", [])} if live else {}

    if not registry.workspaces:
        typer.echo("No workspaces registered.")
    else:
        typer.echo(f"{'ROOT':<50} {'PORT':<8} {'AUTORESUME':<12} {'SCHEDULES':<10} {'STATE'}")
        for entry in registry.workspaces:
            live_entry = live_by_root.get(entry.root)
            port = live_entry["port"] if live_entry else entry.port
            state = live_entry["state"] if live_entry else "-"
            port_str = str(port) if port is not None else "-"
            typer.echo(
                f"{entry.root:<50} {port_str:<8} {str(entry.autoresume):<12} "
                f"{str(entry.schedules):<10} {state}"
            )

    if login_required:
        typer.echo(HUB_AUTH_NOTE)
    elif live is None:
        guess = "" if confirmed else " (guessed default port, not confirmed)"
        typer.echo(f"Note: service daemon not confirmed running on port {hub_port}{guess}.")


@app.command()
def status() -> None:
    """Full service status: live from the hub if reachable, else from persisted state files."""
    state_dir = default_state_dir()
    hub_port, confirmed = _persisted_hub_port(state_dir)
    try:
        live = _probe_hub_status(hub_port)
    except HubLoginRequired:
        typer.echo(
            f"Service daemon running on port {hub_port} (login required for the live view);"
            " persisted state:"
        )
    else:
        if live is not None:
            typer.echo(json.dumps(live, indent=2))
            summary = _schedules_summary(live.get("schedules"))
            if summary:
                typer.echo(summary)
            return
        guess = "" if confirmed else " (guessed default port, not confirmed)"
        typer.echo(f"Service daemon not running (or unreachable) on port {hub_port}{guess}.")

    fallback = _read_fallback_status(state_dir)
    if fallback is None:
        typer.echo("No persisted service state found (never run, or cleanly shut down).")
        return
    typer.echo(json.dumps(fallback, indent=2))


# -- systemd commands ------------------------------------------------------------------------


@app.command()
def install(
    print_only: bool = typer.Option(
        False, "--print", help="Print the rendered unit; do not write to disk."
    ),
    hub_port: int = typer.Option(
        DEFAULT_HUB_PORT, "--hub-port", help="Hub port to bake into the unit's ExecStart."
    ),
    hub_host: str = typer.Option(
        DEFAULT_HUB_HOST,
        "--hub-host",
        help=(
            "Hub bind address to bake into the unit's ExecStart. Defaults to loopback; "
            "use 0.0.0.0 to reach the hub from the LAN (unauthenticated unless --auth / "
            "AO_UI_AUTH -- trusted networks only)."
        ),
    ),
    auth: bool = typer.Option(
        False,
        "--auth",
        help="Bake --auth into the unit's ExecStart (dashboard login on the hub and its children).",
    ),
    webhook: bool = typer.Option(
        False, "--webhook", help="Bake --webhook into the unit's ExecStart (default: not baked)."
    ),
    webhook_host: str | None = typer.Option(
        None, "--webhook-host", help="Webhook bind host to bake in (with --webhook)."
    ),
    webhook_port: int | None = typer.Option(
        None, "--webhook-port", help="Webhook port to bake in (with --webhook)."
    ),
) -> None:
    """Generate (and, unless --print, install) the `ao.service` systemd user unit."""
    try:
        result = install_unit(
            print_only=print_only,
            hub_port=hub_port,
            hub_host=hub_host,
            auth=auth,
            webhook=webhook,
            webhook_host=webhook_host,
            webhook_port=webhook_port,
        )
    except ServiceError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc

    if print_only:
        typer.echo(result)
        return

    typer.echo(f"Wrote unit file: {result}")
    typer.echo("")
    typer.echo("Next steps:")
    typer.echo("  systemctl --user daemon-reload && systemctl --user enable --now ao")
    typer.echo(
        "  Headless box (no active login session)? Run: loginctl enable-linger <user>"
        " -- needed for the user unit to start at boot and survive logout."
    )
    typer.echo(
        "  Populate ~/.config/ao/service.env with any credentials boot-resumed runs need"
        " (EnvironmentFile is optional -- a missing file will not block startup)."
    )
    typer.echo(
        "  Dashboard login (optional): re-run `ao service install --auth` (bakes --auth into"
        " ExecStart; preferred), or add AO_UI_AUTH=1 (and optionally AO_UI_AUTH_TOTP=required)"
        " to ~/.config/ao/service.env. Create an account with `ao auth add-user <name>`, then"
        " `systemctl --user restart ao`."
    )
    typer.echo(
        "  Warning: env-only enablement (service.env) is ignored by a stale `ao` snapshot that"
        " predates auth, so the dashboards start UNAUTHENTICATED without any error. After"
        ' restarting, verify with `ao auth status` or GET /api/auth/status ("enabled": true).'
    )
    typer.echo(
        "  Note: a globally-installed `ao` can be a stale snapshot of a different version"
        " (see meta/ROADMAP.md §4) -- if the running unit looks out of date, re-run"
        " install.sh --force."
    )


@app.command()
def start() -> None:
    """Start the service via `systemctl --user start ao`, or print guidance if unavailable."""
    if not systemctl_available():
        typer.echo("systemctl not found -- no systemd user manager on this box.")
        typer.echo("Run the service in the foreground instead:  ao service run")
        return

    result = start_via_systemctl()
    if result.returncode != 0:
        typer.echo(f"ERROR: systemctl --user start ao failed: {result.stderr}", err=True)
        raise typer.Exit(1)
    typer.echo("Started: systemctl --user start ao")


@app.command()
def stop() -> None:
    """Stop the service via `systemctl --user stop ao`, or print guidance if unavailable."""
    if not systemctl_available():
        typer.echo("systemctl not found -- no systemd user manager on this box.")
        typer.echo("Stop a foreground `ao service run` directly instead (Ctrl-C / SIGTERM).")
        return

    result = stop_via_systemctl()
    if result.returncode != 0:
        typer.echo(f"ERROR: systemctl --user stop ao failed: {result.stderr}", err=True)
        raise typer.Exit(1)
    typer.echo("Stopped: systemctl --user stop ao")


# -- the foreground daemon -------------------------------------------------------------------


@app.command()
def run(
    hub_port: int = typer.Option(
        DEFAULT_HUB_PORT, "--hub-port", help="Port for the hub HTTP server."
    ),
    hub_host: str = typer.Option(
        DEFAULT_HUB_HOST,
        "--hub-host",
        help=(
            "Interface for the hub HTTP server. Defaults to loopback because the hub is "
            "unauthenticated unless --auth / AO_UI_AUTH, and reports every registered "
            "workspace's state; use 0.0.0.0 to expose it on the LAN (trusted networks only)."
        ),
    ),
    auth: bool | None = typer.Option(
        None,
        "--auth/--no-auth",
        help="Require login on the hub and the workspace dashboards. Env: AO_UI_AUTH.",
    ),
    auth_totp: TotpPolicy | None = typer.Option(
        None, "--auth-totp", help="TOTP policy with --auth. Env: AO_UI_AUTH_TOTP."
    ),
    auth_dir: str | None = typer.Option(
        None, "--auth-dir", help="Directory of the user store. Env: AO_AUTH_DIR."
    ),
    no_schedules: bool = typer.Option(
        False,
        "--no-schedules",
        help="Kill switch: do not construct the schedule engine at all.",
    ),
    webhook: bool | None = typer.Option(
        None,
        "--webhook/--no-webhook",
        help="Serve the webhook listener on its own port (default: registry, else off).",
    ),
    webhook_host: str | None = typer.Option(
        None, "--webhook-host", help="Webhook bind host (default: registry, else loopback)."
    ),
    webhook_port: int | None = typer.Option(
        None, "--webhook-port", help="Webhook port (default: registry, else 8771)."
    ),
) -> None:
    """Foreground supervisor + hub daemon (HLD §6/§8). Blocks; installs SIGTERM/SIGINT
    handlers. Its parts (`Supervisor`, `hub.build_hub_app`, `prepare_auth`) are each
    independently tested."""
    try:
        import uvicorn
    except ImportError:
        typer.echo(
            "ERROR: `ao service run` needs the optional 'ui' extra.\n"
            "  uv sync --extra ui       (this repo)\n"
            "  pip install 'agent-orchestrator[ui]'",
            err=True,
        )
        raise typer.Exit(1) from None

    from ..auth.errors import AuthConfigError
    from ..auth.launch import prepare_auth
    from ..auth.settings import AuthCliOverrides
    from .hub import build_hub_app

    # E-Da5Tn9: the one auth startup sequence, BEFORE the Supervisor exists -- a refusal
    # takes no singleton lock and spawns no child (HLD §14.8, S14).
    try:
        launch = prepare_auth(
            cli=AuthCliOverrides(enabled=auth, totp=auth_totp, store_dir=auth_dir),
            env=os.environ,
            workspace_root=None,
            realm_kind="hub",
            port=hub_port,
            bind_host=hub_host,
        )
    except AuthConfigError as exc:
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(EXIT_CONFIG) from None
    for line in launch.warnings:
        typer.echo(line, err=True)

    state_dir = default_state_dir()
    registry_path = default_registry_path()
    registry = ServiceRegistry(registry_path).load()

    wh_cfg = registry.schedules.webhook
    wh_enabled = wh_cfg.enabled if webhook is None else webhook
    wh_host = webhook_host or wh_cfg.host or DEFAULT_WEBHOOK_HOST
    wh_port = webhook_port or wh_cfg.port or DEFAULT_WEBHOOK_PORT
    webhook_app: Any = None
    if wh_enabled:
        from .webhook import NON_LOOPBACK_WARNING, webhook_uvicorn_kwargs

        if wh_host not in LOOPBACK_HOSTS:
            typer.echo(NON_LOOPBACK_WARNING.format(host=wh_host), err=True)
        # Built before the Supervisor exists: a listener that cannot be built (e.g. the [ui]
        # extra is missing) must take no singleton lock and spawn no child.
        webhook_app = build_service_webhook_app(wh_host, registry_path, state_dir)

    supervisor = Supervisor(
        registry, state_dir=state_dir, hub_port=hub_port, child_env=launch.child_env
    )

    try:
        supervisor.start()
    except SupervisorLockHeldError as exc:
        # Nothing was started (the lock itself was never acquired) -- no shutdown() to run.
        typer.echo(f"ERROR: {exc}", err=True)
        raise typer.Exit(1) from exc
    except Exception:
        # start() may have already run boot-resume and spawned some children before failing
        # partway through (e.g. a hub bind check, or a later workspace's spawn raising) --
        # best-effort shutdown() so those children and the singleton lock don't leak past
        # this process exiting on the exception below.
        logger.warning(
            "`ao service run` failed partway through startup -- running best-effort "
            "shutdown() to release the lock and stop any children that did spawn"
        )
        supervisor.shutdown()
        raise

    # AC3: after supervisor.start() so reconciliation sees auto-resumed runs.
    # H2: the scheduler is optional -- a held engine.lock (``ao schedule daemon`` / ``run-now
    # --local``) or a reconcile error must degrade to engine=None, never crash the service after
    # the dashboards have spawned (systemd would crash-loop them).
    engine: ScheduleEngine | None
    try:
        engine = build_schedule_engine(
            registry, registry_path, state_dir, supervisor, no_schedules=no_schedules
        )
    except Exception:
        logger.exception(
            "schedule.engine_start_failed -- continuing without the scheduler "
            "(dashboards stay up; `ao service status` shows schedules off)"
        )
        engine = None

    status_provider = build_status_provider(supervisor, schedule_engine=engine)
    hub_app = build_hub_app(status_provider, auth=launch.runtime)
    server = uvicorn.Server(
        uvicorn.Config(
            hub_app, host=hub_host, port=hub_port, log_level="warning", **launch.uvicorn_kwargs
        )
    )

    stop_event = threading.Event()

    def _handle_stop_signal(signum: int, frame: FrameType | None) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_stop_signal)
    signal.signal(signal.SIGINT, _handle_stop_signal)

    hub_thread = threading.Thread(target=server.run, daemon=True)
    hub_thread.start()
    servers: list[Any] = [server]
    threads: list[threading.Thread] = [hub_thread]
    if webhook_app is not None:
        wh_server = uvicorn.Server(
            uvicorn.Config(
                webhook_app,
                host=wh_host,
                port=wh_port,
                log_level="warning",
                **webhook_uvicorn_kwargs(),  # M2: concurrency + keep-alive bounds
            )
        )
        wh_thread = threading.Thread(target=wh_server.run, daemon=True)
        wh_thread.start()
        servers.append(wh_server)
        threads.append(wh_thread)

    typer.echo(f"Agent Orchestrator service running -- hub on http://{hub_host}:{hub_port}/")
    try:
        while not stop_event.is_set():
            # `.wait()` (unlike `time.sleep()`) returns as soon as the SIGTERM/SIGINT
            # handler sets the event, rather than PEP 475 retrying a sleep for its full
            # remaining duration across the signal -- bounds shutdown latency to well under
            # one tick interval instead of up to a full tick.
            woken_early = stop_event.wait(HUB_TICK_INTERVAL_SECONDS)
            if not woken_early:
                monitor_tick(supervisor, engine)
    finally:
        shutdown_daemon(supervisor, engine, servers, threads)


__all__ = ["app", "build_status_provider"]
