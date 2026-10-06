"""T-kzEzwy: the shared auth vocabulary (constants, errors, model, seams) and test helpers."""

from __future__ import annotations

import ast
import base64
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_orchestrator.auth import constants, seams
from agent_orchestrator.auth.errors import (
    DEFAULT_DETAIL,
    STATUS_BY_CODE,
    AuthConfigError,
    AuthError,
    AuthNotReadyError,
    BusyError,
    ErrorCode,
    StoreCorruptError,
    StoreLockTimeoutError,
    StoreUnavailableError,
    TooManyAttemptsError,
    UnsafePermissionsError,
)
from agent_orchestrator.auth.model import (
    AuditEventName,
    SecondFactor,
    SessionState,
    TotpPolicy,
    TotpRequirement,
    denial_code,
)
from agent_orchestrator.errors import ConfigError, OrchestratorError
from tests.auth.helpers.core import (
    FakeClock,
    SeededEntropy,
    make_client,
    run_async,
    same_origin_headers,
)

# ---------------------------------------------------------------------------
# AC-1: EXIT_CONFIG
# ---------------------------------------------------------------------------


def test_exit_config() -> None:
    from agent_orchestrator.errors import EXIT_CONFIG

    assert EXIT_CONFIG == 78


def test_exit_config_is_additive_only() -> None:
    """The errors.py change is an append: nothing else in the file moved."""
    repo = Path(__file__).resolve().parents[2]
    errors_py = repo / "src" / "agent_orchestrator" / "errors.py"
    assert errors_py.read_text().rstrip().endswith("EXIT_CONFIG = 78")


# ---------------------------------------------------------------------------
# AC-2: constants pin (HLD section 12.6, v2.1; minus EXIT_CONFIG and frontend rows)
# ---------------------------------------------------------------------------

MIB = 1024 * 1024

