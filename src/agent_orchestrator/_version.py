"""Version string assembly for `ao --version`.

A release build (exact git tag, clean tree) prints just the semantic
version. Anything else — a dev build, an untagged commit, or a tree with
local modifications — appends commit/dirty/build-time provenance so it can
never be mistaken for a tagged release.

Provenance is normally baked in at package-build time by ``hatch_build.py``
(see that file for why: `uv tool install` ships an isolated venv with no
``.git``). When no baked info is present — running straight from the source
tree in dev/editable mode — this falls back to live git introspection.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import __version__

try:
    from ._build_info import BUILD_COMMIT, BUILD_DIRTY, BUILD_TAGGED, BUILD_TIME
except ImportError:
    BUILD_COMMIT = None
    BUILD_DIRTY = None
    BUILD_TAGGED = None
    BUILD_TIME = None


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def get_version_string() -> str:
    """Return the string printed by `ao --version`."""
    commit, dirty, tagged, build_time = BUILD_COMMIT, BUILD_DIRTY, BUILD_TAGGED, BUILD_TIME

    if commit is None:
        # No baked build info — dev/editable run against a live source tree.
        commit = _git("rev-parse", "--short", "HEAD")
        status = _git("status", "--porcelain")
        dirty = status != "" if status is not None else None
        tagged = _git("describe", "--tags", "--exact-match", "HEAD") is not None

    if tagged and not dirty:
        return f"ao {__version__}"

    suffix = commit or "unknown"
    if dirty:
        suffix += ".dirty"
    version_str = f"ao {__version__} ({suffix})"
    if build_time:
        version_str += f" built {build_time}"
    return version_str
