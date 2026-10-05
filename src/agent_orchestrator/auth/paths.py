"""Credential-store path resolution and permission gates (HLD section 11.4, L1).

Stdlib plus the shared neutral ``fsutil`` / ``xdg`` only, so ``ui/service.py`` and ``ui/files.py``
may import it (R3). Also holds the file-browser denial helpers (``default_denied_paths``,
``entry_is_denied``; T-Hd4wQ2).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from agent_orchestrator import fsutil, xdg

from .constants import (
    AUDIT_FILENAME,
    AUDIT_LOCK_FILENAME,
    LOCKOUTS_FILENAME,
    LOCKOUTS_LOCK_FILENAME,
    SERVICE_ENV_RELATIVE_PATH,
    USERS_FILENAME,
    USERS_LOCK_FILENAME,
)
from .errors import UnsafePermissionsError

AO_AUTH_DIR_ENV = "AO_AUTH_DIR"
AO_AUTH_STATE_DIR_ENV = "AO_AUTH_STATE_DIR"

# The XDG sub-directory (config side and state side) the store lives in by default.
_XDG_AUTH_SUBDIR = "ao/auth"


def xdg_default_store_dir(env: Mapping[str, str] | None = None) -> Path:
    """``$XDG_CONFIG_HOME/ao/auth`` or ``~/.config/ao/auth``. Ignores ``AO_AUTH_DIR`` on purpose:
    this is the *default*, used to decide what the file browser must deny even when overridden."""
    return xdg.resolve_config_dir(None, _XDG_AUTH_SUBDIR, _XDG_AUTH_SUBDIR, environ=env)


def xdg_default_state_dir(env: Mapping[str, str] | None = None) -> Path:
    """``$XDG_STATE_HOME/ao/auth`` or ``~/.local/state/ao/auth``. Ignores ``AO_AUTH_STATE_DIR``."""
    return xdg.resolve_state_dir(None, _XDG_AUTH_SUBDIR, _XDG_AUTH_SUBDIR, environ=env)


@dataclass(frozen=True)
class StorePaths:
    """Every file the auth layer owns, derived from the store and state directories (§12.6)."""

    store_dir: Path
    state_dir: Path
    users_file: Path
    users_lock: Path
    lockouts_file: Path
    lockouts_lock: Path
    audit_file: Path
    audit_lock: Path

    @classmethod
    def at(cls, store_dir: Path, state_dir: Path) -> StorePaths:
        return cls(
            store_dir=store_dir,
            state_dir=state_dir,
            users_file=store_dir / USERS_FILENAME,
            users_lock=store_dir / USERS_LOCK_FILENAME,
            lockouts_file=state_dir / LOCKOUTS_FILENAME,
            lockouts_lock=state_dir / LOCKOUTS_LOCK_FILENAME,
            audit_file=state_dir / AUDIT_FILENAME,
            audit_lock=state_dir / AUDIT_LOCK_FILENAME,
        )


def _normalized(path: Path) -> Path:
    # normcase folds case on case-insensitive platforms; a no-op on POSIX.
    return Path(os.path.normcase(str(path.resolve())))


def _contains(normalized_path: Path, normalized_root: Path) -> bool:
    return normalized_path == normalized_root or normalized_root in normalized_path.parents


def is_within(path: Path, root: Path) -> bool:
    """True when the resolved ``path`` is ``root`` itself or lies below it.

    Component-wise, so ``/a/bc`` is not within ``/a/b``. ``root`` may be a file: then only that
    exact file is "within" it (used for the denied ``service.env``).
    """
    return _contains(_normalized(path), _normalized(root))


def check_private_paths(store_dir: Path, users_file: Path) -> list[str]:
    """Verify the credential store is private; return the non-fatal parent warnings.

    Read-only (``fix=False``): the server refuses to start on any problem. A missing store
    directory or ``users.json`` is not a permissions problem and is skipped here (the
    "no users" probe reports it). ``fsutil.UnsafePathError`` becomes ``UnsafePermissionsError``.
    """
    notices: list[str] = []
    try:
        try:
            notices.extend(fsutil.ensure_private_dir(store_dir, create=False, fix=False))
        except FileNotFoundError:
            return notices
        try:
            notices.extend(fsutil.check_private_file(users_file, fix=False))
        except FileNotFoundError:
            pass
    except fsutil.UnsafePathError as exc:
        raise UnsafePermissionsError(str(exc)) from exc
    return notices


def check_state_dir(state_dir: Path) -> list[str]:
    """Create the state directory if missing (0700, parent checked) and verify it.

    Never fixes permissions (``fix=False``): the server only refuses. Returns the warnings.
    """
    try:
        return fsutil.ensure_private_dir(state_dir, create=True, fix=False)
    except fsutil.UnsafePathError as exc:
        raise UnsafePermissionsError(str(exc)) from exc


# The XDG-config-relative tail of ``service.env`` (".config/ao/service.env" -> "ao/service.env").
_SERVICE_ENV_XDG_TAIL = PurePosixPath(SERVICE_ENV_RELATIVE_PATH).relative_to(".config")
_XDG_CONFIG_HOME_ENV = "XDG_CONFIG_HOME"


def default_denied_paths(env: Mapping[str, str] | None = None) -> tuple[Path, ...]:
    """Paths the dashboard file browser must never expose (FR-10, FR-24; security L7).

    The XDG default store and state directories, the ``AO_AUTH_DIR`` / ``AO_AUTH_STATE_DIR``
    overrides when set, and the service ``EnvironmentFile`` (it holds API keys) under both
    ``~/.config`` and ``$XDG_CONFIG_HOME``. Resolved and de-duplicated, in a stable order.
    ``env=None`` reads ``os.environ`` at call time.
    """
    source = os.environ if env is None else env
    candidates = [xdg_default_store_dir(source), xdg_default_state_dir(source)]
    for name in (AO_AUTH_DIR_ENV, AO_AUTH_STATE_DIR_ENV):
        value = source.get(name)
        if value:
            candidates.append(Path(value).expanduser())
    candidates.append(Path.home() / SERVICE_ENV_RELATIVE_PATH)
    xdg_config_home = source.get(_XDG_CONFIG_HOME_ENV)
    if xdg_config_home:
        candidates.append(Path(xdg_config_home) / _SERVICE_ENV_XDG_TAIL)
    return tuple(dict.fromkeys(candidate.resolve() for candidate in candidates))


def entry_is_denied(parent_resolved: Path, entry: os.DirEntry[str], denied: Sequence[Path]) -> bool:
    """Whether a directory entry lies within any (already resolved) denied path.

    Only symlink entries are resolved (they may point anywhere); for every other entry the
    already-resolved parent plus the name is the resolved path, so a listing of N entries costs
    no ``Path.resolve`` calls (reviewer R-12). A symlink that cannot be resolved (a loop) is not
    denied: it cannot point into a denied path, and the listing shows it as a dangling row.
    """
    if not denied:
        return False
    if entry.is_symlink():
        try:
            candidate = Path(entry.path).resolve()
        except (OSError, RuntimeError):
            return False
        normalized = Path(os.path.normcase(str(candidate)))
    else:
        normalized = Path(os.path.normcase(str(parent_resolved / entry.name)))
    return any(_contains(normalized, Path(os.path.normcase(str(root)))) for root in denied)
