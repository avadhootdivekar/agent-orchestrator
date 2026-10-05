"""Per-process attempt throttling: client keys, the address throttle and username gates (HLD 11.11).

All state here is **in memory and per process**: the durable per-account lockout lives in
``lockouts.py``. Event-loop confined (no locking beyond ``asyncio.Lock`` for the gates); the
address throttle is synchronous and cheap, so it runs before any hashing or file write.
"""

from __future__ import annotations

import asyncio
import ipaddress
import math
from collections import OrderedDict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from .constants import (
    ADDRESS_BACKOFF_BASE_SECONDS,
    ADDRESS_BACKOFF_MAX_SECONDS,
    ADDRESS_HISTORY_SLACK,
    ADDRESS_TABLE_MAX_ENTRIES,
    ADDRESS_WINDOW_SECONDS,
    IPV6_THROTTLE_PREFIX_LEN,
    UNKNOWN_CLIENT_KEY,
    USERNAME_GATE_MAX_ENTRIES,
)
from .seams import SYSTEM_CLOCK, Clock

# 2 ** n is capped so a long history cannot build a huge integer before min().
_MAX_BACKOFF_EXPONENT = 62


def canonical_client_key(raw: str | None) -> str:
    """The throttle bucket for a peer address (dev-security #3).

    IPv4 stays as is; an IPv4-mapped IPv6 address (``::ffff:a.b.c.d``) unwraps to its IPv4 form;
    any other IPv6 address aggregates to its ``/IPV6_THROTTLE_PREFIX_LEN`` network, so rotating
    privacy addresses share one bucket. Missing or unparseable input is ``UNKNOWN_CLIENT_KEY``.
    """
    if not raw:
        return UNKNOWN_CLIENT_KEY
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return UNKNOWN_CLIENT_KEY
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{ip}/{IPV6_THROTTLE_PREFIX_LEN}", strict=False))
    return str(ip)


class AddressThrottle:
    """Failure back-off per client key: an LRU of bounded monotonic-timestamp histories."""

    def __init__(
        self,
        threshold: int,
        *,
        clock: Clock = SYSTEM_CLOCK,
        window_seconds: int = ADDRESS_WINDOW_SECONDS,
        base_seconds: int = ADDRESS_BACKOFF_BASE_SECONDS,
        max_seconds: int = ADDRESS_BACKOFF_MAX_SECONDS,
        max_entries: int = ADDRESS_TABLE_MAX_ENTRIES,
    ) -> None:
        self._threshold = threshold
        self._clock = clock
        self._window = window_seconds
        self._base = base_seconds
        self._max = max_seconds
        self._max_entries = max_entries
        self._history_len = threshold + ADDRESS_HISTORY_SLACK
        self._failures: OrderedDict[str, deque[float]] = OrderedDict()

    def retry_after(self, key: str) -> int | None:
        """Whole seconds to wait (rounded up), or ``None`` when the key is below the threshold."""
        history = self._failures.get(key)
        if history is None:
            return None
        now = self._clock.monotonic()
        while history and history[0] <= now - self._window:
            history.popleft()
        if not history:
            del self._failures[key]
            return None
        count = len(history)
        if count < self._threshold:
            return None
        delay = min(
            self._max, self._base * 2 ** min(count - self._threshold, _MAX_BACKOFF_EXPONENT)
        )
        remaining = history[-1] + delay - now
        return math.ceil(remaining) if remaining > 0 else None

    def record_failure(self, key: str) -> None:
        history = self._failures.get(key)
        if history is None:
            history = self._failures[key] = deque(maxlen=self._history_len)
        history.append(self._clock.monotonic())
        self._failures.move_to_end(key)
        while len(self._failures) > self._max_entries:
            self._failures.popitem(last=False)


class _Gate:
    """One username's lock plus the number of tasks holding or waiting for it."""

    __slots__ = ("lock", "users")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.users = 0


class UsernameGates:
    """One ``asyncio.Lock`` per normalized username, so attempts on one account serialize.

    The table is a bounded LRU. An entry that is held **or awaited** is never evicted, so two
    tasks can never end up on different locks for the same name.
    """

    def __init__(self, *, max_entries: int = USERNAME_GATE_MAX_ENTRIES) -> None:
        self._max_entries = max_entries
        self._gates: OrderedDict[str, _Gate] = OrderedDict()

    @asynccontextmanager
    async def hold(self, username: str) -> AsyncIterator[None]:
        gate = self._gates.get(username)
        if gate is None:
            gate = self._gates[username] = _Gate()
        self._gates.move_to_end(username)
        gate.users += 1
        self._evict()
        try:
            async with gate.lock:
                yield
        finally:
            gate.users -= 1
            self._evict()

    def _evict(self) -> None:
        if len(self._gates) <= self._max_entries:
            return
        for name in [n for n, g in self._gates.items() if g.users == 0]:
            if len(self._gates) <= self._max_entries:
                break
            del self._gates[name]
