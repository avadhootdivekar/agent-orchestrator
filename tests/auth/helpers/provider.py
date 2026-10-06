"""Local-provider test helpers (owner: T-XchniS; HLD section 20.2). Test-only.

``make_env`` wires a real ``LocalPasswordProvider`` over real file-backed stores in a private temp
directory, with a fixed clock, seeded entropy and the instant ``FastFakeHasher``, so tests exercise
the production flock + atomic-write paths without paying for scrypt.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_orchestrator.auth.audit import AuditLog
from agent_orchestrator.auth.constants import (
    DEFAULT_LOCKOUT_BASE_SECONDS,
    DEFAULT_LOCKOUT_MAX_SECONDS,
    DEFAULT_LOCKOUT_THRESHOLD,
    DEFAULT_MIN_PASSWORD_LENGTH,
)
from agent_orchestrator.auth.guard import AttemptGuard
from agent_orchestrator.auth.local_provider import LocalPasswordProvider
from agent_orchestrator.auth.lockouts import LockoutPolicy, LockoutStore
from agent_orchestrator.auth.model import SessionState, TotpPolicy
from agent_orchestrator.auth.paths import StorePaths
from agent_orchestrator.auth.provider import ClientInfo
from agent_orchestrator.auth.sessions import IssuedSession, SessionManager
from agent_orchestrator.auth.settings import AuthSettings
from agent_orchestrator.auth.store import UserStore, add_user
from agent_orchestrator.auth.throttle import AddressThrottle, UsernameGates

from .core import FakeClock, SeededEntropy
from .crypto import FastFakeHasher
from .sessions import make_identity, make_manager
from .store import make_store_paths

PASSWORD = "correct horse battery"
CLIENT = ClientInfo("203.0.113.9", False, False)
OTHER_CLIENT = ClientInfo("198.51.100.7", False, False)
FAKE_HASH_PREFIX = "fake$"  # FastFakeHasher.hash(raw) == prefix + raw
ADDRESS_THRESHOLD = 20


def fake_hash(raw: str) -> str:
    return FAKE_HASH_PREFIX + raw


class RehashingHasher(FastFakeHasher):
    """Always wants a rehash; its new hashes carry a marker so a rewrite is observable."""

    marker = "#v2"

    def __init__(self) -> None:
        super().__init__(rehash_needed=True)

    async def verify(self, raw: str, encoded: str) -> bool:
        return await super().verify(raw, encoded.removesuffix(self.marker))

    async def hash(self, raw: str) -> str:
        return await super().hash(raw) + self.marker


def make_settings(tmp_path: Path, **overrides: Any) -> AuthSettings:
    """Enabled ``AuthSettings`` over ``<tmp>/store`` and ``<tmp>/state`` (HLD 12.6 defaults)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    paths = make_store_paths(tmp_path)
    values: dict[str, Any] = {
        "enabled": True,
        "totp": TotpPolicy.OPTIONAL,
        "session_idle_seconds": 1800,
        "session_absolute_seconds": 43_200,
        "lockout_threshold": DEFAULT_LOCKOUT_THRESHOLD,
        "lockout_base_seconds": DEFAULT_LOCKOUT_BASE_SECONDS,
        "lockout_max_seconds": DEFAULT_LOCKOUT_MAX_SECONDS,
        "address_threshold": ADDRESS_THRESHOLD,
        "min_password_length": DEFAULT_MIN_PASSWORD_LENGTH,
        "store_dir": paths.store_dir,
        "state_dir": paths.state_dir,
        "trusted_proxies": (),
        "totp_issuer": "ao@test",
        "sources": {"enabled": "cli", "store_dir": "cli", "state_dir": "derived"},
        "config_path": None,
        "warnings": (),
        "config_risks": frozenset(),
    }
    values.update(overrides)
    return AuthSettings(**values)


@dataclass
class ProviderEnv:
    settings: AuthSettings
    paths: StorePaths
    clock: FakeClock
    store: UserStore
    lockouts: LockoutStore
    audit: AuditLog
    throttle: AddressThrottle
    hasher: FastFakeHasher
    guard: AttemptGuard
    provider: LocalPasswordProvider
    sessions: SessionManager

    def audit_lines(self) -> list[dict[str, Any]]:
        return read_audit(self.paths)

    def audit_events(self, name: str) -> list[dict[str, Any]]:
        return [line for line in self.audit_lines() if line["event"] == name]

    def session_for(self, username: str, *, state: SessionState = SessionState.FULL) -> Any:
        """A live session record for ``username`` (epoch and ids as currently stored)."""
        rec = self.store.snapshot().users[username]
        identity = make_identity(
            user_id=rec.user_id,
            username=username,
            credential_epoch=rec.credential_epoch,
            next_state=state,
        )
        issued: IssuedSession = self.sessions.issue(
            identity, state, client_key=CLIENT.key, auth_method="password"
        )
        return issued.record


def read_audit(paths: StorePaths) -> list[dict[str, Any]]:
    if not paths.audit_file.exists():
        return []
    return [json.loads(line) for line in paths.audit_file.read_text().splitlines() if line]


def make_env(
    tmp_path: Path,
    users: Sequence[str] = ("alice",),
    *,
    password: str = PASSWORD,
    totp: TotpPolicy = TotpPolicy.OPTIONAL,
    hasher: FastFakeHasher | None = None,
    threshold: int = DEFAULT_LOCKOUT_THRESHOLD,
    address_threshold: int = ADDRESS_THRESHOLD,
    realm: str = "hub",
    **settings_overrides: Any,
) -> ProviderEnv:
    """Users in ``users`` exist (epoch 1, ``fake_hash(password)``); both stores are file-backed."""
    clock = FakeClock()
    entropy = SeededEntropy(3)
    settings = make_settings(
        tmp_path,
        totp=totp,
        lockout_threshold=threshold,
        address_threshold=address_threshold,
        **settings_overrides,
    )
    paths = StorePaths.at(settings.store_dir, settings.state_dir)
    store = UserStore(paths, clock=clock, entropy=entropy)
    lockouts = LockoutStore(paths, clock=clock, entropy=SeededEntropy(5))
    audit = AuditLog(paths, clock=clock, strict=True)

    def seed(f: Any) -> None:
        for name in users:
            add_user(f, name, fake_hash(password), now=clock.now_utc(), entropy=entropy)

    store.mutate(seed, create=True)
    lockouts.ensure_name_key()  # what check_ready() does at startup (S6)
    throttle = AddressThrottle(address_threshold, clock=clock)
    hasher = hasher or FastFakeHasher()
    policy = LockoutPolicy(
        threshold=threshold,
        base_seconds=settings.lockout_base_seconds,
        max_seconds=settings.lockout_max_seconds,
    )
    guard = AttemptGuard(
        address_throttle=throttle,
        gates=UsernameGates(),
        lockouts=lockouts,
        lockout_policy=policy,
        audit=audit,
        realm=realm,
        clock=clock,
    )
    provider = LocalPasswordProvider(
        store, lockouts, guard, hasher, settings, audit, clock, realm=realm
    )
    sessions, _, _ = make_manager(clock=clock, realm=realm)
    return ProviderEnv(
        settings, paths, clock, store, lockouts, audit, throttle, hasher, guard, provider, sessions
    )
