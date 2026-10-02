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
    page.get_by_role("button", name="Runs", exact=True).first.click()
    page.wait_for_selector("[data-testid=now-running-body]")
    page.screenshot(path=str(out / "runs-list-compact.png"), full_page=True)
    page.get_by_role("link", name=RUN).click()
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


def tab_names(page) -> list[str]:
    return page.get_by_role("tablist", name="Open tabs").get_by_role("tab").all_inner_texts()


def phase2(browser, page, base: str, out: Path) -> dict:
    facts: dict = {}
    page.goto(base)
    page.wait_for_selector("text=Total runs")
    page.get_by_role("button", name=f"Open run {RUN} in new tab").click()
    page.wait_for_selector("text=Now running")
    page.get_by_role("button", name="Open task worker-0 in new tab").click()
    page.wait_for_selector("text=Timing")
    page.get_by_role("tab", name=RUN).click()
    # ctrl-click a task link: background tab, active tab unchanged
    page.get_by_role("link", name="worker-1", exact=True).first.click(modifiers=["Control"])
    page.get_by_role("button", name="Files").click()
    page.get_by_role("button", name="docs").click()
    page.get_by_role("button", name="plan.md").click()
    page.wait_for_selector("text=Staged file")
    page.get_by_role("button", name="Open docs/plan.md in new tab").click()
    page.get_by_role("button", name="Usage").click()
    page.wait_for_timeout(600)
    facts["tabs_after_open"] = tab_names(page)
    facts["hash_active"] = page.evaluate("location.hash")
    page.screenshot(path=str(out / "tabs-workspace.png"))
    # reorder: move the Usage tab left twice with Alt+ArrowLeft
    page.get_by_role("tab", name="Usage").focus()
    page.keyboard.press("Alt+ArrowLeft")
    page.keyboard.press("Alt+ArrowLeft")
    page.wait_for_timeout(200)
    facts["tabs_after_reorder"] = tab_names(page)
    # reload restores tab set (localStorage) and active tab (hash)
    page.reload()
    page.wait_for_selector("[role=tablist]")
    facts["tabs_after_reload"] = tab_names(page)
    facts["active_after_reload"] = page.get_by_role("tab", selected=True).inner_text()
    # a link opened in a REAL new browser tab / fresh profile restores from the hash alone
    url = page.url
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    fresh = ctx.new_page()
    fresh.goto(base + "/#/task?run=" + RUN + "&id=worker-2")
    fresh.wait_for_selector("text=Timing")
    facts["fresh_browser_tab"] = {"url": fresh.url, "tabs": tab_names(fresh)}
    fresh.screenshot(path=str(out / "tab-from-hash-new-browser-tab.png"))
    fresh.goto(base + "/#/run?id=../../etc/passwd")
    fresh.wait_for_selector("[role=tablist]")
    facts["hostile_hash_tabs"] = tab_names(fresh)
    ctx.close()
    # inactive tabs make no requests
    reqs: list[str] = []
    page.on("request", lambda r: reqs.append(r.url))
    page.get_by_role("tab", name="Usage").click()
    page.wait_for_timeout(7500)
    facts["polling_requests_while_runs_tab_inactive"] = [u for u in reqs if u.endswith("/api/runs")]
    page.screenshot(path=str(out / "tabs-reordered-usage-active.png"))
    facts["url"] = url
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
            facts = phase1(page, base, out) if phase == "1" else phase2(browser, page, base, out)
            facts["console_errors"] = errors
            print(facts)
            browser.close()
    finally:
        os.killpg(os.getpgid(proc.pid), 15)


if __name__ == "__main__":
    main()
