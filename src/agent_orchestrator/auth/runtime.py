"""The auth runtime: the realm, the object graph and the one app-state accessor (HLD 11.16; L3).

``build_auth_runtime`` is pure construction: it never touches the disk beyond creating objects
(``prepare_auth`` calls ``provider.check_ready()``), and it never starts a thread of its own beyond
the hasher's pool. FastAPI-free: ``runtime_of`` reads ``app.state`` by the shared constant name.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .audit import AuditLog
from .constants import (
    APP_STATE_AUTH_KEY,
    COOKIE_BASENAME,
    HUB_LOGIN_PATH,
    HUB_REALM_ID,
    SECURE_COOKIE_PREFIX,
    WORKSPACE_ID_HEX_CHARS,
)
from .guard import AttemptGuard
from .local_provider import LocalPasswordProvider
from .lockouts import LockoutPolicy, LockoutStore
from .passwords import BoundedScryptHasher, PasswordHasher
from .paths import StorePaths
from .provider import AuthProvider
from .seams import SYSTEM_CLOCK, SYSTEM_ENTROPY, Clock, Entropy
from .sessions import InMemorySessionStore, SessionManager, SessionStore
from .settings import AuthSettings
from .store import UserStore
from .throttle import AddressThrottle, UsernameGates
from .totp_service import LocalTotpService

# The ``ui`` realm's cookie-less landing page: the SPA shell. The hub has its own login page.
UI_LOGIN_PATH = "/"


@dataclass(frozen=True)
class Realm:
    kind: Literal["ui", "hub"]
    port: int  # ONLY for the cookie name (a port may change across restarts; the id must not)
    workspace_root: Path | None = None  # dashboards only

    def __post_init__(self) -> None:
        if self.kind == "ui" and self.workspace_root is None:
            raise ValueError("a ui realm needs a workspace_root (its id is derived from it)")

    @property
    def id(self) -> str:
        """A stable label (D3): ``hub``, or ``ui:`` + a digest of the resolved workspace root."""
        if self.kind == "hub" or self.workspace_root is None:
            return HUB_REALM_ID
        digest = hashlib.sha256(str(self.workspace_root.resolve()).encode()).hexdigest()
        return f"ui:{digest[:WORKSPACE_ID_HEX_CHARS]}"

    def cookie_name(self, *, secure: bool) -> str:
        return f"{SECURE_COOKIE_PREFIX if secure else ''}{COOKIE_BASENAME}_{self.port}"

    @property
    def login_path(self) -> str:
        return HUB_LOGIN_PATH if self.kind == "hub" else UI_LOGIN_PATH


@dataclass
class AuthRuntime:
    settings: AuthSettings
    realm: Realm
    paths: StorePaths
    store: UserStore
    lockouts: LockoutStore
    audit: AuditLog
    provider: AuthProvider  # the ABC: LocalPasswordProvider in production, a fake in seam tests
    totp: LocalTotpService | None  # None for providers without a local second factor
    sessions: SessionManager
    address_throttle: AddressThrottle
    clock: Clock
    first_insecure_login_warned: bool = False
    proxy_suspected_warned: bool = False  # v2.1 (security M2): one WARNING per process (D17)


def build_auth_runtime(
    settings: AuthSettings,
    realm: Realm,
    *,
    clock: Clock = SYSTEM_CLOCK,
    entropy: Entropy = SYSTEM_ENTROPY,
    hasher: PasswordHasher | None = None,
    audit: AuditLog | None = None,
    session_store: SessionStore | None = None,
    provider: AuthProvider | None = None,
) -> AuthRuntime:
    """Wire the auth object graph for ``realm``.

    Requires ``settings.enabled``. Does NOT call ``check_ready()`` (``prepare_auth`` does). An
    injected ``provider`` is used as given; otherwise a :class:`LocalPasswordProvider` is built
    over the shared store, lockouts, guard and hasher. Two realms in one process (or two
    processes) share the account lockout through ``lockouts.json``, never through memory.
    """
    if not settings.enabled:
        raise ValueError("build_auth_runtime requires settings.enabled")
    paths = StorePaths.at(settings.store_dir, settings.state_dir)
    store = UserStore(paths, clock=clock, entropy=entropy)
    lockouts = LockoutStore(paths, clock=clock, entropy=entropy)
    audit = audit if audit is not None else AuditLog(paths, clock=clock)
    sessions = SessionManager(
        session_store if session_store is not None else InMemorySessionStore(),
        realm=realm.id,
        idle_seconds=settings.session_idle_seconds,
        absolute_seconds=settings.session_absolute_seconds,
        clock=clock,
        entropy=entropy,
    )
    throttle = AddressThrottle(settings.address_threshold, clock=clock)
    if provider is None:
        provider = _local_provider(
            settings, realm, store, lockouts, audit, throttle, hasher, clock, entropy
        )
    return AuthRuntime(
        settings=settings,
        realm=realm,
        paths=paths,
        store=store,
        lockouts=lockouts,
        audit=audit,
        provider=provider,
        totp=_local_totp(provider, settings, realm, store, lockouts, audit, clock, entropy),
        sessions=sessions,
        address_throttle=throttle,
        clock=clock,
    )


def _local_totp(
    provider: AuthProvider,
    settings: AuthSettings,
    realm: Realm,
    store: UserStore,
    lockouts: LockoutStore,
    audit: AuditLog,
    clock: Clock,
    entropy: Entropy,
) -> LocalTotpService | None:
    """The second-factor service shares the provider's guard; other providers have none."""
    if not isinstance(provider, LocalPasswordProvider):
        return None
    return LocalTotpService(
        store, lockouts, provider.guard, provider, settings, audit, clock, entropy, realm=realm.id
    )


def _local_provider(
    settings: AuthSettings,
    realm: Realm,
    store: UserStore,
    lockouts: LockoutStore,
    audit: AuditLog,
    throttle: AddressThrottle,
    hasher: PasswordHasher | None,
    clock: Clock,
    entropy: Entropy,
) -> LocalPasswordProvider:
    guard = AttemptGuard(
        address_throttle=throttle,
        gates=UsernameGates(),
        lockouts=lockouts,
        lockout_policy=LockoutPolicy(
            threshold=settings.lockout_threshold,
            base_seconds=settings.lockout_base_seconds,
            max_seconds=settings.lockout_max_seconds,
        ),
        audit=audit,
        realm=realm.id,
        clock=clock,
    )
    return LocalPasswordProvider(
        store,
        lockouts,
        guard,
        hasher if hasher is not None else BoundedScryptHasher(entropy=entropy),
        settings,
        audit,
        clock,
        realm=realm.id,
    )


def runtime_of(app: Any) -> AuthRuntime | None:
    """The ONE accessor for the runtime on an app (R5); ``None`` when auth is off."""
    runtime: AuthRuntime | None = getattr(app.state, APP_STATE_AUTH_KEY, None)
    return runtime


def audit_log_for(request: Any) -> AuditLog | None:
    """The audit log for the approvals epic's seam (HLD 2.6): ``None`` when auth is off."""
    runtime = runtime_of(request.app)
    return runtime.audit if runtime is not None else None
