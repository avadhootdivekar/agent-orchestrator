"""Pure calendar-window math for scheduled-run quotas (HLD §7, ADR-0025 D2).

Given an aware instant, a period (``day|week|month``), an IANA timezone and a week start, return
the calendar window that contains it. This is the only place window boundaries are computed.

**The local-date label is authoritative (review amendment L5).** Window membership is decided by
the key (``day:YYYY-MM-DD`` ...), which depends only on the *local date* of the instant. ``start
<= t < end`` is derived to agree with it. A DST fall-back day (25 h) and a spring-forward day
(23 h) are therefore each exactly one window, and an instant in the repeated hour belongs to
that day regardless of ``fold``. Label and bounds could diverge only for a fall-back that
happens *after* local midnight and moves the clock back across midnight; no tested zone does
that (Havana, Santiago 24:00 and Beirut midnight gap all agree).

Stdlib only; no I/O and no clock reads. Callers pass an already-constructed ``ZoneInfo`` (load-
time validation of timezone names lives with the schedule models). No caching: the ledger stores
instants and calls this at query time."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal, get_args
from zoneinfo import ZoneInfo

Period = Literal["day", "week", "month"]
WeekStart = Literal["monday", "sunday"]

PERIODS: tuple[str, ...] = get_args(Period)
DAYS_PER_WEEK = 7
MONDAY_WEEKDAY = 0  # date.weekday(): Monday == 0
SUNDAY_WEEKDAY = 6
DECEMBER = 12

DAY_KEY_FORMAT = "day:{:%Y-%m-%d}"
WEEK_KEY_FORMAT = "week:{:%Y-%m-%d}"  # the local date the week starts
MONTH_KEY_FORMAT = "month:{:%Y-%m}"

_KEY_FORMATS: dict[str, str] = {
    "day": DAY_KEY_FORMAT,
    "week": WEEK_KEY_FORMAT,
    "month": MONTH_KEY_FORMAT,
}
_UTC = UTC


@dataclass(frozen=True)
class Window:
    """One calendar window: ``start`` inclusive, ``end`` exclusive, both aware UTC."""

    period: Period
    key: str
    start: datetime
    end: datetime


def day_key(d: date) -> str:
    return DAY_KEY_FORMAT.format(d)


def week_key(start: date) -> str:
    return WEEK_KEY_FORMAT.format(start)


def month_key(first: date) -> str:
    return MONTH_KEY_FORMAT.format(first)


def local_midnight(d: date, tz: ZoneInfo) -> datetime:
    """Earliest instant whose local date is ``d``, as aware UTC.

    ``fold=0`` picks the first occurrence of an ambiguous 00:00; for a non-existent 00:00
    (midnight DST gap) zoneinfo applies the pre-transition offset, which lands exactly on the
    first existing local instant of the date (America/Havana 2024-03-10 -> 05:00Z == 01:00
    local). Deliberately no manual offset arithmetic.
    """
    return datetime.combine(d, time(0, 0), tzinfo=tz).astimezone(_UTC)


def _bounds(d: date, period: str, week_start: WeekStart) -> tuple[date, date]:
    """Local start date (inclusive) and next window's start date for the window containing ``d``."""
    if period == "day":
        return d, d + timedelta(days=1)
    if period == "week":
        first_weekday = MONDAY_WEEKDAY if week_start == "monday" else SUNDAY_WEEKDAY
        start = d - timedelta(days=(d.weekday() - first_weekday) % DAYS_PER_WEEK)
        return start, start + timedelta(days=DAYS_PER_WEEK)
    if period == "month":
        start = d.replace(day=1)
        nxt = date(d.year + (d.month == DECEMBER), 1 if d.month == DECEMBER else d.month + 1, 1)
        return start, nxt
    raise ValueError(f"unknown period: {period!r} (expected one of {', '.join(PERIODS)})")


def window_for(instant: datetime, period: Period, tz: ZoneInfo, week_start: WeekStart) -> Window:
    """The ``period`` window in ``tz`` that contains ``instant``."""
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("instant must be timezone-aware")
    if period not in _KEY_FORMATS:
        raise ValueError(f"unknown period: {period!r} (expected one of {', '.join(PERIODS)})")
    if week_start not in ("monday", "sunday"):
        raise ValueError(f"unknown week_start: {week_start!r} (expected 'monday' or 'sunday')")
    start_d, next_d = _bounds(instant.astimezone(tz).date(), period, week_start)
    return Window(
        period=period,
        key=_KEY_FORMATS[period].format(start_d),
        start=local_midnight(start_d, tz),
        end=local_midnight(next_d, tz),
    )


def windows_for(
    instant: datetime, periods: Iterable[Period], tz: ZoneInfo, week_start: WeekStart
) -> dict[Period, Window]:
    """``window_for`` for each distinct period, in the order first given."""
    return {p: window_for(instant, p, tz, week_start) for p in periods}
