"""Engine-tier cron + ``America/New_York`` DST tests (T-Se4Bk5 TEST-QUALITY FU-2).

Everything runs through ``ScheduleEngine.tick`` with a fixed clock and a recording launcher, so the
assertions cover the exact ``scheduled_for`` list, ``fire_key`` uniqueness and restart behaviour --
not just ``CronScheduler.next_fire``. Pinned behaviour (croniter 'local wall clock' semantics):

* spring gap (2026-03-08): a ``30 2`` cron has no 02:30 that day; it fires **once**, at 03:00 EDT.
* fall fold (2026-11-01): a ``30 1`` cron fires **twice** (01:30 EDT then 01:30 EST), one hour
  apart, and the two fires get **distinct** ``fire_key`` s (keys hash the UTC instant).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from agent_orchestrator.service.fire_store import fire_key

from .test_schedule_engine import Env, binding

NY = ZoneInfo("America/New_York")


def _utc(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def _cron_env(tmp_path: Path, expr: str, **kw: object) -> Env:
    return Env(
        tmp_path,
        [
            binding(
                kind="cron", schedule=expr, interval_seconds=None, timezone="America/New_York", **kw
            )
        ],
    )


def _fired(env: Env) -> list[datetime]:
    """``scheduled_for`` of every launch, as UTC. Cron yields NY-zoned instants and PEP 495 makes
    cross-zone ``==`` False for ambiguous/gap wall times, so compare in UTC only."""
    return [d.scheduled_for.astimezone(UTC) for d in env.launcher.decisions if d.scheduled_for]


def _drive(env: Env, start: datetime, end: datetime, step_min: int = 5) -> list[datetime]:
    """Tick every *step_min* minutes over [start, end]; finish each run so overlap never gates."""
    t = start
    while t <= end:
        env.tick(t)  # tick() itself hands fires to the recording launcher
        for n in range(1, len(env.launcher.decisions) + 1):
            env.end_run(f"run-{n}")
        t += timedelta(minutes=step_min)
    return _fired(env)


def test_spring_gap_nonexistent_0230_fires_once_at_0300_edt(tmp_path: Path) -> None:
    env = _cron_env(tmp_path, "30 2 * * *")
    env.anchor(at=_utc(2026, 3, 7, 12))
    got = _drive(env, _utc(2026, 3, 7, 12), _utc(2026, 3, 9, 12))
    assert got == [
        _utc(2026, 3, 8, 7, 0),  # 02:30 EST never existed -> 03:00 EDT, once
        _utc(2026, 3, 9, 6, 30),  # back to 02:30 EDT the next day
    ]
    assert got[0].astimezone(NY).hour == 3
    keys = {d.fire_key for d in env.launcher.decisions}
    assert len(keys) == 2 and keys == {fire_key(env.root, "s", t) for t in got}


def test_spring_forward_hourly_cron_skips_the_missing_hour_without_double_fire(
    tmp_path: Path,
) -> None:
    env = _cron_env(tmp_path, "0 * * * *")
    env.anchor(at=_utc(2026, 3, 8, 5, 30))  # 00:30 EST
    got = _drive(env, _utc(2026, 3, 8, 5, 30), _utc(2026, 3, 8, 9, 0))
    local = [t.astimezone(NY).strftime("%H:%M") for t in got]
    assert local == ["01:00", "03:00", "04:00", "05:00"]  # no 02:00; gaps are 1h, 1h in UTC
    assert [b - a for a, b in zip(got, got[1:])] == [timedelta(hours=1)] * 3


def test_fall_fold_0130_fires_twice_with_distinct_fire_keys(tmp_path: Path) -> None:
    env = _cron_env(tmp_path, "30 1 * * *")
    env.anchor(at=_utc(2026, 10, 31, 12))
    got = _drive(env, _utc(2026, 10, 31, 12), _utc(2026, 11, 2, 12))
    assert got == [
        _utc(2026, 11, 1, 5, 30),  # 01:30 EDT
        _utc(2026, 11, 1, 6, 30),  # 01:30 EST (the repeated hour)
        _utc(2026, 11, 2, 6, 30),  # 01:30 EST next day
    ]
    # same wall-clock string, different instants -> keys must differ or the 2nd would be burned
    assert got[0].astimezone(NY).strftime("%H:%M") == got[1].astimezone(NY).strftime("%H:%M")
    keys = [d.fire_key for d in env.launcher.decisions]
    assert len(set(keys)) == 3 and keys == [fire_key(env.root, "s", t) for t in got]


def test_fall_fold_ticks_across_the_fold_never_repeat_a_fire(tmp_path: Path) -> None:
    """Re-ticking the same instants (and a rewind into the repeated hour) adds no fire."""
    env = _cron_env(tmp_path, "30 1 * * *")
    env.anchor(at=_utc(2026, 10, 31, 12))
    _drive(env, _utc(2026, 11, 1, 5, 0), _utc(2026, 11, 1, 7, 0))
    assert len(env.launcher.decisions) == 2
    _drive(env, _utc(2026, 11, 1, 5, 0), _utc(2026, 11, 1, 7, 0))  # clock rewinds an hour+
    assert len(env.launcher.decisions) == 2


def test_restart_between_the_two_fold_fires_fires_the_second_exactly_once(tmp_path: Path) -> None:
    env = _cron_env(tmp_path, "30 1 * * *")
    env.anchor(at=_utc(2026, 10, 31, 12))
    _drive(env, _utc(2026, 11, 1, 5, 0), _utc(2026, 11, 1, 5, 59))
    assert _fired(env) == [_utc(2026, 11, 1, 5, 30)]
    env.engine = env.make_engine()  # restart: fresh engine, same state dir, mid-fold
    _drive(env, _utc(2026, 11, 1, 6, 0), _utc(2026, 11, 1, 6, 59))
    env.engine = env.make_engine()  # and again after the second fire
    _drive(env, _utc(2026, 11, 1, 7, 0), _utc(2026, 11, 1, 9, 0))
    assert _fired(env) == [
        _utc(2026, 11, 1, 5, 30),
        _utc(2026, 11, 1, 6, 30),
    ]


def test_restart_across_spring_forward_fires_once_not_twice(tmp_path: Path) -> None:
    """Engine down over the gap, restarted at 03:10 EDT: the one gap fire is caught up once."""
    env = _cron_env(tmp_path, "30 2 * * *", catch_up="once", catch_up_window_seconds=7200)
    env.anchor(at=_utc(2026, 3, 7, 12))
    _drive(env, _utc(2026, 3, 7, 12), _utc(2026, 3, 8, 6, 0), step_min=30)  # before the gap
    assert env.launcher.decisions == []
    env.engine = env.make_engine()
    got = _drive(env, _utc(2026, 3, 8, 7, 10), _utc(2026, 3, 8, 12, 0), step_min=10)
    assert got == [_utc(2026, 3, 8, 7, 0)]
    env.engine = env.make_engine()
    assert _drive(env, _utc(2026, 3, 8, 12, 10), _utc(2026, 3, 8, 14, 0), step_min=10) == got
