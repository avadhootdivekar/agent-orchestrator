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

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, Final, Literal, Union, get_args, get_origin

from pydantic import BaseModel

from .models import EFFORT_MAX_TURNS, SentinelSpec

OptionType = Literal["int", "float", "str", "enum", "bool"]

SOURCE_WORKFLOW: Final = "workflow"
SOURCE_CLI: Final = "cli"
SOURCE_ENV: Final = "env"
SOURCE_CONFIG: Final = "config"
SOURCE_DEFAULT: Final = "default"
SOURCE_UNSET: Final = "unset"
# A sticky-on-resume option that the resume did not re-specify: the value kept from run state.
SOURCE_STATE: Final = "state"

SCHEMA_ID: Final = "ao.run-options/v1"

# Values an env var may use to mean True for a boolean option (anything else non-blank is False).
TRUTHY_ENV_VALUES: Final = frozenset({"1", "true", "yes", "on"})


# Dashboard form groups, in display order (FR-5/FR-7); the form renders whatever groups the schema
# carries, so an unknown future group still shows up (appended after these).
GROUP_MODEL: Final = "Model & reasoning"
GROUP_TURNS: Final = "Turns & retries"
GROUP_CONCURRENCY: Final = "Concurrency & budget"
GROUP_QUOTA: Final = "Quota"
GROUP_RECOVERY: Final = "Recovery"
SENTINEL_GROUP: Final = "Sentinel"
UI_GROUP_ORDER: Final = (
    GROUP_MODEL,
    GROUP_TURNS,
    GROUP_CONCURRENCY,
    GROUP_QUOTA,
    GROUP_RECOVERY,
    SENTINEL_GROUP,
)


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
    choice_labels: tuple[tuple[str, str], ...] = ()
    """Optional ``(choice, display label)`` pairs for the dashboard dropdown (e.g. turn counts)."""
    help: str = ""
    ui_group: str | None = None
    """Dashboard form group; ``None`` = not forwardable from the dashboard."""
    ui_label: str | None = None
    resumable: bool = True
    """May a ``resume`` change it (every current option may -- resolved fresh each invocation)."""
    sticky_on_resume: bool = False
    """A resume that does not re-specify it keeps the value persisted in run state instead of
    re-resolving CLI/env/config (``SOURCE_STATE``). Used by ``cost_cap_usd``."""
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


