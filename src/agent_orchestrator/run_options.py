"""Run-options registry: the single description of every ``ao run`` / ``ao resume`` setting.

One :class:`Option` per setting records its type, CLI flag(s) (including deprecated aliases),
env var, ``.ao/config.yaml`` key, built-in default, help text, dashboard group/label and whether
it may change on resume. Three consumers read it instead of keeping parallel tables:

* ``cli._resolve_run_settings`` & siblings -- the CLI > env > config > default chain
  (:func:`resolve_option`), which also reports *where* each value came from;
* ``ui/processes.py`` -- the dashboard's argv allow-list (:func:`allowed_options` /
  :func:`allowed_bool_options`), so a request body can never name a flag the registry lacks;
* ``GET /api/schema/run-options`` -- :func:`options_schema`, the contract the dashboard form is
  rendered from.

Resolution is deliberately byte-compatible with the historical ``a or b or c`` chains: a falsy
CLI/env/config value (``0``, ``""``) is "unset" and falls through, and a malformed integer env
var is ignored. Tri-state booleans (``--x/--no-x``) are the exception -- ``False`` is a real
choice there, exactly as it was before the registry existed.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Literal

OptionType = Literal["int", "str", "enum", "bool"]

SOURCE_CLI: Final = "cli"
SOURCE_ENV: Final = "env"
SOURCE_CONFIG: Final = "config"
SOURCE_DEFAULT: Final = "default"
SOURCE_UNSET: Final = "unset"

SCHEMA_ID: Final = "ao.run-options/v1"

# Values an env var may use to mean True for a boolean option (anything else non-blank is False).
TRUTHY_ENV_VALUES: Final = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class Option:
    """One run setting. See the module docstring for the consumers."""

    name: str
    type: OptionType
    flag: str | None = None
    """Primary CLI flag (``--max-parallel``); for a bool option, the *on* flag."""
    no_flag: str | None = None
    """Off flag of a ``--x/--no-x`` pair (bool options only)."""
    deprecated_flags: tuple[str, ...] = ()
    env: str | None = None
    config: str | None = None
    """Dotted attribute path on ``ProjectConfig`` (== the ``.ao/config.yaml`` key path)."""
    default: Any = None
    choices: tuple[str, ...] = ()
    help: str = ""
    ui_group: str | None = None
    """Dashboard form group; ``None`` = not forwardable from the dashboard."""
    ui_label: str | None = None
    resumable: bool = True
    """May a ``resume`` change it (every current option may -- resolved fresh each invocation)."""
    providers: tuple[str, ...] = ()
    """Providers/executors that apply the setting (``()`` = provider-independent); shown as
    "applies to: ..." in help and the dashboard."""
    env_strip_blank: bool = False
    """Bool options: treat a whitespace-only env var as unset (legacy quirk of two chains)."""

    @property
    def ui(self) -> bool:
        return self.ui_group is not None


@dataclass(frozen=True)
class Resolved:
    """A resolved setting and the layer it came from (``cli|env|config|default|unset``)."""

    value: Any
    source: str


OPTIONS: Final[tuple[Option, ...]] = (
    Option(
        "model",
        "str",
        flag="--model",
        env="AO_MODEL",
        config="model",
        help="Claude model for all agents (an alias such as sonnet, or a pinned id).",
        ui_group="Agent",
        ui_label="Model",
    ),
    Option(
        "turn_budget",
        "enum",
        flag="--turn-budget",
        env="AO_TURN_BUDGET",
        config="turn_budget",
        choices=("low", "medium", "high", "xhigh"),
        help="Max-turn tier for all agents: low=15, medium=30, high=60, xhigh=120 turns.",
        ui_group="Agent",
        ui_label="Turn budget",
    ),
    Option(
        "effort",
        "enum",
        flag="--effort",
        env="AO_EFFORT",
        config="effort",
        choices=("low", "medium", "high"),
        help="DEPRECATED alias of --turn-budget (will mean thinking effort after migration).",
        ui_group="Agent",
        ui_label="Effort (deprecated: turn budget)",
    ),
    Option(
        "autocompact",
        "str",
        flag="--autocompact",
        env="AO_AUTOCOMPACT",
        config="autocompact",
        help=(
            "Context auto-compaction for agents without their own: 'auto' or a context-token "
            "threshold 100000-1000000."
        ),
        ui_group="Agent",
        ui_label="Autocompact",
        providers=("claude-cli",),
    ),
    Option(
        "thinking_effort",
        "enum",
        flag="--thinking-effort",
        env="AO_THINKING_EFFORT",
        config="thinking_effort",
        choices=("low", "medium", "high", "xhigh", "max"),
        help=(
            "Model reasoning depth for all agents: low, medium, high, xhigh or max "
            "(claude: --effort). Unset = agent default."
        ),
        providers=("claude-cli",),
        ui_group="Agent",
        ui_label="Thinking effort",
    ),
    Option(
        "max_attempts",
        "int",
        flag="--max-attempts",
        env="AO_MAX_ATTEMPTS",
        config="max_attempts",
        help="Max attempts per task (overrides workflow defaults.retries.max_attempts).",
        ui_group="Limits",
        ui_label="Max attempts",
    ),
    Option(
        "max_turns",
        "int",
        flag="--max-turns",
        env="AO_MAX_TURNS",
        config="max_turns",
        help="Max turns per claude agent invocation (overrides the turn-budget-derived value).",
        ui_group="Limits",
        ui_label="Max turns",
    ),
    Option(
        "max_parallel",
        "int",
        flag="--max-parallel",
        env="AO_MAX_PARALLEL",
        config="max_parallel",
        default=1,
        help="Max independent ready tasks to run at once (1 = serial).",
        ui_group="Run",
        ui_label="Max parallel",
    ),
    Option(
        "budget_total",
        "int",
        flag="--budget-total",
        help="Total token budget for the run (CLI only; the workflow's budget otherwise).",
        ui_group="Limits",
        ui_label="Token budget",
    ),
    Option(
        "quota_max_wait",
        "int",
        flag="--quota-max-wait",
        env="AO_QUOTA_MAX_WAIT_SECONDS",
        config="quota_max_wait_seconds",
        default=21600,
        help="Max seconds to wait through a Claude quota-exhaustion episode before failing.",
        ui_group="Quota",
        ui_label="Quota max wait (s)",
    ),
    Option(
        "quota_poll_interval",
        "int",
        flag="--quota-poll-interval",
        env="AO_QUOTA_POLL_SECONDS",
        config="quota_poll_seconds",
        default=900,
        help="Seconds to sleep between quota-exhaustion re-run attempts.",
        ui_group="Quota",
        ui_label="Quota poll interval (s)",
    ),
    Option(
        "self_heal",
        "bool",
        flag="--self-heal",
        no_flag="--no-self-heal",
        env="AO_SELF_HEAL",
        config="monitoring.self_heal",
        default=False,
        help="Enable task-failure self-healing; overrides env/config in either direction.",
        ui_group="Monitoring",
        ui_label="Self-heal",
    ),
    Option(
        "record_git_heads",
        "bool",
        flag="--record-git-heads",
        no_flag="--no-record-git-heads",
        env="AO_RECORD_GIT_HEADS",
        config="record_git_heads",
        default=True,
        help="Record git HEADs for `ao report-survival` serial attribution.",
        env_strip_blank=True,
    ),
)

OPTIONS_BY_NAME: Final[Mapping[str, Option]] = {o.name: o for o in OPTIONS}


def get_option(name: str) -> Option:
    return OPTIONS_BY_NAME[name]


def int_env(name: str) -> int | None:
    """Integer env var, or ``None`` when unset, empty or malformed (legacy ``_int_env``)."""
    v = os.environ.get(name)
    try:
        return int(v) if v else None
    except ValueError:
        return None


def bool_env(name: str, *, strip_blank: bool = False) -> bool | None:
    """Tri-state boolean env var (``None`` = unset); see :data:`TRUTHY_ENV_VALUES`."""
    v = os.environ.get(name)
    if strip_blank:
        v = v.strip() if v is not None else v
    if not v:
        return None
    return v.strip().lower() in TRUTHY_ENV_VALUES


def _config_value(cfg: object | None, dotted: str | None) -> Any:
    if cfg is None or dotted is None:
        return None
    cur: Any = cfg
    for part in dotted.split("."):
        cur = getattr(cur, part, None)
        if cur is None:
            return None
    return cur


def resolve_option(opt: Option, cli: Any, cfg: object | None) -> Resolved:
    """Resolve *opt*: CLI value > env var > project config (*cfg*) > built-in default.

    Non-bool options use the legacy ``or``-chain semantics (falsy == unset, malformed int env
    ignored) and report the first truthy layer. Bool options are tri-state: ``None`` is unset,
    ``False`` is a real value at every layer.
    """
    env_name = opt.env
    cfg_value = _config_value(cfg, opt.config)
    if opt.type == "bool":
        if cli is not None:
            return Resolved(bool(cli), SOURCE_CLI)
        env_value = (
            bool_env(env_name, strip_blank=opt.env_strip_blank) if env_name is not None else None
        )
        if env_value is not None:
            return Resolved(env_value, SOURCE_ENV)
        if cfg_value is not None:
            return Resolved(bool(cfg_value), SOURCE_CONFIG)
        if opt.default is not None:
            return Resolved(opt.default, SOURCE_DEFAULT)
        return Resolved(None, SOURCE_UNSET)

    if env_name is None:
        env_value_any: Any = None
    elif opt.type == "int":
        env_value_any = int_env(env_name)
    else:
        env_value_any = os.environ.get(env_name)
    for value, source in (
        (cli, SOURCE_CLI),
        (env_value_any, SOURCE_ENV),
        (cfg_value, SOURCE_CONFIG),
    ):
        if value:
            return Resolved(
                int(value) if opt.type == "int" and opt.default is not None else value, source
            )
    if opt.default is not None:
        return Resolved(opt.default, SOURCE_DEFAULT)
    # Legacy quirk kept for byte-identical behaviour: `cli or env or cfg` yields the LAST operand
    # even when falsy, so a config value of 0 survives as 0 (not None).
    if cfg_value is not None:
        return Resolved(cfg_value, SOURCE_CONFIG)
    return Resolved(None, SOURCE_UNSET)


# -- dashboard allow-list (derived; ui/processes.py re-exports these) -------------------------


def allowed_options() -> dict[str, str]:
    """``name -> --flag`` for every dashboard-forwardable value option (argv allow-list)."""
    return {o.name: o.flag for o in OPTIONS if o.ui and o.type != "bool" and o.flag is not None}


def allowed_bool_options() -> dict[str, tuple[str, str]]:
    """``name -> (on_flag, off_flag)`` for every dashboard-forwardable boolean option."""
    return {
        o.name: (o.flag, o.no_flag)
        for o in OPTIONS
        if o.ui and o.type == "bool" and o.flag is not None and o.no_flag is not None
    }


# -- schema (GET /api/schema/run-options) -----------------------------------------------------


def _json_schema_for(opt: Option) -> dict[str, Any]:
    if opt.type == "int":
        return {"type": "integer"}
    if opt.type == "bool":
        return {"type": "boolean"}
    if opt.type == "enum":
        return {"type": "string", "enum": list(opt.choices)}
    return {"type": "string"}


def options_schema() -> dict[str, Any]:
    """Machine-readable registry: a JSON Schema for the launch ``options`` object plus the UI
    metadata (group/label/help/precedence) the dashboard renders its form from."""
    ui_options = [o for o in OPTIONS if o.ui]
    return {
        "schema": SCHEMA_ID,
        "precedence": [SOURCE_CLI, SOURCE_ENV, SOURCE_CONFIG, SOURCE_DEFAULT],
        "json_schema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {o.name: _json_schema_for(o) for o in ui_options},
        },
        "options": [
            {
                "name": o.name,
                "type": o.type,
                "flag": o.flag,
                "no_flag": o.no_flag,
                "deprecated_flags": list(o.deprecated_flags),
                "env": o.env,
                "config": o.config,
                "default": o.default,
                "choices": list(o.choices),
                "help": o.help,
                "providers": list(o.providers),
                "ui": ({"group": o.ui_group, "label": o.ui_label} if o.ui else None),
                "resumable": o.resumable,
            }
            for o in OPTIONS
        ],
    }


# -- effective settings snapshot ---------------------------------------------------------------


@dataclass
class SettingsSnapshot:
    """``{name: Resolved}`` captured at one invocation (start or resume)."""

    values: dict[str, Resolved] = field(default_factory=dict)

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return {k: {"value": r.value, "source": r.source} for k, r in self.values.items()}