PINNED_CONSTANTS: list[tuple[str, object]] = [
    ("COOKIE_BASENAME", "ao_sid"),
    ("SECURE_COOKIE_PREFIX", "__Host-"),
    ("SESSION_TOKEN_BYTES", 32),
    ("SESSION_TOKEN_B64_CHARS", 43),
    ("SESSION_PROOF_BYTES", 32),
    ("SESSION_PROOF_B64_CHARS", 43),
    ("SESSION_PROOF_HEADER", "X-AO-Session-Proof"),
    ("PARTIAL_SESSION_TTL_SECONDS", 300),
    ("MAX_SECOND_FACTOR_ATTEMPTS", 5),
    ("MAX_ENROLL_CONFIRM_ATTEMPTS", 5),
    ("MAX_SESSIONS_TOTAL", 10_000),
    ("MAX_SESSIONS_PER_USER", 32),
    ("MAX_PARTIAL_SESSIONS", 1_000),
    ("DEFAULT_SESSION_IDLE_MINUTES", 30),
    ("DEFAULT_SESSION_ABSOLUTE_HOURS", 12),
    ("MAX_IDLE_MINUTES", 1440),
    ("MAX_ABSOLUTE_HOURS", 720),
    ("DEFAULT_LOCKOUT_THRESHOLD", 5),
    ("DEFAULT_LOCKOUT_BASE_SECONDS", 30),
    ("DEFAULT_LOCKOUT_MAX_SECONDS", 900),
    ("MAX_LOCKOUT_THRESHOLD", 100),
    ("PERMISSION_RECHECK_INTERVAL_SECONDS", 5.0),
    ("MAX_LOCKOUT_BASE_SECONDS", 3600),
    ("MAX_LOCKOUT_MAX_SECONDS", 86_400),
    ("LOCKOUT_RESET_AFTER_SECONDS", 86_400),
    ("LOCKOUT_NAME_KEY_BYTES", 32),
    ("INVALID_USERNAME_BUCKET", ""),
    ("PHANTOM_LOCKOUT_MAX_ENTRIES", 4096),
    ("DEFAULT_ADDRESS_THRESHOLD", 20),
    ("MAX_ADDRESS_THRESHOLD", 10_000),
    ("ADDRESS_WINDOW_SECONDS", 900),
    ("ADDRESS_BACKOFF_BASE_SECONDS", 1),
    ("ADDRESS_BACKOFF_MAX_SECONDS", 900),
    ("ADDRESS_TABLE_MAX_ENTRIES", 4096),
    ("ADDRESS_HISTORY_SLACK", 32),
    ("IPV6_THROTTLE_PREFIX_LEN", 64),
    ("UNKNOWN_CLIENT_KEY", "unknown"),
    ("USERNAME_GATE_MAX_ENTRIES", 4096),
    ("USERNAME_PATTERN", r"^[a-z0-9][a-z0-9._-]{0,31}$"),
    ("MAX_USERNAME_CHARS", 64),
    ("DEFAULT_MIN_PASSWORD_LENGTH", 12),
    ("MIN_PASSWORD_LENGTH_FLOOR", 8),
    ("MAX_PASSWORD_LENGTH", 256),
    ("MAX_LOGIN_PASSWORD_CHARS", 1024),
    ("MAX_PASSWORD_BYTES", 4096),
    ("SCRYPT_LOG2_N", 15),
    ("SCRYPT_R", 8),
    ("SCRYPT_P", 3),
    ("SCRYPT_DKLEN", 32),
    ("SCRYPT_SALT_BYTES", 16),
    ("SCRYPT_MAXMEM_BYTES", 64 * MIB),
    ("SCRYPT_MAX_LOG2_N", 17),
    ("SCRYPT_MAX_R", 16),
    ("SCRYPT_MAX_P", 16),
    ("SCRYPT_MAX_HASH_MEMORY_BYTES", 128 * MIB),
    ("HASH_FORMAT_VERSION", 1),
    ("HASH_CONCURRENCY", 2),
    ("HASH_QUEUE_MAX", 16),
    ("TOTP_DIGITS", 6),
    ("TOTP_PERIOD_SECONDS", 30),
    ("TOTP_ALGORITHM", "SHA1"),
    ("TOTP_SECRET_BYTES", 20),
    ("TOTP_WINDOW_STEPS", 1),
    ("DEFAULT_TOTP_ISSUER_PREFIX", "ao@"),
    ("MAX_TOTP_ISSUER_CHARS", 64),
    ("RECOVERY_CODE_COUNT", 10),
    ("RECOVERY_CODE_CHARS", 16),
    ("RECOVERY_CODE_GROUP", 4),
    ("RECOVERY_CODE_BYTES", 10),
    ("RECOVERY_SALT_BYTES", 16),
    ("ENROLLMENT_TOKEN_TTL_SECONDS", 3600),
    ("CLI_TOTP_CONFIRM_ATTEMPTS", 3),
    ("MAX_AUTH_BODY_BYTES", 16_384),
    ("STORE_LOCK_TIMEOUT_SECONDS", 5.0),
    ("LOCK_POLL_SECONDS", 0.05),
    ("STORE_RETRY_AFTER_SECONDS", 5),
    ("MAX_USERS", 1000),
    ("STORE_FILE_MAX_BYTES", 16 * 1024 * 1024),
    ("USER_ID_BYTES", 16),
    ("STORE_SCHEMA_VERSION", 1),
    ("KNOWN_STORE_FEATURES", frozenset()),
    ("STORE_DIR_MODE", 0o700),
    ("STORE_FILE_MODE", 0o600),
    ("USERS_FILENAME", "users.json"),
    ("USERS_LOCK_FILENAME", "users.lock"),
    ("LOCKOUTS_FILENAME", "lockouts.json"),
    ("LOCKOUTS_LOCK_FILENAME", "lockouts.lock"),
    ("STATE_SUBDIR", "state"),
    ("AUDIT_FILENAME", "audit.jsonl"),
    ("AUDIT_LOCK_FILENAME", "audit.lock"),
    ("AUDIT_SCHEMA_VERSION", 1),
    ("AUDIT_MAX_BYTES", 10 * MIB),
    ("AUDIT_BACKUP_COUNT", 5),
    ("AUDIT_LOCK_TIMEOUT_SECONDS", 2.0),
    ("AUDIT_USERNAME_HASH_CHARS", 16),
    ("AUDIT_MAX_DETAIL_CHARS", 200),
    ("AUDIT_FAILURE_EVENTS_PER_MINUTE", 60),
    ("MAX_QUOTED_CONFIG_CHARS", 64),
    ("API_PREFIX", "/api"),
    ("AUTH_API_PREFIX", "/api/auth"),
    ("AO_UI_BOUND_PORT_ENV", "AO_UI_BOUND_PORT"),
    ("HEALTH_PATH", "/api/health"),
    ("SPA_FALLBACK_PATH", "/{full_path:path}"),
    ("SPA_ASSETS_MOUNT_NAME", "assets"),
    ("AUTH_STATUS_PATH", "/api/auth/status"),
    ("AUTH_LOGIN_PATH", "/api/auth/login"),
    ("AUTH_LOGOUT_PATH", "/api/auth/logout"),
    ("AUTH_KEEPALIVE_PATH", "/api/auth/keepalive"),
    ("AUTH_PASSWORD_PATH", "/api/auth/password"),
    ("AUTH_TOTP_VERIFY_PATH", "/api/auth/totp/verify"),
    ("AUTH_ENROLL_BEGIN_PATH", "/api/auth/totp/enroll/begin"),
    ("AUTH_ENROLL_CONFIRM_PATH", "/api/auth/totp/enroll/confirm"),
    ("AUTH_TOTP_DISABLE_PATH", "/api/auth/totp/disable"),
    ("AUTH_RECOVERY_CODES_PATH", "/api/auth/totp/recovery-codes"),
    ("HUB_LOGIN_PATH", "/login"),
    ("HUB_ASSET_ROUTE_PATH", "/auth-assets/{name}"),
    ("HUB_REALM_ID", "hub"),
    ("WORKSPACE_ID_HEX_CHARS", 12),
    ("APP_STATE_AUTH_KEY", "ao_auth"),
    ("SCOPE_PRINCIPAL_KEY", "principal"),
    ("SCOPE_SESSION_KEY", "auth_session"),
    ("SCOPE_AUTH_ENABLED_KEY", "auth_enabled"),
    ("SCOPE_PROOF_OK_KEY", "auth_proof_ok"),
    ("WWW_AUTHENTICATE_SCHEME", "AO-Session"),
    ("WS_POLICY_VIOLATION", 1008),
    ("LOCAL_PROVIDER_ID", "local-password"),
    ("CLI_REALM", "cli"),
    ("SOURCE_CLI", "cli"),
    ("SOURCE_WEB", "web"),
    ("LOOPBACK_HOSTNAMES", frozenset({"localhost", "127.0.0.1", "::1"})),
    ("FORWARDED_HEADER", "forwarded"),
    ("FORWARDING_HEADER_PREFIX", "x-forwarded-"),
    ("SERVICE_ENV_RELATIVE_PATH", ".config/ao/service.env"),
    ("EPHEMERAL_PORT", 0),
]


