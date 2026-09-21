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
from .paths import effective_path, worktree_store_root

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

    M-3 (review, 2026-09-07): case 1's "inside the workspace root" test is NOT sufficient on
    its own when worktree storage itself lies inside the workspace -- a supported layout (an
    operator keeping one tree; `$AO_STATE_DIR` under the workspace, which is also what the
    engine's own tests do). In that layout a sibling task's worktree IS an ordinary
    in-workspace path: `effective_path` does not recognise it as one of *this* task's repo
    toplevels, so it returned the path unchanged and task A could read and write task B's
    uncommitted work, both absolutely and via a workspace-relative path. The whole worktree
    store is therefore a reserved, non-resolvable region here -- the same treatment
    `paths.RESERVED_SHARED_PREFIXES` gets, in the opposite direction -- with this task's own
    worktree roots as the sole exception. Keying the exclusion off the STORE root rather than
    one run's prefix also covers a *different run's* worktrees.
    """

    def __init__(self, base: LocalFsArtifactStore, task_isolation: TaskIsolation) -> None:
        self._base = base
        self._iso = task_isolation
        # Exactly this task's own worktree roots -- never the WorktreeManager's full
        # registry (S-4). Sorted for deterministic iteration only; not security-relevant.
        self._roots: tuple[str, ...] = tuple(
            sorted({os.path.normpath(r.worktree_root) for r in task_isolation.repos})
        )
        # M-3: read fresh at construction, matching `paths`' own read-at-call-time
        # convention. A view outlives no env change within a run: the same process resolved
        # this task's worktrees through the same variable moments earlier.
        self._store_root = os.path.normpath(str(worktree_store_root()))

    @staticmethod
    def _within(full: str, root: str) -> bool:
        """Path-segment containment (never a bare string prefix, so `/ws-worktrees` is not
        judged to be inside `/ws`)."""
        return full == root or full.startswith(root + os.sep)

    def resolve(self, path: str) -> str:
        full = self._base.resolve_unchecked(path)
        # This task's OWN worktrees first: they are always allowed, and checking them ahead
        # of the store-root exclusion is what makes that exclusion safe to apply at all.
        for root in self._roots:
            if self._within(full, root):
                return full
        # M-3: any other path inside the worktree store -- a sibling task's worktree, another
        # run's, or the bookkeeping directories between them -- is out of bounds, whether or
        # not the store happens to sit inside the workspace root.
        if self._within(full, self._store_root):
            raise ArtifactPathError(path)
        base_root = self._base.root
        if self._within(full, base_root):
            return effective_path(full, self._iso)
        raise ArtifactPathError(path)

    def exists(self, path: str) -> bool:
        return _exists_via_resolve(self, path)

    def size(self, path: str) -> int:
        return _size_via_resolve(self, path)
