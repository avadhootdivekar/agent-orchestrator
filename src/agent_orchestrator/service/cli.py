"""`ao service` Typer sub-app (HLD §9): `add`/`remove`/`list`/`status`/`install`/`start`/
`stop`/`run` -- the user-facing surface tying `service/registry.py` + `service/ports.py`
(T-Gr8s2a) and `service/supervisor.py` (T-Sv9d4k) together into runnable commands.

Every command except `run` is fast, non-blocking, and safe to invoke repeatedly; none of
them import `fastapi`/`uvicorn` at module scope (the `[ui]` extra stays optional for
`add`/`remove`/`list`/`status`/`install`/`start`/`stop` -- only `run`, which actually serves
the hub, needs it, and imports it lazily inside its own function body).

`run` is the one command that blocks, binds a real port, and installs signal handlers -- it
is deliberately kept thin (HLD §9): everything it calls (`Supervisor.start/tick/shutdown`,
`hub.build_hub_app`) is independently unit-tested already, so no test in this epic invokes
`run` itself (AC12).
"""

from __future__ import annotations

import json
import logging
import signal
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from types import FrameType
from typing import Any

import typer

from ..ui.runs import RunRepository
from .boot_resume import BOOT_RESUME_FILENAME
from .paths import default_state_dir
from .registry import ServiceRegistry
from .supervisor import (
    PORT_RESOLUTION_FILENAME,
    SUPERVISOR_SNAPSHOT_FILENAME,
    Supervisor,
    SupervisorLockHeldError,
)
from .systemd import (
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

# Short-timeout best-effort probe for "is the hub actually up" (AC8 `list`/`status`) -- long
# enough for a loopback round trip, short enough that a dead/hung daemon doesn't stall the CLI.
HUB_PROBE_TIMEOUT_SECONDS = 0.5


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
) -> Callable[[], dict[str, Any]]:
    """Build the `status_provider` callable handed to `hub.build_hub_app` (AC2/AC15).

    Wraps `Supervisor.status_snapshot()` with a per-workspace `run_summary` (via
    `RunRepository.aggregate()`) -- `hub.py` itself never calls `RunRepository` (AC2 keeps it
    a thin adapter); this is "whatever provides `status_provider`" that call is required to
    live in instead. Cached behind a short TTL (`clock`-injectable for deterministic tests)
    so the same, potentially expensive, `aggregate()` pass is not repeated on every request.
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

    Deliberately stdlib-only (`urllib`, not `httpx`): `list`/`status` must keep working in a
    core install without the optional `[ui]` extra (`httpx` ships with it).
    """
    url = f"http://127.0.0.1:{hub_port}/api/service/status"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
            data = json.loads(response.read().decode("utf-8"))
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
    return sections or None


# -- registry commands --------------------------------------------------------------------


@app.command()
def add(
    directory: str = typer.Argument(..., metavar="DIR", help="Workspace root to register."),
    port: int | None = typer.Option(
        None, "--port", help="Pin a port (P2); omit to auto-resolve (P3) at daemon boot."
    ),
    no_autoresume: bool = typer.Option(
        False, "--no-autoresume", help="Disable boot-resume for this workspace's runs."
    ),
) -> None:
    """Register a workspace with the service."""
    root = _resolve_registered_dir(directory, must_exist=True)
    ServiceRegistry().add(root, port=port, autoresume=not no_autoresume)
    suffix = f" (pinned port {port})" if port is not None else ""
    typer.echo(f"Registered {root}{suffix}")


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
    live = _probe_hub_status(hub_port)
    live_by_root = {w["root"]: w for w in live.get("workspaces", [])} if live else {}

    if not registry.workspaces:
        typer.echo("No workspaces registered.")
    else:
        typer.echo(f"{'ROOT':<50} {'PORT':<8} {'AUTORESUME':<12} {'STATE'}")
        for entry in registry.workspaces:
            live_entry = live_by_root.get(entry.root)
            port = live_entry["port"] if live_entry else entry.port
            state = live_entry["state"] if live_entry else "-"
            port_str = str(port) if port is not None else "-"
            typer.echo(f"{entry.root:<50} {port_str:<8} {str(entry.autoresume):<12} {state}")

    if live is None:
        guess = "" if confirmed else " (guessed default port, not confirmed)"
        typer.echo(f"Note: service daemon not confirmed running on port {hub_port}{guess}.")


@app.command()
def status() -> None:
    """Full service status: live from the hub if reachable, else from persisted state files."""
    state_dir = default_state_dir()
    hub_port, confirmed = _persisted_hub_port(state_dir)
    live = _probe_hub_status(hub_port)

    if live is not None:
        typer.echo(json.dumps(live, indent=2))
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
) -> None:
    """Generate (and, unless --print, install) the `ao.service` systemd user unit."""
    try:
        result = install_unit(print_only=print_only, hub_port=hub_port)
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
) -> None:
    """Foreground supervisor + hub daemon (HLD §6/§8). Blocks; installs SIGTERM/SIGINT
    handlers. Not invoked by any test in this epic (AC12) -- its parts (`Supervisor`,
    `hub.build_hub_app`) are each independently tested."""
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

    from .hub import build_hub_app

    state_dir = default_state_dir()
    registry = ServiceRegistry().load()
    supervisor = Supervisor(registry, state_dir=state_dir, hub_port=hub_port)

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

    status_provider = build_status_provider(supervisor)
    hub_app = build_hub_app(status_provider)
    server = uvicorn.Server(
        uvicorn.Config(hub_app, host="127.0.0.1", port=hub_port, log_level="warning")
    )

    stop_event = threading.Event()

    def _handle_stop_signal(signum: int, frame: FrameType | None) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_stop_signal)
    signal.signal(signal.SIGINT, _handle_stop_signal)

    hub_thread = threading.Thread(target=server.run, daemon=True)
    hub_thread.start()

    typer.echo(f"Agent Orchestrator service running -- hub on http://127.0.0.1:{hub_port}/")
    try:
        while not stop_event.is_set():
            # `.wait()` (unlike `time.sleep()`) returns as soon as the SIGTERM/SIGINT
            # handler sets the event, rather than PEP 475 retrying a sleep for its full
            # remaining duration across the signal -- bounds shutdown latency to well under
            # one tick interval instead of up to a full tick.
            woken_early = stop_event.wait(HUB_TICK_INTERVAL_SECONDS)
            if not woken_early:
                supervisor.tick()
    finally:
        supervisor.shutdown()
        server.should_exit = True
        hub_thread.join(timeout=10.0)


__all__ = ["app", "build_status_provider"]
