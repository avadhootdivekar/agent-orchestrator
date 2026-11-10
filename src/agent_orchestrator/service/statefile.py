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


def write_json_model_atomic(path: Path, model: BaseModel) -> None:
    """Persist *model* to *path* atomically (temp file in the same dir + ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # pid-suffixed so two processes writing the same path never share a temp file.
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(model.model_dump_json(indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
