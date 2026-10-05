"""Session test helpers (owner: T-kwwJ82; HLD section 20.2). Test-only."""

from __future__ import annotations

from typing import Any

from agent_orchestrator.auth.model import SessionState
from agent_orchestrator.auth.provider import VerifiedIdentity
from agent_orchestrator.auth.sessions import InMemorySessionStore, SessionManager, SessionStore
from tests.auth.helpers.core import FakeClock, SeededEntropy

TEST_REALM = "hub"
TEST_IDLE_SECONDS = 1800
TEST_ABSOLUTE_SECONDS = 43_200
TEST_ENTROPY_SEED = 7

_IDENTITY_DEFAULTS: dict[str, Any] = {
    "user_id": "a" * 32,
    "username": "alice",
    "roles": (),
    "credential_epoch": 1,
    "store_id": "store-1",
    "next_state": SessionState.FULL,
}


def make_identity(**overrides: Any) -> VerifiedIdentity:
    """A ``VerifiedIdentity`` with sensible defaults; keyword overrides replace any field."""
    return VerifiedIdentity(**{**_IDENTITY_DEFAULTS, **overrides})


def make_manager(
    *,
    store: SessionStore | None = None,
    clock: FakeClock | None = None,
    idle_seconds: int = TEST_IDLE_SECONDS,
    absolute_seconds: int = TEST_ABSOLUTE_SECONDS,
    realm: str = TEST_REALM,
    seed: int = TEST_ENTROPY_SEED,
) -> tuple[SessionManager, FakeClock, SessionStore]:
    """A ``SessionManager`` over an in-memory store with a fake clock and seeded entropy."""
    clock = clock or FakeClock()
    store = store if store is not None else InMemorySessionStore()
    manager = SessionManager(
        store,
        realm=realm,
        idle_seconds=idle_seconds,
        absolute_seconds=absolute_seconds,
        clock=clock,
        entropy=SeededEntropy(seed),
    )
    return manager, clock, store
