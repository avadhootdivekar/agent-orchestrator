"""Duck-typed auth runtime and route stubs (owner: T-G7qByZ; HLD sections 13.2 and 20.2). Test-only.

``StubRuntime`` / ``StubRealm`` carry the exact HLD 11.16 attribute names the HTTP edge reads
(``realm``, ``sessions``, ``provider``, ``clock``, ``totp`` ...) and use the *real*
``SessionManager`` over a fake clock. ``StubProvider`` answers ``revalidate`` from a tiny JSON
file, so "corrupt ``users.json``" (UNAVAILABLE) and "epoch bumped" (REVOKED) can be staged
without the real user store. ``install_stub_auth_routes`` registers trivial flat handlers at all
ten ``/api/auth/*`` paths so the route enumeration sees the full allowlist before the real
routes exist.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI
from starlette.types import Receive, Scope, Send

from agent_orchestrator.auth.constants import (
    APP_STATE_AUTH_KEY,
    AUTH_ENROLL_BEGIN_PATH,
    AUTH_ENROLL_CONFIRM_PATH,
    AUTH_KEEPALIVE_PATH,
    AUTH_LOGIN_PATH,
    AUTH_LOGOUT_PATH,
    AUTH_PASSWORD_PATH,
    AUTH_RECOVERY_CODES_PATH,
    AUTH_STATUS_PATH,
    AUTH_TOTP_DISABLE_PATH,
    AUTH_TOTP_VERIFY_PATH,
    COOKIE_BASENAME,
    HUB_LOGIN_PATH,
    HUB_REALM_ID,
    SECURE_COOKIE_PREFIX,
    WORKSPACE_ID_HEX_CHARS,
)
from agent_orchestrator.auth.model import SessionState
from agent_orchestrator.auth.provider import Revalidation
from agent_orchestrator.auth.sessions import IssuedSession, SessionManager
from tests.auth.helpers.core import FakeClock
from tests.auth.helpers.sessions import make_identity, make_manager

STUB_PORT = 8765
STUB_USER_ID = "a" * 32
STUB_CLIENT_KEY = "127.0.0.1"

# The ten auth routes of HLD 2.4 (E1-E10): (method, path).
STUB_AUTH_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", AUTH_STATUS_PATH),
    ("POST", AUTH_LOGIN_PATH),
    ("POST", AUTH_TOTP_VERIFY_PATH),
    ("POST", AUTH_ENROLL_BEGIN_PATH),
    ("POST", AUTH_ENROLL_CONFIRM_PATH),
    ("POST", AUTH_TOTP_DISABLE_PATH),
    ("POST", AUTH_RECOVERY_CODES_PATH),
    ("POST", AUTH_PASSWORD_PATH),
    ("POST", AUTH_KEEPALIVE_PATH),
    ("POST", AUTH_LOGOUT_PATH),
)


@dataclass(frozen=True)
class StubRealm:
    """Same shape as ``runtime.Realm`` (HLD 11.16)."""

    kind: Literal["ui", "hub"] = "ui"
    port: int = STUB_PORT
    workspace_root: Path | None = Path("/stub/workspace")

    @property
    def id(self) -> str:
        if self.kind == "hub" or self.workspace_root is None:
            return HUB_REALM_ID
        digest = hashlib.sha256(str(self.workspace_root.resolve()).encode()).hexdigest()
        return f"ui:{digest[:WORKSPACE_ID_HEX_CHARS]}"

    def cookie_name(self, *, secure: bool) -> str:
        return f"{SECURE_COOKIE_PREFIX if secure else ''}{COOKIE_BASENAME}_{self.port}"

    @property
    def login_path(self) -> str:
        return HUB_LOGIN_PATH if self.kind == "hub" else "/"


class StubProvider:
    """``revalidate`` backed by ``users.json``-shaped state: ``{"<user_id>": credential_epoch}``."""

    def __init__(self, users_file: Path) -> None:
        self.users_file = users_file
        self.calls = 0
        self._users: dict[str, int] = {}
        self._write()

    def _write(self) -> None:
        self.users_file.write_text(json.dumps(self._users), encoding="utf-8")

    def register(self, user_id: str = STUB_USER_ID, epoch: int = 1) -> None:
        self._users[user_id] = epoch
        self._write()

    def bump_epoch(self, user_id: str = STUB_USER_ID) -> None:
        self._users[user_id] += 1
        self._write()

    def corrupt(self) -> None:
        self.users_file.write_text("{not json", encoding="utf-8")

    def restore(self) -> None:
        self._write()

    def revalidate(self, user_id: str, credential_epoch: int) -> Revalidation:
        self.calls += 1
        try:
            users = json.loads(self.users_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return Revalidation.UNAVAILABLE
        if users.get(user_id) == credential_epoch:
            return Revalidation.VALID
        return Revalidation.REVOKED


@dataclass
class StubRuntime:
    """Duck-typed ``AuthRuntime`` (HLD 11.16): the attributes the HTTP edge reads."""

    realm: StubRealm
    sessions: SessionManager
    provider: StubProvider
    clock: FakeClock
    idle_seconds: int
    session_store: Any = None  # the SessionStore behind ``sessions`` (tests peek at records)
    settings: Any = None
    paths: Any = None
    store: Any = None
    lockouts: Any = None
    audit: Any = None
    totp: Any = None
    address_throttle: Any = None
    first_insecure_login_warned: bool = False
    proxy_suspected_warned: bool = False
    issued: list[IssuedSession] = field(default_factory=list)

    @property
    def cookie_name(self) -> str:
        return self.realm.cookie_name(secure=False)

    def record_of(self, issued: IssuedSession) -> Any:
        """The stored ``SessionRecord`` for ``issued`` (``None`` once destroyed)."""
        return self.session_store.get(issued.record.token_hash)

    def issue(
        self,
        state: SessionState = SessionState.FULL,
        *,
        user_id: str = STUB_USER_ID,
        roles: tuple[str, ...] = (),
    ) -> IssuedSession:
        """A fresh session of ``state`` for a user the provider knows (epoch 1)."""
        return issue_session(self, state, user_id=user_id, roles=roles)


def issue_session(
    runtime: Any,
    state: SessionState = SessionState.FULL,
    *,
    user_id: str = STUB_USER_ID,
    roles: tuple[str, ...] = (),
) -> IssuedSession:
    """A fresh session of ``state`` for ``user_id`` on ``runtime`` (stub or real; epoch as stored).

    The runtime's provider must offer the ``StubProvider`` staging surface (``_users``,
    ``register``); ``runtime.issued`` collects the result.
    """
    if user_id not in runtime.provider._users:
        runtime.provider.register(user_id)
    identity = make_identity(
        user_id=user_id,
        roles=roles,
        next_state=state,
        credential_epoch=runtime.provider._users[user_id],
    )
    full = state is SessionState.FULL
    issued = runtime.sessions.issue(
        identity,
        state,
        client_key=STUB_CLIENT_KEY,
        auth_method="password" if full else None,
    )
    runtime.issued.append(issued)
    return issued


def make_stub_runtime(
    tmp_path: Path,
    *,
    clock: FakeClock | None = None,
    kind: Literal["ui", "hub"] = "ui",
    idle_seconds: int = 1800,
    absolute_seconds: int = 43_200,
) -> StubRuntime:
    realm = StubRealm(kind=kind, workspace_root=tmp_path if kind == "ui" else None)
    manager, fake_clock, session_store = make_manager(
        clock=clock, realm=realm.id, idle_seconds=idle_seconds, absolute_seconds=absolute_seconds
    )
    return StubRuntime(
        realm=realm,
        sessions=manager,
        provider=StubProvider(tmp_path / "users.json"),
        clock=fake_clock,
        idle_seconds=idle_seconds,
        session_store=session_store,
    )


# --- routes ---------------------------------------------------------------------------------


def move_routes_first(app: FastAPI, count: int) -> None:
    """Move the last ``count`` routes to the front, ahead of any SPA fallback or mount.

    ``install_auth_routes`` runs before every other route in ``create_app``; a test that adds
    routes afterwards needs the same first-match position.
    """
    routes = app.router.routes
    routes[:] = routes[-count:] + routes[:-count]


def add_probe_route(
    app: FastAPI, path: str, endpoint: Callable[..., Any], methods: list[str]
) -> None:
    """Add a flat route at the front of the router (see :func:`move_routes_first`)."""
    app.add_api_route(path, endpoint, methods=methods, include_in_schema=False)
    move_routes_first(app, 1)


def install_stub_auth_routes(app: FastAPI) -> None:
    """Trivial **flat** handlers at all ten ``/api/auth/*`` paths (HLD 13.2)."""
    for method, path in STUB_AUTH_ROUTES:

        async def handler(_path: str = path) -> dict[str, str]:
            return {"stub": _path}

        app.add_api_route(path, handler, methods=[method], name=f"stub{path.replace('/', '_')}")
    move_routes_first(app, len(STUB_AUTH_ROUTES))


def stub_install_auth_routes(app: FastAPI, runtime: Any | None) -> None:
    """Drop-in for ``routes.install_auth_routes`` until T-rpKCjP: state plus the stub routes."""
    setattr(app.state, APP_STATE_AUTH_KEY, runtime)
    install_stub_auth_routes(app)


# --- scope capture --------------------------------------------------------------------------


class ScopeCapture:
    """A transparent proxy around ``app.router`` recording each HTTP request's scope state.

    What it records is what the *application* sees after every middleware has run, so it proves
    (not assumes) that no principal reached a route. It records nothing for a request the
    middleware denied. Install before the first request (the middleware stack is built lazily).
    """

    def __init__(self, router: Any) -> None:
        self._router = router
        self.states: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self._router, name)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            self.states.append(dict(scope.get("state", {})))
        await self._router(scope, receive, send)


def capture_scope(app: FastAPI) -> ScopeCapture:
    capture = ScopeCapture(app.router)
    app.router = capture  # type: ignore[assignment]
    return capture
