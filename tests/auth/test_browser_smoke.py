"""Opt-in browser smoke for dashboard authentication (T-U2ERMo item 3; AC-34; HLD 20.1, 25.4).

``pytest -m browser tests/auth/test_browser_smoke.py`` drives real system Chrome through Playwright
(the ``tests/ui/test_e2e_graph.py`` pattern) against a real ``ao ui --auth`` subprocess. It skips,
with the reason, when Playwright (the ``browser`` extra) or ``/usr/bin/google-chrome`` is missing:
that is a **NOT RUN**, never a pass.

Covered: the CSP console detector and its negative control, the login screen and a password login,
and the cross-port proof check (S21): a cookie captured by a page on another localhost port cannot
call the dashboard API without the proof, and the victim's own session survives.

Not covered here (forced enrollment with the QR, the hub login page, Firefox/WebKit): they need a
browser to author and verify against, and the environment this file was written in had none.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.auth.constants import COOKIE_BASENAME
from agent_orchestrator.ui.security import SPA_CSP
from tests.auth.test_e2e_subprocess import (
    LOOPBACK,
    PASSWORD,
    USERNAME,
    ao_ui_server,
    free_port,
    make_sandbox,
    run_ao,
)

pytestmark = pytest.mark.browser

CHROME_PATH = "/usr/bin/google-chrome"
NAVIGATION_TIMEOUT_MS = 15_000
SETTLE_TIMEOUT_MS = 1_000
SCREENSHOT_DIR = Path(__file__).resolve().parents[2] / "output" / "E-Da5Tn9-dashboard-auth-totp"
CSP_INIT_SCRIPT = (
    "window.__cspViolations=[];"
    "document.addEventListener('securitypolicyviolation',"
    "e=>window.__cspViolations.push(e.violatedDirective))"
)


@pytest.fixture(scope="module")
def browser() -> Iterator[Any]:
    """Chrome via Playwright, or a skip that names what is missing."""
    sync_api = pytest.importorskip(
        "playwright.sync_api", reason="playwright not installed (uv sync --extra browser)"
    )
    if not Path(CHROME_PATH).exists():
        pytest.skip(f"Chrome not found at {CHROME_PATH}")
    with sync_api.sync_playwright() as playwright:
        chrome = playwright.chromium.launch(executable_path=CHROME_PATH)
        try:
            yield chrome
        finally:
            chrome.close()


def new_page(browser: Any) -> tuple[Any, list[str]]:
    """A page that records console errors, page errors and CSP violations (via the init script)."""
    page = browser.new_page()
    page.add_init_script(CSP_INIT_SCRIPT)
    errors: list[str] = []
    page.on(
        "console",
        lambda msg: errors.append(f"console: {msg.text}") if msg.type == "error" else None,
    )
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    return page, errors


class CaptureHandler(BaseHTTPRequestHandler):
    """The 'attacker' page on another localhost port: records every Cookie header it receives."""

    received: list[str] = []

    def do_GET(self) -> None:
        self.received.append(self.headers.get("Cookie", ""))
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>capture</body></html>")

    def log_message(self, format: str, *args: Any) -> None:  # quiet
        pass


def test_the_csp_detector_fires_on_an_inline_script(browser: Any) -> None:
    """Negative control: without it a silent detector would make the real checks meaningless."""

    class InlineScriptHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = "<html><body><script>console.log('inline')</script></body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Security-Policy", SPA_CSP)
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, format: str, *args: Any) -> None:
            pass

    server = HTTPServer((LOOPBACK, 0), InlineScriptHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        page, _ = new_page(browser)
        page.goto(f"http://{LOOPBACK}:{server.server_address[1]}/", timeout=NAVIGATION_TIMEOUT_MS)
        page.wait_for_timeout(SETTLE_TIMEOUT_MS)
        assert len(page.evaluate("window.__cspViolations")) >= 1
        page.close()
    finally:
        server.shutdown()


def test_login_screen_password_login_and_the_cross_port_proof_check(
    browser: Any, tmp_path: Path
) -> None:
    sandbox = make_sandbox(tmp_path)
    added = run_ao(
        sandbox,
        "auth", "add-user", USERNAME, "--password-stdin", "--auth-dir", str(sandbox.auth_dir),
        stdin=PASSWORD + "\n",
    )  # fmt: skip
    assert added.returncode == 0, added.stderr
    port = free_port()

    import httpx

    with ao_ui_server(sandbox, port, "--auth", "--auth-dir", str(sandbox.auth_dir)):
        origin = f"http://{LOOPBACK}:{port}"
        page, errors = new_page(browser)

        # the login screen renders, with no CSP violation
        page.goto(origin, timeout=NAVIGATION_TIMEOUT_MS)
        page.wait_for_selector("input[name=password]", timeout=NAVIGATION_TIMEOUT_MS)
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        login_shot = SCREENSHOT_DIR / "login.png"
        if not login_shot.exists():  # new files only: never overwrite an existing artifact
            page.screenshot(path=str(login_shot))

        # a password login lands in the dashboard (no second factor for this user)
        page.fill("input[name=username]", USERNAME)
        page.fill("input[name=password]", PASSWORD)
        page.click("button[type=submit]")
        page.wait_for_selector(
            "input[name=password]", state="detached", timeout=NAVIGATION_TIMEOUT_MS
        )
        page.wait_for_load_state("networkidle")
        assert page.evaluate("window.__cspViolations") == []

        # S21: another localhost port receives the dashboard cookie (cookies ignore ports) ...
        capture = HTTPServer((LOOPBACK, 0), CaptureHandler)
        CaptureHandler.received.clear()
        thread = threading.Thread(target=capture.serve_forever, daemon=True)
        thread.start()
        try:
            page.goto(
                f"http://{LOOPBACK}:{capture.server_address[1]}/", timeout=NAVIGATION_TIMEOUT_MS
            )
        finally:
            capture.shutdown()
        cookie_name = f"{COOKIE_BASENAME}_{port}"
        sent = next((c for c in CaptureHandler.received if cookie_name in c), None)
        assert sent is not None, (
            "the capture port did not receive the dashboard cookie (A4 premise)"
        )
        stolen = re.search(rf"{re.escape(cookie_name)}=([A-Za-z0-9_-]+)", sent)
        assert stolen is not None

        # ... but the cookie alone is useless: the proof sits in the dashboard origin's
        # localStorage, which port B cannot read
        replay = httpx.get(
            f"{origin}/api/runs",
            headers={"Origin": origin, "Cookie": f"{cookie_name}={stolen.group(1)}"},
            timeout=15.0,
        )
        assert replay.status_code == 401 and replay.json()["code"] == "not_authenticated"

        # the victim's session survived the replay: back on the dashboard, data still loads
        page.goto(origin, timeout=NAVIGATION_TIMEOUT_MS)
        page.wait_for_load_state("networkidle")
        assert page.locator("input[name=password]").count() == 0
        assert errors == []
        page.close()
