"""T-rpKCjP: ``client_info`` and the unconfigured-proxy warning (AC-43, S28; HLD 11.18, 20.3 #17).

A reverse proxy on the same machine looks like a loopback peer. ``is_loopback`` therefore needs a
loopback peer AND a loopback ``Host`` AND no forwarding header; ``proxy_suspected`` flags the
case where nothing was configured to explain such a request. The E4/E5/E7 ``insecure_transport``
consequence is asserted by T-KQ6ZrY.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from agent_orchestrator.auth.http.routes import client_info
from tests.auth.helpers import real_routes
from tests.auth.helpers.real_routes import Dash, DashFactory

dash_factory = real_routes.dash_factory  # the pytest fixture (an assignment: no F811 clash)

STATUS = "/api/auth/status"
LOGIN = "/api/auth/login"
ROUTES_LOGGER = "agent_orchestrator.auth.http.routes"
PROXY_ENV = "AO_UI_AUTH_TRUSTED_PROXIES"
NOT_ENCRYPTED = "not encrypted"
REMOTE_PEER = ("10.0.0.5", 4000)
LOOPBACK = ("127.0.0.1", 1)
PROXY_HOST = "dash.example.com"


@pytest.fixture(autouse=True)
def _allow_the_proxy_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AO_UI_ALLOWED_HOSTS", f"testserver,{PROXY_HOST}")


def transport(dash: Dash, **headers: str) -> dict[str, Any]:
    response = dash.client.get(STATUS, headers=headers)
    assert response.status_code == 200
    value: dict[str, Any] = response.json()["transport"]
    return value


def warnings_of(caplog: pytest.LogCaptureFixture, needle: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.levelno == logging.WARNING and needle in r.getMessage()]


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "[::1]:8765"])
def test_a_plain_loopback_request_is_loopback(
    dash_factory: DashFactory, caplog: pytest.LogCaptureFixture, host: str
) -> None:
    caplog.set_level(logging.WARNING, logger=ROUTES_LOGGER)
    dash = dash_factory(peer=LOOPBACK)
    assert transport(dash, Host=host) == {
        "secure": False,
        "client_is_loopback": True,
        "proxy_suspected": False,
    }
    assert warnings_of(caplog, PROXY_ENV) == []


@pytest.mark.parametrize(
    "case",
    [
        {"X-Forwarded-For": "203.0.113.9", "Host": "127.0.0.1:8765"},  # (b)
        {"Forwarded": "for=203.0.113.9", "Host": "127.0.0.1:8765"},  # (c)
        {"X-Forwarded-Proto": "https", "Host": "127.0.0.1:8765"},  # (c2)
        {"Host": PROXY_HOST},  # (d) a non-loopback Host through a loopback peer
    ],
    ids=["x-forwarded-for", "forwarded", "x-forwarded-proto-alone", "foreign-host"],
)
def test_proxy_signals_make_the_request_remote_and_suspected(
    dash_factory: DashFactory, caplog: pytest.LogCaptureFixture, case: dict[str, str]
) -> None:
    caplog.set_level(logging.WARNING, logger=ROUTES_LOGGER)
    dash = dash_factory(peer=LOOPBACK)
    assert transport(dash, **case) == {
        "secure": False,
        "client_is_loopback": False,
        "proxy_suspected": True,
    }
    assert len(warnings_of(caplog, PROXY_ENV)) == 1


def test_the_proxy_warning_is_logged_once_per_process(
    dash_factory: DashFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=ROUTES_LOGGER)
    dash = dash_factory(peer=LOOPBACK)
    transport(dash, Host="127.0.0.1:8765")  # (a) first: nothing to warn about
    assert warnings_of(caplog, PROXY_ENV) == []
    transport(dash, **{"X-Forwarded-For": "203.0.113.9", "Host": "127.0.0.1:8765"})  # (b)
    transport(dash, **{"Forwarded": "for=203.0.113.9", "Host": "127.0.0.1:8765"})  # (c)
    transport(dash, Host=PROXY_HOST)  # (d)
    (record,) = warnings_of(caplog, PROXY_ENV)
    assert dash.runtime.proxy_suspected_warned is True
    assert "127.0.0.1" in record.getMessage() and "trusted proxy" in record.getMessage()


def test_a_remote_peer_is_remote_but_not_a_proxy_case(
    dash_factory: DashFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=ROUTES_LOGGER)
    dash = dash_factory(peer=REMOTE_PEER)
    assert transport(dash, Host="127.0.0.1:8765") == {
        "secure": False,
        "client_is_loopback": False,
        "proxy_suspected": False,
    }
    # a remote peer with a foreign Host or forwarding headers is simply remote: no proxy guess
    assert transport(dash, Host=PROXY_HOST)["proxy_suspected"] is False
    assert transport(dash, **{"X-Forwarded-For": "1.2.3.4"})["proxy_suspected"] is False
    assert warnings_of(caplog, PROXY_ENV) == []


def test_a_configured_trusted_proxy_silences_the_suspicion(
    dash_factory: DashFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=ROUTES_LOGGER)
    dash = dash_factory(peer=LOOPBACK, trusted_proxies=("127.0.0.1",))
    for headers in (
        {"X-Forwarded-For": "203.0.113.9", "Host": "127.0.0.1:8765"},
        {"Host": PROXY_HOST},
    ):
        info = transport(dash, **headers)
        assert info["proxy_suspected"] is False
        assert info["client_is_loopback"] is False  # still not "local": it came through a proxy
    assert warnings_of(caplog, PROXY_ENV) == []
    assert dash.runtime.proxy_suspected_warned is False


def test_header_names_are_case_insensitive(dash_factory: DashFactory) -> None:
    dash = dash_factory(peer=LOOPBACK)
    assert transport(dash, **{"x-FORWARDED-host": "evil.example", "Host": "localhost:8765"}) == {
        "secure": False,
        "client_is_loopback": False,
        "proxy_suspected": True,
    }


def test_https_is_reported_as_secure(dash_factory: DashFactory) -> None:
    dash = dash_factory(peer=LOOPBACK, base_url="https://127.0.0.1:8765")
    assert transport(dash) == {"secure": True, "client_is_loopback": True, "proxy_suspected": False}


# --- direct unit cases for scopes a TestClient cannot produce ---------------------------------


def _scope(
    client: tuple[str, int] | None,
    host: str | None,
    *,
    scheme: str = "http",
    extra: tuple[tuple[bytes, bytes], ...] = (),
) -> dict[str, Any]:
    headers = list(extra)
    if host is not None:
        headers.append((b"host", host.encode("latin-1")))
    return {"type": "http", "client": client, "scheme": scheme, "headers": headers}


@pytest.mark.parametrize(
    ("client", "host", "key", "loopback", "suspected"),
    [
        (("127.0.0.1", 1), "localhost", "127.0.0.1", True, False),
        (("::1", 1), "[::1]:8765", "::/64", True, False),
        (("::ffff:127.0.0.1", 1), "127.0.0.1:8765", "127.0.0.1", True, False),  # v4-mapped
        (("::ffff:10.0.0.5", 1), "127.0.0.1", "10.0.0.5", False, False),
        (None, "127.0.0.1", "unknown", False, False),  # no peer at all
        (("not-an-ip", 1), "127.0.0.1", "unknown", False, False),
        (("127.0.0.1", 1), None, "127.0.0.1", False, True),  # no Host: not provably local
        (("127.0.0.1", 1), "[::1", "127.0.0.1", False, True),  # a malformed Host is not loopback
        (("127.0.0.1", 1), "LOCALHOST:80", "127.0.0.1", True, False),  # case-insensitive
        (("127.0.0.2", 1), "127.0.0.1", "127.0.0.2", True, False),  # all of 127/8 is loopback
    ],
)
def test_client_info_cases(
    dash_factory: DashFactory,
    client: tuple[str, int] | None,
    host: str | None,
    key: str | None,
    loopback: bool,
    suspected: bool,
) -> None:
    runtime = dash_factory().runtime
    info = client_info(_scope(client, host), runtime)  # type: ignore[arg-type]
    if key is not None:
        assert info.key == key
    assert (info.is_loopback, info.proxy_suspected) == (loopback, suspected)
    assert info.secure is False
    runtime.proxy_suspected_warned = False  # keep the cases independent


def test_client_info_secure_follows_the_scheme(dash_factory: DashFactory) -> None:
    runtime = dash_factory().runtime
    assert client_info(_scope(LOOPBACK, "127.0.0.1", scheme="https"), runtime).secure is True  # type: ignore[arg-type]
    assert client_info(_scope(LOOPBACK, "127.0.0.1", scheme="http"), runtime).secure is False  # type: ignore[arg-type]


# --- the first plain-HTTP login from a remote client (AC-25 part) ------------------------------


def test_one_insecure_login_warning_per_process(
    dash_factory: DashFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=ROUTES_LOGGER)
    dash = dash_factory(peer=REMOTE_PEER)
    assert dash.login().status_code == 200
    assert dash.login().status_code == 200
    (record,) = warnings_of(caplog, NOT_ENCRYPTED)
    assert dash.runtime.first_insecure_login_warned is True
    assert "password" in record.getMessage()


def test_no_insecure_login_warning_from_loopback(
    dash_factory: DashFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=ROUTES_LOGGER)
    dash = dash_factory(peer=LOOPBACK)
    assert dash.login().status_code == 200
    assert warnings_of(caplog, NOT_ENCRYPTED) == []


def test_no_insecure_login_warning_over_https(
    dash_factory: DashFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=ROUTES_LOGGER)
    dash = dash_factory(peer=REMOTE_PEER, base_url="https://testserver")
    assert dash.login().status_code == 200
    assert warnings_of(caplog, NOT_ENCRYPTED) == []


def test_a_failed_login_does_not_use_up_the_insecure_login_warning(
    dash_factory: DashFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger=ROUTES_LOGGER)
    dash = dash_factory(peer=REMOTE_PEER)
    assert dash.login(password="wrong").status_code == 401
    assert warnings_of(caplog, NOT_ENCRYPTED) == []
    assert dash.login().status_code == 200
    assert len(warnings_of(caplog, NOT_ENCRYPTED)) == 1
