"""Artifact store abstraction — path resolution and existence checks."""

from __future__ import annotations

# TODO(future): auto-prune when .orchestrator/runs/ exceeds a configurable size limit.
# This should trigger as part of any normal `ao run` (not the current run's data).
# Implement once product is stable (post Phase-2+). Use `ao prune` in the meantime.

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path

from .errors import ArtifactPathError, GateError
from .models import TaskSpec


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

    @abstractmethod
    def size(self, path: str) -> int:
        """Return the byte size of the artifact at *path* using os.stat.

        Returns 0 if the file does not exist or the path is invalid.
        This is an os.stat call — NOT a content read (NFR-1 safe).
        """
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

    def size(self, path: str) -> int:
        """Return file size in bytes via os.stat. Returns 0 if missing or invalid path."""
        try:
            resolved = self.resolve(path)
            return os.stat(resolved).st_size
        except (ArtifactPathError, FileNotFoundError, OSError):
            return 0


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


def read_task_manifest(artifact_store: ArtifactStore, path: str) -> list[TaskSpec]:
    """Read a machine-written task manifest and return a list of TaskSpec objects.

    Manifest format: {"tasks": [<TaskSpec>, ...]}

    This function reads file content — it is intentionally narrow in scope: only
    machine-written JSON control files are read here (NFR-1, ADR-004).

    Raises ValueError on missing file, invalid JSON, wrong schema, or invalid TaskSpec.
    """
    resolved = artifact_store.resolve(path)
    try:
        data = json.loads(Path(resolved).read_text())
    except FileNotFoundError:
        raise ValueError(f"Task manifest not found: {path!r}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Task manifest at {path!r} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise ValueError(f'Task manifest at {path!r} must be {{"tasks": [...]}}; got: {type(data)}')
    try:
        return [TaskSpec(**t) for t in data["tasks"]]
    except Exception as exc:
        raise ValueError(f"Task manifest at {path!r} contains invalid TaskSpec: {exc}") from exc


def read_gate(artifact_store: ArtifactStore, path: str, field: str = "continue") -> bool:
    """Read a machine-written gate verdict file and return the boolean value of *field*.

    Gate format: {"<field>": true|false, ...}

    This function reads file content — it is intentionally narrow in scope: only
    machine-written JSON control files are read here (NFR-1, ADR-004).

    Raises GateError on missing file, missing field, or non-bool value.
    """
    resolved = artifact_store.resolve(path)
    try:
        data = json.loads(Path(resolved).read_text())
    except FileNotFoundError:
        raise GateError(f"Gate file not found: {path!r}")
    except json.JSONDecodeError as exc:
        raise GateError(f"Gate file at {path!r} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise GateError(f"Gate file at {path!r} must be a JSON object; got: {type(data)}")
    if field not in data:
        raise GateError(f"Gate file at {path!r} missing field {field!r}")
    value = data[field]
    if not isinstance(value, bool):
        raise GateError(
            f"Gate file at {path!r} field {field!r} must be bool; got: {type(value).__name__}"
        )
    return value
