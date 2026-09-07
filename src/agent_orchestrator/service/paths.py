"""XDG-respecting path resolution for the multi-workspace service's registry/state files
(HLD §5.1/§5.2).

Both directories independently honor an env override precisely so tests never touch a real
``$HOME``: ``AO_SERVICE_CONFIG`` names the exact registry file path, ``AO_SERVICE_STATE_DIR``
names the state directory. Absent those, resolution falls back to the XDG base-directory
spec (``XDG_CONFIG_HOME``/``XDG_STATE_HOME``), then a hardcoded ``~/.config``/
``~/.local/state`` default.

Both functions read ``os.environ`` at call time -- no module-level caching -- so tests can
monkeypatch/``os.environ`` per-test without import-order fragility (mirrors the rest of this
codebase's env-driven config resolution, e.g. ``project_config.py``).

``default_state_dir`` (E-Wk9Tz3 T-Wk3Nv6, R-11) is implemented on top of the shared
``xdg.resolve_state_dir`` helper -- this was the second hand-copy of that override ->
``$XDG_STATE_HOME`` -> ``~/.local/state`` precedence, and factoring it keeps
``isolation/paths.py`` from becoming a third. ``default_registry_path`` is deliberately
**not** migrated: it resolves a *file* path via ``$XDG_CONFIG_HOME`` (config-home
anchoring, defaulting under ``~/.config``), a genuinely different shape from
``resolve_state_dir``'s directory-under-``$XDG_STATE_HOME`` precedence -- forcing them
together would be a false DRY (see the R-11 disposition, HLD §24 and the T-Wk3Nv6 ticket's
own Risks section, which calls this out explicitly as the escape hatch to take when the two
resolutions are not genuinely the same shape).
"""

from __future__ import annotations

import os
from pathlib import Path

from ..xdg import resolve_state_dir

# Env var names.
AO_SERVICE_CONFIG_ENV = "AO_SERVICE_CONFIG"
AO_SERVICE_STATE_DIR_ENV = "AO_SERVICE_STATE_DIR"
XDG_CONFIG_HOME_ENV = "XDG_CONFIG_HOME"
XDG_STATE_HOME_ENV = "XDG_STATE_HOME"

# Fixed path segments (HLD §5.1/§5.2: `~/.config/ao/service.yaml`, `~/.local/state/ao/service`).
_SERVICE_SUBDIR = "ao"
_REGISTRY_FILENAME = "service.yaml"
_STATE_SUBDIR = "service"
# `resolve_state_dir`'s xdg_subdir/default_subdir args -- both branches of the pre-migration
# code joined the same two segments ("ao", "service"), so they stay identical here too.
_STATE_SUBPATH = f"{_SERVICE_SUBDIR}/{_STATE_SUBDIR}"


def default_registry_path() -> Path:
    """Resolve the service registry file path.

    Precedence: ``AO_SERVICE_CONFIG`` (exact file path) > ``$XDG_CONFIG_HOME/ao/service.yaml``
    > ``~/.config/ao/service.yaml``.
    """
    override = os.environ.get(AO_SERVICE_CONFIG_ENV)
    if override:
        return Path(override)

    xdg_config_home = os.environ.get(XDG_CONFIG_HOME_ENV)
    config_home = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return config_home / _SERVICE_SUBDIR / _REGISTRY_FILENAME


def default_state_dir() -> Path:
    """Resolve the service runtime-state directory.

    Precedence: ``AO_SERVICE_STATE_DIR`` (exact directory path) >
    ``$XDG_STATE_HOME/ao/service`` > ``~/.local/state/ao/service``.
    """
    return resolve_state_dir(AO_SERVICE_STATE_DIR_ENV, _STATE_SUBPATH, _STATE_SUBPATH)
