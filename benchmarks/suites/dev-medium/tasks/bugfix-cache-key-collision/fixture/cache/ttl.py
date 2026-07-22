"""TTL (time-to-live) wrapper over an `LRUCache` (bench fixture:
bugfix-cache-key-collision).

`clock` is an injectable zero-arg callable returning the current epoch-seconds float --
callers never call `time.time()` directly, so tests stay deterministic (a fixed fake
clock is used throughout the test suite).
"""

from __future__ import annotations

from collections.abc import Callable

from .store import LRUCache

Clock = Callable[[], float]


def set_with_ttl(
    store: LRUCache, key: str, value: object, ttl_seconds: float, clock: Clock
) -> None:
    expires_at = clock() + ttl_seconds
    store.set(key, (value, expires_at))


def get_if_fresh(store: LRUCache, key: str, clock: Clock) -> object | None:
    entry = store.get(key)
    if entry is None:
        return None
    value, expires_at = entry
    if clock() > expires_at:
        return None
    return value