@pytest.mark.parametrize(("name", "value"), PINNED_CONSTANTS, ids=[n for n, _ in PINNED_CONSTANTS])
def test_constants_pin(name: str, value: object) -> None:
    actual = getattr(constants, name)
    assert actual == value
    assert type(actual) is type(value)  # 5.0 vs 5, "" vs None, frozenset vs set


def test_constants_pin_is_complete() -> None:
    """Every public constant is pinned, so adding one is a deliberate two-place edit."""
    public = {n for n in vars(constants) if n.isupper()}
    assert public == {name for name, _ in PINNED_CONSTANTS}
    assert len({name for name, _ in PINNED_CONSTANTS}) == len(PINNED_CONSTANTS)


def test_constants_derived_checks() -> None:
    b64_len = len(base64.urlsafe_b64encode(bytes(32)).rstrip(b"="))
    assert constants.SESSION_TOKEN_B64_CHARS == constants.SESSION_PROOF_B64_CHARS == 43 == b64_len
    assert constants.MAX_PASSWORD_BYTES == 4 * constants.MAX_LOGIN_PASSWORD_CHARS
    paths = [n for n in vars(constants) if n.startswith("AUTH_") and n.endswith("_PATH")]
    assert len(paths) == 10
    for name in paths:
        assert getattr(constants, name).startswith(constants.API_PREFIX + "/auth/")
    assert constants.KNOWN_STORE_FEATURES == frozenset()


# ---------------------------------------------------------------------------
# AC-3: errors
# ---------------------------------------------------------------------------

HLD_2_2_STATUS = {
    "invalid_request": 400,
    "password_policy": 400,
    "not_authenticated": 401,
    "second_factor_required": 401,
    "enrollment_required": 401,
    "invalid_credentials": 401,
    "invalid_code": 401,
    "origin_required": 403,
    "origin_mismatch": 403,
    "cross_site_request": 403,
    "insecure_transport": 403,
    "totp_disabled_by_policy": 403,
    "totp_required": 403,
    "forbidden": 403,
    "already_authenticated": 409,
    "totp_already_enrolled": 409,
    "totp_not_enrolled": 409,
    "no_pending_enrollment": 409,
    "body_too_large": 413,
    "too_many_attempts": 429,
    "busy": 503,
    "store_unavailable": 503,
}


