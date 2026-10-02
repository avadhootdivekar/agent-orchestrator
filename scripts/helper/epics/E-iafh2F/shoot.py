"""Start a real `ao ui` on a staged workspace and take screenshots with system Chrome.

Usage: python shoot.py <phase: 1|2> <workspace_dir> <out_dir>
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

RUN = "epic-runner-20261002T110000Z"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def serve(ws: Path, port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agent_orchestrator.cli",
            "ui",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workspace",
            str(ws),
        ],
        cwd=str(ws),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    for _ in range(100):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health", timeout=1).status_code == 200:
                return proc
        except Exception:
            time.sleep(0.2)
    proc.kill()
    raise SystemExit("server did not start")


def phase1(page, base: str, out: Path) -> dict:
    facts: dict = {}
    page.goto(base)
    page.get_by_role("button", name="Runs").first.click()
    page.wait_for_selector("[data-testid=now-running-body]")
    page.screenshot(path=str(out / "runs-list-compact.png"), full_page=True)
    page.get_by_role("button", name=RUN).click()
    page.wait_for_selector("text=Now running")
    page.wait_for_selector("[data-testid=now-running-body] [role=listitem]")
    page.wait_for_timeout(500)
    body = page.locator("[data-testid=now-running-body]")
    box = body.bounding_box()
    facts["box_height_px"] = box["height"]
    facts["rows_in_dom"] = body.locator("[role=listitem]").count()
    facts["scroll_height"] = body.evaluate("e => e.scrollHeight")
    facts["client_height"] = body.evaluate("e => e.clientHeight")
    facts["toggle_controls"] = page.locator(".now-running button, .now-running details").count()
    page.screenshot(path=str(out / "run-page-now-running-top.png"))
    body.evaluate("e => { e.scrollTop = e.scrollHeight }")
    page.wait_for_timeout(200)
    page.locator(".now-running").screenshot(path=str(out / "now-running-scrolled.png"))
    page.screenshot(path=str(out / "run-page-full.png"), full_page=True)
    return facts


def main() -> None:
    phase, ws, out = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    port = free_port()
    proc = serve(ws, port)
    base = f"http://127.0.0.1:{port}"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            facts = phase1(page, base, out) if phase == "1" else {}
            facts["console_errors"] = errors
            print(facts)
            browser.close()
    finally:
        os.killpg(os.getpgid(proc.pid), 15)


if __name__ == "__main__":
    main()
