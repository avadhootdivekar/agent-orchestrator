"""Atomic JSON state-file helper shared by the scheduler's runtime-state writers.

The idiom -- *write to a temp file then* ``os.replace`` *, and on read treat a corrupt file as
"start clean" with a warning* -- already exists inline in ``service/registry.py``,
``service/boot_resume.py`` and the supervisor's snapshot code. This module is the single home
for it for NEW code (scheduler state, fire records, watch digests). Migrating the three
existing call sites is a deliberate follow-up, not done here.

Concurrency: ``write_json_model_atomic`` guarantees readers never see a torn file; it does NOT
serialize concurrent read-modify-write cycles (see ``ServiceRegistry.mutate`` for that).
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)


def read_json_model(path: Path, model_cls: type[M], *, default_factory: Callable[[], M]) -> M:
    """Load *path* as ``model_cls``; a missing file is ``default_factory()``.

    An unreadable, non-JSON or schema-invalid file is logged at WARNING and also yields
    ``default_factory()`` ("corrupt state starts clean") -- never an exception, because state
    files are derived and the next write repairs them.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default_factory()
    except OSError as exc:
        logger.warning("state file %s unreadable (%s); starting clean", path, exc)
        return default_factory()
    try:
        return model_cls.model_validate_json(text)
    except ValueError as exc:  # pydantic.ValidationError subclasses ValueError
        logger.warning("state file %s is corrupt (%s); starting clean", path, exc)
        return default_factory()


def _fsync_dir(directory: Path) -> None:
    """Best-effort fsync of *directory* so a rename into it survives power loss."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:  # e.g. platforms where directories can't be opened
        return
    try:
        os.fsync(fd)
    except OSError as exc:  # rename may not be durable: warn, never hide (N3)
        logger.warning(
            "fsync of directory %s failed (%s); rename may not be durable", directory, exc
        )
    finally:
        os.close(fd)


def atomic_write_bytes(path: Path, data: bytes, *, durable: bool = False) -> None:
    """Write *data* to *path* atomically (unique temp file in the same dir + ``os.replace``).

    The temp name comes from ``tempfile.mkstemp`` so concurrent writers (threads or processes)
    never share one. An existing target keeps its permission bits; a new file is created 0600.

    ``durable=True`` additionally flushes + fsyncs the temp file before the rename and fsyncs
    the parent directory after it, so the record survives a crash/power loss (for fire records
    and similar must-not-lose state). Off by default: plain atomicity is enough for derived state.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode: int | None = path.stat().st_mode & 0o7777
    except OSError:
        mode = None
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            if mode is not None:
                os.fchmod(fh.fileno(), mode)
            if durable:
                fh.flush()
                os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    if durable:
        _fsync_dir(path.parent)


def write_json_model_atomic(path: Path, model: BaseModel, *, durable: bool = False) -> None:
    """Persist *model* to *path* as indented JSON via :func:`atomic_write_bytes`."""
    atomic_write_bytes(path, model.model_dump_json(indent=2).encode("utf-8"), durable=durable)
