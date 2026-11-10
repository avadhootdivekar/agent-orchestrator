"""File-watch event trigger (T-Fw8Gp4, HLD §6.7): digest store, scanner, FileWatchSource.

Polling, not inotify. ``FileWatchSource`` is a per-binding :class:`TriggerSource`: ``observe`` is
pure and read-only (it never writes the digest) and ``commit`` persists the digest -- the engine
calls it only once the fire key is burned or launched, so a concurrency skip, ``ao schedule next``
or a dry tick can never swallow a pending change.

``Candidate.scheduled_for`` is the **newest changed mtime** (whole seconds), not the observation
time: the same file state derives the same ``fire_key``, so the at-most-once fire store makes a
crash between the fire and the digest save replay-safe. Do not "simplify" it to ``now``.

Path handling is security-relevant: every pattern is anchored at the workspace root resolved
through :class:`LocalFsArtifactStore`; ``..``/absolute patterns are rejected, symlinks that
resolve outside the root are skipped (with a warning), directory symlinks are never followed, the
run output tree and VCS/dependency dirs are ignored (no self-triggering loop), and the scan is
bounded by ``max_files`` and a wall-clock budget (a truncated scan never fires).
"""

from __future__ import annotations

import logging
import os
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import RootModel

from ..artifacts import LocalFsArtifactStore
from ..errors import ArtifactPathError
from ..schedules.models import ResolvedBinding, WatchSpec
from .fire_store import ScheduleState
from .schedule_engine import Candidate
from .statefile import read_json_model, write_json_model_atomic
from .supervisor import _slug_for_root

logger = logging.getLogger(__name__)

WATCH_SCAN_BUDGET_SECONDS = 0.25
DEFAULT_WATCH_MAX_FILES = 5000
# Directory names ignored at any depth; ``.ao/state`` is a relative-path prefix. ``.orchestrator``
# is the run output tree: watching it would let a run trigger itself (AC3).
DEFAULT_WATCH_IGNORES: frozenset[str] = frozenset(
    {".git", "node_modules", ".venv", "__pycache__", ".orchestrator", ".ao/state"}
)
# Hard cap on directory entries *visited* (matched or not), so a huge tree of non-matching files
# stays bounded even with a generous budget.
VISIT_LIMIT_FACTOR = 20
WATCH_DIGEST_SUBDIR = "schedules/watch"
EVENT_WATCH_CHANGED = "watch.changed"
EVENT_WATCH_BUDGET = "schedule.watch_budget_exceeded"

Digest = dict[str, list[int]]  # rel_path -> [mtime_ns, size]
Monotonic = Callable[[], float]
EventSink = Callable[[str, dict[str, Any]], None]


class _DigestFile(RootModel[Digest]):
    root: Digest = {}


# -- digest store --------------------------------------------------------------------------------


def _safe_segment(value: str, what: str) -> str:
    if not value or value in (".", "..") or "/" in value or "\\" in value or "\x00" in value:
        raise ValueError(f"unsafe {what} for a state path: {value!r}")
    return value


class WatchDigestStore:
    """``<state_dir>/schedules/watch/<slug>/<id>.json`` -> ``{rel_path: [mtime_ns, size]}``."""

    def __init__(self, state_dir: Path) -> None:
        self._root = Path(state_dir) / WATCH_DIGEST_SUBDIR

    def path_for(self, workspace_slug: str, schedule_id: str) -> Path:
        return (
            self._root
            / _safe_segment(workspace_slug, "workspace slug")
            / f"{_safe_segment(schedule_id, 'schedule id')}.json"
        )

    def load(self, workspace_slug: str, schedule_id: str) -> Digest:
        model = read_json_model(
            self.path_for(workspace_slug, schedule_id), _DigestFile, default_factory=_DigestFile
        )
        return {k: list(v) for k, v in model.root.items()}

    def save(self, workspace_slug: str, schedule_id: str, digest: Digest) -> None:
        write_json_model_atomic(
            self.path_for(workspace_slug, schedule_id), _DigestFile(root=digest)
        )


