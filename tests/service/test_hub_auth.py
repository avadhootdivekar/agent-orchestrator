"""T-KOv2qD: the hub app with auth off (unchanged) and on (HLD 2.4, 13.2, 16 rows 5 and 16).

Covers AC-19 (anonymous hub), AC-44 hub part (cookie-only principal on ``GET /`` only), the
security-L2 doc-URL check, NFR-1 (auth-off HTML byte-identical to the pre-auth implementation)
and packaging (the wheel ships ``auth/assets/**``).
"""

from __future__ import annotations

import tomllib
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi", reason="hub app needs the optional [ui] extra")
from fastapi.testclient import TestClient  # noqa: E402

from agent_orchestrator.auth.constants import SCOPE_PRINCIPAL_KEY  # noqa: E402
from agent_orchestrator.auth.http.hub_page import (  # noqa: E402
    HUB_ASSET_TYPES,
    hub_head_tags,
    read_hub_asset,
    render_login_page,
    render_signed_in_bar,
)
from agent_orchestrator.service.hub import _render_index_html, build_hub_app  # noqa: E402
from tests.auth.helpers.core import FakeClock  # noqa: E402
from tests.auth.helpers.enumeration import (  # noqa: E402
    ENUMERATED_METHODS,
    MUTATING,
    concrete_path,
    require_route_contexts,
    route_entries,
)
from tests.auth.helpers.hub import (  # noqa: E402
    FIXED_PAYLOAD,
    fixed_status_provider,
    make_hub,
)
from tests.auth.helpers.real_routes import Dash  # noqa: E402
from tests.auth.helpers.stub_runtime import ScopeCapture, capture_scope  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
IDLE_STEP_SECONDS = 25.0
NAVIGATION = {
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Site": "same-origin",
}
HTML = {"Accept": "text/html"}
JSON = {"Accept": "application/json"}

# Captured from the pre-change `_render_index_html(FIXED_PAYLOAD)` before service/hub.py was
# edited: the auth-off page must stay byte-identical (NFR-1).
GOLDEN_AUTH_OFF_INDEX = (
    '<!doctype html>\n<html>\n<head><meta charset="utf-8"><title>Agent Orchestrator Service'
    "</title></head>\n<body>\n<h1>Agent Orchestrator Service</h1>\n<p>Hub port: 8770 &middot; "
    "Supervisor pid: 999 &middot; Uptime: 42s</p>\n"
    '<table border="1" cellpadding="4" cellspacing="0">\n'
    "<thead><tr><th>Workspace</th><th>Dashboard</th><th>State</th><th>Runs</th></tr></thead>\n"
    "<tbody>\n"
    '<tr><td>/home/user/proj1</td><td><a href="http://127.0.0.1:9001/">http://127.0.0.1:9001/'
    "</a></td><td>running</td><td>3</td></tr>\n"
    '<tr><td>/home/user/proj2</td><td><a href="http://127.0.0.1:9002/">http://127.0.0.1:9002/'
    "</a></td><td>stopped</td><td>n/a</td></tr>\n"
    "</tbody>\n</table>\n</body>\n</html>\n"
)


@pytest.fixture()
def captured_hub(tmp_path: Path) -> tuple[Dash, ScopeCapture]:
    """A signed-in hub (alice, FULL: no second factor wired) with a scope capture installed.

    The capture must wrap ``app.router`` before the first request builds the middleware stack,
    so it goes in before the login; the login's own states are then cleared.
    """
    dash = make_hub(tmp_path, FakeClock())
    capture = capture_scope(dash.app)
    assert dash.login().status_code == 200
    capture.states.clear()
    return dash, capture


@pytest.fixture()
def hub(captured_hub: tuple[Dash, ScopeCapture]) -> Dash:
    return captured_hub[0]


@pytest.fixture()
def anon_hub(tmp_path: Path) -> Dash:
    return make_hub(tmp_path, FakeClock())


def _last_activity(dash: Dash, session: Any) -> float:
    record = dash.session_store.get(session.token_hash)
    assert record is not None
    return float(record.last_activity_mono)


# --- AC 1: auth off is unchanged ----------------------------------------------------------------


def test_auth_off_index_html_equals_the_pre_change_golden() -> None:
    assert _render_index_html(FIXED_PAYLOAD) == GOLDEN_AUTH_OFF_INDEX


def test_auth_off_app_serves_the_golden_and_keeps_the_doc_urls() -> None:
    client = TestClient(build_hub_app(fixed_status_provider), base_url="http://localhost")
    response = client.get("/")
    assert response.status_code == 200 and response.text == GOLDEN_AUTH_OFF_INDEX
    assert "Cache-Control" not in response.headers  # no auth header leaked into the off path
    assert "hub-auth" not in response.text
    assert client.get("/api/service/status").json() == FIXED_PAYLOAD
    assert client.get("/redoc").status_code == 200
    assert client.get("/docs/oauth2-redirect").status_code == 200
    assert client.get("/openapi.json").status_code == 200  # was a 500 (unresolvable annotation)
    assert client.get("/login").status_code == 404  # the login page exists only with auth on
    assert client.get("/api/auth/status").json()["enabled"] is False


