"""T-rpKCjP: the route-builder registry, ``install_auth_routes`` and the ``AuthError`` handler.

HLD 11.18 (v2.1, design-review M2): builders are callables in a list-valued registry, a module
can plug its routes in without editing ``routes.py``, and every auth route must be a DIRECT
member of the app router (``assert_flat_auth_routes``).
"""

from __future__ import annotations

import dataclasses
import logging
import sys
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import Response

import agent_orchestrator.auth.http.routes as routes
from agent_orchestrator.auth.constants import (
    APP_STATE_AUTH_KEY,
    AUTH_ENROLL_BEGIN_PATH,
    AUTH_LOGIN_PATH,
    AUTH_TOTP_VERIFY_PATH,
    LOCAL_PROVIDER_ID,
)
from agent_orchestrator.auth.errors import (
    AuthConfigError,
    AuthError,
    ErrorCode,
    StoreUnavailableError,
)
from agent_orchestrator.auth.http.routes import (
    add_core_auth_routes,
    add_disabled_auth_routes,
    add_local_password_routes,
    assert_flat_auth_routes,
    install_auth_routes,
    register_route_builder,
)
from agent_orchestrator.auth.provider import AuthProvider, Revalidation, UserView
from agent_orchestrator.auth.runtime import AuthRuntime
from tests.auth.helpers import real_routes
from tests.auth.helpers.real_routes import DashFactory, json_body

dash_factory = real_routes.dash_factory  # the pytest fixture (an assignment: no F811 clash)

SECOND_FACTOR_MODULE = "agent_orchestrator.auth.http.routes_second_factor"
ROUTES_LOGGER = "agent_orchestrator.auth.http.routes"
SECRET_CAUSE = "disk-gone-SECRET-CAUSE"


class OtherProvider(AuthProvider):
    """A provider that is not the local-password one (and has no registered builder)."""

    provider_id = "other-provider"

    def check_ready(self) -> None:
        return None

    def revalidate(self, user_id: str, credential_epoch: int) -> Revalidation:
        return Revalidation.VALID

    def user_view(self, username: str) -> UserView | None:
        return None


def with_provider(runtime: AuthRuntime, provider_id: str) -> AuthRuntime:
    provider = type("Provider", (OtherProvider,), {"provider_id": provider_id})()
    return dataclasses.replace(runtime, provider=provider)


def auth_paths(app: FastAPI) -> list[str]:
    return [p for r in app.router.routes if (p := getattr(r, "path", "")).startswith("/api/auth")]


@pytest.fixture()
def clean_registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """A copy of the registry, restored afterwards, so tests can add providers freely."""
    copy = {key: list(builders) for key, builders in routes.ROUTE_BUILDERS.items()}
    monkeypatch.setattr(routes, "ROUTE_BUILDERS", copy)
    return copy


# --- the AuthError handler -----------------------------------------------------------------


