"""The one auth startup sequence (HLD 11.20, L3; reviewer R-3).

``prepare_auth`` is shared by ``ao ui``, ``ui/app.py::create_app_from_env`` and (T-PDGw9p)
``ao service run``. It resolves the settings, enforces the fail-closed rules (config-only disable,
``--port 0``, an empty or unusable store), builds and checks the runtime, and computes the launch
adapters (uvicorn kwargs, relay env for children, denied browse paths). Framework-free: the
caller prints ``AuthLaunch.warnings`` and maps :class:`AuthConfigError` to ``EXIT_CONFIG``.

Audit writes here are best-effort and informational: a failure to write one never changes the
outcome and never replaces the error being raised (it is logged at DEBUG).
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from agent_orchestrator.errors import EXIT_CONFIG

from .audit import AuditEvent, AuditLog, AuditOutcome
from .constants import EPHEMERAL_PORT, LOOPBACK_HOSTNAMES
from .errors import AuthConfigError, AuthNotReadyError
from .model import AuditEventName
from .paths import AO_AUTH_DIR_ENV, default_denied_paths
from .runtime import AuthRuntime, Realm, build_auth_runtime
from .scrub import install_log_redaction
from .seams import SYSTEM_CLOCK, Clock
from .settings import (
    AO_UI_AUTH_ENV,
    AO_UI_AUTH_TOTP_ENV,
    AO_UI_AUTH_TRUSTED_PROXIES_ENV,
    AuthCliOverrides,
    AuthSettings,
    ConfigRisk,
    resolve_auth_settings,
)
from .store import count_store_users

_log = logging.getLogger(__name__)

EXIT_FAILURE = 1  # any failure that is not a configuration problem

# Env values the relay (``AuthLaunch.child_env``) writes for a CLI-sourced boolean.
_ENV_TRUE = "1"
_ENV_FALSE = "0"

# Audit ``details["reason"]`` values for the startup events.
_REASON_NOT_READY = "not_ready"
_REASON_DISABLED_BY_CONFIG = "disabled_by_config"
_REASON_TOTP_DOWNGRADED = "totp_downgraded_by_config"

PLAIN_HTTP_WARNING = (
    "WARNING: dashboard authentication is on but {host} is a non-loopback bind: traffic is not "
    "encrypted (plain HTTP), so passwords and session cookies can be read on the network. "
    "Put a TLS-terminating reverse proxy in front and set " + AO_UI_AUTH_TRUSTED_PROXIES_ENV + "."
)
DEPRECATION_NOTICE = (
    "NOTICE: serving a non-loopback address without authentication ({host}) is deprecated and "
    "will be refused in a future release; enable authentication (--auth / AO_UI_AUTH) or bind "
    "to loopback."
)
ACCOUNTS_EXIST_NOTE = (
    "Note: dashboard authentication is disabled (ui.auth.enabled=false from {source}) although "
    "{count} account(s) exist in {store}."
)
PORT_ZERO_MESSAGE = (
    "--port 0 cannot be used with dashboard authentication: the session cookie is named after "
    "the listening port; choose a fixed port"
)
DISABLED_BY_CONFIG_MESSAGE = (
    "ui.auth.enabled=false in {config} while {count} account(s) exist in {store}; refusing to "
    "start an unauthenticated dashboard. To disable authentication on purpose, pass --no-auth "
    "or set " + AO_UI_AUTH_ENV + "=0."
)
_UNKNOWN_COUNT = "an unknown number of"
_NO_CONFIG_FILE = "the workspace config"


@dataclass(frozen=True)
class AuthLaunch:
    """Everything a call site needs; field names are frozen (T-PDGw9p consumes them)."""

    settings: AuthSettings
    runtime: AuthRuntime | None  # None when auth is disabled
    warnings: tuple[str, ...]  # settings + provider + transport warnings, ready to print
    # ``Any`` values so ``**launch.uvicorn_kwargs`` type-checks against ``uvicorn.run``.
    uvicorn_kwargs: dict[str, Any]  # {} when off (byte-identical uvicorn defaults)
    child_env: dict[str, str]  # CLI-sourced relay (supervisor children, --reload)
    denied_paths: tuple[Path, ...]  # for DashboardService / FileBrowser


def is_loopback_bind(host: str) -> bool:
    """Whether a bind address is loopback. Local on purpose: no ``ui.security`` import (R2)."""
    if host in LOOPBACK_HOSTNAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def exit_code_for(exc: BaseException) -> int:
    """The process exit code for a startup failure: ``EXIT_CONFIG`` for any ``AuthConfigError``."""
    return EXIT_CONFIG if isinstance(exc, AuthConfigError) else EXIT_FAILURE


def prepare_auth(
    *,
    cli: AuthCliOverrides,
    env: Mapping[str, str],
    workspace_root: Path | None,
    realm_kind: Literal["ui", "hub"],
    port: int,
    bind_host: str,
    clock: Clock = SYSTEM_CLOCK,
) -> AuthLaunch:
    """Resolve, enforce and wire dashboard/hub auth. Raises :class:`AuthConfigError` (or its
    subclasses, e.g. :class:`AuthNotReadyError`) for every fail-closed outcome."""
    settings = resolve_auth_settings(cli=cli, env=env, workspace_root=workspace_root)
    realm = Realm(realm_kind, port, workspace_root)

    if ConfigRisk.DISABLED_BY_CONFIG in settings.config_risks:
        _audit(
            settings, realm, clock, AuditEventName.STARTUP_DISABLED_BY_CONFIG, AuditOutcome.FAILURE
        )
        raise AuthConfigError(_disabled_by_config_message(settings))

    runtime: AuthRuntime | None = None
    warnings = list(settings.warnings)
    if settings.enabled:
        runtime = _start_enabled(settings, realm, bind_host, clock, warnings)
    else:
        warnings.extend(_disabled_notices(settings, bind_host))

    return AuthLaunch(
        settings=settings,
        runtime=runtime,
        warnings=tuple(warnings),
        uvicorn_kwargs=_uvicorn_kwargs(settings),
        child_env=_child_env(cli),
        denied_paths=_denied_paths(settings, env),
    )


def _start_enabled(
    settings: AuthSettings,
    realm: Realm,
    bind_host: str,
    clock: Clock,
    warnings: list[str],
) -> AuthRuntime:
    if realm.port == EPHEMERAL_PORT:  # security L4: the cookie name embeds the port
        raise AuthConfigError(PORT_ZERO_MESSAGE)
    runtime = build_auth_runtime(settings, realm, clock=clock)
    try:
        runtime.provider.check_ready()
    except AuthNotReadyError:
        _audit(settings, realm, clock, AuditEventName.STARTUP_REFUSED, AuditOutcome.FAILURE)
        raise  # the original error, whatever the audit write did
    warnings.extend(runtime.provider.startup_warnings())
    if ConfigRisk.TOTP_DOWNGRADED_BY_CONFIG in settings.config_risks:
        _audit(
            settings,
            realm,
            clock,
            AuditEventName.STARTUP_TOTP_DOWNGRADED_BY_CONFIG,
            AuditOutcome.INFO,
        )
    if not is_loopback_bind(bind_host) and not settings.trusted_proxies:
        warnings.append(PLAIN_HTTP_WARNING.format(host=bind_host))
    install_log_redaction()
    return runtime


def _disabled_notices(settings: AuthSettings, bind_host: str) -> list[str]:
    notices: list[str] = []
    if not is_loopback_bind(bind_host):
        notices.append(DEPRECATION_NOTICE.format(host=bind_host))
    count = count_store_users(settings.store_dir)
    if count not in (0, None):  # an unknown count stays silent here (it is a refusal elsewhere)
        notices.append(
            ACCOUNTS_EXIST_NOTE.format(
                source=settings.sources["enabled"], count=count, store=settings.store_dir
            )
        )
    return notices


def _disabled_by_config_message(settings: AuthSettings) -> str:
    count = count_store_users(settings.store_dir)
    return DISABLED_BY_CONFIG_MESSAGE.format(
        config=settings.config_path or _NO_CONFIG_FILE,
        count=_UNKNOWN_COUNT if count is None else count,
        store=settings.store_dir,
    )


def _audit(
    settings: AuthSettings,
    realm: Realm,
    clock: Clock,
    name: AuditEventName,
    outcome: AuditOutcome,
) -> None:
    """Best-effort startup audit event: any failure is swallowed and logged at WARNING."""
    try:
        AuditLog.for_state_dir(settings.state_dir, clock=clock).record(
            AuditEvent(name, outcome, realm=realm.id, details={"reason": _REASONS[name]})
        )
    except Exception:  # noqa: BLE001 - informational; must never replace the real error
        _log.warning("could not write the %s audit event", name, exc_info=True)


_REASONS: Mapping[AuditEventName, str] = {
    AuditEventName.STARTUP_REFUSED: _REASON_NOT_READY,
    AuditEventName.STARTUP_DISABLED_BY_CONFIG: _REASON_DISABLED_BY_CONFIG,
    AuditEventName.STARTUP_TOTP_DOWNGRADED_BY_CONFIG: _REASON_TOTP_DOWNGRADED,
}


def _uvicorn_kwargs(settings: AuthSettings) -> dict[str, Any]:
    """HLD 11.3.5: ``{}`` when off; proxy headers explicitly off (no proxies) or on for the
    trusted addresses only (uvicorn would otherwise trust 127.0.0.1)."""
    if not settings.enabled:
        return {}
    if not settings.trusted_proxies:
        return {"proxy_headers": False}
    return {"proxy_headers": True, "forwarded_allow_ips": ",".join(settings.trusted_proxies)}


def _child_env(cli: AuthCliOverrides) -> dict[str, str]:
    """Only the CLI-sourced values, relayed so a ``--reload`` or supervisor child resolves the same
    settings (env and config reach the child on their own)."""
    relay: dict[str, str] = {}
    if cli.enabled is not None:
        relay[AO_UI_AUTH_ENV] = _ENV_TRUE if cli.enabled else _ENV_FALSE
    if cli.totp is not None:
        relay[AO_UI_AUTH_TOTP_ENV] = cli.totp.value
    if cli.store_dir is not None:
        relay[AO_AUTH_DIR_ENV] = cli.store_dir
    return relay


def _denied_paths(settings: AuthSettings, env: Mapping[str, str]) -> tuple[Path, ...]:
    candidates = [*default_denied_paths(env), settings.store_dir, settings.state_dir]
    return tuple(dict.fromkeys(path.resolve() for path in candidates))
