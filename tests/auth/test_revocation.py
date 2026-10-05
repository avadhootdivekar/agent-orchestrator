"""T-rpKCjP: revocation after an out-of-band credential change, route level (AC-39 part; S15, S25).

Two dashboard apps share the user store; each has signed the user in. A CLI-style write to
``users.json`` (here ``store.mutate`` with the same pure functions the CLI uses) must make both
apps answer 401 on the very next request, with no restart and no cache wait.
"""

from __future__ import annotations

from agent_orchestrator.auth.store import (
    UserStoreFile,
    add_user,
    bump_epoch,
    remove_user,
    set_password_hash,
)
from tests.auth.helpers import real_routes
from tests.auth.helpers.core import SeededEntropy
from tests.auth.helpers.provider import PASSWORD, fake_hash
from tests.auth.helpers.real_routes import Dash, DashFactory

dash_factory = real_routes.dash_factory  # the pytest fixture (an assignment: no F811 clash)

KEEPALIVE = "/api/auth/keepalive"
PROBE = "/api/workspace"
NEW_HASH = fake_hash("changed-by-the-cli")


def two_signed_in_apps(dash_factory: DashFactory) -> tuple[Dash, Dash]:
    first = dash_factory(port=8765)
    second = dash_factory(port=8766, users=())
    assert first.login().status_code == 200 and second.login().status_code == 200
    assert first.protected().status_code == 200 and second.protected().status_code == 200
    return first, second


def test_set_password_by_the_cli_revokes_every_session(dash_factory: DashFactory) -> None:
    first, second = two_signed_in_apps(dash_factory)
    now = first.clock.now_utc()
    first.runtime.store.mutate(lambda f: set_password_hash(f, "alice", NEW_HASH, now=now))
    for dash in (first, second):
        response = dash.protected()
        assert response.status_code == 401
        assert response.json()["code"] == "not_authenticated"
        assert dash.status()["state"] == "anonymous"


def test_remove_then_add_with_the_same_name_does_not_resurrect_a_session(
    dash_factory: DashFactory,
) -> None:
    first, second = two_signed_in_apps(dash_factory)
    old = {id(d): (d.cookie_value, d.proof) for d in (first, second)}
    now = first.clock.now_utc()

    def recreate(f: UserStoreFile) -> None:
        remove_user(f, "alice")
        add_user(f, "alice", fake_hash(PASSWORD), now=now, entropy=SeededEntropy(99))

    first.runtime.store.mutate(recreate)
    assert first.runtime.store.snapshot().users["alice"].credential_epoch == 1  # a "fresh" account
    for dash in (first, second):
        cookie, proof = old[id(dash)]
        dash.set_cookie(cookie or "")
        assert dash.protected(proof=proof).status_code == 401  # same name, different user_id
    # the recreated account itself signs in normally
    assert first.login().status_code == 200 and first.protected().status_code == 200


def test_an_epoch_bump_revokes_both_apps_and_clears_the_cookie(dash_factory: DashFactory) -> None:
    first, second = two_signed_in_apps(dash_factory)
    now = first.clock.now_utc()
    first.runtime.store.mutate(lambda f: bump_epoch(f, "alice", now=now))
    for dash in (first, second):
        response = dash.post(KEEPALIVE)
        assert response.status_code == 401
        (cleared,) = response.headers.get_list("set-cookie")
        assert "Max-Age=0" in cleared  # the dead session's cookie is cleared
    assert second.session_store.records() == []  # destroyed server-side, not merely hidden


def test_revocation_is_seen_by_a_status_poll_too(dash_factory: DashFactory) -> None:
    first, _second = two_signed_in_apps(dash_factory)
    now = first.clock.now_utc()
    first.runtime.store.mutate(lambda f: bump_epoch(f, "alice", now=now))
    response = first.get("/api/auth/status")
    assert response.status_code == 200 and response.json()["state"] == "anonymous"
    assert first.session_store.records() == []