# -- scanner -------------------------------------------------------------------------------------


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a workspace-relative glob (``*``, ``?``, ``[..]``, ``**``) to a full-match regex.
    ``*``/``?`` never cross ``/``; a ``**`` segment matches zero or more directories."""
    out: list[str] = []
    parts = pattern.split("/")
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        if part == "**":
            out.append(".*" if last else "(?:[^/]+/)*")
            continue
        seg = ""
        j = 0
        while j < len(part):
            c = part[j]
            if c == "*":
                seg += "[^/]*"
            elif c == "?":
                seg += "[^/]"
            elif c == "[":
                end = part.find("]", j + 2)
                if end == -1:
                    seg += re.escape(c)
                else:
                    body = part[j + 1 : end]
                    if body.startswith("!"):
                        body = "^" + body[1:]
                    seg += "[" + body.replace("\\", "\\\\") + "]"
                    j = end
            else:
                seg += re.escape(c)
            j += 1
        out.append(seg + ("" if last else "/"))
    return re.compile("".join(out))


def _pattern_is_safe(pattern: str) -> bool:
    if not pattern or pattern.startswith(("/", "\\")) or os.path.isabs(pattern):
        return False
    if re.match(r"^[A-Za-z]:", pattern):
        return False
    return ".." not in PurePosixPath(pattern.replace("\\", "/")).parts


def _ignored(rel: str, ignores: frozenset[str]) -> bool:
    parts = rel.split("/")
    if any(p in ignores for p in parts):
        return True
    return any("/" in ig and (rel == ig or rel.startswith(ig + "/")) for ig in ignores)


def scan(
    workspace_root: str | Path,
    watch_spec: WatchSpec,
    *,
    max_files: int = DEFAULT_WATCH_MAX_FILES,
    budget_seconds: float = WATCH_SCAN_BUDGET_SECONDS,
    monotonic: Monotonic = time.monotonic,
    ignores: frozenset[str] = DEFAULT_WATCH_IGNORES,
) -> tuple[Digest, bool]:
    """Stat every file under *workspace_root* matching ``watch_spec.paths``.

    Returns ``(digest, truncated)``. ``truncated`` is True when ``max_files`` matches, the visit
    cap or ``budget_seconds`` (measured with *monotonic*) was exceeded; the digest is then
    partial and **must not drive a fire**. Reads metadata only, never file content or writes.
    """
    store = LocalFsArtifactStore(str(workspace_root))
    root = store.root
    regexes: list[re.Pattern[str]] = []
    for pattern in watch_spec.paths:
        if _pattern_is_safe(pattern):
            regexes.append(_glob_to_regex(pattern.replace("\\", "/")))
        else:
            logger.warning("file watch: pattern %r escapes the workspace root; skipped", pattern)
    digest: Digest = {}
    if not regexes:
        return digest, False

    started = monotonic()
    visit_limit = max(1, max_files) * VISIT_LIMIT_FACTOR
    visited = 0

    def over_budget() -> bool:
        return monotonic() - started > budget_seconds

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        keep: list[str] = []
        for d in sorted(dirnames):
            rel = f"{rel_dir}/{d}" if rel_dir else d
            if _ignored(rel, ignores):
                continue
            if os.path.islink(os.path.join(dirpath, d)):
                # Never descend a directory symlink (cycle/escape risk). One that points outside
                # the root is also worth a warning.
                try:
                    store.resolve(rel)
                except ArtifactPathError:
                    logger.warning(
                        "file watch: symlinked dir %r resolves outside root; skipped", rel
                    )
                continue
            keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            visited += 1
            if visited > visit_limit or over_budget():
                return digest, True
            rel = f"{rel_dir}/{name}" if rel_dir else name
            if _ignored(rel, ignores) or not any(rx.fullmatch(rel) for rx in regexes):
                continue
            full = os.path.join(dirpath, name)
            try:
                if os.path.islink(full):
                    resolved = store.resolve(rel)  # raises if the target is outside the root
                    if _ignored(os.path.relpath(resolved, root).replace(os.sep, "/"), ignores):
                        continue
                    st = os.stat(resolved)
                else:
                    st = os.stat(full)
            except ArtifactPathError:
                logger.warning("file watch: symlink %r resolves outside root; skipped", rel)
                continue
            except OSError:  # vanished or dangling between listing and stat
                continue
            if not os.path.isfile(full):
                continue
            if len(digest) >= max_files:
                return digest, True
            digest[rel] = [st.st_mtime_ns, st.st_size]
    return digest, False


# -- trigger source ------------------------------------------------------------------------------


def changed_paths(prev: Digest, cur: Digest, on: list[str]) -> list[str]:
    """Paths that are ``created`` (absent from *prev*) and/or ``modified`` (different
    ``(mtime_ns, size)``), per the events enabled in *on*."""
    want_created, want_modified = "created" in on, "modified" in on
    out: list[str] = []
    for p, sig in cur.items():
        old = prev.get(p)
        if old is None:
            if want_created:
                out.append(p)
        elif list(old) != list(sig) and want_modified:
            out.append(p)
    return sorted(out)


class FileWatchSource:
    """Per-binding file-watch :class:`TriggerSource` (HLD §6.7)."""

    def __init__(
        self,
        workspace_root: str | Path,
        spec: WatchSpec,
        store: WatchDigestStore,
        *,
        max_files: int = DEFAULT_WATCH_MAX_FILES,
        budget_seconds: float = WATCH_SCAN_BUDGET_SECONDS,
        monotonic: Monotonic = time.monotonic,
        emit: EventSink | None = None,
    ) -> None:
        self._root = str(Path(workspace_root).resolve())
        self._slug = _slug_for_root(self._root)
        self._spec = spec
        self._store = store
        self._max_files = max_files
        self._budget = budget_seconds
        self._monotonic = monotonic
        self._emit = emit

    def _event(self, name: str, payload: dict[str, Any]) -> None:
        logger.info("%s %s", name, payload)
        if self._emit is not None:
            try:
                self._emit(name, payload)
            except Exception:  # noqa: BLE001 - a sink must never break evaluation
                logger.warning("watch event sink failed for %s", name, exc_info=True)

    def observe(
        self, binding: ResolvedBinding, state: ScheduleState, now: datetime
    ) -> Candidate | None:
        prev = self._store.load(self._slug, binding.id)
        cur, truncated = scan(
            self._root,
            self._spec,
            max_files=self._max_files,
            budget_seconds=self._budget,
            monotonic=self._monotonic,
        )
        if truncated:  # never a partial-scan fire
            self._event(
                EVENT_WATCH_BUDGET,
                {"schedule_id": binding.id, "workspace_root": self._root, "files": len(cur)},
            )
            return None
        changed = changed_paths(prev, cur, list(self._spec.on))
        if not changed:
            return None
        newest = datetime.fromtimestamp(max(cur[p][0] for p in changed) / 1e9, tz=UTC)
        if (now - newest).total_seconds() < self._spec.debounce_seconds:
            return None  # still settling; digest untouched so nothing is swallowed
        self._event(
            EVENT_WATCH_CHANGED,
            {"schedule_id": binding.id, "workspace_root": self._root, "paths": changed[:20]},
        )
        return Candidate(scheduled_for=newest.replace(microsecond=0), payload=cur)

    def commit(self, binding: ResolvedBinding, candidate: Candidate) -> None:
        self._store.save(self._slug, binding.id, dict(candidate.payload))


__all__ = [
    "DEFAULT_WATCH_IGNORES",
    "WATCH_SCAN_BUDGET_SECONDS",
    "FileWatchSource",
    "WatchDigestStore",
    "changed_paths",
    "scan",
]
