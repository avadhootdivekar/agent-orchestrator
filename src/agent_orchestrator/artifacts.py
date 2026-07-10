"""Artifact store abstraction — path resolution and existence checks."""

from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from pathlib import Path

from .errors import ArtifactPathError, ControlFileError, GateError
from .models import MAX_CONTROL_FILE_BYTES, TaskSpec

# TODO(future): auto-prune when .orchestrator/runs/ exceeds a configurable size limit.
# This should trigger as part of any normal `ao run` (not the current run's data).
# Implement once product is stable (post Phase-2+). Use `ao prune` in the meantime.


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


def read_control(artifact_store: ArtifactStore, path: str) -> dict:
    """Read one bounded JSON *object* control file.

    This is the ONE audited content-read surface (besides `read_manifest`/`read_task_manifest`)
    used by loop gates, routers, and verdict breakers alike — NFR-1: the engine never reads
    payload artifacts, only small machine-written control/verdict JSON.

    Enforces `MAX_CONTROL_FILE_BYTES` via `artifact_store.size()` **before** reading any content
    (closes the previously-unbounded read in the loop-gate reader). Checks are ordered
    missing-file → too-big → invalid-JSON → non-object-root: `size()` returns 0 for a missing or
    invalid path, so "missing" must be decided first or a missing file could be mis-reported.

    Raises ControlFileError on: missing file, size > MAX_CONTROL_FILE_BYTES, invalid JSON, or a
    non-object root. Every message names *path*.
    """
    resolved = artifact_store.resolve(path)
    if not artifact_store.exists(path):
        raise ControlFileError(f"control file not found: {path!r}")
    size = artifact_store.size(path)
    if size > MAX_CONTROL_FILE_BYTES:
        raise ControlFileError(
            f"control file at {path!r} exceeds {MAX_CONTROL_FILE_BYTES} byte limit: size={size}"
        )
    try:
        data = json.loads(Path(resolved).read_text())
    except FileNotFoundError:
        # TOCTOU guard: file vanished between the exists() check and the read.
        raise ControlFileError(f"control file not found: {path!r}")
    except json.JSONDecodeError as exc:
        raise ControlFileError(f"control file at {path!r} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ControlFileError(
            f"control file at {path!r} root must be a JSON object; got: {type(data).__name__}"
        )
    return data


def read_bool_field(artifact_store: ArtifactStore, path: str, field: str) -> bool:
    """Read *path* as a control file and return the bool value of *field*.

    Shared by the loop gate (`read_gate`) and the verdict breaker. Raises ControlFileError on
    everything `read_control` raises, plus a missing field or a non-bool value.
    """
    data = read_control(artifact_store, path)
    if field not in data:
        raise ControlFileError(f"control file at {path!r} missing field {field!r}")
    value = data[field]
    if not isinstance(value, bool):
        raise ControlFileError(
            f"control file at {path!r} field {field!r} must be bool; got: {type(value).__name__}"
        )
    return value


def read_routes(artifact_store: ArtifactStore, path: str, field: str = "routes") -> list[str]:
    """Read *path* as a control file and return the list of non-empty route-id strings at *field*.

    Used by the router. Raises ControlFileError on everything `read_control` raises, plus a
    missing field or a value that isn't a list of non-empty strings.
    """
    data = read_control(artifact_store, path)
    if field not in data:
        raise ControlFileError(f"control file at {path!r} missing field {field!r}")
    value = data[field]
    if not (isinstance(value, list) and all(isinstance(x, str) and x != "" for x in value)):
        raise ControlFileError(
            f"control file at {path!r} field {field!r} must be a list of non-empty strings"
        )
    return value


def read_gate(artifact_store: ArtifactStore, path: str, field: str = "continue") -> bool:
    """Read a machine-written gate verdict file and return the boolean value of *field*.

    Gate format: {"<field>": true|false, ...}

    Thin alias over `read_bool_field` (NFR-1 single control-read surface) that translates
    `ControlFileError` to the concrete `GateError` subclass for backward compatibility: existing
    `except GateError` handling in engine.py's loop-gate path, and the loop-construct test suite,
    depend on this exact exception type.

    Raises GateError on missing file, size over the cap, invalid JSON, non-object root, missing
    field, or non-bool value.
    """
    try:
        return read_bool_field(artifact_store, path, field)
    except ControlFileError as exc:
        raise GateError(str(exc)) from exc
