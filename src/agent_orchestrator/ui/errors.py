"""Typed errors for the dashboard's schedules API (T-Ap1Xs3).

The status code is a class attribute, so ``ui/app.py`` maps ``exc.status_code`` directly.

Deliberate second idiom: the older run routes in ``ui/app.py`` discriminate 404 vs 409 by
substring-matching exception messages (``"not found" in str(exc)``). New surface starts clean
with this hierarchy; do NOT "unify" the two by reverting these to message matching.
"""

from __future__ import annotations


class ScheduleApiError(Exception):
    """Base class: a schedules-API failure that maps to a 4xx response."""

    status_code: int = 400


class ScheduleNotFound(ScheduleApiError):
    """No such schedule id in the workspace's ``.ao/schedules.yaml`` (HTTP 404)."""

    status_code = 404


class ScheduleFileInvalid(ScheduleApiError):
    """``.ao/schedules.yaml`` is unreadable or invalid; message = parse error (400)."""

    status_code = 400


class ScheduleAlreadyActive(ScheduleApiError):
    """A fire is already active (no ``force``) or the daemon owns the state (409)."""

    status_code = 409
