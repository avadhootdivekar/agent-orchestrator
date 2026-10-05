"""Shared XDG-style state-directory resolution (extracted for E-Wk9Tz3 T-Gt4Pw8).

`service/paths.py::default_state_dir()` already implements this precedence for the
multi-workspace service's own state directory, but is hardcoded to that one env var /
subdir pair. This module generalizes the same precedence so `isolation/git.py` can
resolve its own state directory (for `EMPTY_HOOKS_DIR`) without depending on
`service/paths.py` or importing anything isolation-specific into it.

`service/paths.py` is deliberately left untouched here -- refactoring it onto this helper
is `T-Wk3Nv6`'s (the worktree-lifecycle task's) job, not this one's.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

# Same name/semantics as `service/paths.py`'s own `XDG_STATE_HOME_ENV` -- duplicated
# rather than imported so this module has zero dependency on `service/paths.py` (the
# intended dependency direction is the other way around, per T-Wk3Nv6's forward note).
XDG_STATE_HOME_ENV = "XDG_STATE_HOME"
XDG_CONFIG_HOME_ENV = "XDG_CONFIG_HOME"


def _resolve(
    override_env: str | None,
    xdg_home_env: str,
    xdg_subdir: str,
    home_relative: tuple[str, ...],
    default_subdir: str,
    environ: Mapping[str, str] | None,
    home: Path | None,
) -> Path:
    # `environ=None` -> os.environ and `home=None` -> Path.home(), both read at call time
    # (never cached) so callers and tests can monkeypatch per call.
    env = os.environ if environ is None else environ
    if override_env is not None:
        override = env.get(override_env)
        if override:
            return Path(override)
    xdg_home = env.get(xdg_home_env)
    if xdg_home:
        return Path(xdg_home) / xdg_subdir
    base = Path.home() if home is None else home
    return base.joinpath(*home_relative, default_subdir)


def resolve_state_dir(
    override_env: str | None,
    xdg_subdir: str,
    default_subdir: str,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve a state directory with the precedence:

    ``$<override_env>`` (exact directory path; skipped when ``override_env`` is ``None``) >
    ``$XDG_STATE_HOME/<xdg_subdir>`` > ``<home>/.local/state/<default_subdir>``.

    ``environ=None`` reads ``os.environ`` and ``home=None`` uses ``Path.home()``, both at call
    time -- never cached -- so callers and their tests can monkeypatch per-call without
    import-order fragility (mirrors `service/paths.py`'s own explicit convention for its
    state/registry paths).
    """
    return _resolve(
        override_env,
        XDG_STATE_HOME_ENV,
        xdg_subdir,
        (".local", "state"),
        default_subdir,
        environ,
        home,
    )


def resolve_config_dir(
    override_env: str | None,
    xdg_subdir: str,
    default_subdir: str,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> Path:
    """Resolve a config directory with the precedence:

    ``$<override_env>`` (exact directory path; skipped when ``override_env`` is ``None``) >
    ``$XDG_CONFIG_HOME/<xdg_subdir>`` > ``<home>/.config/<default_subdir>``.

    Same shape as :func:`resolve_state_dir` (and as the approvals epic's definition: one
    implementation serves both epics, E-Da5Tn9 ledger row X1).
    """
    return _resolve(
        override_env,
        XDG_CONFIG_HOME_ENV,
        xdg_subdir,
        (".config",),
        default_subdir,
        environ,
        home,
    )
