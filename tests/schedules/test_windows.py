"""Tests for schedules/windows.py. Fixed instants only; no clock reads."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

from agent_orchestrator.schedules import windows
from agent_orchestrator.schedules.windows import (
    Window,
    local_midnight,
    window_for,
    windows_for,
)

BERLIN = ZoneInfo("Europe/Berlin")
NY = ZoneInfo("America/New_York")
HAVANA = ZoneInfo("America/Havana")
SANTIAGO = ZoneInfo("America/Santiago")
BEIRUT = ZoneInfo("Asia/Beirut")
LORD_HOWE = ZoneInfo("Australia/Lord_Howe")
UTC_ZONE = ZoneInfo("UTC")


def u(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def hours(w: Window) -> float:
    return (w.end - w.start).total_seconds() / 3600


# ---- basic shape / errors -------------------------------------------------------------------


def test_day_window_basic_and_frozen() -> None:
    w = window_for(u(2026, 11, 10, 15, 30), "day", UTC_ZONE, "monday")
    assert w == Window("day", "day:2026-11-10", u(2026, 11, 10), u(2026, 11, 11))
    assert w.start.utcoffset() == timedelta(0) and w.end.utcoffset() == timedelta(0)
    with pytest.raises(AttributeError):
        w.key = "x"  # type: ignore[misc]


def test_naive_instant_rejected() -> None:
    with pytest.raises(ValueError, match="instant must be timezone-aware"):
        window_for(datetime(2026, 11, 10, 12), "day", UTC_ZONE, "monday")


def test_unknown_period_and_week_start_rejected() -> None:
    with pytest.raises(ValueError, match="unknown period"):
        window_for(u(2026, 1, 1), "year", UTC_ZONE, "monday")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unknown week_start"):
        window_for(u(2026, 1, 1), "week", UTC_ZONE, "friday")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unknown period"):
        windows._bounds(date(2026, 1, 1), "year", "monday")


def test_key_formatters() -> None:
    assert windows.day_key(date(2026, 11, 10)) == "day:2026-11-10"
    assert windows.week_key(date(2026, 11, 9)) == "week:2026-11-09"
    assert windows.month_key(date(2026, 11, 1)) == "month:2026-11"
    # isoformat keeps the zero-padded year (HLD form), independent of the platform's %Y.
    assert windows.day_key(date(999, 1, 2)) == "day:0999-01-02"
    assert windows.month_key(date(999, 1, 1)) == "month:0999-01"


def test_windows_for_returns_each_period() -> None:
    t = u(2026, 11, 10, 12)
    got = windows_for(t, ["day", "week", "month", "day"], UTC_ZONE, "monday")
    assert list(got) == ["day", "week", "month"]
    assert got["day"].key == "day:2026-11-10"
    assert got["week"].key == "week:2026-11-09"
    assert got["month"].key == "month:2026-11"
    assert windows_for(t, [], UTC_ZONE, "monday") == {}


# ---- midnight boundaries --------------------------------------------------------------------


def test_exact_local_midnight_belongs_to_new_day() -> None:
    # Berlin midnight 2026-11-10 == 2026-11-09T23:00Z (CET, UTC+1)
    t = u(2026, 11, 9, 23)
    w = window_for(t, "day", BERLIN, "monday")
    assert w.key == "day:2026-11-10" and w.start == t
    prev = window_for(t - timedelta(microseconds=1), "day", BERLIN, "monday")
    assert prev.key == "day:2026-11-09" and prev.end == t


# ---- DST ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tz", "noon", "key", "start", "end", "length"),
    [
        (BERLIN, u(2024, 3, 31, 12), "day:2024-03-31", u(2024, 3, 30, 23), u(2024, 3, 31, 22), 23),
        (
            BERLIN,
            u(2024, 10, 27, 12),
            "day:2024-10-27",
            u(2024, 10, 26, 22),
            u(2024, 10, 27, 23),
            25,
        ),
        (NY, u(2024, 3, 10, 18), "day:2024-03-10", u(2024, 3, 10, 5), u(2024, 3, 11, 4), 23),
        (NY, u(2024, 11, 3, 18), "day:2024-11-03", u(2024, 11, 3, 4), u(2024, 11, 4, 5), 25),
    ],
)
def test_dst_days_are_one_window(tz, noon, key, start, end, length) -> None:
    w = window_for(noon, "day", tz, "monday")
    assert (w.key, w.start, w.end) == (key, start, end)
    assert hours(w) == length


@pytest.mark.parametrize("fold", [0, 1])
def test_fall_back_repeated_hour_belongs_to_that_day(fold: int) -> None:
    # NY 2024-11-03 01:30 local happens twice (05:30Z EDT, 06:30Z EST).
    local = datetime(2024, 11, 3, 1, 30, tzinfo=NY, fold=fold)
    w = window_for(local, "day", NY, "monday")
    assert w.key == "day:2024-11-03"
    assert w.start <= local < w.end


def test_fall_back_both_instants_same_window() -> None:
    # Berlin 02:30 occurs at 00:30Z (CEST) and 01:30Z (CET) on 2024-10-27.
    a = window_for(u(2024, 10, 27, 0, 30), "day", BERLIN, "monday")
    b = window_for(u(2024, 10, 27, 1, 30), "day", BERLIN, "monday")
    assert a == b and a.key == "day:2024-10-27"


# ---- midnight gaps / 24:00 transitions (AC6, AC11) ------------------------------------------


def test_havana_midnight_gap() -> None:
    # 2024-03-10 00:00 does not exist in Havana; the day starts at 01:00 local == 05:00Z.
    assert local_midnight(date(2024, 3, 10), HAVANA) == u(2024, 3, 10, 5)
    assert local_midnight(date(2024, 3, 10), HAVANA).astimezone(HAVANA).hour == 1
    w = window_for(u(2024, 3, 10, 12), "day", HAVANA, "monday")
    assert (w.key, w.start, w.end) == ("day:2024-03-10", u(2024, 3, 10, 5), u(2024, 3, 11, 4))
    assert hours(w) == 23
    # The instant just before the gap-resolved start is still the previous day.
    assert window_for(u(2024, 3, 10, 4, 59), "day", HAVANA, "monday").key == "day:2024-03-09"


def test_beirut_midnight_gap() -> None:
    w = window_for(u(2024, 3, 31, 12), "day", BEIRUT, "monday")
    assert (w.key, w.start, w.end) == ("day:2024-03-31", u(2024, 3, 30, 22), u(2024, 3, 31, 21))
    assert hours(w) == 23


def test_santiago_24h_transition() -> None:
    # Clocks go back at 2024-04-06 24:00 local: the 6th is 25 h, the 7th a plain 24 h.
    sixth = window_for(u(2024, 4, 6, 12), "day", SANTIAGO, "monday")
    seventh = window_for(u(2024, 4, 7, 12), "day", SANTIAGO, "monday")
    assert (sixth.key, sixth.start, sixth.end) == (
        "day:2024-04-06",
        u(2024, 4, 6, 3),
        u(2024, 4, 7, 4),
    )
    assert hours(sixth) == 25
    assert (seventh.key, seventh.start, seventh.end) == (
        "day:2024-04-07",
        u(2024, 4, 7, 4),
        u(2024, 4, 8, 4),
    )
    assert hours(seventh) == 24
    assert sixth.end == seventh.start


# ---- weeks ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("instant", "week_start", "key"),
    [
        (u(2026, 11, 8, 12), "monday", "week:2026-11-02"),  # Sunday
        (u(2026, 11, 9, 12), "monday", "week:2026-11-09"),  # Monday
        (u(2026, 11, 14, 12), "monday", "week:2026-11-09"),  # Saturday
        (u(2026, 11, 8, 12), "sunday", "week:2026-11-08"),  # Sunday
        (u(2026, 11, 9, 12), "sunday", "week:2026-11-08"),  # Monday
        (u(2026, 11, 14, 12), "sunday", "week:2026-11-08"),  # Saturday
    ],
)
def test_week_start_variants(instant: datetime, week_start: str, key: str) -> None:
    w = window_for(instant, "week", UTC_ZONE, week_start)  # type: ignore[arg-type]
    assert w.key == key
    assert w.end - w.start == timedelta(days=7)
    assert w.start <= instant < w.end


def test_week_across_dst_is_seven_local_dates() -> None:
    # Berlin week of 2024-03-25 contains spring-forward: 7 local dates but 167 h.
    w = window_for(u(2024, 3, 31, 12), "week", BERLIN, "monday")
    assert w.key == "week:2024-03-25"
    assert (w.start, w.end) == (u(2024, 3, 24, 23), u(2024, 3, 31, 22))
    assert w.start.astimezone(BERLIN).date() == date(2024, 3, 25)
    assert hours(w) == 7 * 24 - 1


def test_week_across_dst_sunday_start() -> None:
    # Sunday start: the week of 2024-03-24 ends before the spring-forward (a plain 168 h); the
    # transition on Sunday the 31st falls in the next week, which is the 167 h one.
    w = window_for(u(2024, 3, 28, 12), "week", BERLIN, "sunday")
    assert w.key == "week:2024-03-24"
    assert (w.start, w.end) == (u(2024, 3, 23, 23), u(2024, 3, 30, 23))
    assert hours(w) == 7 * 24
    w2 = window_for(u(2024, 3, 31, 12), "week", BERLIN, "sunday")
    assert w2.key == "week:2024-03-31"
    assert (w2.start, w2.end) == (u(2024, 3, 30, 23), u(2024, 4, 6, 22))
    assert hours(w2) == 7 * 24 - 1


def test_week_rolls_over_year_boundary() -> None:
    w = window_for(u(2026, 1, 1, 12), "week", UTC_ZONE, "monday")  # Thursday
    assert w.key == "week:2025-12-29"
    assert w.end == u(2026, 1, 5)


# ---- months ---------------------------------------------------------------------------------


def test_month_dec_to_jan_rollover() -> None:
    w = window_for(u(2026, 12, 31, 23, 59), "month", UTC_ZONE, "monday")
    assert (w.key, w.start, w.end) == ("month:2026-12", u(2026, 12, 1), u(2027, 1, 1))
    nxt = window_for(w.end, "month", UTC_ZONE, "monday")
    assert nxt.key == "month:2027-01" and nxt.start == w.end


def test_month_leap_february_and_31_day_month() -> None:
    feb = window_for(u(2028, 2, 15), "month", UTC_ZONE, "monday")
    assert feb.end - feb.start == timedelta(days=29)
    assert feb.end == u(2028, 3, 1)
    feb_plain = window_for(u(2027, 2, 15), "month", UTC_ZONE, "monday")
    assert feb_plain.end - feb_plain.start == timedelta(days=28)
    jan = window_for(u(2026, 1, 31, 23), "month", UTC_ZONE, "monday")
    assert jan.end - jan.start == timedelta(days=31)


def test_month_lord_howe_dst_transition() -> None:
    # Lord Howe shifts by 30 min. 2024-04-07 (+11:00 -> +10:30) and 2024-10-06 (+10:30 -> +11:00).
    apr = window_for(u(2024, 4, 6), "month", LORD_HOWE, "monday")
    assert (apr.key, apr.start, apr.end) == (
        "month:2024-04",
        u(2024, 3, 31, 13),
        u(2024, 4, 30, 13, 30),
    )
    assert apr.end - apr.start == timedelta(days=30, minutes=30)
    octo = window_for(u(2024, 10, 6), "month", LORD_HOWE, "monday")
    assert (octo.key, octo.start, octo.end) == (
        "month:2024-10",
        u(2024, 9, 30, 13, 30),
        u(2024, 10, 31, 13),
    )
    assert octo.end - octo.start == timedelta(days=30, hours=23, minutes=30)


# ---- invariants over a sweep of fixed instants ----------------------------------------------

_SWEEP_ZONES = [UTC_ZONE, BERLIN, NY, HAVANA, SANTIAGO, BEIRUT, LORD_HOWE]
_SWEEP_STARTS = [
    u(2024, 3, 9),
    u(2024, 3, 30),
    u(2024, 4, 5),
    u(2024, 10, 26),
    u(2024, 11, 2),
    u(2024, 12, 30),
]


@pytest.mark.parametrize("tz", _SWEEP_ZONES, ids=lambda z: z.key)
@pytest.mark.parametrize("period", ["day", "week", "month"])
@pytest.mark.parametrize("week_start", ["monday", "sunday"])
def test_containment_and_adjacency(tz: ZoneInfo, period: str, week_start: str) -> None:
    for base in _SWEEP_STARTS:
        for step in range(0, 72 * 3):  # every 20 min over three days
            t = base + timedelta(minutes=20 * step)
            w = window_for(t, period, tz, week_start)  # type: ignore[arg-type]
            assert w.start <= t < w.end
            nxt = window_for(w.end, period, tz, week_start)  # type: ignore[arg-type]
            assert nxt.key != w.key and nxt.start == w.end
            assert window_for(w.start, period, tz, week_start).key == w.key  # type: ignore[arg-type]


# ---- seeded randomised property test (E-Qp4Zm8 Q-11) -----------------------------------------

_PROP_SEED = 20261111
_PROP_CASES = 400
_PROP_ZONES = (
    "UTC", "Europe/Berlin", "America/New_York", "America/Havana", "America/Santiago",
    "Asia/Beirut", "Australia/Lord_Howe", "Asia/Kolkata", "Pacific/Auckland",
    "America/Sao_Paulo", "Africa/Cairo", "Pacific/Chatham", "Asia/Tehran",
)  # fmt: skip
_PROP_PERIODS = ("day", "week", "month")


@pytest.mark.parametrize("period", _PROP_PERIODS)
def test_random_zone_instant_window_invariants(period: str) -> None:
    """Seeded, bounded: any (zone, instant 2012-2035, week_start) yields a window that contains
    the instant, tiles with its neighbours, has a label-consistent key, and a sane length.
    Replay a failure with the printed seed/case index."""
    rng = random.Random(f"{_PROP_SEED}:{period}")
    lo = int(u(2012, 1, 1).timestamp())
    hi = int(u(2035, 12, 31).timestamp())
    for case in range(_PROP_CASES):
        tz = ZoneInfo(rng.choice(_PROP_ZONES))
        week_start = rng.choice(("monday", "sunday"))
        t = datetime.fromtimestamp(rng.randint(lo, hi), tz=UTC)
        ctx = (
            f"seed={_PROP_SEED}:{period} case={case} tz={tz.key} t={t.isoformat()} ws={week_start}"
        )
        w = window_for(t, period, tz, week_start)  # type: ignore[arg-type]
        assert w.start <= t < w.end, ctx
        nxt = window_for(w.end, period, tz, week_start)  # type: ignore[arg-type]
        assert nxt.start == w.end and nxt.key != w.key, ctx  # adjacent windows tile
        prev = window_for(w.start - timedelta(microseconds=1), period, tz, week_start)  # type: ignore[arg-type]
        assert prev.end == w.start and prev.key != w.key, ctx
        # any instant inside the window maps back to the same window (idempotent)
        mid = w.start + (w.end - w.start) / 2
        assert window_for(mid, period, tz, week_start) == w, ctx  # type: ignore[arg-type]
        # key names the local date of the window start (label authority)
        local_start = w.start.astimezone(tz).date()
        if period == "day":
            assert w.key == f"day:{t.astimezone(tz).date().isoformat()}", ctx
        elif period == "month":
            assert w.key == f"month:{local_start.isoformat()[:7]}", ctx
        else:
            first = 0 if week_start == "monday" else 6
            assert local_start.weekday() == first, ctx
            assert w.key == f"week:{local_start.isoformat()}", ctx
        # DST-safe length: within [22h, 25h+] per day; weeks/months bounded by calendar dates
        span_h = hours(w)
        bound = {
            "day": (22, 26),
            "week": (6 * 24 - 2, 8 * 24 + 2),
            "month": (27 * 24 - 2, 32 * 24 + 2),
        }
        assert bound[period][0] <= span_h <= bound[period][1], f"{ctx} span_h={span_h}"


def test_random_property_is_reproducible() -> None:
    """Same seed -> same draws, so a failing case index replays exactly."""
    a = random.Random(f"{_PROP_SEED}:day")
    b = random.Random(f"{_PROP_SEED}:day")
    assert [a.randint(0, 10**9) for _ in range(5)] == [b.randint(0, 10**9) for _ in range(5)]


# ---- label wins over bounds (historical tzdata) --------------------------------------------


def test_label_wins_when_fall_back_crosses_midnight() -> None:
    # Moncton 1993-10-31: the fall-back starts at local midnight, so the repeated 23:05 of the
    # 30th lies at/after that window's end; the key still names the 30th (label wins).
    moncton = ZoneInfo("America/Moncton")
    t = u(1993, 10, 31, 3, 5)
    w = window_for(t, "day", moncton, "monday")
    assert w.key == "day:1993-10-30"
    assert not (w.start <= t < w.end)


# ---- purity (AC9) ---------------------------------------------------------------------------


def test_no_clock_reads() -> None:
    class _BoomDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            raise AssertionError("clock read")

        @classmethod
        def utcnow(cls):
            raise AssertionError("clock read")

    class _BoomDate(date):
        @classmethod
        def today(cls):
            raise AssertionError("clock read")

    with (
        mock.patch.object(windows, "datetime", _BoomDatetime),
        mock.patch.object(windows, "date", _BoomDate),
    ):
        w = window_for(u(2026, 11, 10, 12), "week", BERLIN, "monday")
        m = window_for(u(2026, 11, 10, 12), "month", BERLIN, "monday")
    assert w.key == "week:2026-11-09"
    assert m.key == "month:2026-11"
