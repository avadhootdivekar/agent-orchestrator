"""Crypto test doubles (owner: T-s6sJmB; HLD sections 11.6 and 20.2).

Test-only: never imported by ``src/`` (reviewer finding R-12).
"""

from __future__ import annotations

from agent_orchestrator.auth.errors import BusyError
from agent_orchestrator.auth.passwords import CURRENT_PARAMS, ScryptParams, format_hash

#: The cheapest parameter set the parser accepts (about 1 ms per hash). Every test except the
#: single real-parameter one uses this.
TEST_PARAMS = ScryptParams(10, 8, 1)

_FAKE_PREFIX = "fake$"
_DUMMY_SALT_BYTES = 16
_DUMMY_DK_BYTES = 32


class FastFakeHasher:
    """An instant, deterministic :class:`~agent_orchestrator.auth.passwords.PasswordHasher`.

    ``hash(raw)`` is ``"fake$" + raw``; ``verify`` compares against that form and counts calls.
    ``dummy_hash`` is a real, parseable hash string at the current parameters, so code that
    parses it (e.g. ``needs_rehash`` on a phantom user) behaves as in production.
    """

    def __init__(self, *, rehash_needed: bool = False, raise_busy: bool = False) -> None:
        self.rehash_needed = rehash_needed
        self.raise_busy = raise_busy
        self.verify_calls = 0
        self.hash_calls = 0
        self.dummy_hash = format_hash(
            CURRENT_PARAMS, bytes(_DUMMY_SALT_BYTES), bytes(_DUMMY_DK_BYTES)
        )

    async def verify(self, raw: str, encoded: str) -> bool:
        self.verify_calls += 1
        if self.raise_busy:
            raise BusyError()
        return encoded == _FAKE_PREFIX + raw

    async def hash(self, raw: str) -> str:
        self.hash_calls += 1
        return _FAKE_PREFIX + raw

    def needs_rehash(self, encoded: str) -> bool:
        return self.rehash_needed
