"""Atomic, file-locked store for ``<workspace>/.ao/schedules.yaml``.

Same locking contract as ``service/registry.py`` (see its module docstring): ``save`` is an
atomic write-then-rename but NOT safe as half of a read-modify-write; ``mutate`` takes an
exclusive ``flock`` on a dedicated companion ``.lock`` file (never the payload, whose inode is
replaced on every save), re-reads the CURRENT file under the lock, applies the caller's pure
function, saves and releases. Every read-modify-write caller (``ao schedule add/remove/
enable/disable``, the dashboard) MUST go through ``mutate``.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Callable
from pathlib import Path

import yaml

from .errors import ScheduleFileError
from .models import ScheduleFile

SCHEDULES_RELPATH = Path(".ao") / "schedules.yaml"


class ScheduleStore:
    def __init__(self, workspace_root: Path | str) -> None:
        self._path = Path(workspace_root) / SCHEDULES_RELPATH

    @property
    def path(self) -> Path:
        return self._path

    def _lock_path(self) -> Path:
        return self._path.with_name(self._path.name + ".lock")

    def load(self) -> ScheduleFile:
        """Load and validate. A missing file is an empty :class:`ScheduleFile`; an unparseable or
        invalid one raises :class:`ScheduleFileError` naming the file (and id/field)."""
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ScheduleFile()
        except OSError as exc:
            raise ScheduleFileError(f"{self._path}: cannot read ({exc})") from exc
        try:
            data = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            raise ScheduleFileError(f"{self._path}: not valid YAML ({exc})") from exc
        if data is not None and not isinstance(data, dict):
            raise ScheduleFileError(f"{self._path}: top level must be a mapping")
        return ScheduleFile.from_data(data, source=str(self._path))

    def save(self, data: ScheduleFile) -> None:
        """Atomically persist *data* (tmp + ``os.replace``). Unlocked -- see module docstring.

        Pydantic does not re-validate on attribute mutation, so *data* is round-tripped through
        ``from_data(to_data())`` first: an invalid model (e.g. a duplicate id appended by a
        ``mutate`` fn) raises :class:`ScheduleFileError` and the existing file is left intact.
        """
        payload = data.to_data()
        ScheduleFile.from_data(payload, source=str(self._path))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(f"{self._path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
            os.replace(tmp, self._path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def mutate(self, fn: Callable[[ScheduleFile], ScheduleFile]) -> ScheduleFile:
        """Apply *fn* to the CURRENT on-disk file under an exclusive lock, save, return it.
        *fn* must be pure w.r.t. any snapshot read before the call."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path(), "a+") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                updated = fn(self.load())
                self.save(updated)
                return updated
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
