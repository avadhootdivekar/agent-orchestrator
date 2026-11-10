"""Sentinel settings resolution (E-Sn7tL2 T-Sn2Set, ADR-0003 / ADR-0024).

Layers, lowest to highest: built-in default < ``.ao/config.yaml`` ``sentinel:`` < env
``AO_SENTINEL_<DOTTED_UPPER>`` < CLI (flags + ``--sentinel-set key=value``) < the workflow's own
``sentinel:`` block. Only *explicitly set* keys of the config/workflow blocks count (pydantic
``model_fields_set``), so a block that merely exists never shadows a lower layer with defaults.

Every scalar leaf is a registry :class:`~.run_options.Option` (generated from
:class:`~.models.SentinelSpec`), so env names, flags and sources come from the one registry; this
module only adds the sentinel-specific *merge* (None == unset at every layer, unlike the legacy
``or``-chains) and validation. The engine receives the resulting :class:`SentinelSettings` and,
in this task, ignores it (wiring only).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from pydantic import BaseModel, TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from .models import SentinelSpec
from .run_options import (
    OPTIONS_BY_NAME,
    SENTINEL_PREFIX,
    SOURCE_CLI,
    SOURCE_CONFIG,
    SOURCE_DEFAULT,
    SOURCE_ENV,
    SOURCE_WORKFLOW,
    Resolved,
    bool_env,
    float_env,
    int_env,
    sentinel_leaves,
)

EXIT_BAD_SENTINEL_SETTING: Final = 2
"""CLI exit code for an invalid sentinel flag / ``--sentinel-set`` / env value."""


class SentinelSettingsError(ValueError):
    """A sentinel setting from the CLI, env or a config block is invalid (message is user-ready)."""


@dataclass(frozen=True)
class SentinelSettings:
    """Resolved settings handed to the Orchestrator, with the layer each leaf came from."""

    spec: SentinelSpec
    sources: Mapping[str, Resolved] = field(default_factory=dict)
    """``dotted leaf -> Resolved`` (list/dict leaves included)."""

    @property
    def enabled(self) -> bool:
        return self.spec.enabled

    def effective_settings(self) -> dict[str, Resolved]:
        """``sentinel.<dotted> -> Resolved`` for ``RunState.effective_settings``."""
        return {f"{SENTINEL_PREFIX}{k}": v for k, v in self.sources.items()}


_LEAVES: Final = {dotted: (ann, default) for dotted, ann, default in sentinel_leaves()}


def explicit_leaves(spec: BaseModel | None, prefix: str = "") -> dict[str, Any]:
    """``dotted -> value`` for the leaves *explicitly set* on a sentinel block."""
    out: dict[str, Any] = {}
    if spec is None:
        return out
    for name in spec.model_fields_set:
        value = getattr(spec, name)
        if isinstance(value, BaseModel):
            out.update(explicit_leaves(value, f"{prefix}{name}."))
        else:
            out[f"{prefix}{name}"] = value
    return out


def parse_sentinel_set(items: Iterable[str]) -> dict[str, Any]:
    """Parse repeatable ``--sentinel-set key=value`` (dotted key, validated against the schema).

    Values are tried as JSON first (``15``, ``true``, ``null``, ``[{"effort":"high"}]``) then as
    a raw string (``act``); the leaf's own type validation decides which reading is accepted.
    """
    out: dict[str, Any] = {}
    for item in items:
        key, sep, raw = item.partition("=")
        key = key.strip()
        if not sep or not key:
            raise SentinelSettingsError(f"--sentinel-set expects key=value, got {item!r}")
        if key not in _LEAVES:
            raise SentinelSettingsError(
                f"--sentinel-set: unknown key {key!r}; valid keys: {', '.join(sorted(_LEAVES))}"
            )
        out[key] = _coerce_leaf(key, raw, origin="--sentinel-set")
    return out


def _coerce_leaf(key: str, raw: str, *, origin: str) -> Any:
    ann, _ = _LEAVES[key]
    adapter: TypeAdapter[Any] = TypeAdapter(ann | None)
    candidates: list[Any] = []
    try:
        candidates.append(json.loads(raw))
    except ValueError:
        pass
    candidates.append(raw)
    last: PydanticValidationError | None = None
    for cand in candidates:
        try:
            return adapter.validate_python(cand, strict=False)
        except PydanticValidationError as e:
            last = e
    assert last is not None
    detail = "; ".join(err["msg"] for err in last.errors())
    raise SentinelSettingsError(f"{origin}: invalid value {raw!r} for {key!r}: {detail}")


def _env_layer(environ: Mapping[str, str] | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, opt in OPTIONS_BY_NAME.items():
        if not name.startswith(SENTINEL_PREFIX) or opt.env is None:
            continue
        key = name[len(SENTINEL_PREFIX) :]
        raw = (environ if environ is not None else os.environ).get(opt.env)
        if raw is None or not raw.strip():
            continue
        parsed: Any
        if opt.type == "bool":
            parsed = bool_env(opt.env) if environ is None else _bool_of(raw)
        elif opt.type == "int":
            parsed = int_env(opt.env) if environ is None else _num_of(raw, int)
        elif opt.type == "float":
            parsed = float_env(opt.env) if environ is None else _num_of(raw, float)
        else:
            parsed = raw.strip()
        if parsed is None:
            raise SentinelSettingsError(f"env {opt.env}={raw!r} is not a valid {opt.type} value")
        out[key] = parsed
    return out


def _bool_of(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _num_of(raw: str, kind: type) -> Any:
    try:
        return kind(raw)
    except ValueError:
        return None


def _set_dotted(tree: dict[str, Any], dotted: str, value: Any) -> None:
    parts = dotted.split(".")
    cur = tree
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def resolve_sentinel_settings(
    *,
    workflow: SentinelSpec | None = None,
    config: SentinelSpec | None = None,
    cli: Mapping[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> SentinelSettings:
    """Merge the layers (see module docstring) into validated :class:`SentinelSettings`.

    *cli* maps dotted leaf -> value (``None`` == flag not given). Raises
    :class:`SentinelSettingsError` for a bad env value or a merged spec that fails validation
    (e.g. ``idle_kill_minutes`` below the floor without ``force``).
    """
    layers: list[tuple[str, dict[str, Any]]] = [
        (SOURCE_CONFIG, explicit_leaves(config)),
        (SOURCE_ENV, _env_layer(environ)),
        (SOURCE_CLI, {k: v for k, v in (cli or {}).items() if v is not None}),
        (SOURCE_WORKFLOW, explicit_leaves(workflow)),
    ]
    tree: dict[str, Any] = {}
    sources: dict[str, Resolved] = {}
    for source, values in layers:
        for dotted, value in values.items():
            _set_dotted(tree, dotted, value)
            sources[dotted] = Resolved(value, source)
    try:
        spec = SentinelSpec.model_validate(tree)
    except PydanticValidationError as e:
        detail = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'sentinel'}: {err['msg']}"
            for err in e.errors()
        )
        raise SentinelSettingsError(f"invalid sentinel settings: {detail}") from e
    for dotted, (_ann, default) in _LEAVES.items():
        sources.setdefault(dotted, Resolved(default, SOURCE_DEFAULT))
    return SentinelSettings(spec=spec, sources=dict(sorted(sources.items())))
