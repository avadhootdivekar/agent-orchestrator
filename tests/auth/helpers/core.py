"""Core auth test doubles and helpers (owner: T-kzEzwy; HLD section 20.2).

Test-only: never imported by ``src/``. ``make_client`` imports ``starlette.testclient`` inside
the function, so this module stays importable without the ``[ui]`` extra.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Coroutine
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, TypeVar

from agent_orchestrator.auth.constants import SESSION_PROOF_HEADER

if TYPE_CHECKING:
    from starlette.testclient import TestClient

_T = TypeVar("_T")

#: The peer address every ``make_client`` request appears to come from (a loopback client).
TEST_CLIENT_ADDR = ("127.0.0.1", 50000)


class FakeClock:
    """Deterministic :class:`~agent_orchestrator.auth.seams.Clock`: wall and monotonic time move
    only when told to."""

    def __init__(
        self,
        start: datetime = datetime(2026, 1, 1, tzinfo=UTC),
        mono_start: float = 1000.0,
    ) -> None:
        self._wall = start
        self._mono = mono_start

    def now_utc(self) -> datetime:
        return self._wall

    def monotonic(self) -> float:
        return self._mono

    def advance(self, seconds: float) -> None:
        """Move wall and monotonic time together."""
        self.advance_wall(seconds)
        self.advance_mono(seconds)

    def advance_wall(self, seconds: float) -> None:
        self._wall += timedelta(seconds=seconds)

    def advance_mono(self, seconds: float) -> None:
        self._mono += seconds


class SeededEntropy:
    """Deterministic :class:`~agent_orchestrator.auth.seams.Entropy`. Tests only: not a CSPRNG."""

    def __init__(self, seed: int) -> None:
        self._rng = random.Random(seed)

    def token_bytes(self, n: int) -> bytes:
        return self._rng.randbytes(n)


def run_async(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run a coroutine to completion (``asyncio.run``; the repo has no pytest-asyncio)."""
    return asyncio.run(coro)


def make_client(app: Any) -> TestClient:
    """A ``TestClient`` that does not follow redirects and appears to be a loopback peer."""
    from starlette.testclient import TestClient

    return TestClient(app, client=TEST_CLIENT_ADDR, follow_redirects=False)


def same_origin_headers(client: TestClient, proof: str | None = None) -> dict[str, str]:
    """Headers a same-origin browser ``fetch`` would send, plus the session proof if given."""
    origin = str(client.base_url).rstrip("/")
    headers = {"Origin": origin, "Sec-Fetch-Site": "same-origin"}
    if proof is not None:
        headers[SESSION_PROOF_HEADER] = proof
    return headers
