"""Scheduler abstractions: Manual, Cron, and Event (stub)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from .models import Trigger


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Scheduler(ABC):
    """Abstract scheduler — returns the next fire time for a trigger."""

    @abstractmethod
    def next_fire(self, trigger: Trigger, now: datetime) -> datetime | None:
        """Return the next datetime this trigger fires after *now*, or None if never."""
        ...


class ManualScheduler(Scheduler):
    """Manual trigger — never fires automatically."""

    def next_fire(self, trigger: Trigger, now: datetime) -> datetime | None:
        return None


class CronScheduler(Scheduler):
    """Cron-based scheduler backed by `croniter`.

    Parameters
    ----------
    clock:
        Injectable callable for the current UTC time (fixed in tests).
    """

    def __init__(self, clock: Callable[[], datetime] = _utcnow) -> None:
        self._clock = clock

    def next_fire(self, trigger: Trigger, now: datetime) -> datetime | None:
        from datetime import tzinfo

        from croniter import croniter

        tz: tzinfo
        if trigger.timezone == "UTC":
            tz = UTC
        else:
            tz = ZoneInfo(trigger.timezone)

        # Ensure now is tz-aware
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)

        now_in_tz = now.astimezone(tz)
        it = croniter(trigger.schedule, now_in_tz)
        return it.get_next(datetime)


class EventScheduler(Scheduler):
    """Stub event scheduler — fires when a sentinel file exists.

    A full event/webhook system is out of MVP scope; this stub allows the
    interface to be exercised in tests without a real event bus.
    """

    def __init__(self, sentinel_path: str = "") -> None:
        self._sentinel = sentinel_path

    def next_fire(self, trigger: Trigger, now: datetime) -> datetime | None:
        import os

        if self._sentinel and os.path.exists(self._sentinel):
            return now
        return None