_BASE_OPTIONS: Final[tuple[Option, ...]] = (
    # Invocation identity, not a setting: no env (AO_RUN_ID is exported INTO hook/agent
    # subprocesses, so an env layer would make a nested `ao run` inherit its parent's id), no
    # config, no ui_group (not a dashboard field; launchers pass ``launch_run(run_id=)``).
    Option(
        "run_id",
        "str",
        flag="--run-id",
        help=(
            "Use this id for the new run instead of <workflow_id>-<UTC timestamp>. Refused if"
            " the run already exists (use `ao resume --run-id`)."
        ),
        resumable=False,
    ),
    Option(
        "model",
        "str",
        flag="--model",
        env="AO_MODEL",
        config="model",
        help="Claude model for all agents (an alias such as sonnet, or a pinned id).",
        ui_group=GROUP_MODEL,
        ui_label="Model",
    ),
    Option(
        "turn_budget",
        "enum",
        flag="--turn-budget",
        env="AO_TURN_BUDGET",
        config="turn_budget",
        choices=("low", "medium", "high", "xhigh"),
        choice_labels=tuple((c, f"{c} ({EFFORT_MAX_TURNS[c]} turns)") for c in EFFORT_MAX_TURNS),
        help="Max-turn tier for all agents: low=15, medium=30, high=60, xhigh=120 turns.",
        ui_group=GROUP_MODEL,
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
        ui_group=GROUP_MODEL,
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
        ui_group=GROUP_MODEL,
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
        ui_group=GROUP_MODEL,
        ui_label="Thinking effort",
    ),
    Option(
        "max_attempts",
        "int",
        flag="--max-attempts",
        env="AO_MAX_ATTEMPTS",
        config="max_attempts",
        help="Max attempts per task (overrides workflow defaults.retries.max_attempts).",
        ui_group=GROUP_TURNS,
        ui_label="Max attempts",
    ),
    Option(
        "max_turns",
        "int",
        flag="--max-turns",
        env="AO_MAX_TURNS",
        config="max_turns",
        help="Max turns per claude agent invocation (overrides the turn-budget-derived value).",
        ui_group=GROUP_TURNS,
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
        ui_group=GROUP_CONCURRENCY,
        ui_label="Max parallel",
    ),
    Option(
        "budget_total",
        "int",
        flag="--budget-total",
        help="Total token budget for the run (CLI only; the workflow's budget otherwise).",
        ui_group=GROUP_CONCURRENCY,
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
        ui_group=GROUP_QUOTA,
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
        ui_group=GROUP_QUOTA,
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
        ui_group=GROUP_RECOVERY,
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

# -- sentinel options (E-Sn7tL2): one Option per SentinelSpec scalar leaf, generated -----------

SENTINEL_PREFIX: Final = "sentinel."
SENTINEL_ENV_PREFIX: Final = "AO_SENTINEL_"

# dotted leaf -> (flag, no_flag): the only leaves with a dedicated CLI flag (the rest are
# reachable through env, config, the workflow block and ``--sentinel-set key=value``).
SENTINEL_FLAGS: Final[Mapping[str, tuple[str, str | None]]] = {
    "enabled": ("--sentinel", "--no-sentinel"),
    "llm.enabled": ("--sentinel-llm", "--no-sentinel-llm"),
    "llm.powers": ("--sentinel-powers", None),
    "llm.budget_usd": ("--sentinel-budget-usd", None),
    "max_parallel_ceiling": ("--max-parallel-ceiling", None),
}


# Sentinel leaves the dashboard may forward (T-Sn5Ui1): dotted -> group / label. Only leaves with a
# dedicated CLI flag can be forwarded (argv allow-list); the schema-driven launch form (T-pMpo4O)
# renders these with no hand-written fields. Tuning knobs stay on config / env / the workflow block.
SENTINEL_UI_GROUPS: Final[Mapping[str, str]] = {
    "enabled": SENTINEL_GROUP,
    "max_parallel_ceiling": SENTINEL_GROUP,
    "llm.enabled": SENTINEL_GROUP,
    "llm.powers": SENTINEL_GROUP,
    "llm.budget_usd": SENTINEL_GROUP,
}
SENTINEL_UI_LABELS: Final[Mapping[str, str]] = {
    "enabled": "Sentinel (live control)",
    "max_parallel_ceiling": "Max-parallel ceiling",
    "llm.enabled": "Sentinel LLM lane",
    "llm.powers": "LLM powers",
    "llm.budget_usd": "LLM budget (USD)",
}


def _unwrap(ann: Any) -> Any:
    """Strip ``Annotated[...]`` and ``X | None`` down to the underlying scalar annotation."""
    while True:
        origin = get_origin(ann)
        if origin is Annotated:
            ann = get_args(ann)[0]
            continue
        if origin is Union or (origin is not None and type(None) in get_args(ann)):
            args = [a for a in get_args(ann) if a is not type(None)]
            if len(args) == 1:
                ann = args[0]
                continue
        return ann


def sentinel_leaves(
    model: type[BaseModel] = SentinelSpec, prefix: str = ""
) -> list[tuple[str, Any, Any]]:
    """``(dotted, annotation, default)`` for every field of *model*, recursing into sub-models.
    List/dict leaves are included (callers filter on :func:`_scalar_type`)."""
    out: list[tuple[str, Any, Any]] = []
    for name, f in model.model_fields.items():
        ann = _unwrap(f.annotation)
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            out.extend(sentinel_leaves(ann, f"{prefix}{name}."))
        else:
            out.append((f"{prefix}{name}", ann, f.get_default(call_default_factory=True)))
    return out


def scalar_option_type(ann: Any) -> tuple[OptionType, tuple[str, ...]] | None:
    """Registry type (+ enum choices) for a scalar annotation; ``None`` for list/dict leaves."""
    if get_origin(ann) is Literal:
        return "enum", tuple(str(a) for a in get_args(ann))
    for py, kind in ((bool, "bool"), (int, "int"), (float, "float"), (str, "str")):
        if ann is py:
            return kind, ()  # type: ignore[return-value]
    return None


def sentinel_options() -> tuple[Option, ...]:
    """Generate the registry entries for :class:`SentinelSpec` (names ``sentinel.<dotted>``)."""
    opts: list[Option] = []
    for dotted, ann, default in sentinel_leaves():
        typed = scalar_option_type(ann)
        if typed is None:
            continue
        kind, choices = typed
        flag, no_flag = SENTINEL_FLAGS.get(dotted, (None, None))
        opts.append(
            Option(
                f"{SENTINEL_PREFIX}{dotted}",
                kind,
                flag=flag,
                no_flag=no_flag,
                env=SENTINEL_ENV_PREFIX + dotted.replace(".", "_").upper(),
                config=f"{SENTINEL_PREFIX}{dotted}",
                default=default,
                choices=choices,
                help=f"Sentinel setting `{dotted}` (see docs-md/sentinel-hld.md §11).",
                ui_group=SENTINEL_UI_GROUPS.get(dotted),
                ui_label=SENTINEL_UI_LABELS.get(dotted, dotted),
            )
        )
    return tuple(opts)


# `--cost-cap-usd` (T-et7Edf). Deliberately NOT yet in OPTIONS: registering it makes every consumer
# (dashboard form, `launch_run` allow-list, the registry<->CLI-flag consistency tests) assume
# `ao run`/`ao resume` accept the flag, and the cli.py wiring is the unfinished part of the ticket.
# The wiring change appends this constant to OPTIONS (in the Concurrency & budget group, after
# `budget_total`) in the same commit that adds the typer option.
COST_CAP_OPTION: Final = Option(
    "cost_cap_usd",
    "float",
    flag="--cost-cap-usd",
    help=(
        "Mid-run USD cost cap (CLI only; sticky on resume). The run halts resumably once its "
        "settled cost reaches the cap."
    ),
    ui_group=GROUP_CONCURRENCY,
    ui_label="Cost cap (USD)",
    sticky_on_resume=True,
)

OPTIONS: Final[tuple[Option, ...]] = _BASE_OPTIONS + sentinel_options()

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


def float_env(name: str) -> float | None:
    """Float env var, or ``None`` when unset, empty or malformed."""
    v = os.environ.get(name)
    try:
        return float(v) if v else None
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
    if opt.type == "float":
        return {"type": "number"}
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
                "choice_labels": dict(o.choice_labels),
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


# -- cost-cap helpers (T-et7Edf) -----------------------------------------------------------------


def validate_cost_cap_usd(raw: float | None) -> float | None:
    """Validate the RAW CLI value of ``--cost-cap-usd`` (AC1/AC2, L1).

    Must run *before* :func:`resolve_option`, which treats falsy CLI values as unset (so a raw
    ``0`` would silently mean "no cap"). ``None`` -> ``None``; zero, negative, NaN and inf raise
    ``ValueError`` (the CLI maps it to exit 2).
    """
    if raw is None:
        return None
    if not math.isfinite(raw) or raw <= 0:
        raise ValueError(f"--cost-cap-usd must be a finite number > 0, got {raw!r}")
    return float(raw)


def resolve_sticky_cost_cap(persisted: float | None, cli: float | None) -> Resolved:
    """Cap in force on resume: an explicit (validated) CLI value replaces the persisted one
    (absolute); otherwise the persisted cap is kept (``SOURCE_STATE``) or stays unset."""
    if cli is not None:
        return Resolved(cli, SOURCE_CLI)
    if persisted is not None:
        return Resolved(persisted, SOURCE_STATE)
    return Resolved(None, SOURCE_UNSET)
