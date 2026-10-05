"""Real-process end-to-end for dashboard authentication (T-U2ERMo item 2; AC-33; HLD 20.2).

Everything crosses a real process boundary and runs against a hermetic environment (tmp HOME/XDG,
tmp ``--auth-dir``, no ambient ``AO_*`` variable):

* ``ao auth add-user`` and ``ao auth enable-2fa`` run as subprocesses (the latter driven over stdin:
  the secret is parsed from its output and the code for the *current* step is sent back);
* a real ``ao ui --auth`` server runs as a subprocess on a free loopback port and is driven over
  real HTTP with a same-origin ``Origin`` header.

The TOTP discipline of HLD 20.2: the CLI enrollment consumed the current step and the server only
accepts strictly increasing steps, so the one web TOTP login uses the code for ``step + 1`` (inside
the +-1 window, never a replay). Every later login uses a recovery code. The scenario runs three
times on fresh directories and ports (AC-33 requires three consecutive passes).
"""

from __future__ import annotations

import base64
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.auth import totp
from agent_orchestrator.auth.constants import (
    COOKIE_BASENAME,
    RECOVERY_CODE_COUNT,
    SESSION_PROOF_HEADER,
)
from agent_orchestrator.errors import EXIT_CONFIG

pytest.importorskip("fastapi", reason="dashboard needs the optional [ui] extra")
pytest.importorskip("uvicorn", reason="dashboard needs the optional [ui] extra")
httpx = pytest.importorskip("httpx", reason="dashboard e2e needs httpx")

pytestmark = pytest.mark.e2e

LOOPBACK = "127.0.0.1"
AO_COMMAND = [sys.executable, "-m", "agent_orchestrator.cli"]
USERNAME = "alice"
PASSWORD = "correct horse battery staple"
WRONG_PASSWORD = "definitely not the password"

CLI_TIMEOUT_SECONDS = 60.0  # real scrypt hashing is ~0.1-0.3 s; generous for a loaded box
SERVER_BOOT_TIMEOUT_SECONDS = 45.0
SERVER_STOP_TIMEOUT_SECONDS = 15.0
HTTP_TIMEOUT_SECONDS = 15.0
POLL_INTERVAL_SECONDS = 0.1
ENROLL_WATCHDOG_SECONDS = 60.0
SCENARIO_RUNS = 3  # AC-33: three consecutive passes, each on fresh dirs and a fresh port

RECOVERY_CODE_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){3}$")
SECRET_LINE_PREFIX = "Secret (base32):"
DISABLED_STATUS_BODY = {
    "enabled": False,
    "state": "disabled",
    "user": None,
    "pending_username": None,
    "second_factors": None,
    "enrollment_token_required": None,
    "policy": None,
    "session": None,
    "transport": None,
}

LOGIN_PATH = "/api/auth/login"
VERIFY_PATH = "/api/auth/totp/verify"
LOGOUT_PATH = "/api/auth/logout"
STATUS_PATH = "/api/auth/status"
HEALTH_PATH = "/api/health"
PROTECTED_PATH = "/api/runs"


# ---------------------------------------------------------------------------
# Hermetic environment and processes
# ---------------------------------------------------------------------------


@dataclass
class Sandbox:
    root: Path
    env: dict[str, str]
    auth_dir: Path
    workspace: Path


def make_sandbox(root: Path) -> Sandbox:
    """A tmp HOME/XDG/AO_AUTH_DIR layout and an environment with no ambient ``AO_*`` setting."""
    home = root / "home"
    workspace = root / "ws"
    for directory in (home, workspace):
        directory.mkdir(parents=True)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("AO_") and key not in {"XDG_CONFIG_HOME", "XDG_STATE_HOME", "HOME"}
    }
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(root / "xdg-config"),
        XDG_STATE_HOME=str(root / "xdg-state"),
        PYTHONUNBUFFERED="1",
    )
    return Sandbox(root=root, env=env, auth_dir=(root / "auth"), workspace=workspace)