def test_errors_codes_and_status_table() -> None:
    assert {c.value for c in ErrorCode} == set(HLD_2_2_STATUS)
    assert len(ErrorCode) == 22
    assert set(STATUS_BY_CODE) == set(ErrorCode)
    assert {c.value: s for c, s in STATUS_BY_CODE.items()} == HLD_2_2_STATUS
    assert STATUS_BY_CODE[ErrorCode.INVALID_CODE] == 401
    assert STATUS_BY_CODE[ErrorCode.INSECURE_TRANSPORT] == 403
    assert STATUS_BY_CODE[ErrorCode.BODY_TOO_LARGE] == 413
    assert STATUS_BY_CODE[ErrorCode.BUSY] == 503


def test_errors_default_detail() -> None:
    assert set(DEFAULT_DETAIL) == set(ErrorCode)
    for code, text in DEFAULT_DETAIL.items():
        assert text and "{" not in text and "}" not in text, code


def test_errors_auth_error_fields() -> None:
    err = AuthError(ErrorCode.INVALID_CODE)
    assert err.code is ErrorCode.INVALID_CODE
    assert err.detail == DEFAULT_DETAIL[ErrorCode.INVALID_CODE]
    assert err.status == 401
    assert err.extra == {} and err.headers == {} and err.cause_for_log is None
    assert str(err) == err.detail


def test_errors_cause_for_log_never_leaks() -> None:
    err = AuthError(ErrorCode.STORE_UNAVAILABLE, cause_for_log="SENTINEL")
    assert err.cause_for_log == "SENTINEL"
    assert "SENTINEL" not in str(err)
    assert "SENTINEL" not in err.detail
    assert "SENTINEL" not in repr(err.extra) + repr(err.headers)


def test_errors_explicit_detail_extra_headers_are_copied() -> None:
    extra = {"attempts_remaining": 2}
    headers = {"X-Test": "1"}
    err = AuthError(ErrorCode.INVALID_CODE, "custom", extra=extra, headers=headers)
    assert err.detail == "custom"
    extra["attempts_remaining"] = 0
    headers["X-Test"] = "2"
    assert err.extra == {"attempts_remaining": 2}
    assert err.headers == {"X-Test": "1"}


def test_errors_subclasses() -> None:
    too_many = TooManyAttemptsError(30)
    assert too_many.code is ErrorCode.TOO_MANY_ATTEMPTS and too_many.status == 429
    assert too_many.extra == {"retry_after_seconds": 30}
    assert too_many.headers == {"Retry-After": "30"}
    busy = BusyError()
    assert busy.code is ErrorCode.BUSY and busy.status == 503
    assert busy.headers == {"Retry-After": "1"}
    assert busy.extra == {"retry_after_seconds": 1}
    unavailable = StoreUnavailableError(cause_for_log="disk gone")
    assert unavailable.code is ErrorCode.STORE_UNAVAILABLE
    assert unavailable.headers == {"Retry-After": str(constants.STORE_RETRY_AFTER_SECONDS)}
    assert unavailable.cause_for_log == "disk gone"


def test_errors_hierarchy() -> None:
    assert issubclass(AuthError, OrchestratorError)
    for cls in (TooManyAttemptsError, BusyError, StoreUnavailableError):
        assert issubclass(cls, AuthError)
    assert issubclass(AuthConfigError, ConfigError)
    assert issubclass(AuthNotReadyError, AuthConfigError)
    assert issubclass(UnsafePermissionsError, AuthConfigError)
    assert issubclass(StoreCorruptError, OrchestratorError)
    assert issubclass(StoreLockTimeoutError, OrchestratorError)
    assert not issubclass(StoreCorruptError, AuthError)
    assert (
        str(AuthNotReadyError("no users; run ao auth add-user")) == "no users; run ao auth add-user"
    )


# ---------------------------------------------------------------------------
# AC-4: model
# ---------------------------------------------------------------------------

HLD_12_4_EVENTS = {
    "auth.login.success",
    "auth.login.failure",
    "auth.login.second_factor_pending",
    "auth.second_factor.failure",
    "auth.reauth.failure",
    "auth.enrollment_token.failure",
    "auth.lockout",
    "auth.failure.burst",
    "auth.logout",
    "auth.logout_all",
    "auth.recovery_code.used",
    "auth.recovery_codes.regenerated",
    "auth.password.changed",
    "auth.totp.enrolled",
    "auth.totp.disabled",
    "auth.totp.reset",
    "auth.enrollment_token.issued",
    "auth.user.added",
    "auth.user.removed",
    "auth.user.unlocked",
    "auth.sessions.revoked",
    "auth.startup.refused",
    "auth.startup.disabled_by_config",
    "auth.startup.totp_downgraded_by_config",
    "auth.store.permissions_loosened",
}


