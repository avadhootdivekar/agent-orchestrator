"""Per-project configuration discovery and schema for AO.

A project config file (`.ao/config.yaml` or `ao.yaml`) provides default values
for `--workflow`, `--reposets`, and `--agents` so users don't have to pass flags
on every invocation.

Discovery rules (``find_project_config``):
1. Start from the given directory (defaults to cwd).
2. Check for ``.ao/config.yaml`` then ``ao.yaml`` in the current directory.
3. Walk up one level and repeat.
4. Stop at a ``.git/`` boundary (git root), or when reaching the filesystem root.
5. Return the first match, or ``None`` if nothing found.

Precedence (highest to lowest):
  CLI flag > environment variable > project config file > built-in default
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from .errors import ConfigError
from .models import (
    DEFAULT_MAX_EXTENSIONS_PER_BREAKER,
    DEFAULT_MAX_HEAL_RETRIES_PER_TASK,
    DEFAULT_MAX_MONITOR_CALLS_PER_RUN,
)
from .monitoring import DEFAULT_HEAL_WAIT_SECONDS

# Config filenames checked in order within each directory during walk-up.
_CONFIG_CANDIDATES = [
    Path(".ao") / "config.yaml",
    Path("ao.yaml"),
]


class MonitoringConfig(BaseModel):
    """Schema for the `monitoring:` block of a per-project AO config file (E-XyfjuZ).

    All fields default such that an ABSENT `monitoring:` block (every config predating
    this epic) is byte-identical: `self_heal` defaults off, the default monitor is the
    deterministic zero-cost `RuleBasedMonitor`, and every bound matches the engine's own
    built-in defaults (`models.DEFAULT_MAX_*`).
    """

    self_heal: bool = False
    """Opt-in switch for Consult Point B (task-failure self-healing). Independent of any
    workflow's circuit-breaker `mode` declarations (Consult Point A activates purely from
    the spec, never from this flag)."""

    monitor: str = "rules"
    """Which Monitor implementation to consult: the literal string "rules" (default,
    `RuleBasedMonitor`) or the name of an agent declared in `agents.json` (`AgentMonitor`)."""

    max_extensions_per_breaker: int = DEFAULT_MAX_EXTENSIONS_PER_BREAKER
    """Cap on monitor-driven breaker-threshold extensions per breaker id, per run."""

    max_heal_retries_per_task: int = DEFAULT_MAX_HEAL_RETRIES_PER_TASK
    """Cap on self-heal retries per task id, per run."""

    max_monitor_calls_per_run: int = DEFAULT_MAX_MONITOR_CALLS_PER_RUN
    """Cap on total ACTUAL monitor consults per run, shared across both consult points."""

    heal_wait_seconds: float = DEFAULT_HEAL_WAIT_SECONDS
    """Seconds `RuleBasedMonitor` recommends waiting before a healed retry."""

    transient_patterns: list[str] = []
    """Additional regex patterns (case-insensitive) ADDED to `RuleBasedMonitor`'s built-in
    transient-failure patterns (network/timeout/5xx/JSON-decode) -- never replaces them."""


class ProjectConfig(BaseModel):
    """Schema for a per-project AO config file.

    All path fields are stored as strings (relative paths are resolved relative
    to the config file's directory at load time by ``load_project_config``).
    """

    workflow: str | None = None
    """Path to the workflow JSON/YAML file."""

    reposets: str | None = None
    """Path to the reposets config JSON/YAML file."""

    agents: str | None = None
    """Path to the agents config JSON/YAML file."""

    workspace_root: str | None = None
    """Override the workspace_root from the reposet (useful in CI)."""

    env: dict[str, str] = {}
    """Key-value environment overrides applied before the command runs."""

    # Runtime execution settings (lowest-priority defaults; CLI > env var > these values)
    max_attempts: int | None = None
    """Max task attempts (overrides workflow defaults.retries.max_attempts)."""

    max_turns: int | None = None
    """Max turns per claude invocation (overrides effort-derived value)."""

    model: str | None = None
    """Claude model to use for all agents (e.g. 'claude-sonnet-4-6')."""

    effort: str | None = None
    """Effort level for all agents: low, medium, or high."""

    quota_max_wait_seconds: int | None = None
    """Max seconds to wait across a quota-exhaustion episode before failing."""

    quota_poll_seconds: int | None = None
    """Seconds to sleep between quota-exhaustion re-run attempts."""

    monitoring: MonitoringConfig = MonitoringConfig()
    """Agent-based monitoring & self-healing settings (E-XyfjuZ). Absent block ->
    all-defaults -> byte-identical to pre-epic behavior."""

    @field_validator("env", mode="before")
    @classmethod
    def _env_must_be_str_mapping(cls, v: object) -> dict[str, str]:
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise ValueError("env must be a mapping of string keys to string values")
        return {str(k): str(val) for k, val in v.items()}

    @model_validator(mode="after")
    def _at_least_one_path(self) -> ProjectConfig:
        if self.workflow is None and self.reposets is None and self.agents is None:
            # A config with no paths is technically valid (user may rely on env vars)
            # but we warn rather than error.
            pass
        return self


def find_project_config(start: Path | None = None) -> Path | None:
    """Walk up the directory tree from *start* to find the nearest project config.

    Stops at a ``.git`` boundary or the filesystem root.  Returns the resolved
    ``Path`` of the first config file found, or ``None``.

    Args:
        start: Directory to begin the search.  Defaults to ``Path.cwd()``.

    Returns:
        Resolved path to the config file, or ``None`` if not found.
    """
    current = (start or Path.cwd()).resolve()

    while True:
        for candidate in _CONFIG_CANDIDATES:
            config_path = current / candidate
            if config_path.exists():
                return config_path.resolve()

        # Stop at git root boundary.
        if (current / ".git").exists():
            break

        parent = current.parent
        if parent == current:
            # Reached filesystem root.
            break
        current = parent

    return None


def load_project_config(config_path: Path) -> ProjectConfig:
    """Load and validate a project config file.

    Relative paths within the config are resolved relative to the config file's
    parent directory so that ``workflow: workflow.json`` works from any location.

    Args:
        config_path: Absolute (or resolvable) path to the config file.

    Returns:
        A validated ``ProjectConfig`` instance.

    Raises:
        ConfigError: If the file is missing, unparseable, or fails schema validation.
    """
    path = Path(config_path).resolve()
    if not path.exists():
        raise ConfigError(f"Project config not found: {path}")

    try:
        raw = path.read_text()
        data: dict = yaml.safe_load(raw) or {}
    except Exception as exc:
        raise ConfigError(f"Failed to parse project config {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ConfigError(
            f"Project config must be a YAML mapping, got {type(data).__name__}: {path}"
        )

    base_dir = path.parent

    # Resolve relative paths to absolute, anchored at the config file's directory.
    for field_name in ("workflow", "reposets", "agents", "workspace_root"):
        raw_val = data.get(field_name)
        if raw_val and not os.path.isabs(raw_val):
            data[field_name] = str((base_dir / raw_val).resolve())

    try:
        return ProjectConfig.model_validate(data)
    except PydanticValidationError as exc:
        # Flatten Pydantic's verbose error into a single readable line.
        errors = "; ".join(
            f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        raise ConfigError(f"Invalid project config {path}: {errors}") from exc


def apply_project_config_env(cfg: ProjectConfig) -> None:
    """Apply ``env`` overrides from a project config to ``os.environ``.

    Only sets variables that are *not already set* in the environment (explicit
    env vars take precedence over config-file values).

    Args:
        cfg: A loaded ``ProjectConfig``.
    """
    for key, value in cfg.env.items():
        if key not in os.environ:
            os.environ[key] = value


# ---------------------------------------------------------------------------
# Scaffold template for `ao init`
# ---------------------------------------------------------------------------

_INIT_TEMPLATE = """\
# .ao/config.yaml — AO per-project configuration
# Generated by `ao init`.  Uncomment and fill in the fields you need.
#
# All paths are relative to this file's directory.
# Precedence for every setting: CLI flag > env var > this file > built-in default.

# workflow: path/to/workflow.json   # required for `ao run` / `ao validate`
# reposets: path/to/reposet.json   # required (or set AO_REPOSETS env var)
# agents:   path/to/agents.json    # required (or set AO_AGENTS env var)

# workspace_root: /path/to/workspace  # overrides reposet workspace_root

# env:                             # environment variable overrides
#   MY_VAR: value
#   ANOTHER_VAR: value

# --- Runtime execution settings (env var equivalents shown) ---
# max_attempts: 1          # AO_MAX_ATTEMPTS — max task attempts (1 = no retry)
# max_turns: 30            # AO_MAX_TURNS    — max turns per claude invocation
# model: claude-sonnet-4-6 # AO_MODEL        — claude model for all agents
# effort: medium           # AO_EFFORT       — low / medium / high

# --- Claude usage-quota exhaustion handling ---
# quota_max_wait_seconds: 21600   # AO_QUOTA_MAX_WAIT_SECONDS — give up after 6h of exhaustion
# quota_poll_seconds: 900         # AO_QUOTA_POLL_SECONDS     — poll every 15 min

# --- Agent-based monitoring & self-healing (absent -> all defaults, byte-identical) ---
# monitoring:
#   self_heal: false               # AO_SELF_HEAL / --self-heal / --no-self-heal — opt-in
#   monitor: rules                 # "rules" (default) or an agent name from agents.json
#   max_extensions_per_breaker: 1  # bound on monitor-driven breaker extensions per breaker id
#   max_heal_retries_per_task: 1   # bound on self-heal retries per task id
#   max_monitor_calls_per_run: 10  # shared cap on real monitor consults per run
#   heal_wait_seconds: 30          # RuleBasedMonitor's recommended wait before a healed retry
#   transient_patterns: []         # extra regexes ADDED to the built-in transient patterns
"""


def scaffold_init(target_dir: Path | None = None) -> Path:
    """Write a starter `.ao/config.yaml` to *target_dir* (default: cwd).

    Creates the `.ao/` directory if it doesn't exist.  Does **not** overwrite
    an existing config file.

    Args:
        target_dir: Directory in which to create `.ao/config.yaml`.

    Returns:
        Path to the written (or already-existing) config file.

    Raises:
        ConfigError: If an existing config file would be overwritten.
    """
    base = (target_dir or Path.cwd()).resolve()
    ao_dir = base / ".ao"
    config_path = ao_dir / "config.yaml"

    if config_path.exists():
        raise ConfigError(
            f"Config file already exists: {config_path}\nRemove it or edit it manually."
        )

    ao_dir.mkdir(parents=True, exist_ok=True)
    config_path.write_text(_INIT_TEMPLATE)
    return config_path
