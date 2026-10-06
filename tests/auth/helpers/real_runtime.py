"""A real ``AuthRuntime`` with the ``StubRuntime`` test surface (owner: T-XchniS; HLD section 20.2).

T-G7qByZ wrote the HTTP-edge tests against ``StubRuntime`` (duck-typed, ``users.json``-shaped
fake provider). This module is the "final wiring": the same tests also run against
``build_auth_runtime(...)`` with a real ``LocalPasswordProvider`` over a real ``UserStore``.
``RealRuntime`` *is* an ``AuthRuntime`` (a dataclass subclass), so the middleware sees exactly the
production type; the extra helpers (``issue``, ``record_of``, ``provider.bump_epoch`` ...) only
stage state. Test-only: never imported by ``src/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Literal

from agent_orchestrator.auth.constants import LOCAL_PROVIDER_ID
from agent_orchestrator.auth.local_provider import LocalPasswordProvider
from agent_orchestrator.auth.model import SessionState
from agent_orchestrator.auth.provider import AuthProvider, Revalidation, UserView
from agent_orchestrator.auth.runtime import AuthRuntime, Realm, build_auth_runtime
from agent_orchestrator.auth.sessions import InMemorySessionStore, IssuedSession
from agent_orchestrator.auth.store import UserStore, add_user
from tests.auth.helpers.core import FakeClock, SeededEntropy
from tests.auth.helpers.crypto import FastFakeHasher
from tests.auth.helpers.provider import fake_hash, make_settings
from tests.auth.helpers.stub_runtime import (
    STUB_PORT,
    STUB_USER_ID,
    StubRuntime,
    issue_session,
    make_stub_runtime,
)

RuntimeImpl = Literal["stub", "real"]
REAL_RUNTIME_ENTROPY_SEED = 7
HARNESS_PASSWORD = "harness-password"
CORRUPT_STORE_TEXT = "{not json"


class HarnessProvider(AuthProvider):
    """The real ``LocalPasswordProvider`` plus the staging helpers of ``StubProvider``.

    ``revalidate`` is delegated untouched (and counted), so every middleware revalidation in a
    "real" run goes through the production code and the real ``users.json``.
    """

    provider_id = LOCAL_PROVIDER_ID

    def __init__(self, inner: LocalPasswordProvider, store: UserStore, clock: FakeClock) -> None:
        self.inner = inner
        self._store = store
        self._clock = clock
        self.calls = 0
        self._good: bytes | None = None

    @property
    def _users(self) -> dict[str, int]:
        return {u.user_id: u.credential_epoch for u in self._store.snapshot().users.values()}

    def register(self, user_id: str = STUB_USER_ID, epoch: int = 1) -> None:
        """A real user record with this ``user_id`` and ``credential_epoch``."""

        def add(f: Any) -> None:
            rec = add_user(
                f,
                f"user-{user_id[:8]}",
                fake_hash(HARNESS_PASSWORD),
                now=self._clock.now_utc(),
                entropy=SeededEntropy(1),
            )
            rec.user_id, rec.credential_epoch = user_id, epoch

        self._store.mutate(add, create=True)

    def bump_epoch(self, user_id: str = STUB_USER_ID) -> None:
        def bump(f: Any) -> None:
            for rec in f.users.values():
                if rec.user_id == user_id:
                    rec.credential_epoch += 1

        self._store.mutate(bump)

    def corrupt(self) -> None:
        path = self._store.paths.users_file
        self._good = path.read_bytes() if path.exists() else None
        path.write_text(CORRUPT_STORE_TEXT, encoding="utf-8")

    def restore(self) -> None:
        if self._good is not None:
            self._store.paths.users_file.write_bytes(self._good)

    def check_ready(self) -> None:
        self.inner.check_ready()

    def revalidate(self, user_id: str, credential_epoch: int) -> Revalidation:
        self.calls += 1
        return self.inner.revalidate(user_id, credential_epoch)

    def user_view(self, username: str) -> UserView | None:
        return self.inner.user_view(username)

    def startup_warnings(self) -> list[str]:
        return self.inner.startup_warnings()


@dataclass
class RealRuntime(AuthRuntime):
    """An ``AuthRuntime`` carrying ``StubRuntime``'s helper surface."""

    session_store: Any = None  # the SessionStore behind ``sessions`` (tests peek at records)
    idle_seconds: int = 0
    issued: list[IssuedSession] = field(default_factory=list)

    @property
    def cookie_name(self) -> str:
        return self.realm.cookie_name(secure=False)

    def record_of(self, issued: IssuedSession) -> Any:
        return self.session_store.get(issued.record.token_hash)

    def issue(
        self,
        state: SessionState = SessionState.FULL,
        *,
        user_id: str = STUB_USER_ID,
        roles: tuple[str, ...] = (),
    ) -> IssuedSession:
        return issue_session(self, state, user_id=user_id, roles=roles)


def make_real_runtime(
    tmp_path: Path,
    *,
    clock: FakeClock | None = None,
    kind: Literal["ui", "hub"] = "ui",
    idle_seconds: int = 1800,
    absolute_seconds: int = 43_200,
) -> RealRuntime:
    clock = clock or FakeClock()
    settings = make_settings(
        tmp_path, session_idle_seconds=idle_seconds, session_absolute_seconds=absolute_seconds
    )
    realm = Realm(kind, STUB_PORT, tmp_path if kind == "ui" else None)
    session_store = InMemorySessionStore()
    base = build_auth_runtime(
        settings,
        realm,
        clock=clock,
        entropy=SeededEntropy(REAL_RUNTIME_ENTROPY_SEED),
        hasher=FastFakeHasher(),
        session_store=session_store,
    )
    assert isinstance(base.provider, LocalPasswordProvider)
    values = {f.name: getattr(base, f.name) for f in fields(AuthRuntime)}
    base.store.mutate(lambda f: None, create=True)  # an empty users.json, like StubProvider's
    values["provider"] = HarnessProvider(base.provider, base.store, clock)
    return RealRuntime(**values, session_store=session_store, idle_seconds=idle_seconds)


def make_runtime(
    impl: RuntimeImpl,
    tmp_path: Path,
    *,
    clock: FakeClock | None = None,
    kind: Literal["ui", "hub"] = "ui",
    idle_seconds: int = 1800,
    absolute_seconds: int = 43_200,
) -> StubRuntime | RealRuntime:
    """The runtime under test: the duck-typed stub or the real ``AuthRuntime``."""
    factory = make_real_runtime if impl == "real" else make_stub_runtime
    return factory(
        tmp_path,
        clock=clock,
        kind=kind,
        idle_seconds=idle_seconds,
        absolute_seconds=absolute_seconds,
    )
