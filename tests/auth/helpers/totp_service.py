"""Second-factor service test helpers (owner: T-yfrfxv; HLD section 20.2). Test-only.

``make_totp_env`` extends the T-XchniS ``ProviderEnv`` with a real :class:`LocalTotpService`
sharing the env's store, lockouts, guard and audit. ``spy`` counts calls to a class attribute.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.auth.errors import AuthError, ErrorCode
from agent_orchestrator.auth.model import SessionState
from agent_orchestrator.auth.seams import Entropy
from agent_orchestrator.auth.store import issue_enrollment_token
from agent_orchestrator.auth.totp_service import LocalTotpService

from .core import SeededEntropy
from .provider import ProviderEnv, make_env
from .store import RFC_KEY, enroll, totp_code

SERVICE_ENTROPY_SEED = 11
TOKEN_ENTROPY_SEED = 13


@dataclass
class TotpEnv:
    env: ProviderEnv
    svc: LocalTotpService
    entropy: Entropy

    def now_unix(self) -> float:
        return self.env.clock.now_utc().timestamp()

    def code(self, key: bytes = RFC_KEY, *, offset_steps: int = 0) -> str:
        """The valid TOTP code at the fake time (shifted by whole 30 s steps)."""
        return totp_code(self.now_unix() + 30 * offset_steps, key)

    def session(self, username: str = "alice", state: SessionState = SessionState.FULL) -> Any:
        return self.env.session_for(username, state=state)

    def enrolled(self, username: str = "alice") -> list[str]:
        """Enroll ``username`` with the RFC seed; returns the plaintext recovery codes."""
        return enroll(self.env.store, username)

    def issue_token(self, username: str = "alice") -> str:
        now = self.env.clock.now_utc()
        return self.env.store.mutate(
            lambda f: issue_enrollment_token(
                f, username, entropy=SeededEntropy(TOKEN_ENTROPY_SEED), now=now
            )
        )

    def user(self, username: str = "alice") -> Any:
        return self.env.store.snapshot().users[username]

    def users_bytes(self) -> bytes:
        return self.env.paths.users_file.read_bytes()


def make_totp_env(tmp_path: Path, **kwargs: Any) -> TotpEnv:
    env = make_env(tmp_path, **kwargs)
    entropy = SeededEntropy(SERVICE_ENTROPY_SEED)
    svc = LocalTotpService(
        env.store,
        env.lockouts,
        env.guard,
        env.provider,
        env.settings,
        env.audit,
        env.clock,
        entropy,
        realm="hub",
    )
    return TotpEnv(env, svc, entropy)


def spy(monkeypatch: pytest.MonkeyPatch, owner: Any, name: str) -> list[tuple[Any, ...]]:
    """Wrap ``owner.name`` (a class attribute) and return the list of call args."""
    calls: list[tuple[Any, ...]] = []
    original = getattr(owner, name)

    def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(owner, name, wrapper)
    return calls


def error_of(coro: Any) -> AuthError:
    """The ``AuthError`` a coroutine raises (fails the test when it does not)."""
    from .core import run_async

    with pytest.raises(AuthError) as caught:
        run_async(coro)
    return caught.value


def code_of(coro: Any) -> ErrorCode:
    return error_of(coro).code
