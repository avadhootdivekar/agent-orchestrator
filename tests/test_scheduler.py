"""Tests for Scheduler implementations."""

from __future__ import annotations

from datetime import UTC, datetime

from agent_orchestrator.models import Trigger
from agent_orchestrator.scheduler import CronScheduler, EventScheduler, ManualScheduler


class TestManualScheduler:
    def test_next_fire_returns_none(self) -> None:
        sched = ManualScheduler()
        trigger = Trigger(type="manual")
        result = sched.next_fire(trigger, datetime(2026, 1, 1, tzinfo=UTC))
        assert result is None


class TestCronScheduler:
    def test_daily_at_7am_utc(self) -> None:
        """0 7 * * * from 2026-01-01T06:00:00Z should fire at 2026-01-01T07:00:00Z."""
        fixed_now = datetime(2026, 1, 1, 6, 0, 0, tzinfo=UTC)
        sched = CronScheduler(clock=lambda: fixed_now)
        trigger = Trigger(type="cron", schedule="0 7 * * *", timezone="UTC")

        result = sched.next_fire(trigger, fixed_now)

        assert result is not None
        assert result.hour == 7
        assert result.minute == 0
        assert result.day == 1
        assert result.month == 1

    def test_every_minute(self) -> None:
        fixed_now = datetime(2026, 6, 1, 12, 0, 30, tzinfo=UTC)
        sched = CronScheduler(clock=lambda: fixed_now)
        trigger = Trigger(type="cron", schedule="* * * * *", timezone="UTC")

        result = sched.next_fire(trigger, fixed_now)
        assert result is not None
        # Next minute should be :01
        assert result.minute == 1

    def test_naive_datetime_treated_as_utc(self) -> None:
        naive_now = datetime(2026, 1, 1, 6, 0, 0)  # no tzinfo
        sched = CronScheduler()
        trigger = Trigger(type="cron", schedule="0 7 * * *", timezone="UTC")
        # Should not raise — naive datetime is treated as UTC
        result = sched.next_fire(trigger, naive_now)
        assert result is not None

    def test_hourly_cron(self) -> None:
        fixed_now = datetime(2026, 3, 15, 10, 45, 0, tzinfo=UTC)
        sched = CronScheduler(clock=lambda: fixed_now)
        trigger = Trigger(type="cron", schedule="0 * * * *", timezone="UTC")
        result = sched.next_fire(trigger, fixed_now)
        assert result is not None
        assert result.hour == 11
        assert result.minute == 0


class TestEventScheduler:
    def test_returns_none_when_no_sentinel(self) -> None:
        sched = EventScheduler(sentinel_path="/nonexistent/sentinel.txt")
        trigger = Trigger(type="event", event="some-event")
        result = sched.next_fire(trigger, datetime(2026, 1, 1, tzinfo=UTC))
        assert result is None

    def test_returns_now_when_sentinel_exists(self, tmp_path) -> None:
        sentinel = tmp_path / "sentinel.txt"
        sentinel.write_text("ready")
        sched = EventScheduler(sentinel_path=str(sentinel))
        trigger = Trigger(type="event", event="some-event")
        now = datetime(2026, 1, 1, tzinfo=UTC)
        result = sched.next_fire(trigger, now)
        assert result == now

    def test_empty_sentinel_path_returns_none(self) -> None:
        sched = EventScheduler(sentinel_path="")
        trigger = Trigger(type="event", event="e")
        assert sched.next_fire(trigger, datetime(2026, 1, 1, tzinfo=UTC)) is None