# --- AC 2: anonymous with auth on ---------------------------------------------------------------


def test_anonymous_index_redirects_html_and_401s_json(anon_hub: Dash) -> None:
    html = anon_hub.client.get("/", headers=HTML)
    assert html.status_code == 303 and html.headers["location"] == "/login"
    api = anon_hub.client.get("/", headers=JSON)
    assert api.status_code == 401 and api.json()["code"] == "not_authenticated"
    assert anon_hub.client.get("/api/service/status").status_code == 401


# --- AC 3: login page ---------------------------------------------------------------------------


class _PageAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[dict[str, str | None]] = []
        self.on_attributes: list[str] = []
        self.styles = 0
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        mapping = dict(attrs)
        if tag == "script":
            self.scripts.append(mapping)
        if tag == "style":
            self.styles += 1
        self.on_attributes += [name for name in mapping if name.startswith("on")]
        if mapping.get("id"):
            self.ids.add(str(mapping["id"]))


def test_login_page_is_static_and_script_safe(anon_hub: Dash) -> None:
    response = anon_hub.client.get("/login")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    audit = _PageAudit()
    audit.feed(response.text)
    assert audit.scripts and all(script.get("src") for script in audit.scripts)
    assert audit.on_attributes == [] and audit.styles == 0
    assert {"ao-login-form", "ao-totp-form", "ao-enroll-token-form", "ao-transport-warning"} <= (
        audit.ids
    )
    assert response.text == render_login_page()


# --- AC 4: assets -------------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(HUB_ASSET_TYPES))
def test_assets_are_served_with_their_exact_content_type(anon_hub: Dash, name: str) -> None:
    response = anon_hub.client.get(f"/auth-assets/{name}")
    assert response.status_code == 200
    assert response.headers["content-type"] == HUB_ASSET_TYPES[name]
    assert response.content and response.content == read_hub_asset(name)


def test_an_unknown_asset_name_is_404_without_detail(anon_hub: Dash) -> None:
    response = anon_hub.client.get("/auth-assets/x.js")
    assert response.status_code == 404
    assert response.content == b""


@pytest.mark.parametrize("path", ["/auth-assets/..%2Fusers.json", "/auth-assets/"])
def test_paths_that_match_no_asset_route_are_never_served(anon_hub: Dash, path: str) -> None:
    # They match no route at all (``{name}`` is one path segment), so the deny-by-default
    # middleware answers an anonymous caller 401 before any handler runs; the ticket's "404"
    # for these two is superseded by that rule (HLD 13.2: an unknown route is AUTHENTICATED).
    response = anon_hub.client.get(path)
    assert response.status_code in (401, 404)
    assert b"users" not in response.content.lower() or response.status_code == 401
    assert not response.content.startswith(b"(function")


def test_read_hub_asset_allowlists_before_touching_the_filesystem() -> None:
    for bad in ("x.js", "../users.json", "", "hub-auth.js/../hub-auth.css"):
        with pytest.raises(KeyError):
            read_hub_asset(bad)


# --- AC 5: signed in ----------------------------------------------------------------------------


def test_cookie_only_index_renders_the_signed_in_page(hub: Dash) -> None:
    response = hub.client.get("/", headers=HTML)  # a navigation: cookie only, no proof header
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "Signed in as <strong>alice</strong>" in response.text
    assert 'id="ao-logout"' in response.text
    assert '<script src="/auth-assets/hub-auth.js" defer></script>' in response.text
    assert "proj1" in response.text  # today's table is still there


def test_status_json_needs_the_proof(hub: Dash) -> None:
    assert hub.client.get("/api/service/status").status_code == 401
    proof = hub.proof
    assert proof
    ok = hub.client.get("/api/service/status", headers=hub.headers(proof))
    assert ok.status_code == 200 and ok.json() == FIXED_PAYLOAD


# --- AC 6: cookie-only principal, hub part (AC-44, S20, S27) ------------------------------------


def test_the_index_alone_yields_a_cookie_only_principal(
    captured_hub: tuple[Dash, ScopeCapture],
) -> None:
    hub, capture = captured_hub
    assert hub.client.get("/", headers=HTML).status_code == 200
    (state,) = capture.states
    assert state[SCOPE_PRINCIPAL_KEY].username == "alice"


