"""T-rpKCjP: cookie isolation across realms and restarts (AC-16, S3/S5/S26; HLD 20.3 #4).

Two dashboard apps (ports 8765 and 8766, different workspaces) and a hub-realm app on 8770 share
ONE user store, like three ``ao`` processes on one machine; each has its own session table.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_orchestrator.auth.runtime import Realm
from tests.auth.helpers import real_routes
from tests.auth.helpers.real_routes import DASH_PORT, HUB_PORT, Dash, DashFactory

dash_factory = real_routes.dash_factory  # the pytest fixture (an assignment: no F811 clash)

KEEPALIVE = "/api/auth/keepalive"
LOGIN = "/api/auth/login"
OTHER_PORT = 8766
HEX12 = 12


def three_realms(dash_factory: DashFactory, tmp_path: Path) -> tuple[Dash, Dash, Dash]:
    (tmp_path / "ws-a").mkdir()
    (tmp_path / "ws-b").mkdir()
    a = dash_factory(port=DASH_PORT, workspace=tmp_path / "ws-a")
    b = dash_factory(port=OTHER_PORT, workspace=tmp_path / "ws-b", users=())
    hub = dash_factory(port=HUB_PORT, kind="hub", users=())
    return a, b, hub


def alive(dash: Dash) -> int:
    """The status of a protected probe (keepalive works on dashboards and on the hub)."""
    return dash.post(KEEPALIVE).status_code


def test_each_realm_accepts_only_its_own_cookie(dash_factory: DashFactory, tmp_path: Path) -> None:
    a, b, hub = three_realms(dash_factory, tmp_path)
    assert [a.login().status_code, b.login().status_code, hub.login().status_code] == [200] * 3
    assert [alive(a), alive(b), alive(hub)] == [200] * 3
    assert {a.cookie_name, b.cookie_name, hub.cookie_name} == {
        f"ao_sid_{DASH_PORT}",
        f"ao_sid_{OTHER_PORT}",
        f"ao_sid_{HUB_PORT}",
    }


def test_a_token_under_another_realms_cookie_name_is_rejected(
    dash_factory: DashFactory, tmp_path: Path
) -> None:
    a, b, hub = three_realms(dash_factory, tmp_path)
    for dash in (a, b, hub):
        assert dash.login().status_code == 200
    tokens = {id(d): (d.cookie_value, d.proof) for d in (a, b, hub)}
    for owner in (a, b, hub):
        for other in (a, b, hub):
            if owner is other:
                continue
            token, proof = tokens[id(owner)]
            assert token is not None
            other.set_cookie(token)  # owner's token under the other realm's cookie name
            assert other.post(KEEPALIVE, proof=proof).status_code == 401
            # ... and the other realm's own session is a different thing entirely
            other.set_cookie(tokens[id(other)][0] or "")
            assert other.post(KEEPALIVE, proof=tokens[id(other)][1]).status_code == 200


def test_a_new_instance_on_the_same_port_rejects_the_old_cookie_and_clears_it(
    dash_factory: DashFactory,
) -> None:
    old = dash_factory(port=DASH_PORT)
    old.login()
    cookie, proof = old.cookie_value, old.proof
    restarted = dash_factory(port=DASH_PORT, users=())  # same realm id, a fresh session table
    assert cookie is not None
    restarted.set_cookie(cookie)
    response = restarted.post(KEEPALIVE, proof=proof)
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"
    assert response.headers["www-authenticate"].startswith('AO-Session realm="ui:')
    (cleared,) = response.headers.get_list("set-cookie")
    assert cleared.startswith(f"ao_sid_{DASH_PORT}=; ") and "Max-Age=0" in cleared


def test_cookie_attributes_in_every_realm(dash_factory: DashFactory, tmp_path: Path) -> None:
    a, b, hub = three_realms(dash_factory, tmp_path)
    for dash in (a, b, hub):
        (cookie,) = dash.login().headers.get_list("set-cookie")
        attributes = [part.strip() for part in cookie.split(";")][1:]
        assert attributes == ["Path=/", "HttpOnly", "SameSite=Strict"]
        assert "Domain" not in cookie and "Max-Age" not in cookie
        (cleared,) = dash.post("/api/auth/logout", {}).headers.get_list("set-cookie")
        assert "Max-Age=0" in cleared and "Domain" not in cleared


def test_https_uses_the_host_prefixed_secure_cookie(dash_factory: DashFactory) -> None:
    dash = dash_factory(base_url="https://testserver")
    response = dash.login()
    assert response.status_code == 200
    (cookie,) = response.headers.get_list("set-cookie")
    assert cookie.startswith(f"__Host-ao_sid_{DASH_PORT}=")
    assert cookie.endswith("; Secure")
    assert dash.cookie_name == f"__Host-ao_sid_{DASH_PORT}"
    assert dash.post(KEEPALIVE).status_code == 200
    # the plain-http cookie name is not a session over https
    dash.client.cookies.clear()
    assert dash.post(KEEPALIVE).status_code == 401


def test_the_realm_id_does_not_depend_on_the_port(tmp_path: Path) -> None:
    on_8765 = Realm("ui", DASH_PORT, tmp_path)
    on_9000 = Realm("ui", 9000, tmp_path)
    assert on_8765.id == on_9000.id
    assert on_8765.id.startswith("ui:") and len(on_8765.id) == len("ui:") + HEX12
    assert on_8765.cookie_name(secure=False) != on_9000.cookie_name(secure=False)
    assert Realm("hub", HUB_PORT).id == "hub"
    other = tmp_path / "other"
    other.mkdir()
    assert Realm("ui", DASH_PORT, other).id != on_8765.id  # a different workspace is a realm


def test_a_duplicated_realm_cookie_is_no_session(
    dash_factory: DashFactory, caplog: pytest.LogCaptureFixture
) -> None:
    dash = dash_factory()
    dash.login()
    cookie, proof = dash.cookie_value, dash.proof
    name = dash.cookie_name
    dash.client.cookies.clear()
    response = dash.post(
        KEEPALIVE, proof=proof, headers={"Cookie": f"{name}={cookie}; {name}={cookie}"}
    )
    assert response.status_code == 401
    assert "set-cookie" not in response.headers  # the (valid) session is not cleared
    assert cookie is not None and cookie not in caplog.text  # values are never logged
    # the single cookie still works: the duplicate did not destroy anything
    dash.set_cookie(cookie)
    assert dash.post(KEEPALIVE, proof=proof).status_code == 200


def test_login_through_a_foreign_cookie_leaves_that_session_alone(
    dash_factory: DashFactory, tmp_path: Path
) -> None:
    """A's cookie, sent to B's login, is just another cookie name B does not read."""
    a, b, _hub = three_realms(dash_factory, tmp_path)
    a.login()
    b.set_cookie(a.cookie_value or "")
    assert b.login(proof=a.proof).status_code == 200
    assert alive(a) == 200
