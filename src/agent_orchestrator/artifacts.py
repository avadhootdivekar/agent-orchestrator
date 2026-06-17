"""Artifact store abstraction — path resolution and existence checks."""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path

from .errors import ArtifactPathError


class ArtifactStore(ABC):
    """Abstract interface for artifact path resolution and existence checking.

    Implementations must never read file contents (NFR-1 invariant).
    """

    @abstractmethod
    def resolve(self, path: str) -> str:
        """Return the absolute, workspace-rooted path for *path*.

        Raises ArtifactPathError if the resolved path escapes the workspace root.
        """
        ...

    @abstractmethod
    def exists(self, path: str) -> bool:
        """Return True if the artifact at *path* exists on disk."""
        ...


class LocalFsArtifactStore(ArtifactStore):
    """Artifact store backed by the local filesystem.

    All relative paths are resolved against *workspace_root*. Absolute paths
    that fall outside the workspace root are rejected with ArtifactPathError.
    """

    def __init__(self, workspace_root: str) -> None:
        self._root = str(Path(workspace_root).resolve())

    def resolve(self, path: str) -> str:
        if os.path.isabs(path):
            full = str(Path(path).resolve())
        else:
            full = str((Path(self._root) / path).resolve())

        # Guard against path traversal
        if not (full.startswith(self._root + os.sep) or full == self._root):
            raise ArtifactPathError(path)

        return full

    def exists(self, path: str) -> bool:
        try:
            return os.path.exists(self.resolve(path))
        except ArtifactPathError:
            return False


def read_manifest(artifact_store: ArtifactStore, path: str) -> list[str]:
    """Read a machine-written output manifest and return artifact paths.

    Manifest format: {"artifacts": ["path1", "path2", ...]}

    This function reads file content — it is intentionally narrow in scope: only
    machine-written JSON control files (not user payload artifacts) are read here.

    Raises ValueError on missing file, invalid JSON, or wrong schema.
    """
    resolved = artifact_store.resolve(path)
    try:
        data = json.loads(Path(resolved).read_text())
    except FileNotFoundError:
        raise ValueError(f"Manifest not found: {path!r}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Manifest at {path!r} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("artifacts"), list):
        raise ValueError(f'Manifest at {path!r} must be {{"artifacts": [...]}}; got: {type(data)}')
    return [str(p) for p in data["artifacts"]]
