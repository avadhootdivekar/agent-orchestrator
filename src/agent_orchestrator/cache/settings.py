"""Run-level result-cache mode and per-task author policy (E-Rc4Hk8, HLD 8.1.3 / 8.1.6).

Pure functions: no I/O, no clock, no engine imports. `CacheConfig` is only imported under
`TYPE_CHECKING` (the config module imports `cache.constants`, so a runtime import here would
add a needless edge to the import graph).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from . import constants

if TYPE_CHECKING:
    from agent_orchestrator.models import TaskSpec, WorkflowSpec
    from agent_orchestrator.project_config import CacheConfig


@dataclass(frozen=True)
class ResultCacheSettings:
    """The resolved run-level result-cache switches (mode + limits)."""

    mode: str  # constants.MODE_OFF | constants.MODE_ON | constants.MODE_SHADOW
    source: str  # SOURCE_*
    max_bytes: int
    max_entry_bytes: int
    ttl_days: int | None
    include_repo_heads: bool
    max_input_bytes: int
    max_input_files: int


def resolve_result_cache_settings(
    cli_flag: bool | None,
    environ: Mapping[str, str],
    cfg: CacheConfig | None,
) -> tuple[ResultCacheSettings, list[str]]:
    """Resolve mode: CLI flag > `AO_CACHE` > config > off. Returns (settings, warnings).

    An unrecognised `AO_CACHE` value (including the deferred ``refresh``) fails closed to
    `off` with one warning rather than silently enabling anything.
    """
    warnings: list[str] = []
    if cli_flag is True:
        mode, source = constants.MODE_ON, constants.SOURCE_CLI
    elif cli_flag is False:
        mode, source = constants.MODE_OFF, constants.SOURCE_CLI
    else:
        raw = environ.get(constants.ENV_CACHE, "").strip().lower()
        if raw in constants.ENV_ON_VALUES:
            mode, source = constants.MODE_ON, constants.SOURCE_ENV
        elif raw in constants.ENV_OFF_VALUES:
            mode, source = constants.MODE_OFF, constants.SOURCE_ENV
        elif raw == constants.MODE_SHADOW:
            mode, source = constants.MODE_SHADOW, constants.SOURCE_ENV
        elif raw != "":
            mode, source = constants.MODE_OFF, constants.SOURCE_ENV
            warnings.append(
                f"{constants.ENV_CACHE}={raw!r} not recognised (use 1|0|shadow); result cache OFF"
            )
        elif cfg is not None and cfg.enabled is True:
            mode, source = cfg.mode, constants.SOURCE_CONFIG
        elif cfg is not None and cfg.enabled is False:
            mode, source = constants.MODE_OFF, constants.SOURCE_CONFIG
        else:
            mode, source = constants.MODE_OFF, constants.SOURCE_DEFAULT
    if cfg is None:
        from agent_orchestrator.project_config import CacheConfig as _CacheConfig

        base = _CacheConfig()
    else:
        base = cfg
    settings = ResultCacheSettings(
        mode=mode,
        source=source,
        max_bytes=base.max_bytes,
        max_entry_bytes=base.effective_max_entry_bytes,
        ttl_days=base.ttl_days,
        include_repo_heads=base.include_repo_heads,
        max_input_bytes=base.max_input_bytes,
        max_input_files=base.max_input_files,
    )
    return settings, warnings


def task_cache_policy(task: TaskSpec, workflow: WorkflowSpec, *, injected: bool) -> bool:
    """AUTHOR layer (D1): task.cache > defaults.cache > constants.DEFAULT_TASK_CACHE_POLICY.

    An `emit_tasks`-injected task's ``True`` is ignored: agent-authored manifests may only
    narrow (opt out), never opt in.
    """
    flag = task.cache
    if injected and flag is True:
        flag = None
    if flag is not None:
        return flag
    if workflow.defaults.cache is not None:
        return workflow.defaults.cache
    # Read through the module (not a name import) so U-S4 can monkeypatch the flip point.
    return constants.DEFAULT_TASK_CACHE_POLICY


def opted_in_count(workflow: WorkflowSpec) -> tuple[int, int]:
    """(opted-in static tasks, total static tasks) for the stderr banner."""
    n = sum(task_cache_policy(t, workflow, injected=False) for t in workflow.tasks)
    return n, len(workflow.tasks)
