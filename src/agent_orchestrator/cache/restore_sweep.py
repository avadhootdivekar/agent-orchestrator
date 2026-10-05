"""Stale restore-staging sweep for `ao cache prune` (E-Rc4Hk8, T-6tRKml; G1a SEC-19, G1b S-1).

A restore stages each output next to its destination (`.ao-result-cache-*.tmp`, plus a `.bak`
hard link of the file it replaces). A crash between staging and commit leaves them behind, in the
user's OUTPUT directories, not in the 0700 cache root, so `store.prune` (which only sweeps
`cache/tmp/`) never sees them. This module is the one place that removes them, and only under a
deliberately narrow definition of "safe":

* WHERE: the parent directory of each output of a VALID cache entry, and only a directly
  contained child of it. The paths come from an entry (hostile data, comparison-only in every
  other module), so each is re-validated here as a plain, normalized, workspace-relative path
  that is not a protected location, and every existing component down to the directory is
  checked to be a real directory (no symlinks) with `safeio.check_dir_chain`. The directory is
  then opened `O_NOFOLLOW` and lstat / unlink go through that descriptor.
* WHAT: a REGULAR file (never a symlink, directory or FIFO) whose name is exactly a restore
  staging name (`safeio.is_restore_tmp_name`, the same predicate directory hashing uses to
  ignore these files), owned by the current user, whose ctime is older than the sweep grace (a
  live restore in another process is never younger than that). ctime, not mtime: a `.bak` hard
  link shares the old file's inode, so its mtime can be arbitrarily old.
* NEVER: a `.bak` that is the only remaining name of its inode. After a crash that happened
  past the commit rename, that file is the sole copy of the content the restore replaced; it is
  reported as kept, not deleted.

A directory that only an evicted or never-stored entry mentioned is not visited (the sweep has
no other source of "declared output directories" than the entries still in the store when
`prune` starts); that residual is documented in ADR-0019.
"""

from __future__ import annotations

import os
import posixpath
import stat
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from agent_orchestrator.cache import safeio
from agent_orchestrator.cache.constants import (
    CLI_MAX_DIR_ITEMS,
    RESTORE_BACKUP_SUFFIX,
    RESTORE_SWEEP_MAX_DIRS,
    TMP_SWEEP_GRACE_SECONDS,
)
from agent_orchestrator.cache.types import CacheEntry

_POSIX_SEP = "/"


@dataclass(frozen=True)
class RestoreSweepReport:
    """`removed` counts what was deleted (what WOULD be on a dry run); `kept_backups` the sole
    copies left alone; `truncated` is True when a bound stopped the collection or a listing."""

    removed: int = 0
    kept_backups: int = 0
    dirs_scanned: int = 0
    truncated: bool = False


def safe_parent_dir(rel_path: str) -> str | None:
    """The parent directory ("" = the workspace root) of a stored output path, or None when the
    path is not a plain normalized relative path or names a protected location."""
    if not rel_path or safeio.strip_control_chars(rel_path) != rel_path or "\\" in rel_path:
        return None
    if rel_path.startswith(_POSIX_SEP) or posixpath.normpath(rel_path) != rel_path:
        return None
    if ".." in rel_path.split(_POSIX_SEP) or safeio.is_sensitive_rel_path(rel_path):
        return None
    return posixpath.dirname(rel_path)


def collect_output_dirs(entries: Iterable[CacheEntry]) -> tuple[list[str], bool]:
    """Sorted, de-duplicated parent directories of every output of `entries`, and whether the
    `RESTORE_SWEEP_MAX_DIRS` bound cut the collection short."""
    dirs: set[str] = set()
    for entry in entries:
        for output in entry.outputs:
            parent = safe_parent_dir(output.path)
            if parent is None:
                continue
            if parent not in dirs and len(dirs) >= RESTORE_SWEEP_MAX_DIRS:
                return sorted(dirs), True
            dirs.add(parent)
    return sorted(dirs), False


def _owned_by_us(st: os.stat_result) -> bool:
    geteuid = getattr(os, "geteuid", None)
    return geteuid is None or st.st_uid == geteuid()


def _sweep_dir(fd: int, *, cutoff: float, dry_run: bool) -> tuple[int, int, bool]:
    """(removed, kept backups, truncated) for the directory open as `fd`."""
    names: list[str] = []
    truncated = False
    with os.scandir(fd) as listing:
        for count, item in enumerate(listing, start=1):
            if count > CLI_MAX_DIR_ITEMS:
                truncated = True
                break
            if safeio.is_restore_tmp_name(item.name):
                names.append(item.name)
    removed = kept = 0
    for name in sorted(names):
        try:
            st = os.lstat(name, dir_fd=fd)
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(st.st_mode) or not _owned_by_us(st) or st.st_ctime >= cutoff:
            continue
        if name.endswith(RESTORE_BACKUP_SUFFIX) and st.st_nlink < 2:
            kept += 1  # the only name left for replaced content: never deleted
            continue
        if not dry_run:
            try:
                os.unlink(name, dir_fd=fd)
            except FileNotFoundError:
                continue
        removed += 1
    return removed, kept, truncated


def sweep_restore_leftovers(
    workspace_root: str, rel_dirs: Iterable[str], *, now: datetime, dry_run: bool
) -> RestoreSweepReport:
    """Remove stale restore staging files from `rel_dirs` (see the module docstring).

    A directory that is missing, a symlink, not a directory or unreadable is skipped, never an
    error: a prune must not fail because an output directory was deleted or replaced.
    """
    cutoff = now.timestamp() - TMP_SWEEP_GRACE_SECONDS
    real_ws = os.path.realpath(workspace_root)
    removed = kept = scanned = 0
    truncated = False
    for rel in rel_dirs:
        directory = os.path.join(real_ws, *rel.split(_POSIX_SEP)) if rel else real_ws
        try:
            safeio.check_dir_chain(real_ws, directory)
            fd = safeio.open_dir_fd(directory)
        except (safeio.UnsafePathError, OSError):
            continue
        try:
            done, held, cut = _sweep_dir(fd, cutoff=cutoff, dry_run=dry_run)
        except OSError:
            continue
        finally:
            os.close(fd)
        scanned += 1
        removed += done
        kept += held
        truncated = truncated or cut
    return RestoreSweepReport(
        removed=removed, kept_backups=kept, dirs_scanned=scanned, truncated=truncated
    )
