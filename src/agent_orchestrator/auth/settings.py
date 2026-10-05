"""Layered, fail-closed resolution of dashboard/hub auth settings (HLD section 11.3, L2).

``resolve_auth_settings`` computes one immutable :class:`AuthSettings` from CLI overrides, the
environment, the workspace ``.ao/config.yaml`` ``ui.auth`` block and defaults, recording where
every value came from. Rules worth knowing before reading the code:

* The env layer is strict: an auth variable that is set but empty, or unparseable, is an error.
* The workspace layer is **tighten-only** (a cloned repository's config is attacker input): a
  value that loosens a default, or any ``trusted_proxies``, is refused.
* A problematic ``ui.auth`` block is judged by the pure :func:`decide` (HLD section 11.3.4).
* Config-only weakening while accounts exist is *recorded* as a :class:`ConfigRisk`, never raised
  here (security M3): ``ao auth`` must keep working, ``prepare_auth`` enforces.
* ``ProjectConfig`` is never used; only ``find_project_config`` is reused, read-only.

Error messages never echo a configuration *value* beyond ``MAX_QUOTED_CONFIG_CHARS`` characters
of an env/CLI string, and never a value read from the workspace file (FR-30, HLD section 11.3.3).
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from agent_orchestrator.project_config import find_project_config

from .constants import (
    DEFAULT_ADDRESS_THRESHOLD,
    DEFAULT_LOCKOUT_BASE_SECONDS,
    DEFAULT_LOCKOUT_MAX_SECONDS,
    DEFAULT_LOCKOUT_THRESHOLD,
    DEFAULT_MIN_PASSWORD_LENGTH,
    DEFAULT_SESSION_ABSOLUTE_HOURS,
    DEFAULT_SESSION_IDLE_MINUTES,
    DEFAULT_TOTP_ISSUER_PREFIX,
    MAX_ABSOLUTE_HOURS,
    MAX_ADDRESS_THRESHOLD,
    MAX_IDLE_MINUTES,
    MAX_LOCKOUT_BASE_SECONDS,
    MAX_LOCKOUT_MAX_SECONDS,
    MAX_LOCKOUT_THRESHOLD,
    MAX_PASSWORD_LENGTH,
    MAX_QUOTED_CONFIG_CHARS,
    MAX_TOTP_ISSUER_CHARS,
    MIN_PASSWORD_LENGTH_FLOOR,
    STATE_SUBDIR,
)
from .errors import AuthConfigError
from .model import TotpPolicy
from .paths import (
    AO_AUTH_DIR_ENV,
    AO_AUTH_STATE_DIR_ENV,
    is_within,
    xdg_default_state_dir,
    xdg_default_store_dir,
)
from .store import count_store_users

# --- Environment variable names (HLD section 11.3.1); the two directory names live in paths.py ---
AO_UI_AUTH_ENV = "AO_UI_AUTH"
AO_UI_AUTH_TOTP_ENV = "AO_UI_AUTH_TOTP"
AO_UI_AUTH_IDLE_MINUTES_ENV = "AO_UI_AUTH_IDLE_MINUTES"
AO_UI_AUTH_ABSOLUTE_HOURS_ENV = "AO_UI_AUTH_ABSOLUTE_HOURS"
AO_UI_AUTH_LOCKOUT_THRESHOLD_ENV = "AO_UI_AUTH_LOCKOUT_THRESHOLD"
AO_UI_AUTH_LOCKOUT_BASE_SECONDS_ENV = "AO_UI_AUTH_LOCKOUT_BASE_SECONDS"
AO_UI_AUTH_LOCKOUT_MAX_SECONDS_ENV = "AO_UI_AUTH_LOCKOUT_MAX_SECONDS"
AO_UI_AUTH_ADDRESS_THRESHOLD_ENV = "AO_UI_AUTH_ADDRESS_THRESHOLD"
AO_UI_AUTH_MIN_PASSWORD_LENGTH_ENV = "AO_UI_AUTH_MIN_PASSWORD_LENGTH"
AO_UI_AUTH_TRUSTED_PROXIES_ENV = "AO_UI_AUTH_TRUSTED_PROXIES"
AO_UI_AUTH_TOTP_ISSUER_ENV = "AO_UI_AUTH_TOTP_ISSUER"

# --- `sources` vocabulary ---
SOURCE_LAYER_CLI = "cli"
SOURCE_LAYER_DEFAULT = "default"
SOURCE_ENV_PREFIX = "env:"
SOURCE_CONFIG_PREFIX = "config:"
# state_dir only: no layer set it, it follows the (non-default) store directory.
SOURCE_DERIVED = "derived"

_TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off"})
_BOOL_WORDS_TEXT = "1, true, yes, on, 0, false, no, off"
_DIGITS = re.compile(r"[0-9]+")
_FALLBACK_HOSTNAME = "localhost"
_PROBLEM_MESSAGE_MAX_CHARS = 200
_UNKNOWN_COUNT = "unknown number of"

# field -> (min, max, default) for every integer setting. The ceilings are the env/CLI ceilings;
# the workspace layer is further restricted by TIGHTEN_RULES.
_INT_FIELDS: Mapping[str, tuple[int, int, int]] = MappingProxyType(
    {
        "session_idle_minutes": (1, MAX_IDLE_MINUTES, DEFAULT_SESSION_IDLE_MINUTES),
        "session_absolute_hours": (1, MAX_ABSOLUTE_HOURS, DEFAULT_SESSION_ABSOLUTE_HOURS),
        "lockout_threshold": (1, MAX_LOCKOUT_THRESHOLD, DEFAULT_LOCKOUT_THRESHOLD),
        "lockout_base_seconds": (1, MAX_LOCKOUT_BASE_SECONDS, DEFAULT_LOCKOUT_BASE_SECONDS),
        "lockout_max_seconds": (1, MAX_LOCKOUT_MAX_SECONDS, DEFAULT_LOCKOUT_MAX_SECONDS),
        "address_threshold": (1, MAX_ADDRESS_THRESHOLD, DEFAULT_ADDRESS_THRESHOLD),
        "min_password_length": (
            MIN_PASSWORD_LENGTH_FLOOR,
            MAX_PASSWORD_LENGTH,
            DEFAULT_MIN_PASSWORD_LENGTH,
        ),
    }
)


def _bounded_int(field: str) -> Any:
    low, high, _ = _INT_FIELDS[field]
    return Field(None, ge=low, le=high)


class Stricter(StrEnum):
    """Which direction of a numeric setting is the stricter one."""

    LOWER = "lower"
    HIGHER = "higher"


# The seven numeric settings the workspace config may only tighten (A12, HLD section 12.5).
TIGHTEN_RULES: tuple[tuple[str, Stricter], ...] = (
    ("session_idle_minutes", Stricter.LOWER),
    ("session_absolute_hours", Stricter.LOWER),
    ("lockout_threshold", Stricter.LOWER),
    ("lockout_base_seconds", Stricter.HIGHER),
    ("lockout_max_seconds", Stricter.HIGHER),
    ("address_threshold", Stricter.LOWER),
    ("min_password_length", Stricter.HIGHER),
)


class ConfigRisk(StrEnum):
    """Config-only weakening of auth while accounts exist (security M3, v2.1)."""

    DISABLED_BY_CONFIG = "disabled_by_config"
    TOTP_DOWNGRADED_BY_CONFIG = "totp_downgraded_by_config"


class UIAuthConfig(BaseModel):
    """The workspace ``ui.auth:`` block. Every field is optional; unknown keys are an error."""

    # A typo fails closed; a ValidationError never echoes the offending value.
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    enabled: bool | None = None
    totp: TotpPolicy | None = None
    session_idle_minutes: int | None = _bounded_int("session_idle_minutes")
    session_absolute_hours: int | None = _bounded_int("session_absolute_hours")
    lockout_threshold: int | None = _bounded_int("lockout_threshold")
    lockout_base_seconds: int | None = _bounded_int("lockout_base_seconds")
    lockout_max_seconds: int | None = _bounded_int("lockout_max_seconds")
    address_threshold: int | None = _bounded_int("address_threshold")
    min_password_length: int | None = _bounded_int("min_password_length")
    store_dir: str | None = Field(None, min_length=1)
    # Present ONLY so the error can say "env only" instead of "extra key" (A12).
    trusted_proxies: list[str] | None = None
    totp_issuer: str | None = Field(None, min_length=1, max_length=MAX_TOTP_ISSUER_CHARS)

    @field_validator("totp", mode="before")
    @classmethod
    def _yaml_off_is_false(cls, value: object) -> object:
        # YAML 1.1 parses a bare `off` as False and `on` as True; `off` is a real policy name.
        if value is False:
            return TotpPolicy.OFF.value
        if value is True:
            raise ValueError('totp must be "off", "optional" or "required" (quote "off" and "on")')
        return value


class ConfigProblemKind(StrEnum):
    UNPARSEABLE = "unparseable"
    INVALID = "invalid"


@dataclass(frozen=True)
class ConfigProblem:
    """Why the workspace ``ui.auth`` block cannot be used. ``message`` never contains a value."""

    kind: ConfigProblemKind
    message: str


class DecisionAction(StrEnum):
    USE_CONFIG = "use_config"
    IGNORE_CONFIG = "ignore_config"
    CONTINUE_WITHOUT_CONFIG = "continue_without_config"
    REFUSE = "refuse"


@dataclass(frozen=True)
class Decision:
    """The outcome of :func:`decide`. ``message`` is value-free and carries no path or source;
    ``resolve_auth_settings`` adds those (it is the layer that knows them)."""

    action: DecisionAction
    message: str = ""


@dataclass(frozen=True)
class AuthCliOverrides:
    """Values given on the command line; ``None`` means the flag was not given."""

    enabled: bool | None = None  # --auth / --no-auth
    totp: TotpPolicy | None = None  # --auth-totp
    store_dir: str | None = None  # --auth-dir


@dataclass(frozen=True)
class AuthSettings:
    """Resolved settings. Pure data: the launch adapters live in ``auth/launch.py`` (R-3)."""

    enabled: bool
    totp: TotpPolicy
    session_idle_seconds: int
    session_absolute_seconds: int
    lockout_threshold: int
    lockout_base_seconds: int
    lockout_max_seconds: int
    address_threshold: int
    min_password_length: int
    store_dir: Path
    state_dir: Path
    trusted_proxies: tuple[str, ...]
    totp_issuer: str
    # field -> "cli" | "env:<VAR>" | "config:<abs path>" | "default" (state_dir: also "derived").
    # Keys are the config field names (``session_idle_minutes``, not the derived seconds).
    sources: Mapping[str, str]
    config_path: Path | None
    warnings: tuple[str, ...]
    config_risks: frozenset[ConfigRisk]

    @property
    def store_dir_from_config(self) -> bool:
        """True when ``store_dir`` came from the workspace file. Mutating ``ao auth`` commands
        must then neither create nor chmod it (security M6)."""
        return self.sources["store_dir"].startswith(SOURCE_CONFIG_PREFIX)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _quote(raw: str) -> str:
    """Quote at most ``MAX_QUOTED_CONFIG_CHARS`` characters of an env/CLI string."""
    suffix = "..." if len(raw) > MAX_QUOTED_CONFIG_CHARS else ""
    return repr(raw[:MAX_QUOTED_CONFIG_CHARS]) + suffix


def _expand(raw: str, what: str) -> Path:
    try:
        return Path(raw).expanduser()
    except RuntimeError as exc:  # `~unknownuser`: no home directory to expand to
        raise AuthConfigError(f"{what}: cannot expand the home directory of {_quote(raw)}") from exc


def _source_of_env(var: str) -> str:
    return f"{SOURCE_ENV_PREFIX}{var}"


# ---------------------------------------------------------------------------
# Env layer (strict)
# ---------------------------------------------------------------------------


def _parse_bool(var: str, raw: str) -> bool:
    word = raw.strip().lower()
    if word in _TRUE_WORDS:
        return True
    if word in _FALSE_WORDS:
        return False
    raise AuthConfigError(f"{var}={_quote(raw)} is not a boolean; use one of: {_BOOL_WORDS_TEXT}")


def _int_parser(field: str) -> Callable[[str, str], int]:
    low, high, _ = _INT_FIELDS[field]

    def parse(var: str, raw: str) -> int:
        text = raw.strip()
        if not (text.isascii() and _DIGITS.fullmatch(text)):
            raise AuthConfigError(
                f"{var}={_quote(raw)} is not a whole number; expected {low}..{high}"
            )
        value = int(text)
        if not low <= value <= high:
            raise AuthConfigError(f"{var}={value} is out of range; expected {low}..{high}")
        return value

    return parse


def _parse_totp(var: str, raw: str) -> TotpPolicy:
    word = raw.strip().lower()
    try:
        return TotpPolicy(word)
    except ValueError:
        accepted = ", ".join(p.value for p in TotpPolicy)
        raise AuthConfigError(
            f"{var}={_quote(raw)} is not a TOTP policy; use one of: {accepted}"
        ) from None


def _parse_text(var: str, raw: str) -> str:
    return raw  # emptiness was already rejected by the caller


def _parse_issuer(var: str, raw: str) -> str:
    if len(raw) > MAX_TOTP_ISSUER_CHARS:
        raise AuthConfigError(f"{var} is longer than {MAX_TOTP_ISSUER_CHARS} characters")
    return raw


def _parse_proxies(var: str, raw: str) -> tuple[str, ...]:
    entries: list[str] = []
    for part in raw.split(","):
        text = part.strip()
        try:
            entries.append(str(ipaddress.ip_address(text)))
        except ValueError:
            raise AuthConfigError(
                f"{var} entry {_quote(text)} is not an IP address "
                "(CIDR ranges are not supported yet)"
            ) from None
    return tuple(entries)


@dataclass(frozen=True)
class _EnvBinding:
    field: str
    var: str
    parse: Callable[[str, str], Any]


ENV_BINDINGS: tuple[_EnvBinding, ...] = (
    _EnvBinding("enabled", AO_UI_AUTH_ENV, _parse_bool),
    _EnvBinding("totp", AO_UI_AUTH_TOTP_ENV, _parse_totp),
    _EnvBinding("store_dir", AO_AUTH_DIR_ENV, _parse_text),
    _EnvBinding("state_dir", AO_AUTH_STATE_DIR_ENV, _parse_text),
    _EnvBinding(
        "session_idle_minutes", AO_UI_AUTH_IDLE_MINUTES_ENV, _int_parser("session_idle_minutes")
    ),
    _EnvBinding(
        "session_absolute_hours",
        AO_UI_AUTH_ABSOLUTE_HOURS_ENV,
        _int_parser("session_absolute_hours"),
    ),
    _EnvBinding(
        "lockout_threshold", AO_UI_AUTH_LOCKOUT_THRESHOLD_ENV, _int_parser("lockout_threshold")
    ),
    _EnvBinding(
        "lockout_base_seconds",
        AO_UI_AUTH_LOCKOUT_BASE_SECONDS_ENV,
        _int_parser("lockout_base_seconds"),
    ),
    _EnvBinding(
        "lockout_max_seconds",
        AO_UI_AUTH_LOCKOUT_MAX_SECONDS_ENV,
        _int_parser("lockout_max_seconds"),
    ),
    _EnvBinding(
        "address_threshold", AO_UI_AUTH_ADDRESS_THRESHOLD_ENV, _int_parser("address_threshold")
    ),
    _EnvBinding(
        "min_password_length",
        AO_UI_AUTH_MIN_PASSWORD_LENGTH_ENV,
        _int_parser("min_password_length"),
    ),
    _EnvBinding("trusted_proxies", AO_UI_AUTH_TRUSTED_PROXIES_ENV, _parse_proxies),
    _EnvBinding("totp_issuer", AO_UI_AUTH_TOTP_ISSUER_ENV, _parse_issuer),
)
_VAR_BY_FIELD: Mapping[str, str] = MappingProxyType({b.field: b.var for b in ENV_BINDINGS})


def _read_env_layer(env: Mapping[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for binding in ENV_BINDINGS:
        raw = env.get(binding.var)
        if raw is None:
            continue
        if not raw.strip():
            # ADR-0003: an empty value is an error, never "unset".
            raise AuthConfigError(f"{binding.var} is set but empty; unset it or set a value")
        values[binding.field] = binding.parse(binding.var, raw)
    return values


# ---------------------------------------------------------------------------
# Workspace layer
# ---------------------------------------------------------------------------


def _first_line(text: str) -> str:
    return (text.splitlines() or [""])[0][:_PROBLEM_MESSAGE_MAX_CHARS]


def _validation_message(exc: ValidationError) -> str:
    # Never include err["input"]: messages stay bounded and value-free.
    parts = []
    for err in exc.errors(include_input=False, include_url=False, include_context=False):
        loc = ".".join(str(p)[:MAX_QUOTED_CONFIG_CHARS] for p in err["loc"])
        parts.append(f"ui.auth.{loc}: {err['msg']}")
    return "; ".join(parts)


def load_auth_block(config_path: Path) -> tuple[UIAuthConfig | None, ConfigProblem | None]:
    """Read ``ui.auth`` from a workspace config file. Never raises; never echoes input values."""
    unparseable = ConfigProblemKind.UNPARSEABLE
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, ConfigProblem(unparseable, _first_line(exc.strerror or "cannot be read"))
    except UnicodeError:
        return None, ConfigProblem(unparseable, "not valid UTF-8")
    except yaml.YAMLError as exc:
        # str(exc) can quote file content; the position is all an operator needs.
        mark = getattr(exc, "problem_mark", None)
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark is not None else ""
        return None, ConfigProblem(unparseable, f"YAML syntax error{where}")
    except (ValueError, RecursionError):
        return None, ConfigProblem(unparseable, "the YAML document cannot be loaded")
    if data is None:
        return None, None
    if not isinstance(data, dict):
        return None, ConfigProblem(unparseable, "top level is not a mapping")
    invalid = ConfigProblemKind.INVALID
    ui = data.get("ui")
    if ui is None:
        return None, None
    if not isinstance(ui, dict):
        return None, ConfigProblem(invalid, "`ui` must be a mapping")
    block = ui.get("auth")
    if block is None:
        return None, None
    if not isinstance(block, dict):
        return None, ConfigProblem(invalid, "`ui.auth` must be a mapping")
    try:
        return UIAuthConfig.model_validate(block), None
    except ValidationError as exc:
        return None, ConfigProblem(invalid, _validation_message(exc))


def decide(
    explicit_enabled: bool | None, problem: ConfigProblem | None, user_count: int | None
) -> Decision:
    """The HLD section 11.3.4 table (rows 2-5) for a problematic ``ui.auth`` block, as a pure
    function of values. ``user_count`` is ``None`` when unknown, which fails closed."""
    if problem is None:
        return Decision(DecisionAction.USE_CONFIG)
    if explicit_enabled is False:
        return Decision(
            DecisionAction.IGNORE_CONFIG,
            f"ignoring invalid ui.auth configuration ({problem.message}) "
            "because auth is explicitly disabled",
        )
    if explicit_enabled is True:
        return Decision(DecisionAction.REFUSE, f"invalid ui.auth configuration: {problem.message}")
    if problem.kind is ConfigProblemKind.UNPARSEABLE:
        if user_count is None or user_count > 0:
            return Decision(
                DecisionAction.REFUSE,
                f"the config cannot be parsed ({problem.message}) and account(s) may exist; "
                "refusing to guess whether dashboard authentication is required. Fix the file, "
                "or pass --auth / --no-auth explicitly.",
            )
        return Decision(
            DecisionAction.CONTINUE_WITHOUT_CONFIG,
            f"the config cannot be parsed ({problem.message}); continuing without workspace auth "
            "settings (no accounts exist)",
        )
    return Decision(DecisionAction.REFUSE, f"invalid ui.auth configuration: {problem.message}")


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _loosens(value: int, default: int, stricter: Stricter) -> bool:
    return value > default if stricter is Stricter.LOWER else value < default


def _check_tighten_only(cfg: UIAuthConfig) -> None:
    if cfg.trusted_proxies is not None:
        raise AuthConfigError(
            f"ui.auth.trusted_proxies may only be set via the {AO_UI_AUTH_TRUSTED_PROXIES_ENV} "
            "environment variable"
        )
    for field, stricter in TIGHTEN_RULES:
        value = getattr(cfg, field)
        default = _INT_FIELDS[field][2]
        if value is not None and _loosens(value, default, stricter):
            raise AuthConfigError(
                f"ui.auth.{field}={value} would weaken the default ({default}); workspace config "
                "may only tighten auth settings. Set it via env/CLI instead "
                f"({_VAR_BY_FIELD[field]})"
            )


def _short_hostname() -> str:
    return socket.gethostname().split(".")[0] or _FALLBACK_HOSTNAME


def _default_issuer() -> str:
    issuer = f"{DEFAULT_TOTP_ISSUER_PREFIX}{_short_hostname()}"[:MAX_TOTP_ISSUER_CHARS]
    # A hostname is never trusted to satisfy the issuer rules: neutralize, do not fail.
    return "".join(c if c.isprintable() and c != ":" else "-" for c in issuer)


def _explicit_enabled(
    cli: AuthCliOverrides, env_vals: Mapping[str, Any]
) -> tuple[bool | None, str | None]:
    if cli.enabled is not None:
        return cli.enabled, SOURCE_LAYER_CLI
    if "enabled" in env_vals:
        return env_vals["enabled"], _source_of_env(AO_UI_AUTH_ENV)
    return None, None


def _probe_store_dir(
    cli: AuthCliOverrides, env: Mapping[str, str], env_vals: Mapping[str, Any]
) -> Path:
    raw = cli.store_dir if cli.store_dir is not None else env_vals.get("store_dir")
    path = xdg_default_store_dir(env) if raw is None else _expand(raw, "store directory")
    return path.resolve()


def _workspace_layer(
    cli: AuthCliOverrides,
    env: Mapping[str, str],
    env_vals: Mapping[str, Any],
    workspace_root: Path | None,
    count_users: Callable[[Path], int | None],
    warnings: list[str],
) -> tuple[UIAuthConfig | None, Path | None]:
    """Find, load and judge the workspace ``ui.auth`` block (HLD section 11.3.2 step 2)."""
    if workspace_root is None:
        return None, None
    cfg_path = find_project_config(workspace_root)
    if cfg_path is None:
        return None, None
    cfg, problem = load_auth_block(cfg_path)
    if problem is None:
        return cfg, cfg_path

    explicit, explicit_source = _explicit_enabled(cli, env_vals)
    needs_count = explicit is None and problem.kind is ConfigProblemKind.UNPARSEABLE
    # Lazy: the probe runs only when its answer can change the decision.
    probe_dir = _probe_store_dir(cli, env, env_vals) if needs_count else None
    count = count_users(probe_dir) if probe_dir is not None else 0
    decision = decide(explicit, problem, count)

    if decision.action is DecisionAction.IGNORE_CONFIG:
        warnings.append(f"{cfg_path}: {decision.message} by {explicit_source}")
    elif decision.action is DecisionAction.CONTINUE_WITHOUT_CONFIG:
        warnings.append(f"{cfg_path}: {decision.message}")
    else:  # REFUSE (USE_CONFIG is unreachable with a problem)
        detail = ""
        if probe_dir is not None:
            n = _UNKNOWN_COUNT if count is None else str(count)
            detail = f" [{n} account(s) in {probe_dir}]"
        raise AuthConfigError(f"{cfg_path}: {decision.message}{detail}")
    return None, cfg_path


def resolve_auth_settings(
    *,
    cli: AuthCliOverrides,
    env: Mapping[str, str],
    workspace_root: Path | None,
    count_users: Callable[[Path], int | None] = count_store_users,
) -> AuthSettings:
    """Resolve CLI > env > workspace config > default (HLD section 11.3.2).

    ``workspace_root=None`` (the hub) never reads a workspace config. Raises
    :class:`AuthConfigError` for anything unsafe or invalid, except a :class:`ConfigRisk`, which
    is recorded in ``config_risks`` for ``prepare_auth`` to enforce.
    """
    warnings: list[str] = []
    env_vals = _read_env_layer(env)
    cfg, cfg_path = _workspace_layer(cli, env, env_vals, workspace_root, count_users, warnings)
    if cfg is not None:
        _check_tighten_only(cfg)

    sources: dict[str, str] = {}

    def pick(field: str, default: Any, cli_value: Any = None) -> Any:
        if cli_value is not None:
            sources[field] = SOURCE_LAYER_CLI
            return cli_value
        if field in env_vals:
            sources[field] = _source_of_env(_VAR_BY_FIELD[field])
            return env_vals[field]
        if cfg is not None and (cfg_value := getattr(cfg, field, None)) is not None:
            sources[field] = f"{SOURCE_CONFIG_PREFIX}{cfg_path}"
            return cfg_value
        sources[field] = SOURCE_LAYER_DEFAULT
        return default

    enabled: bool = pick("enabled", False, cli.enabled)
    totp: TotpPolicy = pick("totp", TotpPolicy.OFF, cli.totp)
    ints = {field: pick(field, default) for field, (_, _, default) in _INT_FIELDS.items()}
    raw_store = pick("store_dir", None, cli.store_dir)
    issuer_raw = pick("totp_issuer", None)
    trusted: tuple[str, ...] = pick("trusted_proxies", ())

    store_dir = _resolve_store_dir(raw_store, sources["store_dir"], env, workspace_root, warnings)
    state_dir = _resolve_state_dir(env_vals, sources, store_dir, env)

    if ints["lockout_base_seconds"] > ints["lockout_max_seconds"]:
        raise AuthConfigError("lockout_base_seconds must be <= lockout_max_seconds")
    issuer = issuer_raw if issuer_raw is not None else _default_issuer()
    if ":" in issuer or not issuer.isprintable():
        raise AuthConfigError("totp_issuer must be printable and must not contain ':'")

    risks = _detect_config_risks(enabled, totp, sources, cfg_path, store_dir, count_users, warnings)

    return AuthSettings(
        enabled=enabled,
        totp=totp,
        session_idle_seconds=ints["session_idle_minutes"] * 60,
        session_absolute_seconds=ints["session_absolute_hours"] * 3600,
        lockout_threshold=ints["lockout_threshold"],
        lockout_base_seconds=ints["lockout_base_seconds"],
        lockout_max_seconds=ints["lockout_max_seconds"],
        address_threshold=ints["address_threshold"],
        min_password_length=ints["min_password_length"],
        store_dir=store_dir,
        state_dir=state_dir,
        trusted_proxies=trusted,
        totp_issuer=issuer,
        sources=MappingProxyType(sources),
        config_path=cfg_path,
        warnings=tuple(warnings),
        config_risks=frozenset(risks),
    )


def _resolve_store_dir(
    raw: str | None,
    source: str,
    env: Mapping[str, str],
    workspace_root: Path | None,
    warnings: list[str],
) -> Path:
    from_config = source.startswith(SOURCE_CONFIG_PREFIX)
    path = xdg_default_store_dir(env) if raw is None else _expand(raw, "store directory")
    if from_config and not path.is_absolute():
        raise AuthConfigError(
            "ui.auth.store_dir must be an absolute path (relative paths would live inside "
            "the workspace)"
        )
    store_dir = path.resolve()  # follows symlinks, so the containment check below sees through them
    if workspace_root is not None and is_within(store_dir, workspace_root.resolve()):
        if from_config:
            raise AuthConfigError(
                f"ui.auth.store_dir {store_dir} is inside the workspace {workspace_root}; the "
                "user store must live outside every dashboard-browsable root"
            )
        warnings.append(
            f"the user store {store_dir} is inside the served workspace; it is never served by "
            "the dashboard, but agent tasks can write there"
        )
    return store_dir


def _resolve_state_dir(
    env_vals: Mapping[str, Any],
    sources: dict[str, str],
    store_dir: Path,
    env: Mapping[str, str],
) -> Path:
    if "state_dir" in env_vals:
        sources["state_dir"] = _source_of_env(AO_AUTH_STATE_DIR_ENV)
        return _expand(env_vals["state_dir"], "state directory").resolve()
    if sources["store_dir"] != SOURCE_LAYER_DEFAULT:
        sources["state_dir"] = SOURCE_DERIVED
        return store_dir / STATE_SUBDIR
    sources["state_dir"] = SOURCE_LAYER_DEFAULT
    return xdg_default_state_dir(env).resolve()


def _detect_config_risks(
    enabled: bool,
    totp: TotpPolicy,
    sources: Mapping[str, str],
    cfg_path: Path | None,
    store_dir: Path,
    count_users: Callable[[Path], int | None],
    warnings: list[str],
) -> set[ConfigRisk]:
    """Step 5 (security M3): record config-only weakening while accounts may exist. Never raises."""
    weak_enabled = sources["enabled"].startswith(SOURCE_CONFIG_PREFIX) and not enabled
    weak_totp = (
        enabled
        and sources["totp"].startswith(SOURCE_CONFIG_PREFIX)
        and totp is not TotpPolicy.REQUIRED
    )
    risks: set[ConfigRisk] = set()
    if not (weak_enabled or weak_totp):
        return risks
    count = count_users(store_dir)  # once; None (unknown) counts as "accounts may exist"
    if count is not None and count <= 0:
        return risks
    if weak_enabled:
        risks.add(ConfigRisk.DISABLED_BY_CONFIG)
    if weak_totp:
        risks.add(ConfigRisk.TOTP_DOWNGRADED_BY_CONFIG)
        warnings.append(
            f"totp={totp.value} comes only from {cfg_path}; a repository change can lower it. "
            f"Pin it with {AO_UI_AUTH_TOTP_ENV} or --auth-totp."
        )
    return risks
