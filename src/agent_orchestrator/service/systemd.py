"""systemd user-unit generation + install/start/stop helpers for `ao service` (HLD §7,
ADR-0012 D2 + early-gate correction #6's sibling systemd-hardening gaps).

`KillMode=process` (ADR-0012 D2) is the single most safety-critical line this module
renders: systemd's default `KillMode=control-group` would SIGTERM every process in the
unit's cgroup on `systemctl --user stop ao`, including in-flight agent runs that
`start_new_session=True` (D2) deliberately keeps outside the *process group* but NOT
outside the *cgroup*. Left at the default, a stop would silently kill runs this whole
feature exists to keep alive across restarts. Never omit or default this line.

`RestartSec`/`StartLimitIntervalSec`/`StartLimitBurst`/`TimeoutStopSec` and
`EnvironmentFile=-%h/.config/ao/service.env` are early-gate corrections (HLD §7): systemd's
own default restart-storm guard (5 restarts/10s) is tighter than the supervisor's own
internal child backoff operates on, and the systemd **user manager**'s environment is
minimal (no login-shell profile), so agent credentials a normal interactive `ao ui` inherits
from the operator's shell are otherwise silently absent for a boot-resumed run.

No test in this module (or anywhere in the epic, per the locked "no systemd in tests"
decision) invokes real `systemctl` -- unit text is asserted as text, and
`start_via_systemctl`/`stop_via_systemctl` are tested by injecting a fake runner.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from .paths import XDG_CONFIG_HOME_ENV

# Hub port baked into a freshly-generated unit's `ExecStart` when `ao service install` is
# invoked without an explicit `--hub-port` (HLD §2.6 / §7). Deliberately its own constant,
# not `ui.cli.UI_DEFAULT_PORT` -- the hub is a distinct control-plane server from any single
# workspace's dashboard.
DEFAULT_HUB_PORT = 8770

# Loopback by default, mirroring `ao ui`'s own deliberate bind default: the hub is
# unauthenticated and exposes every workspace's state, so reaching it from the network must
# be an explicit operator choice (`--hub-host 0.0.0.0`), never something a default hands out.
DEFAULT_HUB_HOST = "127.0.0.1"

# Where a written unit lands by default: `(XDG_CONFIG_HOME or ~/.config)/systemd/user/
# ao.service` (HLD §7). `_UNIT_SUBPATH` mirrors `paths.py`'s own segment-tuple style.
_UNIT_SUBPATH = ("systemd", "user")
UNIT_FILENAME = "ao.service"

# The exact HLD §7 unit text, including the early-gate corrections. `{ao_executable}` and
# `{hub_port}` are the only two rendered values -- everything else is fixed by design.
_UNIT_TEMPLATE = """[Unit]
Description=Agent Orchestrator multi-workspace service
After=network.target

[Service]
Type=simple
ExecStart={ao_executable} service run --hub-host {hub_host} --hub-port {hub_port}
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=120
StartLimitBurst=5
TimeoutStopSec=30
KillMode=process
EnvironmentFile=-%h/.config/ao/service.env