def _session(hub: Dash) -> Any:
    """The login's session record (looked up by its cookie; a lookup does not slide it)."""
    record = hub.runtime.sessions.lookup(hub.client.cookies.get(hub.cookie_name))
    assert record is not None
    return record


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_a_browser_navigation_slides_the_index(hub: Dash, site: str) -> None:
    issued = _session(hub)
    before = _last_activity(hub, issued)
    hub.clock.advance(IDLE_STEP_SECONDS)
    response = hub.client.get("/", headers={**NAVIGATION, "Sec-Fetch-Site": site})
    assert response.status_code == 200
    assert _last_activity(hub, issued) == before + IDLE_STEP_SECONDS


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Sec-Fetch-Site": "same-origin"},
        {**NAVIGATION, "Sec-Fetch-Site": "same-site"},
        {**NAVIGATION, "Sec-Fetch-Site": "cross-site"},
    ],
)
def test_the_index_does_not_slide_without_browser_attestation(
    captured_hub: tuple[Dash, ScopeCapture], headers: dict[str, str]
) -> None:
    hub, capture = captured_hub
    issued = _session(hub)
    before = _last_activity(hub, issued)
    hub.clock.advance(IDLE_STEP_SECONDS)
    assert hub.client.get("/", headers=headers).status_code == 200
    assert capture.states[0][SCOPE_PRINCIPAL_KEY] is not None  # still served from the cookie
    assert _last_activity(hub, issued) == before


def test_every_other_hub_route_sees_no_principal_and_never_slides(
    captured_hub: tuple[Dash, ScopeCapture],
) -> None:
    hub, capture = captured_hub
    require_route_contexts()
    issued = _session(hub)
    baseline = _last_activity(hub, issued)
    hub.clock.advance(IDLE_STEP_SECONDS)  # a slide would now be visible
    headers = {**NAVIGATION}  # worst case: navigation-shaped, same-origin, but no proof
    requests = 0
    for entry in route_entries(hub.app):
        path = concrete_path(entry.template)
        for method in ENUMERATED_METHODS:
            if (method, entry.template) == ("GET", "/"):
                continue
            seen = len(capture.states)
            kwargs: dict[str, Any] = {"headers": headers}
            if method in MUTATING:
                kwargs["json"] = {}
            hub.client.request(method, path, **kwargs)
            requests += 1
            for state in capture.states[seen:]:
                assert state[SCOPE_PRINCIPAL_KEY] is None, (method, entry.template)
            assert _last_activity(hub, issued) == baseline, (method, entry.template)
    assert requests >= 40 and capture.states


@pytest.mark.parametrize("path", ["/docs", "/openapi.json"])
def test_docs_and_openapi_need_the_proof(hub: Dash, path: str) -> None:
    html = hub.client.get(path, headers=HTML)
    assert html.status_code == 303 and html.headers["location"] == "/login"
    api = hub.client.get(path, headers=JSON)
    assert api.status_code == 401
    assert hub.client.get(path, headers=hub.headers(hub.proof)).status_code == 200


# --- AC 7: doc URLs (security L2) ---------------------------------------------------------------


def test_redoc_and_oauth2_redirect_are_not_registered_with_auth_on(anon_hub: Dash) -> None:
    paths = {getattr(route, "path", None) for route in anon_hub.app.router.routes}
    assert "/redoc" not in paths and "/docs/oauth2-redirect" not in paths
    assert {"/docs", "/openapi.json"} <= paths
    assert anon_hub.client.get("/redoc", headers=HTML).status_code == 303
    assert anon_hub.client.get("/docs/oauth2-redirect", headers=JSON).status_code == 401


# --- AC 8: escaping and rendering ---------------------------------------------------------------


def test_signed_in_bar_escapes_its_values() -> None:
    bar = render_signed_in_bar("<b>x</b>", "password")
    assert "&lt;b&gt;" in bar and "<b>" not in bar
    assert '<button type="button" id="ao-logout">' in bar


def test_index_with_a_principal_adds_head_tags_and_the_bar_only() -> None:
    from types import SimpleNamespace

    principal: Any = SimpleNamespace(username="alice", auth_method="password")
    page = _render_index_html(FIXED_PAYLOAD, principal)
    assert hub_head_tags() + "</head>" in page
    assert page.index('<body>\n<div id="ao-account-bar">') > 0
    stripped = page.replace(hub_head_tags(), "").replace(
        render_signed_in_bar("alice", "password"), ""
    )
    assert stripped == GOLDEN_AUTH_OFF_INDEX


# --- AC 10: packaging ---------------------------------------------------------------------------


def test_wheel_artifacts_include_auth_assets() -> None:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    artifacts = config["tool"]["hatch"]["build"]["targets"]["wheel"]["artifacts"]
    assert "src/agent_orchestrator/auth/assets/**" in artifacts
    assert "src/agent_orchestrator/ui/static/**" in artifacts
    assert read_hub_asset("hub-auth.js") and read_hub_asset("hub-auth.css")
