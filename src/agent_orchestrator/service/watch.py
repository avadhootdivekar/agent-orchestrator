"""File-watch event trigger (T-Fw8Gp4, HLD §6.7): digest store, scanner, FileWatchSource.

Polling, not inotify. ``FileWatchSource`` is a per-binding :class:`TriggerSource`: ``observe``
never consumes a fireable change -- its only write is a baseline seed when nothing *wanted*
changed (see H1 below) -- and ``commit`` persists the digest of a fired change; the engine calls
it only once the fire key is burned or launched, so a concurrency skip, ``ao schedule next``
or a dry tick can never swallow a pending change.

``Candidate.scheduled_for`` is the **newest changed mtime** (whole seconds), not the observation
time: the same file state derives the same ``fire_key``, so the at-most-once fire store makes a
crash between the fire and the digest save replay-safe. Do not "simplify" it to ``now``.

Path handling is security-relevant: every pattern is anchored at the workspace root resolved
through :class:`LocalFsArtifactStore`; ``..``/absolute patterns are rejected, symlinks that
resolve outside the root are skipped (with a warning), directory symlinks are never followed --
including a symlink anywhere in a pattern's static prefix (N1) -- the
run output tree and VCS/dependency dirs are ignored (no self-triggering loop), and the scan is
bounded by ``max_files``, a visit cap and a wall-clock budget checked per directory entry (a
truncated scan never fires). The walk is pruned to directories that can still match a pattern.

Review fixes (w13-03): ``observe`` seeds/refreshes the baseline digest when *nothing wanted*
changed (the only write it makes -- nothing can be swallowed, so ``on: [modified]`` works);
a future-mtime file never delays the fire of other changes (N3); the candidate carries a content
``discriminator`` (fire key) and ``skip_catch_up`` (an observed
change is never "late" because its mtime is old); ``poll_seconds`` gates rescans.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import stat
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
from .supervisor import slug_for_root

logger = logging.getLogger(__name__)

# Per-tick scan cap (250 ms): a huge tree must not stall the scheduler loop (overrun is reported).
WATCH_SCAN_BUDGET_SECONDS = 0.25
# Files stat'ed per watch scan; keeps cost bounded on large trees (override per binding).
DEFAULT_WATCH_MAX_FILES = 5000
# Directory names ignored at any depth; ``.ao/state`` is a relative-path prefix. ``.orchestrator``
# is the run output tree: watching it would let a run trigger itself (AC3).
DEFAULT_WATCH_IGNORES: frozenset[str] = frozenset(
    {".git", "node_modules", ".venv", "__pycache__", ".orchestrator", ".ao/state"}
)
# Hard cap on directory entries *visited* (matched or not), so a huge tree of non-matching files
# stays bounded even with a generous budget.
VISIT_LIMIT_FACTOR = 20
# Template instance trees (``workflows/<template>/runs/<id>/``): a template-launched schedule
# writes ``prompt.md``/outputs there, so watching them is a self-trigger loop (H2). Matched by
# shape: ``workflows/*/runs`` at the workspace root.
INSTANCE_TREE_ROOT = "workflows"
INSTANCE_TREE_RUNS = "runs"
WATCH_DIGEST_SUBDIR = "schedules/watch"
EVENT_WATCH_CHANGED = "watch.changed"
EVENT_WATCH_BUDGET = "schedule.watch_budget_exceeded"
EVENT_WATCH_FUTURE_MTIME = "schedule.watch_future_mtime"
# Changed paths listed in one watch event; the rest are summarised by count (bounds event size).
MAX_EVENT_PATHS = 20

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

    def exists(self, workspace_slug: str, schedule_id: str) -> bool:
        return self.path_for(workspace_slug, schedule_id).is_file()

    def save(self, workspace_slug: str, schedule_id: str, digest: Digest) -> None:
        write_json_model_atomic(
            self.path_for(workspace_slug, schedule_id), _DigestFile(root=digest)
        )


# -- scanner -------------------------------------------------------------------------------------

GLOB_CHARS = "*?["
_Segments = list["re.Pattern[str] | None"]  # one regex per path segment; None = ``**``


def _segment_regex(part: str) -> str:
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
                negate = body.startswith(("!", "^"))  # a leading ``^`` is negation too (L5)
                if negate:
                    body = body[1:]
                body = body.replace("\\", "\\\\")
                # A negated class must never match ``/`` or a segment could cross directories.
                seg += "[^/" + body + "]" if negate else "[" + body + "]"
                j = end
        else:
            seg += re.escape(c)
        j += 1
    return seg


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
        out.append(_segment_regex(part) + ("" if last else "/"))
    return re.compile("".join(out))


def _segments(pattern: str) -> _Segments:
    return [None if p == "**" else re.compile(_segment_regex(p)) for p in pattern.split("/")]


def _static_prefix(pattern: str) -> str:
    """Leading glob-free directory segments of *pattern* (``epics/*/prompt.md`` -> ``epics``)."""
    out: list[str] = []
    for part in pattern.split("/")[:-1]:
        if any(c in part for c in GLOB_CHARS):
            break
        out.append(part)
    return "/".join(out)


def _can_descend(rel_dir: str, patterns: list[_Segments]) -> bool:
    """True if some pattern could still match a file below *rel_dir* (prunes the walk, M3)."""
    parts = rel_dir.split("/") if rel_dir else []
    for segs in patterns:
        viable = True
        for i, part in enumerate(parts):
            if i >= len(segs):
                viable = False
                break
            seg = segs[i]
            if seg is None:  # ``**``: any depth below can still match
                return True
            if i == len(segs) - 1 or not seg.fullmatch(part):  # last segment is the file
                viable = False
                break
        if viable:
            return True
    return False


def pattern_under_ignored_tree(
    pattern: str, ignores: frozenset[str] = DEFAULT_WATCH_IGNORES
) -> bool:
    """True if *pattern* can only match inside an ignored tree (it would validate and never
    fire, L-d). Only literal directory segments are judged; the instance-tree shape is
    ``workflows/<anything>/runs``."""
    dirs = pattern.replace("\\", "/").split("/")[:-1]
    for i, part in enumerate(dirs):
        if not any(c in part for c in GLOB_CHARS) and part in ignores:
            return True
    if len(dirs) >= 3 and dirs[0] == INSTANCE_TREE_ROOT and dirs[2] == INSTANCE_TREE_RUNS:
        return True
    prefix = _static_prefix(pattern.replace("\\", "/"))
    return bool(prefix) and any(
        "/" in ig and (prefix == ig or prefix.startswith(ig + "/")) for ig in ignores
    )


def _has_symlink_component(root: str, rel_dir: str) -> bool:
    """True if any component of *rel_dir* below *root* is a symlink (N1, via ``lstat``)."""
    cur = root
    for part in rel_dir.split("/"):
        cur = os.path.join(cur, part)
        try:
            if stat.S_ISLNK(os.lstat(cur).st_mode):
                return True
        except OSError:  # missing/unreadable: the scandir below skips it as before
            return False
    return False


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
    if len(parts) >= 3 and parts[0] == INSTANCE_TREE_ROOT and parts[2] == INSTANCE_TREE_RUNS:
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
    cap or ``budget_seconds`` (measured with *monotonic*, checked per directory entry) was
    exceeded; the digest is then partial and **must not drive a fire**. Only directories that can
    still match a pattern are walked. Reads metadata only, never file content or writes.
    """
    store = LocalFsArtifactStore(str(workspace_root))
    root = store.root
    regexes: list[re.Pattern[str]] = []
    segs: list[_Segments] = []
    starts: set[str] = set()
    for pattern in watch_spec.paths:
        if _pattern_is_safe(pattern):
            norm = pattern.replace("\\", "/")
            regexes.append(_glob_to_regex(norm))
            segs.append(_segments(norm))
            starts.add(_static_prefix(norm))
        else:
            logger.warning("file watch: pattern %r escapes the workspace root; skipped", pattern)
    digest: Digest = {}
    if not regexes:
        return digest, False
    # Walk each distinct static prefix once ("" subsumes every other start).
    start_dirs = (
        [""]
        if "" in starts
        else sorted(p for p in starts if not any(p.startswith(o + "/") for o in starts if o != p))
    )

    started = monotonic()
    visit_limit = max(1, max_files) * VISIT_LIMIT_FACTOR
    visited = 0
    stack = [d for d in reversed(start_dirs)]
    while stack:
        rel_dir = stack.pop()
        if rel_dir and _ignored(rel_dir, ignores):
            continue
        if rel_dir and _has_symlink_component(root, rel_dir):
            # A symlinked prefix would walk out of the root / into an ignored tree (N1).
            logger.warning("file watch: pattern prefix %r contains a symlink; skipped", rel_dir)
            continue
        dirpath = os.path.join(root, rel_dir) if rel_dir else root
        try:
            it = os.scandir(dirpath)
        except OSError:  # missing prefix dir / vanished / unreadable
            continue
        with it:
            for entry in it:
                visited += 1
                if visited > visit_limit or monotonic() - started > budget_seconds:
                    return digest, True
                name = entry.name
                rel = f"{rel_dir}/{name}" if rel_dir else name
                if _ignored(rel, ignores):
                    continue
                try:
                    is_link = entry.is_symlink()
                    if is_link and entry.is_dir():
                        # Never descend a directory symlink (cycle/escape risk); one that points
                        # outside the root is also worth a warning.
                        try:
                            store.resolve(rel)
                        except ArtifactPathError:
                            logger.warning(
                                "file watch: symlinked dir %r resolves outside root; skipped", rel
                            )
                        continue
                    if not is_link and entry.is_dir(follow_symlinks=False):
                        if _can_descend(rel, segs):
                            stack.append(rel)
                        continue
                    if not any(rx.fullmatch(rel) for rx in regexes):
                        continue
                    if is_link:
                        resolved = store.resolve(rel)  # raises if the target is outside the root
                        if _ignored(os.path.relpath(resolved, root).replace(os.sep, "/"), ignores):
                            continue
                        st = os.stat(resolved)
                    else:
                        st = os.lstat(entry.path)  # no link following: no swap-in TOCTOU (L4)
                except ArtifactPathError:
                    logger.warning("file watch: symlink %r resolves outside root; skipped", rel)
                    continue
                except OSError:  # vanished or dangling between listing and stat
                    continue
                if not stat.S_ISREG(st.st_mode):
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


def change_discriminator(prev: Digest, cur: Digest, changed: list[str]) -> str:
    """Short content-derived token for one observed change: ``(path, previous sig, new sig)`` of
    every changed path. Deterministic for a crash replay (same prev, same cur), but a different
    state -- or the *same* state reached from a different predecessor, e.g. a ``cp -p`` revert --
    gets a different fire key (M2)."""
    material = json.dumps([[p, prev.get(p), cur[p]] for p in changed], sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


class FileWatchSource:
    """Per-binding file-watch :class:`TriggerSource` (HLD §6.7)."""

    def __init__(
        self,
        workspace_root: str | Path,
        spec: WatchSpec,
        store: WatchDigestStore,
        *,
        max_files: int | None = None,
        budget_seconds: float = WATCH_SCAN_BUDGET_SECONDS,
        monotonic: Monotonic = time.monotonic,
        emit: EventSink | None = None,
    ) -> None:
        self._root = str(Path(workspace_root).resolve())
        self._slug = slug_for_root(self._root)
        self._spec = spec
        self._store = store
        self._max_files = max_files if max_files is not None else spec.max_files
        self._budget = budget_seconds
        self._monotonic = monotonic
        self._emit = emit
        self._next_scan_at: float | None = None  # in-memory poll gate (M4)
        self._budget_warned = False
        self._future_warned = False
        for pattern in spec.paths:
            if pattern_under_ignored_tree(pattern):
                logger.warning(
                    "file watch: pattern %r is under an ignored tree and will never fire", pattern
                )

    def _event(self, name: str, payload: dict[str, Any]) -> None:
        if self._emit is None:
            logger.info("%s %s", name, payload)
            return
        try:
            self._emit(name, payload)
        except Exception:  # noqa: BLE001 - a sink must never break evaluation
            logger.warning("watch event sink failed for %s", name, exc_info=True)

    def _gate(self, pending: bool) -> None:
        """Arm the poll gate unless a change is pending (then rescan on the next tick)."""
        self._next_scan_at = None if pending else self._monotonic() + self._spec.poll_seconds

    def observe(
        self, binding: ResolvedBinding, state: ScheduleState, now: datetime
    ) -> Candidate | None:
        if self._next_scan_at is not None and self._monotonic() < self._next_scan_at:
            return None
        known = self._store.exists(self._slug, binding.id)
        prev = self._store.load(self._slug, binding.id)
        cur, truncated = scan(
            self._root,
            self._spec,
            max_files=self._max_files,
            budget_seconds=self._budget,
            monotonic=self._monotonic,
        )
        if truncated:  # never a partial-scan fire
            if not self._budget_warned:  # once per episode, not every tick (L3)
                self._budget_warned = True
                self._event(
                    EVENT_WATCH_BUDGET,
                    {"schedule_id": binding.id, "workspace_root": self._root, "files": len(cur)},
                )
            self._gate(pending=False)
            return None
        self._budget_warned = False
        changed = changed_paths(prev, cur, list(self._spec.on))
        if not changed:
            # Nothing *wanted* changed, so nothing can be swallowed: seed/refresh the baseline so
            # e.g. ``on: [modified]`` sees a later edit as a modification, not a creation (H1).
            if cur != prev or not known:
                self._store.save(self._slug, binding.id, cur)
            self._gate(pending=False)
            return None
        now_s = int(now.timestamp())
        # Whole seconds, integer (L2). A path whose mtime is in the future (clock skew) must not
        # hold back the other changes, so it is excluded from the settle time (N3). Not
        # clamped to ``now``: that would make ``scheduled_for`` (the fire key) non-deterministic.
        stamps = [cur[p][0] // 10**9 for p in changed]
        settled = [t for t in stamps if t <= now_s]
        if len(settled) < len(stamps):
            if not self._future_warned:  # once per episode
                self._future_warned = True
                self._event(
                    EVENT_WATCH_FUTURE_MTIME,
                    {
                        "schedule_id": binding.id,
                        "workspace_root": self._root,
                        "paths": [p for p, t in zip(changed, stamps, strict=True) if t > now_s][
                            :MAX_EVENT_PATHS
                        ],
                    },
                )
        else:
            self._future_warned = False
        if not settled:
            self._gate(pending=True)
            return None  # only future-dated changes: wait until their mtime has passed
        newest = datetime.fromtimestamp(max(settled), tz=UTC)
        if (now - newest).total_seconds() < self._spec.debounce_seconds:
            self._gate(pending=True)
            return None  # still settling; digest untouched so nothing is swallowed
        self._gate(pending=True)
        return Candidate(
            scheduled_for=newest,
            payload=(cur, changed),
            discriminator=change_discriminator(prev, cur, changed),
            skip_catch_up=True,
        )

    def commit(self, binding: ResolvedBinding, candidate: Candidate) -> None:
        cur, changed = candidate.payload
        self._store.save(self._slug, binding.id, dict(cur))
        self._next_scan_at = None
        self._event(
            EVENT_WATCH_CHANGED,
            {
                "schedule_id": binding.id,
                "workspace_root": self._root,
                "paths": changed[:MAX_EVENT_PATHS],
            },
        )


def file_watch_factory(
    state_dir: Path, emit: EventSink | None = None, monotonic: Monotonic = time.monotonic
) -> Callable[[str, ResolvedBinding], FileWatchSource]:
    """Production ``TriggerSourceFactory`` for ``kind: file_watch`` bindings."""
    store = WatchDigestStore(state_dir)

    def factory(root: str, binding: ResolvedBinding) -> FileWatchSource:
        assert binding.watch is not None, "file_watch binding without a watch spec"
        return FileWatchSource(root, binding.watch, store, monotonic=monotonic, emit=emit)

    return factory


__all__ = [
    "DEFAULT_WATCH_IGNORES",
    "WATCH_SCAN_BUDGET_SECONDS",
    "FileWatchSource",
    "WatchDigestStore",
    "change_discriminator",
    "changed_paths",
    "file_watch_factory",
    "pattern_under_ignored_tree",
    "scan",
]
