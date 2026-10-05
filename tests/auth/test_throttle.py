"""``throttle.py``: client-key canonicalisation, the address throttle and the username gates
(T-CsT5gk AC 5-7). Fixed clock; the gates run through ``run_async`` (no pytest-asyncio)."""

from __future__ import annotations

import asyncio

import pytest

from agent_orchestrator.auth import constants
from agent_orchestrator.auth.throttle import AddressThrottle, UsernameGates, canonical_client_key

from .helpers.core import FakeClock, run_async

# ---------------------------------------------------------------------------
# canonical_client_key (AC 5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("203.0.113.9", "203.0.113.9"),
        ("::ffff:127.0.0.1", "127.0.0.1"),
        ("::FFFF:10.0.0.1", "10.0.0.1"),
        ("2001:db8::1", "2001:db8::/64"),
        ("2001:db8::ffff", "2001:db8::/64"),
        ("2001:db8:0:1::1", "2001:db8:0:1::/64"),
        ("::1", "::/64"),
        (None, "unknown"),
        ("", "unknown"),
        ("testclient", "unknown"),
        ("not-an-ip", "unknown"),
        ("999.1.1.1", "unknown"),
        ("127.0.0.1\n", "unknown"),
    ],
)
def test_canonical_client_key(raw: str | None, expected: str) -> None:
    assert canonical_client_key(raw) == expected


def test_unknown_key_is_the_named_constant() -> None:
    assert canonical_client_key(None) == constants.UNKNOWN_CLIENT_KEY


# ---------------------------------------------------------------------------
# AddressThrottle (AC 6)
# ---------------------------------------------------------------------------


class TestAddressThrottle:
    def test_backoff_starts_at_the_threshold_and_doubles(self) -> None:
        throttle = AddressThrottle(3, clock=FakeClock())
        throttle.record_failure("a")
        throttle.record_failure("a")
        assert throttle.retry_after("a") is None
        throttle.record_failure("a")
        assert throttle.retry_after("a") == 1
        throttle.record_failure("a")
        assert throttle.retry_after("a") == 2
        throttle.record_failure("a")
        assert throttle.retry_after("a") == 4

    def test_other_keys_and_unseen_keys_are_unaffected(self) -> None:
        throttle = AddressThrottle(1, clock=FakeClock())
        throttle.record_failure("a")
        assert throttle.retry_after("a") == 1
        assert throttle.retry_after("b") is None

    def test_wait_counts_down_with_the_monotonic_clock(self) -> None:
        clock = FakeClock()
        throttle = AddressThrottle(1, clock=clock, base_seconds=10)
        throttle.record_failure("a")
        clock.advance_mono(4)
        assert throttle.retry_after("a") == 6
        clock.advance_mono(6)
        assert throttle.retry_after("a") is None

    def test_wall_clock_jumps_do_not_matter(self) -> None:
        clock = FakeClock()
        throttle = AddressThrottle(1, clock=clock, base_seconds=10)
        throttle.record_failure("a")
        clock.advance_wall(10_000)
        assert throttle.retry_after("a") == 10

    def test_backoff_is_capped(self) -> None:
        throttle = AddressThrottle(1, clock=FakeClock(), base_seconds=1, max_seconds=5)
        for _ in range(20):
            throttle.record_failure("a")
        assert throttle.retry_after("a") == 5

    def test_failures_older_than_the_window_are_pruned(self) -> None:
        clock = FakeClock()
        throttle = AddressThrottle(3, clock=clock)
        for _ in range(3):
            throttle.record_failure("a")
        assert throttle.retry_after("a") == 1
        clock.advance_mono(constants.ADDRESS_WINDOW_SECONDS)
        assert throttle.retry_after("a") is None
        assert "a" not in throttle._failures  # the empty history is dropped too

    def test_partial_pruning_keeps_the_recent_failures(self) -> None:
        clock = FakeClock()
        throttle = AddressThrottle(2, clock=clock, window_seconds=100)
        throttle.record_failure("a")
        clock.advance_mono(60)
        throttle.record_failure("a")
        throttle.record_failure("a")
        clock.advance_mono(60)  # the first has aged out; two remain
        assert throttle.retry_after("a") is None  # two left, and their 1 s back-off is over
        assert len(throttle._failures["a"]) == 2
        clock.advance_mono(50)
        assert throttle.retry_after("a") is None  # all aged out
        assert "a" not in throttle._failures

    def test_lru_evicts_the_least_recently_used_key(self) -> None:
        throttle = AddressThrottle(1, clock=FakeClock(), max_entries=2)
        throttle.record_failure("a")
        throttle.record_failure("b")
        throttle.record_failure("a")  # a is now the most recent
        throttle.record_failure("c")  # evicts b
        assert throttle.retry_after("a") is not None
        assert throttle.retry_after("c") is not None
        assert throttle.retry_after("b") is None

    def test_history_is_bounded_per_key(self) -> None:
        throttle = AddressThrottle(3, clock=FakeClock())
        for _ in range(1000):
            throttle.record_failure("a")
        assert len(throttle._failures["a"]) == 3 + constants.ADDRESS_HISTORY_SLACK
        assert throttle.retry_after("a") is not None