def test_model_api_state() -> None:
    assert SessionState.PARTIAL_SECOND_FACTOR.api_state == "second_factor_required"
    assert SessionState.PARTIAL_ENROLL.api_state == "enrollment_required"
    assert SessionState.FULL.api_state == "authenticated"


def test_model_denial_code() -> None:
    assert denial_code(None) is ErrorCode.NOT_AUTHENTICATED
    assert denial_code(SessionState.PARTIAL_SECOND_FACTOR) is ErrorCode.SECOND_FACTOR_REQUIRED
    assert denial_code(SessionState.PARTIAL_ENROLL) is ErrorCode.ENROLLMENT_REQUIRED
    assert SessionState.PARTIAL_SECOND_FACTOR.denial_code is ErrorCode.SECOND_FACTOR_REQUIRED
    assert SessionState.PARTIAL_ENROLL.denial_code is ErrorCode.ENROLLMENT_REQUIRED
    with pytest.raises(ValueError):
        denial_code(SessionState.FULL)
    with pytest.raises(ValueError):
        _ = SessionState.FULL.denial_code


def test_model_audit_events() -> None:
    values = [e.value for e in AuditEventName]
    assert len(values) == 25 == len(set(values))
    assert set(values) == HLD_12_4_EVENTS
    assert AuditEventName.STARTUP_DISABLED_BY_CONFIG == "auth.startup.disabled_by_config"
    assert AuditEventName.STARTUP_TOTP_DOWNGRADED_BY_CONFIG == (
        "auth.startup.totp_downgraded_by_config"
    )


def test_model_enums() -> None:
    assert {p.value for p in TotpPolicy} == {"off", "optional", "required"}
    assert {r.value for r in TotpRequirement} == {"none", "enroll_allowed", "blocked"}
    assert {f.value for f in SecondFactor} == {"totp", "recovery_code"}


# ---------------------------------------------------------------------------
# AC-5: seams
# ---------------------------------------------------------------------------


def test_seams_now_utc_is_aware_utc() -> None:
    assert seams.SYSTEM_CLOCK.now_utc().utcoffset() == timedelta(0)


def test_seams_monotonic_uses_boottime(monkeypatch: pytest.MonkeyPatch) -> None:
    if not hasattr(time, "CLOCK_BOOTTIME"):
        monkeypatch.setattr(time, "CLOCK_BOOTTIME", 7, raising=False)
    seen: list[int] = []

    def fake_gettime(clock_id: int) -> float:
        seen.append(clock_id)
        return 12345.5

    monkeypatch.setattr(time, "clock_gettime", fake_gettime)
    assert seams.SystemClock().monotonic() == 12345.5
    assert seen == [time.CLOCK_BOOTTIME]


def test_seams_monotonic_fallback_logs_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delattr(time, "CLOCK_BOOTTIME", raising=False)
    monkeypatch.setattr(time, "monotonic", lambda: 42.0)
    clock = seams.SystemClock()
    with caplog.at_level(logging.DEBUG, logger=seams.__name__):
        assert clock.monotonic() == 42.0
        assert clock.monotonic() == 42.0
    records = [r for r in caplog.records if r.name == seams.__name__]
    assert len(records) == 1
    assert records[0].levelno == logging.DEBUG


def test_seams_monotonic_real_value_is_non_decreasing() -> None:
    first = seams.SYSTEM_CLOCK.monotonic()
    assert seams.SYSTEM_CLOCK.monotonic() >= first


def test_seams_entropy() -> None:
    assert len(seams.SYSTEM_ENTROPY.token_bytes(32)) == 32
    assert seams.SYSTEM_ENTROPY.token_bytes(16) != seams.SYSTEM_ENTROPY.token_bytes(16)


def test_seams_run_sync_runs_off_thread() -> None:
    main_thread = threading.get_ident()

    def fn(a: int, k: int = 0) -> tuple[int, int, int]:
        return (a, k, threading.get_ident())

    result = run_async(seams.run_sync(fn, 1, k=2))
    assert result[:2] == (1, 2)
    assert result[2] != main_thread


