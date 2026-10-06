"""T-QJ1vyQ: session-proof enforcement (HLD 13.3 step 5; AC-35 middleware part, S21, D25, M1)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI

from agent_orchestrator.auth.constants import (
    AUTH_API_PREFIX,
    AUTH_LOGIN_PATH,
    AUTH_LOGOUT_PATH,
    AUTH_STATUS_PATH,
    AUTH_TOTP_VERIFY_PATH,
    SESSION_PROOF_B64_CHARS,
    SESSION_PROOF_HEADER,
)
from agent_orchestrator.auth.model import SessionState
from agent_orchestrator.auth.policy import DASHBOARD_ROUTE_POLICIES, MOUNT_METHOD, RoutePolicy
from tests.auth.helpers.core import make_client, same_origin_headers
from tests.auth.helpers.edge import (
    HTML_ACCEPT,
    NAV_PAGE_PATH,
    PAGE_PATH,
    EdgeApp,
    build_edge_app,
)
from tests.auth.helpers.enumeration import (
    MUTATING,
    classify_request,
    concrete_path,
    require_route_contexts,
    route_entries,
)
from tests.auth.helpers.stub_runtime import StubRuntime

require_route_contexts()  # skip the whole module, with a reason, on an older FastAPI

WRONG_PROOF = "A" * SESSION_PROOF_B64_CHARS  # valid shape, not this session's proof
MALFORMED_PROOFS = {
    "too short": "A" * (SESSION_PROOF_B64_CHARS - 1),
    "too long": "A" * (SESSION_PROOF_B64_CHARS + 1),
    "bad base64": "!" * SESSION_PROOF_B64_CHARS,
    "empty": "",
}


def _authenticated_api_contexts(app: FastAPI) -> list[tuple[str, str]]:
    contexts: list[tuple[str, str]] = []
    for entry in route_entries(app):
        for method in entry.methods:
            if method == MOUNT_METHOD or not entry.template.startswith("/api"):
                continue
            path = concrete_path(entry.template)
            _route, policy, _ = classify_request(app, method, path, DASHBOARD_ROUTE_POLICIES)
            if policy is RoutePolicy.AUTHENTICATED:
                contexts.append((method, entry.template))
    return contexts


@pytest.fixture()
def destroyed(stub_runtime: StubRuntime, monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Every ``SessionManager.destroy`` call, spied (the call still goes through)."""
    calls: list[Any] = []
    real = stub_runtime.sessions.destroy

    def spy(record: Any) -> None:
        calls.append(record)
        real(record)

    monkeypatch.setattr(stub_runtime.sessions, "destroy", spy)
    return calls


def _send(client: Any, method: str, template: str, headers: dict[str, str]) -> Any:
    kwargs: dict[str, Any] = {"headers": headers}
    if method in MUTATING:
        kwargs["json"] = {}
    return client.request(method, concrete_path(template), **kwargs)


def test_every_authenticated_dashboard_api_route_enforces_the_proof(
    build_dashboard: Callable[..., FastAPI], stub_runtime: StubRuntime, destroyed: list[Any]
) -> None:
    app = build_dashboard(stub_runtime, built=True)
    client = make_client(app)
    contexts = _authenticated_api_contexts(app)
    assert len(contexts) >= 20  # the real dashboard surface, not a toy list
    assert (("POST", AUTH_API_PREFIX + "/password")) in contexts  # an auth route is included
    for method, template in contexts:
        issued = stub_runtime.issue()
        client.cookies.set(stub_runtime.cookie_name, issued.token)
        base = same_origin_headers(client)
        proofs: dict[str, str | None] = {"none": None, "wrong": WRONG_PROOF, **MALFORMED_PROOFS}
        for label, proof in proofs.items():
            headers = dict(base) if proof is None else {**base, SESSION_PROOF_HEADER: proof}
            response = _send(client, method, template, headers)
            where = (method, template, label)
            assert response.status_code == 401, where
            if method != "HEAD":  # a HEAD response carries no body to inspect
                assert response.json()["code"] == "not_authenticated", where
            assert "set-cookie" not in response.headers, where  # never a clear-cookie
            assert stub_runtime.record_of(issued) is not None, where  # session survives
        assert destroyed == [], (method, template)
        response = _send(client, method, template, {**base, SESSION_PROOF_HEADER: issued.proof})
        assert response.status_code != 401, (method, template, "correct proof")
    assert destroyed == []