[Install]
WantedBy=default.target
"""

# systemd user-unit name (without the `.service` suffix) `ao service start/stop` targets.
SYSTEMCTL_UNIT_NAME = "ao"


class ServiceError(Exception):
    """Raised for user-facing `ao service` configuration problems -- e.g. no absolute `ao`
    executable path could be resolved for a systemd `ExecStart` line."""


def resolve_ao_executable() -> str:
    """Resolve an absolute path to the currently-running `ao` for a systemd `ExecStart` line.

    Resolution order (HLD §7): `sys.argv[0]` when its basename is `ao` **and** it is already
    absolute (a relative `sys.argv[0]` -- e.g. `./ao` or a `python -m` dev invocation with a
    `ao`-named shim on a relative path -- is rejected the same as a missing one, since
    systemd requires an absolute `ExecStart` and a relative path would fail at
    systemd-parse time rather than here, a much worse place to discover it) -> `shutil.which
    ("ao")` -> a clear `ServiceError`.

    This function's only job is resolving the path; it performs no filesystem writes.
    """
    argv0 = Path(sys.argv[0])
    if argv0.name == "ao" and argv0.is_absolute():
        return sys.argv[0]

    which_ao = shutil.which("ao")
    if which_ao:
        return which_ao

    raise ServiceError(
        "no absolute `ao` executable path found -- installed via `uv tool install` or on "
        "PATH required for a systemd unit; a `python -m` dev invocation cannot be handed to "
        "systemd"
    )


def render_unit(
    ao_executable: str,
    *,
    hub_port: int = DEFAULT_HUB_PORT,
    hub_host: str = DEFAULT_HUB_HOST,
) -> str:
    """Render the exact HLD §7 unit text for *ao_executable* / *hub_host* / *hub_port*.

    Both `hub_host` and `hub_port` are always rendered explicitly (never left to `service
    run`'s own defaults) -- an implicit default that later changed would silently desync a
    previously-installed unit from a new binary's default (HLD §7, AC16).
    """
    return _UNIT_TEMPLATE.format(ao_executable=ao_executable, hub_port=hub_port, hub_host=hub_host)


def _default_unit_dir() -> Path:
    """`(XDG_CONFIG_HOME or ~/.config)/systemd/user` -- reuses `paths.py`'s own env-reading
    pattern (read at call time, no module-level caching) rather than inventing a third one."""
    xdg_config_home = os.environ.get(XDG_CONFIG_HOME_ENV)
    config_home = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return config_home.joinpath(*_UNIT_SUBPATH)


def install_unit(
    *,
    print_only: bool,
    unit_dir: Path | None = None,
    hub_port: int = DEFAULT_HUB_PORT,
    hub_host: str = DEFAULT_HUB_HOST,
) -> Path | str:
    """Render the unit and either print it (`print_only=True`, zero filesystem writes -- safe
    for CI/tests) or write it to `unit_dir or _default_unit_dir()` (creating parent dirs) and
    return the written path.
    """
    ao_executable = resolve_ao_executable()
    text = render_unit(ao_executable, hub_port=hub_port, hub_host=hub_host)
    if print_only:
        return text

    target_dir = unit_dir if unit_dir is not None else _default_unit_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    unit_path = target_dir / UNIT_FILENAME
    unit_path.write_text(text, encoding="utf-8")
    return unit_path


def systemctl_available(which: Callable[[str], str | None] = shutil.which) -> bool:
    """Whether `systemctl` is on PATH. `which` is injectable so tests can force both
    branches without touching the real PATH."""
    return which("systemctl") is not None


SystemctlRunner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _default_systemctl_runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv is a fixed, hardcoded systemctl invocation
        argv, check=False, capture_output=True, text=True
    )


def start_via_systemctl(
    runner: SystemctlRunner = _default_systemctl_runner,
) -> subprocess.CompletedProcess[str]:
    """`systemctl --user start ao`. Only ever called by the CLI once `systemctl_available()`
    is true. `runner` is injectable so tests assert the built argv without shelling out."""
    return runner(["systemctl", "--user", "start", SYSTEMCTL_UNIT_NAME])


def stop_via_systemctl(
    runner: SystemctlRunner = _default_systemctl_runner,
) -> subprocess.CompletedProcess[str]:
    """`systemctl --user stop ao`. Only ever called by the CLI once `systemctl_available()`
    is true. `runner` is injectable so tests assert the built argv without shelling out."""
    return runner(["systemctl", "--user", "stop", SYSTEMCTL_UNIT_NAME])


__all__ = [
    "DEFAULT_HUB_HOST",
    "DEFAULT_HUB_PORT",
    "SYSTEMCTL_UNIT_NAME",
    "UNIT_FILENAME",
    "ServiceError",
    "SystemctlRunner",
    "install_unit",
    "render_unit",
    "resolve_ao_executable",
    "start_via_systemctl",
    "stop_via_systemctl",
    "systemctl_available",
]
