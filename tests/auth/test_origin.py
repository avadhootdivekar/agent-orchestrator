"""T-QJ1vyQ: ``origin_matches_host`` unit table and parity with ``ui.security`` (AC-41 part, S4)."""

from __future__ import annotations

import pytest
from starlette.requests import Request

from agent_orchestrator.auth.http.origin import origin_matches_host
from agent_orchestrator.ui.security import _is_origin_allowed

ALLOWLIST = frozenset({"127.0.0.1", "localhost", "testserver"})


@pytest.mark.parametrize(
    ("origin", "scheme", "host", "expected"),
    [
        ("http://h:8765", "http", "h:8765", True),
        ("http://h", "http", "h:80", True),
        ("http://h:80", "http", "h", True),
        ("https://h:443", "https", "h", True),
        ("https://h", "https", "h:443", True),
        ("http://[::1]:8765", "http", "[::1]:8765", True),
        ("http://H.Example:8765", "http", "h.example:8765", True),
        ("http://h:8765", "http", "H:8765", True),
        ("http://h:8765/", "http", "h:8765", True),
        ("  http://h:8765 ", "http", "h:8765", True),
        # different port / scheme / host
        ("http://h:9999", "http", "h:8765", False),
        ("https://h:8765", "http", "h:8765", False),
        ("http://h:8765", "https", "h:8765", False),
        ("http://other:8765", "http", "h:8765", False),
        ("http://h:443", "http", "h", False),
        ("https://h:80", "https", "h", False),
        ("http://[::1]:8765", "http", "[::2]:8765", False),
        # hostile or malformed
        ("null", "http", "h:8765", False),
        ("NULL", "http", "h:8765", False),
        ("http://u@h:8765", "http", "h:8765", False),
        ("http://h:8765", "http", "u@h:8765", False),
        ("http://h:8765/x", "http", "h:8765", False),
        ("http://h:8765?q", "http", "h:8765", False),
        ("http://h:8765#f", "http", "h:8765", False),
        ("", "http", "h:8765", False),
        ("http://h:8765", "http", "", False),
        ("garbage", "http", "h:8765", False),
        ("ftp://h:8765", "ftp", "h:8765", False),
        ("http://h:8765", "gopher", "h:8765", False),
        ("http://h:notaport", "http", "h:notaport", False),
        ("http://h:99999", "http", "h:99999", False),
        ("http://", "http", "h", False),
        ("http://h:8765", "http", "h:8765/x", False),
        ("http://h:8765", "http", "h:8765?q", False),
        ("http://[::1", "http", "h", False),
        ("http://h:8765", "http", "[::1", False),
    ],
)
def test_origin_matches_host_table(origin: str, scheme: str, host: str, expected: bool) -> None:
    assert origin_matches_host(origin, scheme=scheme, host_header=host) is expected


# --- parity with ui.security._is_origin_allowed -----------------------------------------------


def _request(scheme: str, host: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": scheme,
            "path": "/",
            "query_string": b"",
            "headers": [(b"host", host.encode())],
        }
    )


def _both(
    origin: str, scheme: str, host: str, allowed: frozenset[str] | None = ALLOWLIST
) -> tuple[bool, bool]:
    legacy = _is_origin_allowed(origin, _request(scheme, host), allowed)
    return legacy, origin_matches_host(origin, scheme=scheme, host_header=host)


# (origin, scheme, Host header, expected) -- both functions must agree on every one of these.
AGREEING_CASES = [
    ("http://127.0.0.1:8765", "http", "127.0.0.1:8765", True),
    ("http://localhost:8765", "http", "localhost:8765", True),
    ("http://LOCALHOST:8765", "http", "localhost:8765", True),
    ("http://testserver", "http", "testserver", True),
    ("https://127.0.0.1:8765", "https", "127.0.0.1:8765", True),
    ("http://127.0.0.1:9999", "http", "127.0.0.1:8765", False),
    ("https://127.0.0.1:8765", "http", "127.0.0.1:8765", False),
    ("http://127.0.0.1:8765", "https", "127.0.0.1:8765", False),
    ("http://evil.example:8765", "http", "127.0.0.1:8765", False),
    ("null", "http", "127.0.0.1:8765", False),
    ("http://u@127.0.0.1:8765", "http", "127.0.0.1:8765", False),
    ("", "http", "127.0.0.1:8765", False),
    ("garbage", "http", "127.0.0.1:8765", False),
    ("http://testserver:8765", "http", "testserver:9", False),
]


@pytest.mark.parametrize(("origin", "scheme", "host", "expected"), AGREEING_CASES)
def test_parity_on_agreeing_cases(origin: str, scheme: str, host: str, expected: bool) -> None:
    assert _both(origin, scheme, host) == (expected, expected)


def test_parity_list_is_large_enough() -> None:
    assert len(AGREEING_CASES) >= 12


# The documented differences: ``(legacy _is_origin_allowed, origin_matches_host)`` observed.
DOCUMENTED_DIFFERENCES = [
    # The legacy check is an allowlist test on the origin host, so an allowlisted host that is
    # not this request's Host passes it; origin_matches_host compares against the Host itself.
    ("http://localhost:8765", "http", "127.0.0.1:8765", ALLOWLIST, (True, False)),
    # Allowlist disabled (AO_UI_ALLOWED_HOSTS=*): legacy accepts anything.
    ("http://evil.example:1", "http", "127.0.0.1:8765", None, (True, False)),
    ("null", "http", "127.0.0.1:8765", None, (True, False)),
    # Default-port spellings: legacy compares urlsplit ports literally (None vs 80/443).
    ("http://testserver:80", "http", "testserver", ALLOWLIST, (False, True)),
    ("http://testserver", "http", "testserver:80", ALLOWLIST, (False, True)),
    ("https://testserver:443", "https", "testserver", ALLOWLIST, (False, True)),
    # An IPv6 loopback is not in the test allowlist; Host-relative comparison accepts it.
    ("http://[::1]:8765", "http", "[::1]:8765", ALLOWLIST, (False, True)),
    # Path, query and fragment: legacy only looks at scheme/host/port.
    ("http://127.0.0.1:8765/x", "http", "127.0.0.1:8765", ALLOWLIST, (True, False)),
    ("http://127.0.0.1:8765?q", "http", "127.0.0.1:8765", ALLOWLIST, (True, False)),
    ("http://127.0.0.1:8765#f", "http", "127.0.0.1:8765", ALLOWLIST, (True, False)),
]


@pytest.mark.parametrize(
    ("origin", "scheme", "host", "allowed", "observed"), DOCUMENTED_DIFFERENCES
)
def test_documented_differences(
    origin: str,
    scheme: str,
    host: str,
    allowed: frozenset[str] | None,
    observed: tuple[bool, bool],
) -> None:
    assert _both(origin, scheme, host, allowed) == observed