def free_port() -> int:
    """Reserve an ephemeral loopback port, then release it for the server to bind."""
    with socket.socket() as sock:
        sock.bind((LOOPBACK, 0))
        return int(sock.getsockname()[1])


def run_ao(
    sandbox: Sandbox, *args: str, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*AO_COMMAND, *args],
        input=stdin,
        env=sandbox.env,
        cwd=sandbox.root,
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT_SECONDS,
    )


def create_user_with_totp(sandbox: Sandbox) -> tuple[bytes, list[str]]:
    """``ao auth add-user`` then ``ao auth enable-2fa`` (driven over stdin); returns the seed and
    the ten recovery codes the CLI printed once."""
    added = run_ao(
        sandbox,
        "auth", "add-user", USERNAME, "--password-stdin", "--auth-dir", str(sandbox.auth_dir),
        stdin=PASSWORD + "\n",
    )  # fmt: skip
    assert added.returncode == 0, added.stderr

    proc = subprocess.Popen(
        [*AO_COMMAND, "auth", "enable-2fa", USERNAME, "--auth-dir", str(sandbox.auth_dir)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=sandbox.env,
        cwd=sandbox.root,
        text=True,
    )
    watchdog = threading.Timer(ENROLL_WATCHDOG_SECONDS, proc.kill)
    watchdog.start()
    try:
        assert proc.stdin is not None and proc.stdout is not None
        secret: bytes | None = None
        while secret is None:
            line = proc.stdout.readline()
            assert line, "enable-2fa ended before printing the secret"
            if line.startswith(SECRET_LINE_PREFIX):
                grouped = line.removeprefix(SECRET_LINE_PREFIX)
                secret = base64.b32decode("".join(grouped.split()))
        # The code for the CURRENT step; the CLI records that step as used (HLD 20.2).
        proc.stdin.write(totp.hotp(secret, totp.totp_step(time.time())) + "\n")
        proc.stdin.flush()
        proc.stdin.close()
        rest = proc.stdout.read()
        assert proc.wait(timeout=CLI_TIMEOUT_SECONDS) == 0, rest
    finally:
        watchdog.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    codes = [line.strip() for line in rest.splitlines() if RECOVERY_CODE_RE.fullmatch(line.strip())]
    assert len(codes) == RECOVERY_CODE_COUNT and len(set(codes)) == RECOVERY_CODE_COUNT
    return secret, codes


@contextmanager
def ao_ui_server(sandbox: Sandbox, port: int, *extra: str) -> Iterator[subprocess.Popen[bytes]]:
    """A real ``ao ui`` subprocess with a bounded boot wait and a bounded teardown."""
    log_path = sandbox.root / f"ui-{port}.log"
    with log_path.open("wb") as log:
        proc = subprocess.Popen(
            [
                *AO_COMMAND,
                "ui",
                "--workspace",
                str(sandbox.workspace),
                "--host",
                LOOPBACK,
                "--port",
                str(port),
                *extra,
            ],  # fmt: skip
            stdout=log,
            stderr=subprocess.STDOUT,
            env=sandbox.env,
            cwd=sandbox.root,
        )
        try:
            wait_until_serving(proc, port, log_path)
            yield proc
        finally:
            if proc.poll() is None:
                proc.send_signal(signal.SIGTERM)
                try:
                    proc.wait(timeout=SERVER_STOP_TIMEOUT_SECONDS)
                except subprocess.TimeoutExpired:  # pragma: no cover - only on a wedged server
                    proc.kill()
                    proc.wait()
        assert_log_is_clean(log_path)  # reached only when the test body did not raise


# T-2wE08U H2: the process-wide log redaction once broke uvicorn's access formatter, so every
# request printed a "--- Logging error ---" traceback. stderr is merged into the server log.
LOG_FAILURE_MARKERS = ("Logging error", "Traceback")


def assert_log_is_clean(log_path: Path) -> None:
    text = log_path.read_text(errors="replace")
    for marker in LOG_FAILURE_MARKERS:
        assert marker not in text, f"{marker!r} in the server log:\n{text[:3000]}"


def wait_until_serving(proc: subprocess.Popen[bytes], port: int, log_path: Path) -> None:
    deadline = time.monotonic() + SERVER_BOOT_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        assert proc.poll() is None, (
            f"ao ui exited early ({proc.returncode}):\n{log_path.read_text()}"
        )
        try:
            if httpx.get(f"http://{LOOPBACK}:{port}{HEALTH_PATH}", timeout=2.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(POLL_INTERVAL_SECONDS)
    proc.kill()
    raise AssertionError(f"ao ui did not serve in time:\n{log_path.read_text()}")


# ---------------------------------------------------------------------------
# A tiny HTTP browser model: manual cookie + proof, same-origin Origin
# ---------------------------------------------------------------------------


class Browser:
    """One tab: the cookie and the session proof of the latest session-issuing response."""

    def __init__(self, port: int) -> None:
        self.port = port
        self.origin = f"http://{LOOPBACK}:{port}"
        self.cookie_name = f"{COOKIE_BASENAME}_{port}"
        self.cookie: str | None = None
        self.proof: str | None = None
        self.client = httpx.Client(base_url=self.origin, timeout=HTTP_TIMEOUT_SECONDS)

    def close(self) -> None:
        self.client.close()

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        cookie: str | None | bool = True,
        proof: str | None | bool = True,
    ) -> httpx.Response:
        """``cookie``/``proof``: True = the current one, a string = that, None/False = omit."""
        headers = {"Origin": self.origin}
        sent_cookie = self.cookie if cookie is True else cookie
        sent_proof = self.proof if proof is True else proof
        if sent_cookie:
            headers["Cookie"] = f"{self.cookie_name}={sent_cookie}"
        if sent_proof:
            headers[SESSION_PROOF_HEADER] = str(sent_proof)
        if method == "GET":
            return self.client.get(path, headers=headers)
        return self.client.post(path, json=body if body is not None else {}, headers=headers)

    def adopt(self, response: httpx.Response) -> dict[str, Any]:
        """Follow a session-issuing 200: store its cookie and proof."""
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        self.proof = body["session_proof"]
        set_cookie = response.headers["set-cookie"]
        attributes = "; Path=/; HttpOnly; SameSite=Strict"
        match = re.fullmatch(
            rf"{re.escape(self.cookie_name)}=([A-Za-z0-9_-]{{43}}){re.escape(attributes)}",
            set_cookie,
        )
        assert match, f"unexpected Set-Cookie: {set_cookie!r}"
        self.cookie = match.group(1)
        return body

    def login(self, password: str = PASSWORD) -> httpx.Response:
        return self.request(
            "POST",
            LOGIN_PATH,
            {"username": USERNAME, "password": password},
            cookie=None,
            proof=None,
        )


def assert_error(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    assert body["code"] == code, body
    return body


def next_step_code(secret: bytes) -> str:
    """The code for ``step + 1``, computed immediately before it is sent (HLD 20.2)."""
    return totp.hotp(secret, totp.totp_step(time.time()) + 1)


# ---------------------------------------------------------------------------
# The scenarios
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("run_number", range(1, SCENARIO_RUNS + 1))
def test_login_totp_proof_logout_and_recovery_code_over_a_real_server(
    tmp_path: Path, run_number: int
) -> None:
    sandbox = make_sandbox(tmp_path)
    secret, recovery_codes = create_user_with_totp(sandbox)
    port = free_port()

    with ao_ui_server(sandbox, port, "--auth", "--auth-dir", str(sandbox.auth_dir)) as server:
        tab = Browser(port)
        try:
            # 0. the one unauthenticated endpoint a probe needs stays public
            assert tab.request("GET", HEALTH_PATH, cookie=None, proof=None).status_code == 200

            # 1. status -> anonymous (auth on, loopback client, no proxy suspected)
            status = tab.request("GET", STATUS_PATH, cookie=None, proof=None).json()
            assert status["enabled"] is True and status["state"] == "anonymous"
            assert status["transport"] == {
                "secure": False, "client_is_loopback": True, "proxy_suspected": False,
            }  # fmt: skip

            # an anonymous API call is refused outright
            assert_error(
                tab.request("GET", PROTECTED_PATH, cookie=None, proof=None),
                401,
                "not_authenticated",
            )

            # 2. wrong password is a uniform 401 and sets no session
            wrong = tab.login(WRONG_PASSWORD)
            assert_error(wrong, 401, "invalid_credentials")
            assert "set-cookie" not in wrong.headers

            # 3. login -> second_factor_required (+ proof P1)
            first = tab.login()
            partial = tab.adopt(first)
            assert partial["state"] == "second_factor_required"
            assert "totp" in partial["second_factors"]
            partial_pair = (tab.cookie, tab.proof)

            # a second-factor-pending session reaches nothing but its step (partial confinement)
            assert_error(tab.request("GET", PROTECTED_PATH), 401, "second_factor_required")

            # 4. verify with the code for current step + 1 -> authenticated (+ proof P2)
            verified = tab.adopt(tab.request("POST", VERIFY_PATH, {"code": next_step_code(secret)}))
            assert verified["state"] == "authenticated"
            assert verified["user"]["username"] == USERNAME
            assert verified["user"]["auth_method"] == "password+totp"
            assert verified["used_recovery_code"] is False
            assert (tab.cookie, tab.proof) != partial_pair  # the pair rotated on the 2nd factor
            assert_error(  # the old pair is dead
                tab.request("GET", PROTECTED_PATH, cookie=partial_pair[0], proof=partial_pair[1]),
                401,
                "not_authenticated",
            )

            # 5. the session proof requirement, asserted explicitly (D25)
            assert tab.request("GET", PROTECTED_PATH).status_code == 200  # cookie + P2
            assert_error(  # cookie, no proof
                tab.request("GET", PROTECTED_PATH, proof=None), 401, "not_authenticated"
            )
            assert_error(  # cookie, wrong proof
                tab.request("GET", PROTECTED_PATH, proof="A" * 43), 401, "not_authenticated"
            )
            # a missing proof never destroys the session: P2 works again afterwards
            assert tab.request("GET", PROTECTED_PATH).status_code == 200
            # status without the proof reports anonymous; with it, the user
            assert tab.request("GET", STATUS_PATH, proof=None).json()["state"] == "anonymous"
            assert tab.request("GET", STATUS_PATH).json()["state"] == "authenticated"

            # 6. logout with P2 -> 200 and the cache-clearing header
            logout = tab.request("POST", LOGOUT_PATH)
            assert logout.status_code == 200 and logout.json() == {"state": "anonymous"}
            assert logout.headers["clear-site-data"] == '"cache"'
            assert "Max-Age=0" in logout.headers["set-cookie"]

            # 7. the old cookie plus the old proof is dead
            assert_error(tab.request("GET", PROTECTED_PATH), 401, "not_authenticated")

            # 8. login again with a RECOVERY code (never a second TOTP code)
            tab.adopt(tab.login())
            used_code = recovery_codes[0]
            recovered = tab.adopt(tab.request("POST", VERIFY_PATH, {"recovery_code": used_code}))
            assert recovered["state"] == "authenticated"
            assert recovered["used_recovery_code"] is True
            assert recovered["user"]["recovery_codes_remaining"] == RECOVERY_CODE_COUNT - 1, (
                recovered
            )
            assert tab.request("GET", PROTECTED_PATH).status_code == 200
            assert tab.request("POST", LOGOUT_PATH).status_code == 200

            # a recovery code is single-use: replaying it fails and the count stays at 9
            tab.adopt(tab.login())
            replay = assert_error(
                tab.request("POST", VERIFY_PATH, {"recovery_code": used_code}), 401, "invalid_code"
            )
            assert replay["attempts_remaining"] >= 1
            assert_error(tab.request("GET", PROTECTED_PATH), 401, "second_factor_required")
            # a different, unused code still works (formatting is normalized: lower case, no dashes)
            other = recovery_codes[1].lower().replace("-", "")
            final = tab.adopt(tab.request("POST", VERIFY_PATH, {"recovery_code": other}))
            assert final["user"]["recovery_codes_remaining"] == RECOVERY_CODE_COUNT - 2
            assert tab.request("POST", LOGOUT_PATH).status_code == 200
        finally:
            tab.close()
        assert server.poll() is None  # the server survived the whole scenario

    # 9. SIGTERM (context exit) stopped the server inside its timeout. uvicorn finishes its
    # graceful shutdown and then re-raises the signal, so "clean" is 0 or death-by-SIGTERM.
    assert server.returncode in (0, -signal.SIGTERM)


# T-2wE08U N1: an empty-value key (`?code=`) made the formatted access line and its per-arg
# redaction disagree, the fallback flattened uvicorn's positional args, and every such
# unauthenticated request then printed a "Logging error" traceback (a remote log-noise vector).
EMPTY_VALUE_QUERIES = ("code=", "password=", "token=&x=1", "secret=&code=&password=")


def test_n1_empty_value_secret_queries_do_not_break_the_access_log(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    create_user_with_totp(sandbox)
    port = free_port()
    with ao_ui_server(sandbox, port, "--auth", "--auth-dir", str(sandbox.auth_dir)) as server:
        base = f"http://{LOOPBACK}:{port}"
        for query in EMPTY_VALUE_QUERIES:
            response = httpx.get(f"{base}{PROTECTED_PATH}?{query}", timeout=HTTP_TIMEOUT_SECONDS)
            assert response.status_code == 401, query  # anonymous, and the server still answers
        assert server.poll() is None
    # assert_log_is_clean (run by ao_ui_server on exit) fails on "Logging error"/"Traceback"


def test_ui_auth_without_users_exits_78_and_refuses_to_start(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    port = free_port()
    result = subprocess.run(
        [
            *AO_COMMAND,
            "ui",
            "--auth",
            "--auth-dir",
            str(sandbox.auth_dir),
            "--workspace",
            str(sandbox.workspace),
            "--host",
            LOOPBACK,
            "--port",
            str(port),
        ],  # fmt: skip
        env=sandbox.env,
        cwd=sandbox.root,
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT_SECONDS,
    )
    assert result.returncode == EXIT_CONFIG == 78, result.stderr
    assert "ao auth add-user" in result.stderr  # the hint names the fix
    assert "Serving on" not in result.stdout  # it never reached the server start
    with pytest.raises(httpx.HTTPError):  # and nothing is listening
        httpx.get(f"http://{LOOPBACK}:{port}{HEALTH_PATH}", timeout=2.0)


def test_auth_off_is_unchanged_over_a_real_server(tmp_path: Path) -> None:
    sandbox = make_sandbox(tmp_path)
    port = free_port()
    with ao_ui_server(sandbox, port):  # no --auth, no env: the dashboard as it was before
        base = f"http://{LOOPBACK}:{port}"
        status = httpx.get(base + STATUS_PATH, timeout=HTTP_TIMEOUT_SECONDS)
        assert status.status_code == 200 and status.json() == DISABLED_STATUS_BODY
        # no credentials needed, and none of the auth-only headers appear
        runs = httpx.get(base + PROTECTED_PATH, timeout=HTTP_TIMEOUT_SECONDS)
        assert runs.status_code == 200
        for header in ("x-frame-options", "clear-site-data", "www-authenticate", "set-cookie"):
            assert header not in runs.headers, header
        assert "no-store" not in runs.headers.get("cache-control", "")
        # the auth endpoints that need a session do not exist as such: login is a 404/405, not 401
        login = httpx.post(
            base + LOGIN_PATH,
            json={"username": USERNAME, "password": PASSWORD},
            headers={"Origin": base},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        assert login.status_code != 401 and "set-cookie" not in login.headers
