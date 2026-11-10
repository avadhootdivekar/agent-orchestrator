"""Workspace schedule bindings: the ``<workspace>/.ao/schedules.yaml`` model and its store.

Not to be confused with its two neighbours:

* ``scheduling/`` -- *task co-scheduling* inside one run (overlap detection between tasks);
* ``scheduler.py`` -- the ``Scheduler`` ABC computing a trigger's next fire time.

This package holds *what fires, when, and with which run arguments* across runs (HLD
``scheduler-triggers-hld.md`` §5.1): the validated declaration file and an atomic,
file-locked store. It performs no firing itself.
"""

from __future__ import annotations

from .errors import DuplicateScheduleIdError, ScheduleFileError, ScheduleNotFoundError
from .models import (
    MIN_INTERVAL_SECONDS,
    SCHEDULE_RUN_ARG_NAMES,
    ResolvedBinding,
    RunArgs,
    ScheduleBinding,
    ScheduleDefaults,
    ScheduleFile,
    UntilSpec,
    WatchSpec,
    WebhookSpec,
)
from .store import ScheduleStore

__all__ = [
    "MIN_INTERVAL_SECONDS",
    "SCHEDULE_RUN_ARG_NAMES",
    "DuplicateScheduleIdError",
    "ResolvedBinding",
    "RunArgs",
    "ScheduleBinding",
    "ScheduleDefaults",
    "ScheduleFile",
    "ScheduleFileError",
    "ScheduleNotFoundError",
    "ScheduleStore",
    "UntilSpec",
    "WatchSpec",
    "WebhookSpec",
]
