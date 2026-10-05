"""Harness for the REAL core auth routes (owner: T-rpKCjP; HLD section 20.2). Test-only.

``dash_factory`` builds dashboards (the real ``create_app``, or a bare hub-realm app) over a real
``AuthRuntime``: a real ``LocalPasswordProvider`` on file-backed stores with the instant
``FastFakeHasher``, a ``FakeClock`` and seeded entropy. Apps built from one factory (and one
``settings``) share the user store, lockout state and audit log -- like two ``ao ui`` processes
on one machine -- but each has its own in-memory session table.

Test modules import the fixture by name::

    from tests.auth.helpers import real_routes

    dash_factory = real_routes.dash_factory
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pytest
from fastapi import FastAPI
from httpx import Response
from starlette.testclient import TestClient

import agent_orchestrator.ui.app as ui_app
from agent_orchestrator.auth.audit import AuditLog
from agent_orchestrator.auth.constants import SESSION_PROOF_HEADER
from agent_orchestrator.auth.http.middleware import AuthMiddleware
from agent_orchestrator.auth.http.routes import install_auth_routes
from agent_orchestrator.auth.model import SessionState, TotpPolicy
from agent_orchestrator.auth.paths import StorePaths
from agent_orchestrator.auth.policy import HUB_COOKIE_ONLY_NAVIGATION, HUB_ROUTE_POLICIES
from agent_orchestrator.auth.runtime import AuthRuntime, Realm, build_auth_runtime
from agent_orchestrator.auth.sessions import InMemorySessionStore, IssuedSession
from agent_orchestrator.auth.settings import AuthSettings
from agent_orchestrator.auth.store import (
    RecoveryCodeHash,
    TotpEnrollment,
    UserStoreFile,
    add_user,
)
from agent_orchestrator.ui.service import DashboardService
from tests.auth.helpers.core import FakeClock, SeededEntropy
from tests.auth.helpers.crypto import FastFakeHasher
from tests.auth.helpers.provider import PASSWORD, fake_hash, make_settings, read_audit

DASH_PORT = 8765
HUB_PORT = 8770
LOOPBACK_PEER = ("127.0.0.1", 50000)
LOOPBACK_BASE_URL = "http://127.0.0.1:8765"
AUTO = object()  # "send the proof this Dash last learned"
JSON_HEADERS = {"content-type": "application/json"}
SEED_STEP = 101  # entropy seed offset between runtimes built by one factory


def enroll_totp_directly(f: UserStoreFile, username: str) -> None:
    """Mark ``username`` TOTP-enrolled in the store (the core routes never read the secret)."""
    f.users[username].totp = TotpEnrollment(
        secret_b32="JBSWY3DPEHPK3PXP", enrolled_at="2026-01-01T00:00:00Z", last_used_step=0
    )
    f.users[username].recovery_codes = [
        RecoveryCodeHash(salt_hex="00", hash_hex="00") for _ in range(10)
    ]


@dataclass
class Dash:
    """One app + client + runtime. ``proof`` follows the latest session-issuing response."""

    runtime: AuthRuntime
    app: FastAPI
    client: TestClient
    clock: FakeClock
    session_store: InMemorySessionStore
    proof: str | None = None
    cookie_value: str | None = None
    extra_clients: list[TestClient] = field(default_factory=list)

    # -- requests ------------------------------------------------------------------------------

    def headers(self, proof: Any = AUTO, *, client: TestClient | None = None) -> dict[str, str]:
        client = client or self.client
        origin = str(client.base_url).rstrip("/")
        headers = {"Origin": origin, "Sec-Fetch-Site": "same-origin", **JSON_HEADERS}
        sent = self.proof if proof is AUTO else proof
        if sent is not None:
            headers[SESSION_PROOF_HEADER] = sent
        return headers

    def post(
        self,
        path: str,
        body: Any = None,
        *,
        proof: Any = AUTO,
        raw: bytes | None = None,
        client: TestClient | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        client = client or self.client
        merged = {**self.headers(proof, client=client), **(headers or {})}
        if raw is not None:
            return client.post(path, content=raw, headers=merged)
        if body is None:
            return client.post(path, headers=merged)
        return client.post(path, json=body, headers=merged)

    def get(self, path: str, *, proof: Any = AUTO, client: TestClient | None = None) -> Response:
        client = client or self.client
        return client.get(path, headers=self.headers(proof, client=client))

    def login(
        self,
        username: str = "alice",
        password: str = PASSWORD,
        *,
        proof: Any = AUTO,
        client: TestClient | None = None,
        remember: bool = True,
    ) -> Response:
        response = self.post(
            "/api/auth/login",
            {"username": username, "password": password},
            proof=proof,
            client=client,
        )
        if remember and response.status_code == 200:
            self.proof = response.json()["session_proof"]
            self.cookie_value = (client or self.client).cookies.get(self.cookie_name)
        return response

    def status(self, proof: Any = AUTO) -> dict[str, Any]:
        response = self.get("/api/auth/status", proof=proof)
        assert response.status_code == 200
        body: dict[str, Any] = response.json()
        return body

    def protected(self, proof: Any = AUTO, *, client: TestClient | None = None) -> Response:
        """A representative AUTHENTICATED route of the dashboard (proof required)."""
        return self.get("/api/workspace", proof=proof, client=client)

    # -- state ---------------------------------------------------------------------------------

    @property
    def cookie_name(self) -> str:
        secure = str(self.client.base_url).startswith("https")
        return self.runtime.realm.cookie_name(secure=secure)

    def set_cookie(self, value: str, *, client: TestClient | None = None) -> None:
        target = client or self.client
        jar = target.cookies
        jar.delete(self.cookie_name)
        # Same domain as the server's own Set-Cookie, so a later login REPLACES it (no duplicate).
        jar.set(self.cookie_name, value, domain=target.base_url.host)

    def new_browser(self) -> TestClient:
        """A second client (own cookie jar) on the same app."""
        client = TestClient(
            self.app,
            base_url=str(self.client.base_url),
            client=LOOPBACK_PEER,
            follow_redirects=False,
        )
        self.extra_clients.append(client)
        return client

    def audit_events(self, *names: str) -> list[dict[str, Any]]:
        lines = read_audit(self.runtime.paths)
        return [line for line in lines if not names or line["event"] in names]

    def epoch(self, username: str = "alice") -> int:
        return self.runtime.store.snapshot().users[username].credential_epoch

    def issue(
        self, state: SessionState = SessionState.FULL, username: str = "alice"
    ) -> IssuedSession:
        """A session of ``state`` straight from the session manager (bypassing login)."""
        from tests.auth.helpers.sessions import make_identity

        snapshot = self.runtime.store.snapshot()
        rec = snapshot.users[username]
        identity = make_identity(
            user_id=rec.user_id,
            username=username,
            credential_epoch=rec.credential_epoch,
            store_id=snapshot.store_id,
            next_state=state,
        )
        return self.runtime.sessions.issue(
            identity,
            state,
            client_key="127.0.0.1",
            auth_method="password" if state is SessionState.FULL else None,
        )

    def issue_full(self, username: str = "alice") -> IssuedSession:
        return self.issue(SessionState.FULL, username)


DashFactory = Callable[..., Dash]


@pytest.fixture()
def dash_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dashboard_service: DashboardService,
    static_dir: Path,
    fake_clock: FakeClock,
) -> DashFactory:
    """``factory(**options) -> Dash``; see :func:`build_dash` for the options."""
    built: list[Dash] = []

    def factory(*, built_frontend: bool = True, **options: Any) -> Dash:
        # ``built_frontend=False`` points STATIC_DIR at a missing directory (the unbuilt SPA).
        monkeypatch.setattr(
            ui_app, "STATIC_DIR", static_dir if built_frontend else static_dir / "missing"
        )
        options.setdefault("clock", fake_clock)
        options.setdefault("seed", 11 + SEED_STEP * len(built))
        if built and "settings" not in options:
            options["settings"] = built[0].runtime.settings
        dash = build_dash(tmp_path, dashboard_service, **options)
        built.append(dash)
        return dash

    return factory


def build_dash(
    tmp_path: Path,
    service: DashboardService,
    *,
    clock: FakeClock,
    seed: int,
    port: int = DASH_PORT,
    kind: Literal["ui", "hub"] = "ui",
    workspace: Path | None = None,
    users: Sequence[str] = ("alice",),
    totp: TotpPolicy = TotpPolicy.OPTIONAL,
    settings: AuthSettings | None = None,
    session_store: InMemorySessionStore | None = None,
    hasher: FastFakeHasher | None = None,
    totp_service: Any = None,
    peer: tuple[str, int] = LOOPBACK_PEER,
    base_url: str = LOOPBACK_BASE_URL,
    **settings_overrides: Any,
) -> Dash:
    """A running app over a real runtime. ``users`` are seeded once per store (epoch 1)."""
    settings = settings or make_settings(tmp_path, totp=totp, **settings_overrides)
    realm = Realm(kind, port, (workspace or tmp_path) if kind == "ui" else None)
    paths = StorePaths.at(settings.store_dir, settings.state_dir)
    sessions = session_store or InMemorySessionStore()
    runtime = build_auth_runtime(
        settings,
        realm,
        clock=clock,
        entropy=SeededEntropy(seed),
        hasher=hasher or FastFakeHasher(),
        audit=AuditLog(paths, clock=clock, strict=True),
        session_store=sessions,
    )
    if users:
        entropy = SeededEntropy(3)

        def seed_users(f: UserStoreFile) -> None:
            for name in users:
                add_user(f, name, fake_hash(PASSWORD), now=clock.now_utc(), entropy=entropy)

        runtime.store.mutate(seed_users, create=True)
    # Deterministic: the core routes are tested without a second-factor service (HLD 13.2).
    runtime.totp = totp_service
    runtime.lockouts.ensure_name_key()  # what check_ready() does at startup (S6)
    if kind == "ui":
        app = ui_app.create_app(service, auth=runtime)
    else:
        app = FastAPI()
        app.add_middleware(
            AuthMiddleware,
            runtime=runtime,
            policies=HUB_ROUTE_POLICIES,
            cookie_only_navigation=HUB_COOKIE_ONLY_NAVIGATION,
        )
        install_auth_routes(app, runtime)
    client = TestClient(app, base_url=base_url, client=peer, follow_redirects=False)
    return Dash(runtime, app, client, clock, sessions)


def json_body(response: Response) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(response.content)
    return body
