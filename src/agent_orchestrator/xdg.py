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
from pathlib import Path

# Same name/semantics as `service/paths.py`'s own `XDG_STATE_HOME_ENV` -- duplicated
# rather than imported so this module has zero dependency on `service/paths.py` (the
# intended dependency direction is the other way around, per T-Wk3Nv6's forward note).
XDG_STATE_HOME_ENV = "XDG_STATE_HOME"


def resolve_state_dir(override_env: str, xdg_subdir: str, default_subdir: str) -> Path:
    """Resolve a state directory with the precedence:

    ``$<override_env>`` (exact directory path) > ``$XDG_STATE_HOME/<xdg_subdir>`` >
    ``~/.local/state/<default_subdir>``.

    Reads ``os.environ``/``Path.home()`` at call time -- never cached -- so callers and
    their tests can monkeypatch per-call without import-order fragility (mirrors
    `service/paths.py`'s own explicit convention for its state/registry paths).
    """
    override = os.environ.get(override_env)
    if override:
        return Path(override)
    xdg_state_home = os.environ.get(XDG_STATE_HOME_ENV)
    if xdg_state_home:
        return Path(xdg_state_home) / xdg_subdir
    return Path.home() / ".local" / "state" / default_subdir
