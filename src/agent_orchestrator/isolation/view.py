"""`IsolatedArtifactView` -- the per-task, producer-restricted artifact path guard
(E-Wk9Tz3 T-Wk3Nv6, HLD §7.3, review finding S-4).

Normative shape (HLD §7.3): a per-task WRAPPER around the engine's single shared
`LocalFsArtifactStore`, never a widened shared store. Widening `LocalFsArtifactStore`
itself (e.g. via a constructor parameter) would silently widen `RunStateStore`'s own path
resolution too, since one store instance is shared between them -- so the widening lives
here instead, as a distinct `ArtifactStore` implementation.

S-4, the security property this module exists to hold: a view is built from exactly ONE
task's `TaskIsolation` -- never from a `WorktreeManager`'s registry of every worktree it has
created for the run. Built from the registry, task A could declare an absolute input/
output/`cwd` path under task B's worktree and read (or write into, laundering content
through) B's uncommitted work. See `tests/isolation/test_view.py` for the task-A/task-B
test this guards against.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from ..artifacts import ArtifactStore, LocalFsArtifactStore, _exists_via_resolve, _size_via_resolve
from ..errors import ArtifactPathError
from .paths import effective_path

if TYPE_CHECKING:
    from .worktrees import TaskIsolation


class IsolatedArtifactView(ArtifactStore):
    """Per-task view over the engine's shared `LocalFsArtifactStore`.

    `resolve()` accepts a path in one of two cases only:
    1. It resolves (abspath + symlink resolution) inside the shared workspace root -- in
       which case it is passed through `effective_path` so it remaps into this task's own
       worktree exactly as HLD §7.2 specifies (or stays shared, for `.orchestrator`/`.ao`).
    2. It resolves to an ALREADY-ABSOLUTE path under one of THIS task's own worktree roots
       (`task_isolation.repos[*].worktree_root`) -- e.g. a `repo_paths` entry the engine
       constructed directly from this same `TaskIsolation`.

    Anything else -- including an absolute path under a *different* task's or a *different
    run's* worktree -- raises `ArtifactPathError`, exactly like the base store's traversal
    guard. Traversal (`../..`) and symlink escapes are rejected the same way: resolution
    (abspath + symlink following) happens before either containment test.
    """

    def __init__(self, base: LocalFsArtifactStore, task_isolation: TaskIsolation) -> None:
        self._base = base
        self._iso = task_isolation
        # Exactly this task's own worktree roots -- never the WorktreeManager's full
        # registry (S-4). Sorted for deterministic iteration only; not security-relevant.
        self._roots: tuple[str, ...] = tuple(
            sorted({os.path.normpath(r.worktree_root) for r in task_isolation.repos})
        )

    def resolve(self, path: str) -> str:
        full = self._base.resolve_unchecked(path)
        base_root = self._base.root
        if full == base_root or full.startswith(base_root + os.sep):
            return effective_path(full, self._iso)
        for root in self._roots:
            if full == root or full.startswith(root + os.sep):
                return full
        raise ArtifactPathError(path)

    def exists(self, path: str) -> bool:
        return _exists_via_resolve(self, path)

    def size(self, path: str) -> int:
        return _size_via_resolve(self, path)
