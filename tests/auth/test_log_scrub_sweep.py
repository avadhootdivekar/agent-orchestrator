"""The secret-scrub sweep (T-U2ERMo item 3; AC-24, invariant S11, HLD 20.3 #13; AC 10 surfaces).

Real flows run with *sentinel* secrets (passwords, TOTP seeds and codes, recovery codes,
enrollment tokens, session proofs, cookie tokens, stored hashes). Afterwards no sentinel may appear
in log text (message, args, ``exc_text``, the formatted traceback and every other record field),
stdout/stderr, ``audit.jsonl``, any request URL, any response header or body, nor in CLI output --
except the intended one-time locations:

``ALLOWED_SECRET_LOCATIONS`` (HTTP, field by field) and ``CLI_ALLOWED`` (the CLI printouts).

Every flow runs in BOTH modes of the ticket: (a) with ``install_log_redaction()`` and the
``auth_logger`` filters installed; (b) with the default LogRecord factory and **every** redaction
filter removed. Mode (b) proves the code never logs a raw secret in the first place (redaction is
defence in depth only), so a mode (b) hit is a real leak.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from httpx import Response
from typer.testing import CliRunner

import agent_orchestrator.auth.launch as launch_mod
from agent_orchestrator.auth import cli as auth_cli
from agent_orchestrator.auth import passwords, totp
from agent_orchestrator.auth.constants import (
    ENROLLMENT_TOKEN_TTL_SECONDS,
    USERS_FILENAME,
)
from agent_orchestrator.auth.errors import AuthConfigError
from agent_orchestrator.auth.launch import prepare_auth
from agent_orchestrator.auth.model import TotpPolicy
from agent_orchestrator.auth.scrub import (
    SecretRedactingFilter,
    auth_logger,
    install_log_redaction,
)
from agent_orchestrator.auth.settings import AuthCliOverrides
from agent_orchestrator.auth.store import (
    UserStoreFile,
    add_user,
    issue_enrollment_token,
)
from tests.auth.helpers import real_routes
from tests.auth.helpers.core import SeededEntropy
from tests.auth.helpers.crypto import TEST_PARAMS
from tests.auth.helpers.launch import auth_block, make_launch_env, write_ws_config
from tests.auth.helpers.provider import fake_hash
from tests.auth.helpers.real_routes import AUTO, Dash, DashFactory
from tests.auth.helpers.store import RFC_KEY, RFC_SECRET_B32, enroll, totp_code

dash_factory = real_routes.dash_factory  # the pytest fixture (an assignment: no F811 clash)
runner = CliRunner()

# --- sentinel kinds ---------------------------------------------------------------------------
PASSWORD_KIND = "password"
HASH_KIND = "hash"
SECRET_KIND = "totp_secret"
URI_KIND = "otpauth_uri"
CODE_KIND = "totp_code"
RECOVERY_KIND = "recovery_code"
TOKEN_KIND = "enrollment_token"
PROOF_KIND = "session_proof"
COOKIE_KIND = "cookie_token"

LOGIN = "/api/auth/login"
LOGOUT = "/api/auth/logout"
STATUS = "/api/auth/status"
KEEPALIVE = "/api/auth/keepalive"
PASSWORD_PATH = "/api/auth/password"
VERIFY = "/api/auth/totp/verify"
BEGIN = "/api/auth/totp/enroll/begin"
CONFIRM = "/api/auth/totp/enroll/confirm"
DISABLE = "/api/auth/totp/disable"
REGEN = "/api/auth/totp/recovery-codes"

#: Where a secret MAY appear in a response body, per endpoint: {endpoint: {json key: kinds}}.
#: HLD 20.3 #13: E4 {secret, otpauth_uri}; E5 and E7 {recovery_codes}; every session-issuing
#: response (E2, E3, E5, E6, E7, E8) {session_proof}.
ALLOWED_SECRET_LOCATIONS: dict[str, dict[str, frozenset[str]]] = {
    LOGIN: {"session_proof": frozenset({PROOF_KIND})},
    VERIFY: {"session_proof": frozenset({PROOF_KIND})},
    BEGIN: {"secret": frozenset({SECRET_KIND}), "otpauth_uri": frozenset({SECRET_KIND, URI_KIND})},
    CONFIRM: {
        "recovery_codes": frozenset({RECOVERY_KIND}),
        "session_proof": frozenset({PROOF_KIND}),
    },
    DISABLE: {"session_proof": frozenset({PROOF_KIND})},
    REGEN: {
        "recovery_codes": frozenset({RECOVERY_KIND}),
        "session_proof": frozenset({PROOF_KIND}),
    },
    PASSWORD_PATH: {"session_proof": frozenset({PROOF_KIND})},
}

#: CLI commands whose output intentionally carries a one-time secret (HLD 11.19).
CLI_ALLOWED: dict[str, frozenset[str]] = {
    # CODE_KIND: ``CliRunner`` echoes the typed prompt answer into stdout, as a terminal shows the
    # user's own typing; the code the user typed is not a leak.
    "enable-2fa": frozenset({SECRET_KIND, URI_KIND, RECOVERY_KIND, CODE_KIND}),
    "add-user --require-totp": frozenset({TOKEN_KIND}),
    "reset-2fa": frozenset({TOKEN_KIND}),
    "enrollment-token": frozenset({TOKEN_KIND}),
}

SIX_DIGITS = re.compile(r"^\d{6}$")
TOKEN_LINE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){3}$")
STEP_SECONDS = 30
TEST_THRESHOLD = 4  # lockout threshold of the HTTP scenario (kept low so lockout is reachable)
HUGE_ADDRESS_THRESHOLD = 10_000  # the address throttle must not interfere with the scenario
REMOTE_PEER = ("10.0.0.5", 1)
INSECURE_BASE = "http://testserver"
UNKNOWN_TOKEN = "AAAA-AAAA-AAAA-AAAA"  # well formed, never issued
TOKEN_SEED = 17


def sentinel(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


# ---------------------------------------------------------------------------
# The sentinel registry and the leak checks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Secret:
    kind: str
    value: str

    def found_in(self, text: str) -> bool:
        if self.kind == CODE_KIND:  # six digits collide with numbers: match whole digit runs only
            return re.search(rf"(?<!\d){re.escape(self.value)}(?!\d)", text) is not None
        return self.value in text


@dataclass
class Capture:
    """One HTTP response plus the request URL that produced it."""

    endpoint: str
    status: int
    url: str
    headers: list[tuple[str, str]]
    text: str
    body: Any
    issued_cookie: str | None = None


@dataclass
class Sweep:
    secrets: dict[str, Secret] = field(default_factory=dict)
    captures: list[Capture] = field(default_factory=list)
    cli_outputs: list[tuple[str, str]] = field(default_factory=list)  # (label, text)
    state_dirs: list[Path] = field(default_factory=list)
    extra_texts: list[tuple[str, str]] = field(default_factory=list)  # (label, text)

    def add(self, kind: str, value: str) -> None:
        if not value or len(value) < 6:
            return
        self.secrets.setdefault(value, Secret(kind, value))
        if kind in (RECOVERY_KIND, TOKEN_KIND):  # also the separator-free spelling
            plain = value.replace("-", "")
            self.secrets.setdefault(plain, Secret(kind, plain))

    def add_hashes_of(self, users_file: Path) -> None:
        """Register every hash string read back from a ``users.json`` (ticket sentinel list)."""
        if not users_file.exists():
            return
        try:
            users = json.loads(users_file.read_text())["users"]
        except (ValueError, KeyError):
            return
        for rec in users.values():
            if isinstance(rec, dict) and isinstance(rec.get("password_hash"), str):
                self.add(HASH_KIND, rec["password_hash"])

    # -- HTTP -------------------------------------------------------------------------------

    def call(
        self,
        dash: Dash,
        method: str,
        path: str,
        body: Any = None,
        *,
        expect: int,
        proof: Any = AUTO,
        headers: dict[str, str] | None = None,
        adopt: bool = False,
    ) -> Response:
        response = (
            dash.get(path, proof=proof)
            if method == "GET"
            else dash.post(path, body, proof=proof, headers=headers)
        )
        if method == "GET" and headers:  # extra request headers on a GET
            response = dash.client.get(path, headers={**dash.headers(proof), **headers})
        assert response.status_code == expect, (path, response.status_code, response.text)
        parsed: Any
        try:
            parsed = response.json()
        except ValueError:
            parsed = None
        issued = None
        for name, value in response.headers.multi_items():
            if name.lower() == "set-cookie":
                match = re.match(r"[^=]+=([A-Za-z0-9_-]{43})", value)
                if match:
                    issued = match.group(1)
                    self.add(COOKIE_KIND, issued)
        if isinstance(parsed, dict) and isinstance(parsed.get("session_proof"), str):
            self.add(PROOF_KIND, parsed["session_proof"])
            if adopt:
                dash.proof = parsed["session_proof"]
        elif adopt:
            raise AssertionError(f"{path} issued no session: {response.text}")
        self.captures.append(
            Capture(
                endpoint=path,
                status=response.status_code,
                url=str(response.request.url),
                headers=list(response.headers.multi_items()),
                text=response.text,
                body=parsed,
                issued_cookie=issued,
            )
        )
        return response

    # -- CLI --------------------------------------------------------------------------------

    def cli(
        self,
        auth_dir: Path,
        label: str,
        *args: str,
        stdin: str | None = None,
        exit_code: int = 0,
        extra: tuple[str, ...] = (),
    ) -> str:
        """Run ``ao auth <args>``; returns stdout. ``label`` keys ``CLI_ALLOWED``."""
        result = runner.invoke(
            auth_cli.app, [*args, "--auth-dir", str(auth_dir), *extra], input=stdin
        )
        assert result.exit_code == exit_code, (label, result.output, result.stderr)
        self.cli_outputs.append((label, result.stdout + "\n" + result.stderr))
        self.add_hashes_of(auth_dir / USERS_FILENAME)
        return str(result.stdout)

    # -- the checks -------------------------------------------------------------------------

    def leaks_in_responses(self) -> list[str]:
        found: list[str] = []
        for cap in self.captures:
            allowed = ALLOWED_SECRET_LOCATIONS.get(cap.endpoint, {}) if cap.status == 200 else {}
            for secret in self.secrets.values():
                if secret.found_in(cap.url):
                    found.append(f"{secret.kind} in the URL {cap.url}")
                for name, value in cap.headers:
                    if not secret.found_in(value):
                        continue
                    own_cookie = (
                        secret.kind == COOKIE_KIND
                        and name.lower() == "set-cookie"
                        and secret.value == cap.issued_cookie
                    )
                    if not own_cookie:
                        found.append(f"{secret.kind} in header {name} of {cap.endpoint}")
                if cap.body is None:
                    if secret.found_in(cap.text):
                        found.append(f"{secret.kind} in the raw body of {cap.endpoint}")
                    continue
                for key in _keys_containing(cap.body, secret):
                    if secret.kind not in allowed.get(key, frozenset()):
                        found.append(f"{secret.kind} in field {key!r} of {cap.endpoint}")
        return found

    def leaks_in_text(self, label: str, text: str) -> list[str]:
        return [f"{s.kind} in {label}" for s in self.secrets.values() if s.found_in(text)]

    def leaks_in_cli(self) -> list[str]:
        found: list[str] = []
        for label, text in self.cli_outputs:
            allowed = CLI_ALLOWED.get(label, frozenset())
            for secret in self.secrets.values():
                if secret.found_in(text) and secret.kind not in allowed:
                    found.append(f"{secret.kind} in the output of `{label}`")
        return found

    def leaks_in_audit(self) -> list[str]:
        found: list[str] = []
        for state_dir in self.state_dirs:
            for path in sorted(state_dir.glob("audit.jsonl*")):
                found += self.leaks_in_text(str(path), path.read_text())
        return found


def _keys_containing(node: Any, secret: Secret, key: str = "<root>") -> list[str]:
    """JSON keys (nearest dict key) whose string value, at any depth, contains the secret."""
    if isinstance(node, str):
        return [key] if secret.found_in(node) else []
    if isinstance(node, dict):
        return [k for name, child in node.items() for k in _keys_containing(child, secret, name)]
    if isinstance(node, list):
        return [k for child in node for k in _keys_containing(child, secret, key)]
    return []


def record_texts(records: list[logging.LogRecord]) -> list[str]:
    """Every textual rendering of every record: message, args, exc_text, traceback, all fields."""
    formatter = logging.Formatter()
    texts: list[str] = []
    for record in records:
        try:
            texts.append(record.getMessage())
        except Exception:  # noqa: BLE001 - an unformattable record is itself checked via msg
            pass
        texts.append(str(record.msg))
        texts.append(str(record.args))
        if record.exc_text:
            texts.append(record.exc_text)
        if record.exc_info:
            texts.append(formatter.formatException(record.exc_info))
        if record.stack_info:
            texts.append(record.stack_info)
        texts.append(repr(vars(record)))
    return texts


# ---------------------------------------------------------------------------
# The two redaction modes
# ---------------------------------------------------------------------------


def _all_loggers() -> list[logging.Logger]:
    named = [x for x in logging.root.manager.loggerDict.values() if isinstance(x, logging.Logger)]
    return [logging.getLogger(), *named]


@pytest.fixture(params=["redacted", "raw"])
def log_mode(
    request: pytest.FixtureRequest,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[str]:
    """(a) redaction installed + auth_logger filters; (b) default factory, no redaction filter."""
    mode: str = request.param
    previous_factory = logging.getLogRecordFactory()
    removed: list[tuple[logging.Logger, Any]] = []
    added: list[tuple[logging.Logger, Any]] = []
    logging.setLogRecordFactory(logging.LogRecord)  # a known-clean base, whatever ran before
    if mode == "redacted":
        install_log_redaction()
        for logger in _all_loggers():
            if logger.name.startswith("agent_orchestrator.auth"):
                before = set(logger.filters)
                auth_logger(logger.name)
                added += [(logger, f) for f in logger.filters if f not in before]
    else:
        for logger in _all_loggers():
            for flt in [f for f in logger.filters if isinstance(f, SecretRedactingFilter)]:
                logger.removeFilter(flt)
                removed.append((logger, flt))
        # prepare_auth() would reinstall the factory: keep mode (b) raw
        monkeypatch.setattr(launch_mod, "install_log_redaction", lambda: None)
    # DEBUG for the root and every logger; non-propagating loggers (uvicorn) get the handler too
    saved_levels = [(lg, lg.level) for lg in _all_loggers()]
    attached: list[logging.Logger] = []
    for logger in _all_loggers():
        logger.setLevel(logging.DEBUG)
        if not logger.propagate and caplog.handler not in logger.handlers:
            logger.addHandler(caplog.handler)
            attached.append(logger)
    caplog.set_level(logging.DEBUG)
    try:
        yield mode
    finally:
        for logger, level in saved_levels:
            logger.setLevel(level)
        for logger in attached:
            logger.removeHandler(caplog.handler)
        for logger, flt in added:
            logger.removeFilter(flt)
        for logger, flt in removed:
            logger.addFilter(flt)
        logging.setLogRecordFactory(previous_factory)


@pytest.fixture(autouse=True)
def hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    for name in [n for n in os.environ if n.startswith("AO_") and n != "AO_UI_ALLOWED_HOSTS"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(passwords, "CURRENT_PARAMS", TEST_PARAMS)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)


# ---------------------------------------------------------------------------
# Flow 1: every HTTP flow (E1-E10), with a failing variant of each
# ---------------------------------------------------------------------------


def key_of(begin_body: dict[str, Any]) -> bytes:
    return base64.b32decode(begin_body["secret"])


def code_at(dash: Dash, key: bytes = RFC_KEY, *, advance: bool = True) -> str:
    """A TOTP code for the next, never-used step (the fake clock moves one step on)."""
    if advance:
        dash.clock.advance(STEP_SECONDS)
    return totp_code(dash.clock.now_utc().timestamp(), key)


def wrong_code(dash: Dash) -> str:
    return totp_code(dash.clock.now_utc().timestamp() + 50 * STEP_SECONDS)


def seed_user(dash: Dash, name: str, password: str, *, totp_required: bool = False) -> None:
    now = dash.clock.now_utc()
    entropy = SeededEntropy(3 + sum(map(ord, name)))  # a distinct user_id per user

    def seed(f: UserStoreFile) -> None:
        add_user(
            f, name, fake_hash(password), now=now, entropy=entropy, totp_required=totp_required
        )

    dash.runtime.store.mutate(seed, create=True)


def run_http_flows(sweep: Sweep, dash_factory: DashFactory) -> None:
    pw, new_pw = sentinel("PW"), sentinel("NPW")
    other_pw, wrong_pw = sentinel("PW-bob"), sentinel("WRONG")
    carol_pw = sentinel("PW-carol")
    for kind, value in ((PASSWORD_KIND, pw), (PASSWORD_KIND, new_pw), (PASSWORD_KIND, other_pw),
                        (PASSWORD_KIND, wrong_pw), (PASSWORD_KIND, carol_pw)):  # fmt: skip
        sweep.add(kind, value)

    a = dash_factory(
        with_totp=True,
        users=(),
        lockout_threshold=TEST_THRESHOLD,
        address_threshold=HUGE_ADDRESS_THRESHOLD,
    )
    sweep.state_dirs.append(a.runtime.paths.state_dir)
    seed_user(a, "alice", pw)
    seed_user(a, "bob", other_pw)
    seed_user(a, "carol", carol_pw, totp_required=True)
    sweep.add_hashes_of(a.runtime.paths.users_file)
    sweep.add(SECRET_KIND, RFC_SECRET_B32)
    for code in enroll(a.runtime.store, "alice", now_unix=0):  # alice: enrolled with the RFC seed
        sweep.add(RECOVERY_KIND, code)

    def creds(user: str, password: str) -> dict[str, str]:
        return {"username": user, "password": password}

    def login(user: str, password: str, *, expect: int = 200, adopt: bool = True) -> Response:
        return sweep.call(a, "POST", LOGIN, creds(user, password), expect=expect, proof=None,
                          adopt=adopt and expect == 200)  # fmt: skip

    # --- alice: failing and passing login, second factor -----------------------------------
    login("alice", wrong_pw, expect=401)
    login("nobody", wrong_pw, expect=401)  # unknown user: the uniform failure
    assert login("alice", pw).json()["state"] == "second_factor_required"
    sweep.call(a, "POST", VERIFY, {"code": wrong_code(a)}, expect=401)
    code = code_at(a)
    sweep.add(CODE_KIND, code)
    sweep.call(a, "POST", VERIFY, {"code": code}, expect=200, adopt=True)
    # authenticated: status with / without / with a wrong proof, keepalive, protected routes
    assert sweep.call(a, "GET", STATUS, expect=200).json()["state"] == "authenticated"
    assert sweep.call(a, "GET", STATUS, proof=None, expect=200).json()["state"] == "anonymous"
    sweep.call(a, "POST", KEEPALIVE, expect=200)
    sweep.call(a, "POST", KEEPALIVE, proof=None, expect=401)
    sweep.call(a, "GET", "/api/workspace", proof="A" * 43, expect=401)
    # password change: wrong current, then right
    sweep.call(a, "POST", PASSWORD_PATH,
               {"current_password": wrong_pw, "new_password": new_pw}, expect=401)  # fmt: skip
    change = {"current_password": pw, "new_password": new_pw}
    sweep.call(a, "POST", PASSWORD_PATH, change, expect=200, adopt=True)
    sweep.call(a, "POST", PASSWORD_PATH,  # policy violation: the offending password is not echoed
               {"current_password": new_pw, "new_password": "short"}, expect=400)  # fmt: skip
    # regenerate recovery codes: wrong code first, then right
    sweep.call(a, "POST", REGEN, {"current_password": new_pw, "code": wrong_code(a)}, expect=401)
    code = code_at(a)
    sweep.add(CODE_KIND, code)
    regen = sweep.call(a, "POST", REGEN, {"current_password": new_pw, "code": code}, expect=200,
                       adopt=True)  # fmt: skip
    fresh_codes = regen.json()["recovery_codes"]
    for value in fresh_codes:
        sweep.add(RECOVERY_KIND, value)
    sweep.call(a, "POST", LOGOUT, {}, expect=200)

    # --- alice: replayed TOTP code, recovery code login and its replay -----------------------
    assert login("alice", new_pw).json()["state"] == "second_factor_required"
    sweep.call(a, "POST", VERIFY, {"code": code}, expect=401)  # the same step: replayed
    sweep.call(a, "POST", VERIFY, {"recovery_code": fresh_codes[0]}, expect=200, adopt=True)
    sweep.call(a, "POST", LOGOUT, {}, expect=200)
    login("alice", new_pw)
    sweep.call(a, "POST", VERIFY, {"recovery_code": fresh_codes[0]}, expect=401)  # replayed
    sweep.call(a, "POST", VERIFY, {"recovery_code": "not-a-code"}, expect=401)
    sweep.call(a, "POST", VERIFY, {"recovery_code": fresh_codes[1].lower()}, expect=200, adopt=True)

    # --- alice: disable (wrong password, then right), then voluntary enrollment ------------
    code = code_at(a)
    sweep.add(CODE_KIND, code)
    sweep.call(a, "POST", DISABLE, {"current_password": wrong_pw, "code": code}, expect=401)
    sweep.call(a, "POST", DISABLE, {"current_password": new_pw, "code": code}, expect=200,
               adopt=True)  # fmt: skip
    sweep.call(a, "POST", BEGIN, {"current_password": wrong_pw}, expect=401)
    begin = sweep.call(a, "POST", BEGIN, {"current_password": new_pw}, expect=200)
    sweep.add(SECRET_KIND, begin.json()["secret"])
    sweep.add(URI_KIND, begin.json()["otpauth_uri"])
    new_key = key_of(begin.json())
    sweep.call(a, "POST", CONFIRM, {"code": wrong_code(a)}, expect=401)
    code = code_at(a, new_key)
    sweep.add(CODE_KIND, code)
    confirm = sweep.call(a, "POST", CONFIRM, {"code": code}, expect=200, adopt=True)
    for value in confirm.json()["recovery_codes"]:
        sweep.add(RECOVERY_KIND, value)
    sweep.call(a, "POST", LOGOUT, {"everywhere": True}, expect=200)
    sweep.call(a, "POST", LOGOUT, {}, proof=None, expect=200)  # idempotent, no session

    # --- bob: password-only session, then lockout and unknown-user bucket -------------------
    login("bob", other_pw)
    sweep.call(a, "POST", LOGOUT, {}, expect=200)
    for _ in range(TEST_THRESHOLD):
        login("bob", wrong_pw, expect=401)
    login("bob", other_pw, expect=429)  # locked out: even the right password is refused
    login("Not A Valid Name!", wrong_pw, expect=401)  # the invalid-username bucket

    # --- carol: forced enrollment with a CLI-issued token -----------------------------------
    now = a.clock.now_utc()
    token = a.runtime.store.mutate(
        lambda f: issue_enrollment_token(f, "carol", entropy=SeededEntropy(TOKEN_SEED), now=now)
    )
    sweep.add(TOKEN_KIND, token)
    assert login("carol", carol_pw).json()["state"] == "enrollment_required"
    sweep.call(a, "POST", BEGIN, {"enrollment_token": UNKNOWN_TOKEN}, expect=401)
    sweep.call(a, "POST", BEGIN, {}, expect=401)  # a missing token counts like a wrong one
    forced = sweep.call(a, "POST", BEGIN, {"enrollment_token": token}, expect=200)
    sweep.add(SECRET_KIND, forced.json()["secret"])
    sweep.add(URI_KIND, forced.json()["otpauth_uri"])
    sweep.call(a, "POST", BEGIN, {"enrollment_token": token}, expect=401)  # the token is used up
    sweep.call(a, "POST", CONFIRM, {"code": wrong_code(a)}, expect=401)
    code = code_at(a, key_of(forced.json()))
    sweep.add(CODE_KIND, code)
    done = sweep.call(a, "POST", CONFIRM, {"code": code}, expect=200, adopt=True)
    for value in done.json()["recovery_codes"]:
        sweep.add(RECOVERY_KIND, value)
    sweep.call(a, "POST", LOGOUT, {}, expect=200)

    # an expired token is refused too (dave is a second forced-enrollment account)
    dave_pw = sentinel("PW-dave")
    sweep.add(PASSWORD_KIND, dave_pw)
    seed_user(a, "dave", dave_pw, totp_required=True)
    sweep.add_hashes_of(a.runtime.paths.users_file)
    token = a.runtime.store.mutate(
        lambda f: issue_enrollment_token(f, "dave", entropy=SeededEntropy(TOKEN_SEED + 1), now=now)
    )
    sweep.add(TOKEN_KIND, token)
    a.clock.advance(ENROLLMENT_TOKEN_TTL_SECONDS + 1)
    assert login("dave", dave_pw).json()["state"] == "enrollment_required"
    sweep.call(a, "POST", BEGIN, {"enrollment_token": token}, expect=401)
    sweep.call(a, "POST", LOGOUT, {}, expect=200)

    # --- request-shape failures: none may echo the secret it carried -----------------------
    sweep.call(a, "POST", LOGIN, creds("alice", new_pw), proof=None, expect=403,
                      headers={"Origin": "http://evil.example"})  # fmt: skip
    unknown_key = sweep.call(a, "POST", LOGIN, {**creds("alice", new_pw), "unexpected_key": new_pw},
                             proof=None, expect=400)  # fmt: skip
    assert unknown_key.json()["code"] == "invalid_request"
    sweep.call(a, "POST", VERIFY, {"code": "123456", "recovery_code": pw}, proof=None, expect=401)

    # --- v2.1: the proxy_suspected status response ------------------------------------------
    proxied = sweep.call(a, "GET", STATUS, expect=200, headers={"X-Forwarded-For": "203.0.113.9"})
    assert proxied.json()["transport"]["proxy_suspected"] is True

    # --- insecure_transport: a plain-http non-loopback client may log in but not enroll ------
    remote = dash_factory(with_totp=True, users=(), peer=REMOTE_PEER, base_url=INSECURE_BASE)
    sweep.state_dirs.append(remote.runtime.paths.state_dir)
    sweep.call(remote, "POST", LOGIN, creds("bob", other_pw), expect=200, proof=None, adopt=True)
    refused = sweep.call(remote, "POST", BEGIN, {"current_password": other_pw}, expect=403)
    assert refused.json()["code"] == "insecure_transport"

    # --- an unreadable (corrupt) store: 503, and the corrupt content is not echoed -----------
    corrupt_seed = sentinel("CORRUPT-SEED").replace("-", "").upper()[:32]
    sweep.add(SECRET_KIND, corrupt_seed)
    users_file = a.runtime.paths.users_file
    original = users_file.read_bytes()
    users_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "users": {
                    "alice": {"password_hash": corrupt_seed, "totp": {"secret_b32": corrupt_seed}}
                },
            }
        )
    )
    try:
        sweep.call(a, "POST", LOGIN, creds("alice", new_pw), expect=503, proof=None)
    finally:
        users_file.write_bytes(original)


# ---------------------------------------------------------------------------
# Flow 2: every CLI command, plus corrupt-store and startup surfaces
# ---------------------------------------------------------------------------


def run_cli_flows(sweep: Sweep, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(totp, "new_totp_secret", lambda entropy=None: RFC_KEY)
    sweep.add(SECRET_KIND, RFC_SECRET_B32)
    auth_dir = (tmp_path / "cli-auth").resolve()
    sweep.state_dirs.append(auth_dir / "state")
    cli_pw, cli_new = sentinel("CLI-PW"), sentinel("CLI-NPW")
    sweep.add(PASSWORD_KIND, cli_pw)
    sweep.add(PASSWORD_KIND, cli_new)

    def tokens(text: str) -> list[str]:
        return [line.strip() for line in text.splitlines() if TOKEN_LINE.fullmatch(line.strip())]

    sweep.cli(auth_dir, "add-user", "add-user", "carol", "--password-stdin", stdin=cli_pw + "\n")
    out = sweep.cli(auth_dir, "add-user --require-totp", "add-user", "dave", "--password-stdin",
                    "--require-totp", stdin=cli_pw + "\n")  # fmt: skip
    for value in tokens(out):
        sweep.add(TOKEN_KIND, value)
    out = sweep.cli(auth_dir, "enrollment-token", "enrollment-token", "dave")
    for value in tokens(out):
        sweep.add(TOKEN_KIND, value)
    out = sweep.cli(auth_dir, "reset-2fa", "reset-2fa", "dave", "--yes")
    for value in tokens(out):
        sweep.add(TOKEN_KIND, value)
    sweep.cli(auth_dir, "set-password", "set-password", "carol", "--password-stdin",
              stdin=cli_new + "\n")  # fmt: skip
    code = totp.hotp(RFC_KEY, totp.totp_step(_now()))
    sweep.add(CODE_KIND, code)
    out = sweep.cli(auth_dir, "enable-2fa", "enable-2fa", "carol", stdin=code + "\n")
    for value in tokens(out):
        sweep.add(RECOVERY_KIND, value)
    sweep.cli(auth_dir, "list-users", "list-users")
    sweep.cli(auth_dir, "status", "status")
    sweep.cli(auth_dir, "status --json", "status", "--json")
    sweep.cli(auth_dir, "disable-2fa", "disable-2fa", "carol", "--yes")
    sweep.cli(auth_dir, "unlock", "unlock", "carol")
    sweep.cli(auth_dir, "revoke-sessions", "revoke-sessions", "carol")
    # the failing variants: a wrong code gives up after three attempts, an unknown user exits 1
    wrong = "000000"
    sweep.cli(auth_dir, "enable-2fa", "enable-2fa", "carol", stdin=f"{wrong}\n{wrong}\n{wrong}\n",
              exit_code=1)  # fmt: skip
    sweep.cli(auth_dir, "set-password", "set-password", "nobody", "--password-stdin",
              stdin=cli_new + "\n", exit_code=1)  # fmt: skip

    # status flags for the v2.1 config surfaces (disabled_by_config / totp_downgraded_by_config)
    risky_configs = ("    enabled: false\n", "    enabled: true\n    totp: optional\n")
    for index, risk_config in enumerate(risky_configs):
        ws = tmp_path / f"ws-{index}"
        (ws / ".git").mkdir(parents=True)
        (ws / ".ao").mkdir()
        (ws / ".ao" / "config.yaml").write_text("ui:\n  auth:\n" + risk_config)
        text = sweep.cli(auth_dir, "status", "status", "--workspace", str(ws))
        assert "by_config" in text
        sweep.cli(auth_dir, "status --json", "status", "--json", "--workspace", str(ws))

    # a corrupt store with a sentinel seed in a field the schema rejects: the load error must
    # carry field paths only (the CLI logs its traceback at DEBUG, chained exceptions included)
    corrupt_dir = (tmp_path / "cli-corrupt").resolve()
    sweep.state_dirs.append(corrupt_dir / "state")
    sweep.cli(corrupt_dir, "add-user", "add-user", "erin", "--password-stdin", stdin=cli_pw + "\n")
    corrupt_seed = sentinel("CLI-CORRUPT").replace("-", "").upper()[:32]
    sweep.add(SECRET_KIND, corrupt_seed)
    (corrupt_dir / USERS_FILENAME).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "users": {
                    "erin": {"password_hash": corrupt_seed, "totp": {"secret_b32": corrupt_seed}}
                },
            }
        )
    )
    sweep.cli(corrupt_dir, "list-users", "list-users", exit_code=1)
    sweep.cli(corrupt_dir, "set-password", "set-password", "erin", "--password-stdin",
              stdin=cli_new + "\n", exit_code=1)  # fmt: skip

    sweep.cli(auth_dir, "remove-user", "remove-user", "dave", "--yes")
    sweep.cli(auth_dir, "remove-user", "remove-user", "carol", "--yes", "--force")


def _now() -> float:
    import time

    return time.time()


def run_startup_flows(sweep: Sweep, tmp_path: Path, caplog_capture: list[str]) -> None:
    """``prepare_auth`` surfaces: the two ``auth.startup.*_by_config`` audit events (v2.1)."""
    for case, config, refused in (
        ("disabled", ("enabled: false",), True),
        ("downgraded", ("enabled: true", "totp: optional"), False),
    ):
        lenv = make_launch_env(tmp_path / f"launch-{case}", users=("alice",))
        sweep.state_dirs.append(lenv.state_dir)
        sweep.add_hashes_of(lenv.store_dir / USERS_FILENAME)
        write_ws_config(lenv.workspace, auth_block(*config))
        secret_env = sentinel("ENV-PW")
        sweep.add(PASSWORD_KIND, secret_env)
        env = {**lenv.env, "AO_AUTH_PASSWORD": secret_env}  # never read; must never be echoed
        try:
            launch = prepare_auth(
                cli=AuthCliOverrides(),
                env=env,
                workspace_root=lenv.workspace,
                realm_kind="ui",
                port=8765,
                bind_host="127.0.0.1",
            )
        except AuthConfigError as exc:
            assert refused
            caplog_capture.append(str(exc))
        else:
            assert not refused
            caplog_capture.extend(launch.warnings)
            assert launch.settings.totp is TotpPolicy.OPTIONAL
        events = [
            json.loads(line)["event"]
            for line in (lenv.state_dir / "audit.jsonl").read_text().splitlines()
        ]
        assert events == [
            "auth.startup.disabled_by_config"
            if refused
            else "auth.startup.totp_downgraded_by_config"
        ]


# ---------------------------------------------------------------------------
# The sweep itself
# ---------------------------------------------------------------------------


def test_the_scrub_sweep_finds_no_secret_anywhere(
    log_mode: str,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    dash_factory: DashFactory,
) -> None:
    sweep = Sweep()
    startup_texts: list[str] = []
    run_http_flows(sweep, dash_factory)
    run_cli_flows(sweep, tmp_path / "cli", monkeypatch)
    run_startup_flows(sweep, tmp_path / "startup", startup_texts)
    assert len(sweep.captures) > 40 and len(sweep.secrets) > 60  # the sweep really ran
    assert {c.status for c in sweep.captures} >= {200, 400, 401, 403, 429, 503}

    leaks: list[str] = []
    leaks += sweep.leaks_in_responses()
    leaks += sweep.leaks_in_cli()
    leaks += sweep.leaks_in_audit()
    for text in startup_texts:
        leaks += sweep.leaks_in_text("a startup message", text)
    for text in record_texts(caplog.records):
        leaks += sweep.leaks_in_text(f"log text ({log_mode})", text)
    captured = capsys.readouterr()
    leaks += sweep.leaks_in_text("stdout", captured.out)
    leaks += sweep.leaks_in_text("stderr", captured.err)
    assert len(caplog.records) > 20, "no log records were captured: the capture is broken"
    assert sorted(set(leaks)) == [], sorted(set(leaks))


# ---------------------------------------------------------------------------
# Negative controls: the detector really fires, and each mode really differs
# ---------------------------------------------------------------------------


def test_the_log_check_detects_a_planted_leak_when_redaction_is_off(
    log_mode: str, caplog: pytest.LogCaptureFixture
) -> None:
    sweep = Sweep()
    planted = "ABCD-EFGH-JKMN-PQRS"
    sweep.add(RECOVERY_KIND, planted)
    cookie = "x" * 43
    sweep.add(COOKIE_KIND, cookie)
    logging.getLogger("tests.sweep.control").warning(
        "recovery %s cookie ao_sid_8765=%s", planted, cookie
    )
    found = [
        hit for text in record_texts(caplog.records) for hit in sweep.leaks_in_text("log", text)
    ]
    if log_mode == "raw":
        assert found, "mode (b) must expose a planted secret: the sweep's detector is broken"
    else:
        assert found == [], "mode (a) must redact the planted secret"


def test_the_response_check_detects_a_secret_outside_its_allowed_field() -> None:
    sweep = Sweep()
    sweep.add(RECOVERY_KIND, "ABCD-EFGH-JKMN-PQRS")
    sweep.add(COOKIE_KIND, "c" * 43)
    ok = Capture(CONFIRM, 200, "http://x/api", [], "", {"recovery_codes": ["ABCD-EFGH-JKMN-PQRS"]})
    wrong_field = Capture(CONFIRM, 200, "http://x/api", [], "", {"detail": "ABCD-EFGH-JKMN-PQRS"})
    wrong_endpoint = Capture(
        LOGIN, 200, "http://x/api", [], "", {"recovery_codes": ["ABCD-EFGH-JKMN-PQRS"]}
    )
    in_url = Capture(STATUS, 200, "http://x/api?c=" + "c" * 43, [], "", {})
    in_header = Capture(STATUS, 200, "http://x/api", [("x-leak", "c" * 43)], "", {})
    own_cookie = Capture(
        LOGIN, 200, "http://x/api", [("set-cookie", "ao_sid_1=" + "c" * 43)], "", {}, "c" * 43
    )
    for cap, leaky in (
        (ok, False), (wrong_field, True), (wrong_endpoint, True), (in_url, True),
        (in_header, True), (own_cookie, False),
    ):  # fmt: skip
        sweep.captures = [cap]
        assert bool(sweep.leaks_in_responses()) is leaky, cap