def test_a_missing_proof_cannot_log_the_victim_out(stub_runtime: StubRuntime) -> None:
    edge = build_edge_app(stub_runtime)
    issued, headers = edge.login()
    headers.pop(SESSION_PROOF_HEADER)
    assert edge.client.post("/api/runs", headers=headers).status_code == 401
    # The very same cookie still works once the proof is supplied.
    with_proof = {**headers, SESSION_PROOF_HEADER: issued.proof}
    assert edge.client.post("/api/runs", headers=with_proof).status_code == 204


# --- other policies and the v2.1 non-API rule -------------------------------------------------


def test_a_partial_session_without_a_proof_is_401_on_a_partial_route(
    stub_runtime: StubRuntime, destroyed: list[Any]
) -> None:
    edge = build_edge_app(stub_runtime)
    issued, headers = edge.login(SessionState.PARTIAL_SECOND_FACTOR)
    with_proof = dict(headers)
    headers.pop(SESSION_PROOF_HEADER)
    response = edge.client.post(AUTH_TOTP_VERIFY_PATH, headers=headers, json={})
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"  # not second_factor_required
    assert "set-cookie" not in response.headers
    assert stub_runtime.record_of(issued) is not None and destroyed == []
    assert edge.client.post(AUTH_TOTP_VERIFY_PATH, headers=with_proof, json={}).status_code == 200


def test_public_routes_are_reached_without_a_proof(stub_runtime: StubRuntime) -> None:
    edge = build_edge_app(stub_runtime)
    issued, headers = edge.login()
    headers.pop(SESSION_PROOF_HEADER)
    assert edge.client.get("/api/health", headers=headers).status_code == 200
    assert edge.client.get(AUTH_STATUS_PATH, headers=headers).status_code == 200
    assert edge.client.post(AUTH_LOGIN_PATH, headers=headers, json={}).status_code == 200
    assert edge.client.post(AUTH_LOGOUT_PATH, headers=headers, json={}).status_code == 200
    assert stub_runtime.record_of(issued) is not None  # the stub logout does not destroy


def test_a_non_api_authenticated_page_needs_the_proof_unless_flagged(
    stub_runtime: StubRuntime,
) -> None:
    edge = build_edge_app(stub_runtime)
    issued, headers = edge.login()
    with_proof = dict(headers)
    headers.pop(SESSION_PROOF_HEADER)

    response = edge.client.get(PAGE_PATH, headers=headers)
    assert response.status_code == 401 and response.json()["code"] == "not_authenticated"
    response = edge.client.get(PAGE_PATH, headers={**headers, **HTML_ACCEPT})
    assert response.status_code == 303
    assert response.headers["location"] == stub_runtime.realm.login_path
    assert "set-cookie" not in response.headers
    assert edge.client.get(PAGE_PATH, headers=with_proof).status_code == 200


def test_the_flagged_cookie_only_page_passes_with_the_cookie_alone(
    stub_runtime: StubRuntime,
) -> None:
    edge = build_edge_app(stub_runtime)
    _issued, headers = edge.login()
    headers.pop(SESSION_PROOF_HEADER)
    assert edge.client.get(NAV_PAGE_PATH, headers=headers).status_code == 200
    # ...but only that exact route: the unflagged page next to it still needs the proof.
    assert edge.client.get(PAGE_PATH, headers=headers).status_code == 401


def test_the_flag_follows_the_apps_set_not_the_path(stub_runtime: StubRuntime) -> None:
    edge: EdgeApp = build_edge_app(stub_runtime, cookie_only=frozenset())
    _issued, headers = edge.login()
    headers.pop(SESSION_PROOF_HEADER)
    assert edge.client.get(NAV_PAGE_PATH, headers=headers).status_code == 401


def test_an_anonymous_request_is_unaffected_by_enforcement(stub_runtime: StubRuntime) -> None:
    edge = build_edge_app(stub_runtime)
    response = edge.client.get("/api/runs", headers=same_origin_headers(edge.client))
    assert response.status_code == 401 and "set-cookie" not in response.headers
