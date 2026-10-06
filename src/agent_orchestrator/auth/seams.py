"""Injectable time, randomness and thread-offload seams (HLD section 11.2, L0).

Core auth logic never calls ``time``/``secrets`` directly: it takes a :class:`Clock` and an
:class:`Entropy`, so tests inject ``FakeClock`` / ``SeededEntropy`` (``tests/auth/helpers``).
The system implementations here are the only place that touches the real clock and CSPRNG.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol, TypeVar

_log = logging.getLogger(__name__)

_T = TypeVar("_T")


class Clock(Protocol):
    def now_utc(self) -> datetime:
        """Timezone-aware UTC wall clock (display, audit, lockout timestamps, TOTP)."""
        ...

    def monotonic(self) -> float:
        """Monotonic seconds; the session-expiry timeline."""
        ...


class Entropy(Protocol):
    def token_bytes(self, n: int) -> bytes: ...


class SystemClock:
    """Real clock. ``monotonic()`` counts system suspend where the platform allows it.

    Linux ``CLOCK_BOOTTIME`` keeps running while the machine sleeps, so an idle session cannot
    outlive its timeout by suspending the laptop (D23). Elsewhere it falls back to
    ``time.monotonic()``, which may not count suspend; that is logged once at DEBUG.
    """

    def __init__(self) -> None:
        self._fallback_logged = False

    def now_utc(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        boottime = getattr(time, "CLOCK_BOOTTIME", None)
        if boottime is not None:
            return time.clock_gettime(boottime)
        if not self._fallback_logged:
            self._fallback_logged = True
            _log.debug("CLOCK_BOOTTIME unavailable; using time.monotonic (may not count suspend)")
        return time.monotonic()


class SystemEntropy:
    """CSPRNG-backed :class:`Entropy` (``secrets.token_bytes``)."""

    def token_bytes(self, n: int) -> bytes:
        return secrets.token_bytes(n)


SYSTEM_CLOCK: Clock = SystemClock()
SYSTEM_ENTROPY: Entropy = SystemEntropy()


async def run_sync(fn: Callable[..., _T], *args: object, **kwargs: object) -> _T:
    """Run a blocking callable in the default executor, keeping the event loop responsive."""
    return await asyncio.get_running_loop().run_in_executor(
        None, functools.partial(fn, *args, **kwargs)
    )
