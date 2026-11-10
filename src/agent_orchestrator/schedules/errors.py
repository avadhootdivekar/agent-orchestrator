"""Errors raised by the schedule binding model and store.

``ScheduleFileError`` subclasses ``ValueError`` so pydantic validators can raise it directly
(pydantic wraps ``ValueError`` into its ``ValidationError``, which ``ScheduleFile.from_data``
then unwraps back into a named ``ScheduleFileError``).
"""

from __future__ import annotations


class ScheduleFileError(ValueError):
    """A schedules file (or one binding in it) is invalid. The message names file/id/field."""

    def __init__(
        self, message: str, *, schedule_id: str | None = None, field: str | None = None
    ) -> None:
        super().__init__(message)
        self.schedule_id = schedule_id
        self.field = field


class DuplicateScheduleIdError(ScheduleFileError):
    """Two bindings in one file share an ``id``."""


class ScheduleNotFoundError(KeyError):
    """A schedule id was looked up (``ao schedule enable/remove ...``) and does not exist."""