# ---------------------------------------------------------------------------
# UsernameGates (AC 7)
# ---------------------------------------------------------------------------


class TestUsernameGates:
    def test_same_name_never_overlaps_and_different_names_do(self) -> None:
        events: list[str] = []
        gates = UsernameGates()

        async def worker(name: str, tag: str) -> None:
            async with gates.hold(name):
                events.append(f"enter:{tag}")
                await asyncio.sleep(0.01)
                events.append(f"exit:{tag}")

        async def main() -> None:
            await asyncio.gather(worker("alice", "a1"), worker("alice", "a2"), worker("bob", "b1"))

        run_async(main())
        a_events = [e for e in events if e.endswith(("a1", "a2"))]
        assert a_events in (
            ["enter:a1", "exit:a1", "enter:a2", "exit:a2"],
            ["enter:a2", "exit:a2", "enter:a1", "exit:a1"],
        )
        # bob entered while alice's first holder was still inside (they overlap)
        assert events.index("enter:b1") < events.index("exit:a1")

    def test_held_entries_are_never_evicted(self) -> None:
        gates = UsernameGates(max_entries=1)
        in_flight: dict[str, int] = {}
        peak: dict[str, int] = {}

        async def main() -> None:
            release = asyncio.Event()

            async def worker(name: str, wait_for: asyncio.Event | None) -> None:
                async with gates.hold(name):
                    in_flight[name] = in_flight.get(name, 0) + 1
                    peak[name] = max(peak.get(name, 0), in_flight[name])
                    if wait_for is not None:
                        await wait_for.wait()
                    in_flight[name] -= 1

            first = asyncio.create_task(worker("alice", release))
            await asyncio.sleep(0)  # alice holds her lock
            second = asyncio.create_task(worker("alice", None))  # waits on it
            other = asyncio.create_task(worker("bob", None))  # pushes the table over capacity
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert "alice" in gates._gates  # held and awaited: kept despite max_entries=1
            release.set()
            await asyncio.gather(first, second, other)

        run_async(main())
        assert peak == {"alice": 1, "bob": 1}  # the two alice tasks never overlapped

    def test_idle_entries_are_evicted_back_to_the_cap(self) -> None:
        gates = UsernameGates(max_entries=2)

        async def main() -> None:
            for i in range(10):
                async with gates.hold(f"user{i}"):
                    pass

        run_async(main())
        assert len(gates._gates) <= 2

    def test_lock_is_released_when_the_body_raises(self) -> None:
        gates = UsernameGates()

        async def main() -> None:
            with pytest.raises(RuntimeError):
                async with gates.hold("alice"):
                    raise RuntimeError("boom")
            async with asyncio.timeout(1):
                async with gates.hold("alice"):
                    pass

        run_async(main())
        assert gates._gates["alice"].users == 0