def test_a_5xx_is_logged_once_with_its_cause_and_never_sent(
    dash_factory: DashFactory, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    dash = dash_factory()

    async def explode(*_args: object) -> None:
        raise StoreUnavailableError(cause_for_log=SECRET_CAUSE)

    monkeypatch.setattr(dash.runtime.provider, "authenticate", explode)
    caplog.set_level(logging.WARNING)
    response = dash.login()
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    assert json_body(response) == {
        "detail": "The account store is temporarily unavailable.",
        "code": "store_unavailable",
    }
    assert SECRET_CAUSE not in response.text
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(errors) == 1 and SECRET_CAUSE in errors[0].getMessage()


def test_a_4xx_is_not_logged(dash_factory: DashFactory, caplog: pytest.LogCaptureFixture) -> None:
    dash = dash_factory()
    caplog.set_level(logging.DEBUG, logger=ROUTES_LOGGER)
    assert dash.login(password="wrong").status_code == 401
    assert [r for r in caplog.records if r.name == ROUTES_LOGGER] == []


def test_the_handler_keeps_the_errors_own_headers_and_extra_keys(
    dash_factory: DashFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    dash = dash_factory()

    async def too_many(*_args: object) -> None:
        raise AuthError(
            ErrorCode.TOO_MANY_ATTEMPTS,
            extra={"retry_after_seconds": 7},
            headers={"Retry-After": "7"},
        )

    monkeypatch.setattr(dash.runtime.provider, "authenticate", too_many)
    response = dash.login()
    assert response.status_code == 429 and response.headers["retry-after"] == "7"
    assert response.json()["retry_after_seconds"] == 7


@pytest.mark.parametrize(
    ("code", "challenge"),
    [
        (ErrorCode.NOT_AUTHENTICATED, True),
        (ErrorCode.SECOND_FACTOR_REQUIRED, True),
        (ErrorCode.ENROLLMENT_REQUIRED, True),
        (ErrorCode.INVALID_CREDENTIALS, False),
        (ErrorCode.TOTP_REQUIRED, False),
        (ErrorCode.INVALID_REQUEST, False),
    ],
)
def test_only_the_no_session_codes_carry_the_challenge(
    dash_factory: DashFactory, monkeypatch: pytest.MonkeyPatch, code: ErrorCode, challenge: bool
) -> None:
    dash = dash_factory()

    async def fail(*_args: object) -> None:
        raise AuthError(code)

    monkeypatch.setattr(dash.runtime.provider, "authenticate", fail)
    response = dash.login()
    assert ("www-authenticate" in response.headers) is challenge
    if challenge:
        assert response.headers["www-authenticate"] == f'AO-Session realm="{dash.runtime.realm.id}"'


# --- the builder registry ----------------------------------------------------------------------


def test_register_appends_and_ignores_a_repeat(clean_registry: dict[str, list[Any]]) -> None:
    def first(app: FastAPI, runtime: AuthRuntime) -> None: ...

    def second(app: FastAPI, runtime: AuthRuntime) -> None: ...

    register_route_builder("provider-x", first)
    register_route_builder("provider-x", second)
    register_route_builder("provider-x", first)  # a no-op
    assert clean_registry["provider-x"] == [first, second]


def test_the_builtin_builders_are_registered_in_order(
    dash_factory: DashFactory, clean_registry: dict[str, list[Any]]
) -> None:
    dash_factory()  # create_app -> install_auth_routes imports the built-in modules
    module = sys.modules[SECOND_FACTOR_MODULE]
    assert clean_registry[LOCAL_PROVIDER_ID] == [add_local_password_routes, module.add_totp_routes]
    dash_factory(port=8766, users=())  # a second install changes nothing
    assert len(clean_registry[LOCAL_PROVIDER_ID]) == 2


def test_builders_run_in_registration_order(
    dash_factory: DashFactory, clean_registry: dict[str, list[Any]]
) -> None:
    runtime = with_provider(dash_factory().runtime, "ordered-provider")
    calls: list[str] = []

    def first(app: FastAPI, rt: AuthRuntime) -> None:
        calls.append("first")

        async def login(request: Request) -> Response:
            return Response(status_code=204)

        app.add_api_route(AUTH_LOGIN_PATH, login, methods=["POST"])

    def second(app: FastAPI, rt: AuthRuntime) -> None:
        calls.append("second")

    register_route_builder("ordered-provider", first)
    register_route_builder("ordered-provider", second)
    app = FastAPI()
    install_auth_routes(app, runtime)
    assert calls == ["first", "second"]
    assert getattr(app.state, APP_STATE_AUTH_KEY) is runtime
    assert AuthError in app.exception_handlers


def test_a_provider_without_a_builder_is_a_config_error(
    dash_factory: DashFactory, clean_registry: dict[str, list[Any]]
) -> None:
    runtime = with_provider(dash_factory().runtime, "no-such-provider")
    app = FastAPI()
    with pytest.raises(AuthConfigError, match="no route builder registered for provider"):
        install_auth_routes(app, runtime)
    assert auth_paths(app) == []


def test_install_imports_the_second_factor_module_and_adds_no_totp_route_without_a_service(
    dash_factory: DashFactory,
) -> None:
    sys.modules.pop(SECOND_FACTOR_MODULE, None)
    dash = dash_factory()  # totp_service=None
    module = sys.modules[SECOND_FACTOR_MODULE]
    assert module.add_totp_routes in routes.ROUTE_BUILDERS[LOCAL_PROVIDER_ID]
    paths = {getattr(r, "path", "") for r in dash.app.router.routes}
    assert {"/api/auth/status", "/api/auth/login", "/api/auth/logout"} <= paths
    assert {"/api/auth/keepalive", "/api/auth/password"} <= paths
    assert not {AUTH_TOTP_VERIFY_PATH, AUTH_ENROLL_BEGIN_PATH} & paths
    scratch = FastAPI()
    module.add_totp_routes(scratch, dash.runtime)
    assert [
        r for r in scratch.router.routes if getattr(r, "path", "").startswith("/api/auth")
    ] == []


def test_without_a_runtime_only_the_disabled_status_route_exists() -> None:
    app = FastAPI()
    install_auth_routes(app, None)
    assert getattr(app.state, APP_STATE_AUTH_KEY) is None
    assert auth_paths(app) == ["/api/auth/status"]


def test_add_disabled_auth_routes_serves_the_disabled_body() -> None:
    from starlette.testclient import TestClient

    app = FastAPI()
    add_disabled_auth_routes(app)
    body = TestClient(app).get("/api/auth/status").json()
    assert body["enabled"] is False and body["state"] == "disabled"


# --- assert_flat_auth_routes -------------------------------------------------------------------


def test_the_flat_check_passes_for_the_installed_routes(dash_factory: DashFactory) -> None:
    assert_flat_auth_routes(dash_factory().app, optional=routes.TOTP_ROUTE_KEYS)


def test_the_flat_check_names_a_route_added_through_include_router(
    dash_factory: DashFactory,
) -> None:
    runtime = dash_factory().runtime
    app = FastAPI()
    add_core_auth_routes(app, runtime)  # status, logout, keepalive: flat
    router = APIRouter()

    async def login(request: Request) -> Response:
        return Response(status_code=204)

    router.add_api_route(AUTH_LOGIN_PATH, login, methods=["POST"])
    app.include_router(router)  # reachable, but opaque to the middleware's classification
    with pytest.raises(RuntimeError, match="POST /api/auth/login"):
        assert_flat_auth_routes(app, optional=routes.TOTP_ROUTE_KEYS)


def test_a_half_installed_totp_group_is_refused(dash_factory: DashFactory) -> None:
    dash = dash_factory()
    app = FastAPI()
    add_core_auth_routes(app, dash.runtime)
    add_local_password_routes(app, dash.runtime)

    async def verify(request: Request) -> Response:
        return Response(status_code=204)

    app.add_api_route(AUTH_TOTP_VERIFY_PATH, verify, methods=["POST"])
    with pytest.raises(RuntimeError, match="/api/auth/totp/enroll/begin"):
        assert_flat_auth_routes(app, optional=routes.TOTP_ROUTE_KEYS)


def test_the_flat_check_without_the_optional_group_demands_every_route(
    dash_factory: DashFactory,
) -> None:
    with pytest.raises(RuntimeError, match="/api/auth/totp/"):
        assert_flat_auth_routes(dash_factory().app)