def test_seams_run_sync_propagates_exceptions() -> None:
    def boom() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        run_async(seams.run_sync(boom))


# ---------------------------------------------------------------------------
# AC-6: helpers
# ---------------------------------------------------------------------------


def test_helpers_fake_clock() -> None:
    clock = FakeClock()
    wall0, mono0 = clock.now_utc(), clock.monotonic()
    assert wall0 == datetime(2026, 1, 1, tzinfo=UTC) and mono0 == 1000.0
    clock.advance(10)
    assert clock.now_utc() == wall0 + timedelta(seconds=10)
    assert clock.monotonic() == mono0 + 10
    clock.advance_wall(5)
    assert clock.now_utc() == wall0 + timedelta(seconds=15)
    assert clock.monotonic() == mono0 + 10
    clock.advance_mono(3)
    assert clock.monotonic() == mono0 + 13
    assert clock.now_utc() == wall0 + timedelta(seconds=15)
    assert clock.now_utc().utcoffset() == timedelta(0)


def test_helpers_seeded_entropy() -> None:
    a, b, c = SeededEntropy(7), SeededEntropy(7), SeededEntropy(8)
    first = a.token_bytes(16)
    assert len(first) == 16
    assert first == b.token_bytes(16)
    assert first != c.token_bytes(16)
    assert a.token_bytes(16) != first  # the stream advances


def test_helpers_run_async() -> None:
    async def coro() -> int:
        return 5

    assert run_async(coro()) == 5


def test_helpers_make_client_and_same_origin_headers() -> None:
    pytest.importorskip("starlette")
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, RedirectResponse
    from starlette.routing import Route

    async def probe(request):
        return JSONResponse({"host": request.client.host})

    async def redir(request):
        return RedirectResponse("/probe")

    app = Starlette(routes=[Route("/probe", probe), Route("/redir", redir)])

    client = make_client(app)
    assert client.get("/probe").json() == {"host": "127.0.0.1"}
    resp = client.get("/redir")
    assert resp.status_code in (302, 307) and resp.headers["location"] == "/probe"

    headers = same_origin_headers(client, proof="p")
    assert headers["Origin"] == "http://testserver"
    assert headers["Sec-Fetch-Site"] == "same-origin"
    assert headers["X-AO-Session-Proof"] == "p"
    assert "X-AO-Session-Proof" not in same_origin_headers(client)


def test_helpers_is_a_package_without_reexports() -> None:
    helpers_dir = Path(__file__).parent / "helpers"
    init = helpers_dir / "__init__.py"
    assert init.is_file()
    tree = ast.parse(init.read_text())
    # Only a docstring: no imports, no assignments, no definitions.
    assert len(tree.body) == 1
    node = tree.body[0]
    assert isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
    doc = ast.get_docstring(tree) or ""
    owners = {
        "core.py": "T-kzEzwy",
        "crypto.py": "T-s6sJmB",
        "store.py": "T-8NQP8J",
        "sessions.py": "T-kwwJ82",
        "stub_runtime.py": "T-G7qByZ",
        "enumeration.py": "T-G7qByZ",
    }
    for module, owner in owners.items():
        line = next((ln for ln in doc.splitlines() if f"``{module}``" in ln), "")
        assert owner in line, f"{module} must be listed with owner {owner}"


# ---------------------------------------------------------------------------
# AC-7: hermetic fixture
# ---------------------------------------------------------------------------


def test_hermetic_env(tmp_path_factory: pytest.TempPathFactory) -> None:
    assert not [k for k in os.environ if k.startswith("AO_UI_AUTH")]
    assert "AO_UI_BOUND_PORT" not in os.environ
    assert "AO_AUTH_STATE_DIR" not in os.environ
    auth_dir = Path(os.environ["AO_AUTH_DIR"])
    assert auth_dir.is_relative_to(tmp_path_factory.getbasetemp())


def test_hermetic_env_overrides_exported_shell_values() -> None:
    """A child pytest with the AC-7 variables exported still sees a clean environment."""
    env = {
        **os.environ,
        "AO_UI_AUTH": "1",
        "AO_UI_AUTH_TOTP": "required",
        "AO_AUTH_DIR": "/nonexistent",
        "AO_AUTH_STATE_DIR": "/nonexistent",
        "AO_UI_BOUND_PORT": "9999",
    }
    root = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            "tests/auth/test_foundation.py::test_hermetic_env",
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
