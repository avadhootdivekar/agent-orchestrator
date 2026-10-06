"""T-G7qByZ: a partial session is confined to its own step (AC-12, S2; HLD 20.3 #2).

The same enumeration as ``test_route_enumeration_dashboard.py`` with a PARTIAL_SECOND_FACTOR or
PARTIAL_ENROLL session **and its proof**: every pair except the PUBLIC ones and the session's own
step answers 401 with the state's denial code.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi import FastAPI

from agent_orchestrator.auth.model import SessionState
from tests.auth.helpers.core import make_client, same_origin_headers
from tests.auth.helpers.enumeration import anonymous_matrix, require_route_contexts
from tests.auth.helpers.stub_runtime import StubRuntime

require_route_contexts()

# PUBLIC pairs of the dashboard (HLD 13.2), spelled out literally.
PUBLIC_PAIRS = {
    ("GET", "/api/auth/status"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("GET", "/api/health"),
    ("GET", "/"),
    ("GET", "/{full_path:path}"),
    ("GET", "/assets"),
}
VERIFY = ("POST", "/api/auth/totp/verify")
ENROLL = {("POST", "/api/auth/totp/enroll/begin"), ("POST", "/api/auth/totp/enroll/confirm")}

CASES = [
    (SessionState.PARTIAL_SECOND_FACTOR, {VERIFY}, "second_factor_required"),
    (SessionState.PARTIAL_ENROLL, ENROLL, "enrollment_required"),
]


@pytest.mark.parametrize("built", [True, False])
@pytest.mark.parametrize(("state", "allowed", "code"), CASES)
def test_partial_session_is_confined_to_its_own_step(
    build_dashboard: Callable[..., FastAPI],
    stub_runtime: StubRuntime,
    built: bool,
    state: SessionState,
    allowed: set[tuple[str, str]],
    code: str,
) -> None:
    app = build_dashboard(stub_runtime, built=built)
    client = make_client(app)
    issued = stub_runtime.issue(state)
    client.cookies.set(stub_runtime.cookie_name, issued.token)
    headers = same_origin_headers(client, issued.proof)

    open_templates = {template for _, template in PUBLIC_PAIRS | allowed}
    reached: set[tuple[str, str]] = set()
    for method, template, status, body in anonymous_matrix(client, app, headers):
        pair = (method, template)
        if pair in PUBLIC_PAIRS or pair in allowed:
            assert status != 401, pair
            reached.add(pair)
        elif template in open_templates:
            assert status in (401, 405), (pair, status)  # another method on an open template
            if status == 401:
                assert body is not None and body["code"] == code, pair
        else:
            assert status == 401, (pair, status)
            assert body is not None and body["code"] == code, pair
    assert allowed <= reached  # the session's own step is actually served


@pytest.mark.parametrize(("state", "allowed", "code"), CASES)
def test_partial_confinement_holds_for_a_stale_state_after_revocation(
    build_dashboard: Callable[..., FastAPI],
    stub_runtime: StubRuntime,
    state: SessionState,
    allowed: set[tuple[str, str]],
    code: str,
) -> None:
    """A revoked partial session is plain anonymous (not_authenticated), never confined."""
    app = build_dashboard(stub_runtime)
    client = make_client(app)
    issued = stub_runtime.issue(state)
    client.cookies.set(stub_runtime.cookie_name, issued.token)
    stub_runtime.provider.bump_epoch()
    response = client.get("/api/workspace", headers=same_origin_headers(client, issued.proof))
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"
