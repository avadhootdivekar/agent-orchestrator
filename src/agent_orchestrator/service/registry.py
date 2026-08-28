"""Service registry: the list of workspaces `ao service` supervises (HLD §5.1).

The registry file (``~/.config/ao/service.yaml`` by default, see ``paths.py``) is the single
source of truth for which workspaces the service serves -- registration is explicit via
``ao service add``/``remove`` (CLI itself lands in a later task; this module is what the CLI,
and the supervisor's own port-persistence step, both call into).

Locking (AC7 / ADR-0012 early-gate correction #2)
--------------------------------------------------
Atomic write-then-rename (``save()``, mirroring ``runstate.py``'s ``tmp`` file + ``os.replace``
pattern) prevents a crash mid-write from corrupting the file, but it does NOT prevent a lost
update between two concurrent writers -- e.g. an operator's ``ao service add`` racing the
supervisor's own port-persistence step at boot. A bare ``load()`` then ``save()`` sequence is
therefore unsafe for any read-modify-write call site.

``ServiceRegistry.mutate()`` is the safe primitive: it acquires an exclusive ``flock`` on a
companion ``.lock`` file, re-reads the CURRENT on-disk registry under that lock (never a
caller-cached copy), applies the caller's pure function, saves, and releases. The lock lives
in its own file (not the registry file itself) because the registry file is *replaced*
(``os.replace``) on every save -- flock-ing a path whose underlying inode can be swapped out
from under a waiter is not a reliable mutual-exclusion primitive, whereas a dedicated lock
file's inode never changes across registry saves.

Every registry-mutating call site (this module's own ``add``/``remove``, and the later
supervisor's port-persistence step) MUST go through ``mutate()``.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Callable
from pathlib import Path

import yaml
from pydantic import BaseModel

from .paths import default_registry_path


class WorkspaceEntry(BaseModel):
    """One registered workspace (HLD §5.1)."""

    root: str
    """Absolute, resolved path -- the single source of truth for workspace identity.
    Always normalized on save/add; never trust a caller-relative path downstream."""

    port: int | None = None
    """Pinned port (P2), or `None` to let port resolution assign one (P3) and persist it
    back here so the URL is stable across restarts."""

    host: str | None = None
    """Pinned bind host (P2), or `None` to defer to the workspace's own `ui.host` (P1) or
    the loopback default. A non-loopback value exposes the UNAUTHENTICATED dashboard --
    deliberate, warned-about choice (same posture as `ao ui --host`)."""

    autoresume: bool = True
    """Whether boot-resume should consider this workspace's orphaned runs."""


class ServiceRegistryFile(BaseModel):
    """The full on-disk shape of the registry file (HLD §5.1)."""

    workspaces: list[WorkspaceEntry] = []


class ServiceRegistry:
    """Wraps a registry file path with atomic, file-locked load/save/mutate."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or default_registry_path()

    @property
    def path(self) -> Path:
        return self._path

    def _lock_path(self) -> Path:
        # A dedicated lock file, deliberately never the payload -- see module docstring.
        return self._path.with_name(self._path.name + ".lock")

    def load(self) -> ServiceRegistryFile:
        """Load the registry. A missing file is an empty registry, not an error."""
        if not self._path.exists():
            return ServiceRegistryFile()
        raw = self._path.read_text()
        data = yaml.safe_load(raw) or {}
        return ServiceRegistryFile.model_validate(data)

    def save(self, data: ServiceRegistryFile) -> None:
        """Atomically persist *data* (write-then-rename), normalizing every ``root`` to an
        absolute, resolved path first.

        This is a plain, unlocked write -- safe for a single-writer context (e.g. a test
        asserting round-trip) but NOT safe as the write half of a read-modify-write sequence
        under concurrency. Use ``mutate()`` for that.
        """
        normalized = ServiceRegistryFile(
            workspaces=[
                entry.model_copy(update={"root": str(Path(entry.root).resolve())})
                for entry in data.workspaces
            ]
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(yaml.safe_dump(normalized.model_dump(mode="json"), sort_keys=False))
        os.replace(tmp, self._path)

    def mutate(
        self, fn: Callable[[ServiceRegistryFile], ServiceRegistryFile]
    ) -> ServiceRegistryFile:
        """Read-lock-merge-write: apply *fn* to the CURRENT on-disk registry under an
        exclusive file lock, save the result, and return it.

        This is the only safe way for a caller to read-then-write the registry when another
        process might be mutating it concurrently (AC7). *fn* must be pure (no reliance on a
        registry snapshot read before ``mutate`` was called) since it is handed the freshly
        re-read state, not whatever the caller last saw.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self._lock_path()
        with open(lock_path, "a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                current = self.load()
                updated = fn(current)
                self.save(updated)
                return updated
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def add(
        self,
        root: str,
        port: int | None = None,
        autoresume: bool = True,
        host: str | None = None,
    ) -> ServiceRegistryFile:
        """Register (or re-register, replacing the prior entry) a workspace, via ``mutate``."""
        resolved_root = str(Path(root).resolve())

        def _add(data: ServiceRegistryFile) -> ServiceRegistryFile:
            remaining = [w for w in data.workspaces if w.root != resolved_root]
            remaining.append(
                WorkspaceEntry(root=resolved_root, port=port, autoresume=autoresume, host=host)
            )
            return ServiceRegistryFile(workspaces=remaining)

        return self.mutate(_add)

    def remove(self, root: str) -> ServiceRegistryFile:
        """Deregister a workspace (no-op if not present), via ``mutate``."""
        resolved_root = str(Path(root).resolve())

        def _remove(data: ServiceRegistryFile) -> ServiceRegistryFile:
            return ServiceRegistryFile(
                workspaces=[w for w in data.workspaces if w.root != resolved_root]
            )

        return self.mutate(_remove)
