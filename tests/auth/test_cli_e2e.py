"""``ao auth`` end to end through ``CliRunner`` (T-j9dfsw; HLD 11.19; AC-1, AC-26, AC-36, AC-45/46).

Every test uses a tmp ``--auth-dir`` (or a tmp XDG/config layout), a hermetic environment and
cheap scrypt parameters (the real hasher at ``TEST_PARAMS``). TOTP enrollment pins the generated
secret to the RFC seed so the code can be computed before the (single) invocation.
"""

from __future__ import annotations

import json
import os
import re
import stat
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from agent_orchestrator import cli as root_cli
from agent_orchestrator import fsutil
from agent_orchestrator.auth import cli as auth_cli
from agent_orchestrator.auth import passwords, totp
from agent_orchestrator.auth.constants import (
    ENROLLMENT_TOKEN_TTL_SECONDS,
    LOCKOUTS_FILENAME,
    USERS_FILENAME,
)
from agent_orchestrator.auth.errors import (
    StoreCorruptError,
    StoreLockTimeoutError,
    StoreUnavailableError,
)
from agent_orchestrator.auth.lockouts import LockoutKey, LockoutPolicy, LockoutStore
from agent_orchestrator.auth.model import TotpPolicy
from agent_orchestrator.auth.paths import StorePaths
from agent_orchestrator.auth.recovery import normalize_recovery_code
from agent_orchestrator.auth.store import (
    StoreMissingError,
    consume_enrollment_token,
    load_store_file,
    parse_timestamp,
)
from agent_orchestrator.errors import EXIT_CONFIG
from tests.auth.helpers.crypto import TEST_PARAMS
from tests.auth.helpers.store import RFC_KEY

runner = CliRunner()

PW = "correct horse battery"
NEW_PW = "another long passphrase"
TOKEN_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{4}(-[0-9A-HJKMNP-TV-Z]{4}){3}$")
HEX32_RE = re.compile(r"^[0-9a-f]{32}$")
BANNER = "=== STORE THESE NOW"
AUTH_ENV_VARS = (
    "AO_UI_AUTH",
    "AO_UI_AUTH_TOTP",
    "AO_UI_AUTH_IDLE_MINUTES",
    "AO_UI_AUTH_ABSOLUTE_HOURS",
    "AO_UI_AUTH_LOCKOUT_THRESHOLD",
    "AO_UI_AUTH_LOCKOUT_BASE_SECONDS",
    "AO_UI_AUTH_LOCKOUT_MAX_SECONDS",
    "AO_UI_AUTH_ADDRESS_THRESHOLD",
    "AO_UI_AUTH_MIN_PASSWORD_LENGTH",
    "AO_UI_AUTH_TRUSTED_PROXIES",
    "AO_UI_AUTH_TOTP_ISSUER",
    "AO_AUTH_DIR",
    "AO_AUTH_STATE_DIR",
    "AO_WORKSPACE_ROOT",
    "AO_AUTH_PASSWORD",
)


@pytest.fixture(autouse=True)
def hermetic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No ambient auth env, XDG and HOME inside tmp, cheap scrypt, cwd inside tmp."""
    for name in AUTH_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setattr(passwords, "CURRENT_PARAMS", TEST_PARAMS)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)


@pytest.fixture()
def auth_dir(tmp_path: Path) -> Path:
    """The (not yet existing) store directory the CLI creates."""
    return (tmp_path / "auth").resolve()


@pytest.fixture()
def fixed_secret(monkeypatch: pytest.MonkeyPatch) -> bytes:
    monkeypatch.setattr(totp, "new_totp_secret", lambda entropy=None: RFC_KEY)
    return RFC_KEY


def invoke(
    *args: str, input: str | None = None, env: dict[str, str] | None = None, app: Any = None
) -> Any:
    return runner.invoke(app or auth_cli.app, list(args), input=input, env=env)


def add(auth_dir: Path, name: str = "alice", *extra: str, pw: str = PW) -> Any:
    """``add-user`` through ``--password-stdin``; asserts success."""
    result = invoke("add-user", name, "--password-stdin", "--auth-dir", str(auth_dir), *extra,
                    input=pw + "\n")  # fmt: skip
    assert result.exit_code == 0, result.output
    return result


def records(auth_dir: Path) -> dict[str, dict[str, Any]]:
    data = json.loads((auth_dir / USERS_FILENAME).read_text())
    users: dict[str, dict[str, Any]] = data["users"]
    return users


def tokens_in(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if TOKEN_RE.fullmatch(line.strip())]


def lockout_store(auth_dir: Path) -> LockoutStore:
    return LockoutStore(StorePaths.at(auth_dir, auth_dir / "state"))


def lock_out(auth_dir: Path, user_id: str) -> None:
    policy = LockoutPolicy(threshold=2, base_seconds=600, max_seconds=600)
    for _ in range(2):
        lockout_store(auth_dir).record_failure(LockoutKey(user_id=user_id), policy)


def audit_events(auth_dir: Path) -> list[dict[str, Any]]:
    path = auth_dir / "state" / "audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()]


def current_code() -> str:
    return totp.hotp(RFC_KEY, totp.totp_step(time.time()))


def token_verifies(auth_dir: Path, username: str, token: str) -> bool:
    """Whether ``token`` verifies against the stored record (on a throwaway parsed copy)."""
    model = load_store_file(auth_dir / USERS_FILENAME)
    return consume_enrollment_token(
        model,
        username,
        normalize_recovery_code(token),
        user_id=model.users[username].user_id,
        now=datetime.now(UTC),
    )


def write_workspace(tmp_path: Path, auth_yaml: str) -> Path:
    ws = tmp_path / "ws"
    (ws / ".git").mkdir(parents=True)
    (ws / ".ao").mkdir()
    (ws / ".ao" / "config.yaml").write_text("ui:\n  auth:\n" + auth_yaml)
    return ws.resolve()


# ---------------------------------------------------------------------------
# AC 1 + AC 7: the lifecycle, in order, then the audit trail
# ---------------------------------------------------------------------------


def test_lifecycle_and_audit_trail(
    auth_dir: Path, fixed_secret: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 1. add-user alice (hidden prompt with confirmation)
    result = invoke("add-user", "alice", "--auth-dir", str(auth_dir), input=f"{PW}\n{PW}\n")
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines()[0] == f"store: {auth_dir}"
    assert stat.S_IMODE(auth_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((auth_dir / USERS_FILENAME).stat().st_mode) == 0o600
    assert stat.S_IMODE((auth_dir / "state").stat().st_mode) == 0o700
    alice = records(auth_dir)["alice"]
    assert HEX32_RE.fullmatch(alice["user_id"])
    assert passwords.verify_password(PW, alice["password_hash"])
    # 5d (security L1): the name key exists after any mutating command
    name_key = json.loads((auth_dir / "state" / LOCKOUTS_FILENAME).read_text())["name_key_hex"]
    assert re.fullmatch(r"[0-9a-f]{64}", name_key)

    # 2. again -> exit 1, already exists
    result = invoke("add-user", "alice", "--auth-dir", str(auth_dir), input=f"{PW}\n{PW}\n")
    assert result.exit_code == 1
    assert "already exists" in result.stderr

    # 3. bob with a forced TOTP requirement and an enrollment token
    result = invoke(
        "add-user", "bob", "--password-stdin", "--require-totp", "--auth-dir", str(auth_dir),
        input=PW + "\n",
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    bob = records(auth_dir)["bob"]
    assert bob["totp_required"] is True
    (t1,) = tokens_in(result.stdout)
    assert BANNER in result.stdout
    stored = bob["enrollment_token"]
    assert set(stored) >= {"salt_hex", "hash_hex", "expires_at"}
    expires = parse_timestamp(stored["expires_at"])
    assert expires is not None
    expected = datetime.now(UTC) + timedelta(seconds=ENROLLMENT_TOKEN_TTL_SECONDS)
    assert abs((expires - expected).total_seconds()) <= 5
    assert t1 not in (auth_dir / USERS_FILENAME).read_text()
    assert token_verifies(auth_dir, "bob", t1)

    # 4. list-users
    result = invoke("list-users", "--auth-dir", str(auth_dir))
    assert result.exit_code == 0
    header = result.stdout.splitlines()[2]
    assert header.split() == [
        "USERNAME", "TOTP", "TOTP_REQUIRED", "RECOVERY_LEFT", "LOCKED", "LAST_LOGIN",
    ]  # fmt: skip
    assert "alice" in result.stdout and "bob" in result.stdout
    assert "$scrypt$" not in result.stdout
    assert not re.search(r"[A-Z2-7]{32}", result.stdout)
    assert t1 not in result.stdout

    # 5. set-password alice -> epoch + 1
    epoch = records(auth_dir)["alice"]["credential_epoch"]
    result = invoke(
        "set-password", "alice", "--auth-dir", str(auth_dir), input=f"{NEW_PW}\n{NEW_PW}\n"
    )
    assert result.exit_code == 0, result.output
    assert "revoked on their next request" in result.stdout
    assert records(auth_dir)["alice"]["credential_epoch"] == epoch + 1
    assert passwords.verify_password(NEW_PW, records(auth_dir)["alice"]["password_hash"])

    # 6. enable-2fa alice (code computed right before the invocation)
    epoch = records(auth_dir)["alice"]["credential_epoch"]
    result = invoke("enable-2fa", "alice", "--auth-dir", str(auth_dir), input=current_code() + "\n")
    assert result.exit_code == 0, result.output
    assert totp.b32encode_secret(RFC_KEY) in result.stdout.replace(" ", "")
    assert "otpauth://totp/" in result.stdout
    recovery = tokens_in(result.stdout)
    assert len(recovery) == 10 and len(set(recovery)) == 10
    alice = records(auth_dir)["alice"]
    assert alice["credential_epoch"] == epoch + 1
    assert alice["totp"] is not None and len(alice["recovery_codes"]) == 10
    stored_text = (auth_dir / USERS_FILENAME).read_text()
    assert not any(code in stored_text for code in recovery)
    again = invoke("enable-2fa", "alice", "--auth-dir", str(auth_dir))
    assert again.exit_code == 1 and "already enrolled" in again.stderr
    already = invoke("enrollment-token", "alice", "--auth-dir", str(auth_dir))
    assert already.exit_code == 1 and "already enrolled" in already.stderr

    # 7. disable-2fa alice --yes
    result = invoke("disable-2fa", "alice", "--yes", "--auth-dir", str(auth_dir))
    assert result.exit_code == 0, result.output
    alice = records(auth_dir)["alice"]
    assert alice["totp"] is None and alice["totp_required"] is False

    # 8. reset-2fa bob --yes (not enrolled): required again, a NEW token
    old_hash = records(auth_dir)["bob"]["enrollment_token"]["hash_hex"]
    result = invoke("reset-2fa", "bob", "--yes", "--auth-dir", str(auth_dir))
    assert result.exit_code == 0, result.output
    (t2,) = tokens_in(result.stdout)
    bob = records(auth_dir)["bob"]
    assert bob["totp_required"] is True
    assert bob["enrollment_token"]["hash_hex"] != old_hash
    assert not token_verifies(auth_dir, "bob", t1)
    assert token_verifies(auth_dir, "bob", t2)
    assert "set-password bob" in result.stdout

    # 9. enrollment-token bob replaces T2
    result = invoke("enrollment-token", "bob", "--auth-dir", str(auth_dir))
    assert result.exit_code == 0, result.output
    (t3,) = tokens_in(result.stdout)
    assert not token_verifies(auth_dir, "bob", t2)
    assert token_verifies(auth_dir, "bob", t3)

    # 10. unlock alice
    alice_id = records(auth_dir)["alice"]["user_id"]
    lock_out(auth_dir, alice_id)
    assert lockout_store(auth_dir).state(LockoutKey(user_id=alice_id)).locked_until is not None
    listed = invoke("list-users", "--auth-dir", str(auth_dir)).stdout
    assert re.search(r"alice\s+no\s+no\s+-\s+\d{4}-\d\d-\d\dT", listed)
    assert invoke("unlock", "alice", "--auth-dir", str(auth_dir)).exit_code == 0
    state = lockout_store(auth_dir).state(LockoutKey(user_id=alice_id))
    assert state.locked_until is None and state.failures == 0

    # 11. revoke-sessions alice
    epoch = records(auth_dir)["alice"]["credential_epoch"]
    assert invoke("revoke-sessions", "alice", "--auth-dir", str(auth_dir)).exit_code == 0
    assert records(auth_dir)["alice"]["credential_epoch"] == epoch + 1

    # 12. remove-user bob --yes also forgets his lockout entry
    bob_id = records(auth_dir)["bob"]["user_id"]
    lock_out(auth_dir, bob_id)
    result = invoke("remove-user", "bob", "--yes", "--auth-dir", str(auth_dir))
    assert result.exit_code == 0, result.output
    assert bob_id not in (auth_dir / "state" / LOCKOUTS_FILENAME).read_text()
    assert "bob" not in records(auth_dir)

    # 13. last-user guard with auth enabled
    monkeypatch.setenv("AO_UI_AUTH", "1")
    result = invoke("remove-user", "alice", "--yes", "--auth-dir", str(auth_dir))
    assert result.exit_code == 1
    assert "78" in result.stderr and "alice" in records(auth_dir)
    result = invoke("remove-user", "alice", "--yes", "--force", "--auth-dir", str(auth_dir))
    assert result.exit_code == 0, result.output
    assert records(auth_dir) == {}

    # AC 7: the exact audit sequence, every line a CLI event on a known account
    events = audit_events(auth_dir)
    assert [(e["event"], e["username"]) for e in events] == [
        ("auth.user.added", "alice"),
        ("auth.user.added", "bob"),
        ("auth.enrollment_token.issued", "bob"),
        ("auth.password.changed", "alice"),
        ("auth.totp.enrolled", "alice"),
        ("auth.totp.disabled", "alice"),
        ("auth.totp.reset", "bob"),
        ("auth.enrollment_token.issued", "bob"),
        ("auth.enrollment_token.issued", "bob"),
        ("auth.user.unlocked", "alice"),
        ("auth.sessions.revoked", "alice"),
        ("auth.user.removed", "bob"),
        ("auth.user.removed", "alice"),
    ]
    for event in events:
        assert event["realm"] == "cli" and event["details"]["source"] == "cli"
        assert HEX32_RE.fullmatch(event["user_id"])
    raw_audit = (auth_dir / "state" / "audit.jsonl").read_text()
    secrets = [PW, NEW_PW, totp.b32encode_secret(RFC_KEY), t1, t2, t3, *recovery]
    assert not any(secret in raw_audit for secret in secrets)


# ---------------------------------------------------------------------------
# AC 2 / 3: password input and secret-free surface
# ---------------------------------------------------------------------------


def test_short_password_is_rejected_with_violations(auth_dir: Path) -> None:
    result = invoke("add-user", "alice", "--auth-dir", str(auth_dir), input="short\nshort\n")
    assert result.exit_code == 1
    assert "too_short" in result.stderr
    assert not (auth_dir / USERS_FILENAME).exists()


def test_mismatching_confirmation_then_eof_exits_1(auth_dir: Path) -> None:
    result = invoke("add-user", "alice", "--auth-dir", str(auth_dir), input="pw1\npw2\n")
    assert result.exit_code == 1
    assert not (auth_dir / USERS_FILENAME).exists()


@pytest.mark.parametrize("stdin", ["", "\n", "\r\n"])
def test_password_stdin_empty_is_rejected(auth_dir: Path, stdin: str) -> None:
    result = invoke(
        "add-user", "alice", "--password-stdin", "--auth-dir", str(auth_dir), input=stdin
    )
    assert result.exit_code == 1
    assert "stdin" in result.stderr


@pytest.mark.parametrize(
    ("stdin", "expected"),
    [
        (PW + " \n", PW + " "),  # a trailing space is part of the password
        (PW + "\r\n", PW),  # CRLF is one line ending
        (PW + "\n\n", PW),  # only the first line is read; one newline stripped
        (PW, PW),  # no newline at all
    ],
)
def test_password_stdin_strips_exactly_one_line_ending(
    auth_dir: Path, stdin: str, expected: str
) -> None:
    result = invoke(
        "add-user", "alice", "--password-stdin", "--auth-dir", str(auth_dir), input=stdin
    )
    assert result.exit_code == 0, result.output
    assert passwords.verify_password(expected, records(auth_dir)["alice"]["password_hash"])


def test_overlong_stdin_line_is_rejected_by_the_policy(auth_dir: Path) -> None:
    result = invoke(
        "add-user",
        "alice",
        "--password-stdin",
        "--auth-dir",
        str(auth_dir),
        input="x" * 5000 + "\n",
    )
    assert result.exit_code == 1 and "too_long" in result.stderr


def test_password_equal_to_username_is_rejected(auth_dir: Path) -> None:
    name = "alice-with-a-long-name"
    result = invoke(
        "add-user", name, "--password-stdin", "--auth-dir", str(auth_dir), input=name + "\n"
    )
    assert result.exit_code == 1 and "equals_username" in result.stderr


def test_no_command_has_a_secret_option() -> None:
    group: Any = typer.main.get_command(auth_cli.app)
    assert len(group.commands) == 11
    for name, command in group.commands.items():
        flags = {opt for param in command.params for opt in getattr(param, "opts", [])}
        assert not flags & {"--password", "--token", "--code"}, name
        assert all(param.envvar is None for param in command.params), name


def test_password_environment_variable_is_never_read(auth_dir: Path) -> None:
    add(auth_dir)
    before = records(auth_dir)["alice"]["password_hash"]
    result = invoke(
        "set-password", "alice", "--auth-dir", str(auth_dir), env={"AO_AUTH_PASSWORD": "x" * 20}
    )
    assert result.exit_code == 1
    assert records(auth_dir)["alice"]["password_hash"] == before


# ---------------------------------------------------------------------------
# AC 4: configuration and permission errors -> 78
# ---------------------------------------------------------------------------


def test_invalid_env_exits_78_naming_the_variable(
    auth_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AO_UI_AUTH", "maybe")
    result = invoke("status", "--auth-dir", str(auth_dir))
    assert result.exit_code == EXIT_CONFIG
    assert "AO_UI_AUTH" in result.stderr


def test_workspace_trusted_proxies_exits_78(tmp_path: Path, auth_dir: Path) -> None:
    ws = write_workspace(tmp_path, '    trusted_proxies: ["127.0.0.1"]\n')
    result = invoke("status", "--workspace", str(ws), "--auth-dir", str(auth_dir))
    assert result.exit_code == EXIT_CONFIG
    assert "AO_UI_AUTH_TRUSTED_PROXIES" in result.stderr


def test_empty_auth_dir_exits_78() -> None:
    result = invoke("status", "--auth-dir", " ")
    assert result.exit_code == EXIT_CONFIG and "--auth-dir" in result.stderr


def test_workspace_must_be_a_directory(tmp_path: Path, auth_dir: Path) -> None:
    result = invoke("status", "--workspace", str(tmp_path / "nope"), "--auth-dir", str(auth_dir))
    assert result.exit_code == 1 and "not a directory" in result.stderr


def test_other_writable_parent_exits_78_with_the_chmod_hint(tmp_path: Path) -> None:
    parent = tmp_path / "loose"
    parent.mkdir()
    os.chmod(parent, 0o777)
    result = invoke(
        "add-user", "alice", "--password-stdin", "--auth-dir", str(parent / "auth"), input=PW + "\n"
    )
    assert result.exit_code == EXIT_CONFIG
    assert "chmod o-w" in result.stderr


def test_group_writable_parent_owned_by_another_user_exits_78(
    auth_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(path: Path) -> list[str]:
        raise fsutil.UnsafePathError(
            f"parent directory {path.parent} is group-writable and owned by uid 0; "
            f"run chmod g-w {path.parent}"
        )

    monkeypatch.setattr(fsutil, "_check_parent", refuse)
    result = invoke(
        "add-user", "alice", "--password-stdin", "--auth-dir", str(auth_dir), input=PW + "\n"
    )
    assert result.exit_code == EXIT_CONFIG and "chmod g-w" in result.stderr


def test_group_writable_parent_owned_by_you_only_warns(tmp_path: Path) -> None:
    parent = tmp_path / "group"
    parent.mkdir()
    os.chmod(parent, 0o770)
    result = invoke(
        "add-user", "alice", "--password-stdin", "--auth-dir", str(parent / "auth"), input=PW + "\n"
    )
    assert result.exit_code == 0, result.output
    assert "chmod g-w" in result.stderr


def test_mutating_commands_tighten_loose_permissions(auth_dir: Path) -> None:
    add(auth_dir)
    os.chmod(auth_dir, 0o755)
    os.chmod(auth_dir / USERS_FILENAME, 0o644)
    result = invoke("revoke-sessions", "alice", "--auth-dir", str(auth_dir))
    assert result.exit_code == 0, result.output
    assert "tightened" in result.stderr
    assert stat.S_IMODE(auth_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((auth_dir / USERS_FILENAME).stat().st_mode) == 0o600


def test_read_only_commands_only_report_loose_permissions(auth_dir: Path) -> None:
    add(auth_dir)
    os.chmod(auth_dir, 0o755)
    result = invoke("list-users", "--auth-dir", str(auth_dir))
    assert result.exit_code == 0
    assert "chmod 700" in result.stderr
    assert stat.S_IMODE(auth_dir.stat().st_mode) == 0o755
    status = invoke("status", "--auth-dir", str(auth_dir))
    assert status.exit_code == 0
    assert "store_dir: unsafe" in status.stdout


# ---------------------------------------------------------------------------
# AC 5: status
# ---------------------------------------------------------------------------


def test_status_prints_sources_permissions_counts_and_notes(
    tmp_path: Path, auth_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    add(auth_dir, "alice")
    add(auth_dir, "bob", "--require-totp")
    monkeypatch.setenv("AO_UI_AUTH", "0")
    result = invoke("status", "--auth-dir", str(auth_dir))
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert out.splitlines()[0] == f"store: {auth_dir}"
    assert "enabled: false (env:AO_UI_AUTH)" in out
    assert "totp: off (default)" in out
    assert "store_dir: " in out and "(cli)" in out
    assert "store_dir: ok" in out and "state_dir: ok" in out
    assert "lockouts.json: ok" in out and "users.json: ok" in out
    assert "users: 2" in out and "totp_required_unenrolled: 1" in out
    assert "enrollment_tokens_pending: 1" in out and "enrolled: 0" in out
    assert re.search(r"server time \(UTC\): \d{4}-\d\d-\d\dT", out)
    assert "stray temp files: none" in out
    # a sentinel grep: no password, hash or token material
    stored = records(auth_dir)["bob"]["enrollment_token"]
    for secret in (PW, "$scrypt$", stored["hash_hex"], stored["salt_hex"]):
        assert secret not in result.output


def test_status_reports_lockouts_health_and_stray_files(auth_dir: Path) -> None:
    add(auth_dir)
    (auth_dir / ".users.json.deadbeef.tmp").write_text("{}")
    result = invoke("status", "--auth-dir", str(auth_dir))
    assert "stray temp files: .users.json.deadbeef.tmp" in result.stdout
    assert "WARNING: stray temp files" in result.stdout
    (auth_dir / "state" / LOCKOUTS_FILENAME).write_text("{not json")
    result = invoke("status", "--auth-dir", str(auth_dir))
    assert result.exit_code == 0
    assert "lockouts.json: corrupt" in result.stdout
    (auth_dir / "state" / LOCKOUTS_FILENAME).unlink()
    assert "lockouts.json: missing" in invoke("status", "--auth-dir", str(auth_dir)).stdout


def test_status_counts_locked_accounts_and_missing_store(auth_dir: Path) -> None:
    result = invoke("status", "--auth-dir", str(auth_dir))
    assert result.exit_code == 0
    assert "users.json: missing" in result.stdout and "users: 0" in result.stdout
    add(auth_dir)
    lock_out(auth_dir, records(auth_dir)["alice"]["user_id"])
    assert "locked: 1" in invoke("status", "--auth-dir", str(auth_dir)).stdout


def test_status_with_a_corrupt_users_file_exits_0(auth_dir: Path) -> None:
    add(auth_dir)
    (auth_dir / USERS_FILENAME).write_text("garbage")
    result = invoke("status", "--auth-dir", str(auth_dir))
    assert result.exit_code == 0
    assert "users.json: corrupt" in result.stdout and "users: -" in result.stdout
    assert invoke("list-users", "--auth-dir", str(auth_dir)).exit_code == 1


def test_status_policy_notes(
    auth_dir: Path, monkeypatch: pytest.MonkeyPatch, fixed_secret: bytes
) -> None:
    add(auth_dir)
    monkeypatch.setenv("AO_UI_AUTH_TOTP", "required")
    out = invoke("status", "--auth-dir", str(auth_dir)).stdout
    assert "ao auth enrollment-token <user>" in out
    monkeypatch.setenv("AO_UI_AUTH_TOTP", "off")
    assert "still asked for a code" not in invoke("status", "--auth-dir", str(auth_dir)).stdout
    assert (
        invoke(
            "enable-2fa", "alice", "--auth-dir", str(auth_dir), input=current_code() + "\n"
        ).exit_code
        == 0
    )
    out = invoke("status", "--auth-dir", str(auth_dir)).stdout
    assert "1 enrolled user(s) are still asked for a code (enrollment is sticky)" in out


def test_status_json_carries_the_same_keys(tmp_path: Path, auth_dir: Path) -> None:
    add(auth_dir)
    ws = write_workspace(tmp_path, "    totp: required\n")
    result = invoke("status", "--json", "--workspace", str(ws), "--auth-dir", str(auth_dir))
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)  # stdout is one JSON document
    assert result.stderr.splitlines()[0] == f"store: {auth_dir}"
    assert set(data) >= {
        "store_dir", "state_dir", "settings", "permissions", "files", "counts",
        "stray_temp_files", "server_time_utc", "config_risks", "recent_startup_events",
        "notes", "warnings",
    }  # fmt: skip
    assert set(data["counts"]) == {
        "users", "enrolled", "totp_required_unenrolled", "locked", "enrollment_tokens_pending",
    }  # fmt: skip
    assert data["settings"]["totp"] == {
        "value": "required",
        "source": f"config:{ws}/.ao/config.yaml",
    }
    assert "$scrypt$" not in result.stdout and PW not in result.stdout


def test_status_source_labels(tmp_path: Path, auth_dir: Path) -> None:
    ws = write_workspace(tmp_path, "    totp: required\n")
    out = invoke("status", "--workspace", str(ws), "--auth-dir", str(auth_dir)).stdout
    assert f"totp: required (config:{ws}/.ao/config.yaml)" in out
    assert "enabled: false (default)" in out


# ---------------------------------------------------------------------------
# AC 5b: config-risk flags
# ---------------------------------------------------------------------------


def test_status_flags_disabled_by_config(tmp_path: Path, auth_dir: Path) -> None:
    add(auth_dir)
    ws = write_workspace(tmp_path, "    enabled: false\n")
    result = invoke("status", "--workspace", str(ws), "--auth-dir", str(auth_dir))
    assert result.exit_code == 0  # reported, never an exit 78
    assert "disabled_by_config" in result.stdout
    assert (
        "ao ui in this workspace will refuse to start (exit 78); "
        "use --no-auth / AO_UI_AUTH=0 to disable on purpose"
    ) in result.stdout
    data = json.loads(
        invoke("status", "--json", "--workspace", str(ws), "--auth-dir", str(auth_dir)).stdout
    )
    assert [r["flag"] for r in data["config_risks"]] == ["disabled_by_config"]


def test_status_flags_totp_downgraded_by_config(tmp_path: Path, auth_dir: Path) -> None:
    add(auth_dir)
    ws = write_workspace(tmp_path, "    enabled: true\n    totp: optional\n")
    result = invoke("status", "--workspace", str(ws), "--auth-dir", str(auth_dir))
    assert result.exit_code == 0
    assert "totp_downgraded_by_config" in result.stdout
    assert "disabled_by_config" not in result.stdout


def test_status_flags_nothing_without_accounts(tmp_path: Path, auth_dir: Path) -> None:
    ws = write_workspace(tmp_path, "    enabled: false\n")
    result = invoke("status", "--workspace", str(ws), "--auth-dir", str(auth_dir))
    assert result.exit_code == 0
    assert "disabled_by_config" not in result.stdout
    assert "totp_downgraded_by_config" not in result.stdout


def test_status_lists_recent_startup_events(auth_dir: Path) -> None:
    from agent_orchestrator.auth.audit import AuditEvent, AuditLog, AuditOutcome
    from agent_orchestrator.auth.model import AuditEventName

    add(auth_dir)
    log = AuditLog.for_state_dir(auth_dir / "state")
    for _ in range(7):  # more than the five shown
        log.record(
            AuditEvent(
                AuditEventName.STARTUP_DISABLED_BY_CONFIG, AuditOutcome.FAILURE, realm="ui:x"
            )
        )
    log.record(AuditEvent(AuditEventName.LOGOUT, AuditOutcome.INFO, realm="ui:x"))
    with (auth_dir / "state" / "audit.jsonl").open("a") as handle:
        handle.write("not json\n")
    result = invoke("status", "--auth-dir", str(auth_dir))
    assert result.stdout.count("auth.startup.disabled_by_config") == 5
    data = json.loads(invoke("status", "--json", "--auth-dir", str(auth_dir)).stdout)
    assert len(data["recent_startup_events"]) == 5
    assert "auth.logout" not in result.stdout


# ---------------------------------------------------------------------------
# AC 5c: a config-sourced store_dir is never created or chmod-ed
# ---------------------------------------------------------------------------


@pytest.fixture()
def chmod_spy(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    calls: list[tuple[Any, ...]] = []
    real_chmod, real_fchmod = os.chmod, os.fchmod

    def chmod(*args: Any, **kwargs: Any) -> None:
        calls.append(("chmod", *args))
        real_chmod(*args, **kwargs)

    def fchmod(fd: int, mode: int) -> None:
        calls.append(("fchmod", fd, mode))
        real_fchmod(fd, mode)

    monkeypatch.setattr(os, "chmod", chmod)
    monkeypatch.setattr(os, "fchmod", fchmod)
    return calls


def config_store_workspace(tmp_path: Path, store: Path) -> Path:
    return write_workspace(tmp_path, f"    store_dir: {store}\n")


def test_config_store_dir_missing_is_not_created(
    tmp_path: Path, chmod_spy: list[tuple[Any, ...]]
) -> None:
    store = tmp_path / "chosen-by-a-repo"
    ws = config_store_workspace(tmp_path, store)
    result = invoke(
        "add-user", "alice", "--password-stdin", "--workspace", str(ws), input=PW + "\n"
    )
    assert result.exit_code == EXIT_CONFIG
    assert "--auth-dir" in result.stderr and str(ws / ".ao" / "config.yaml") in result.stderr
    assert "must already exist and be private" in result.stderr
    assert not store.exists()
    assert chmod_spy == []


def test_config_store_dir_with_loose_mode_is_not_fixed(
    tmp_path: Path, chmod_spy: list[tuple[Any, ...]]
) -> None:
    store = tmp_path / "chosen-by-a-repo"
    store.mkdir()
    os.chmod(store, 0o755)
    chmod_spy.clear()
    ws = config_store_workspace(tmp_path, store)
    result = invoke(
        "add-user", "alice", "--password-stdin", "--workspace", str(ws), input=PW + "\n"
    )
    assert result.exit_code == EXIT_CONFIG
    assert "--auth-dir" in result.stderr
    assert stat.S_IMODE(store.stat().st_mode) == 0o755
    assert not (store / USERS_FILENAME).exists() and not (store / "state").exists()
    assert chmod_spy == []


def test_config_store_dir_private_is_used_without_chmod(
    tmp_path: Path, chmod_spy: list[tuple[Any, ...]]
) -> None:
    store = tmp_path / "chosen-by-a-repo"
    store.mkdir(mode=0o700)
    os.chmod(store, 0o700)
    chmod_spy.clear()
    ws = config_store_workspace(tmp_path, store)
    result = invoke(
        "add-user", "alice", "--password-stdin", "--workspace", str(ws), input=PW + "\n"
    )
    assert result.exit_code == 0, result.output
    assert (store / USERS_FILENAME).exists()
    assert stat.S_IMODE((store / "state").stat().st_mode) == 0o700
    assert stat.S_IMODE(store.stat().st_mode) == 0o700
    assert not [call for call in chmod_spy if call[0] == "chmod"]


def test_config_store_dir_with_loose_users_file_is_not_fixed(tmp_path: Path) -> None:
    store = tmp_path / "chosen-by-a-repo"
    store.mkdir(mode=0o700)
    os.chmod(store, 0o700)
    ws = config_store_workspace(tmp_path, store)
    assert (
        invoke(
            "add-user", "alice", "--password-stdin", "--workspace", str(ws), input=PW + "\n"
        ).exit_code
        == 0
    )
    os.chmod(store / USERS_FILENAME, 0o644)
    result = invoke("revoke-sessions", "alice", "--workspace", str(ws))
    assert result.exit_code == EXIT_CONFIG and "chmod 600" in result.stderr
    assert stat.S_IMODE((store / USERS_FILENAME).stat().st_mode) == 0o644


@pytest.mark.parametrize("how", ["flag", "env"])
def test_explicit_auth_dir_restores_create_and_fix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str
) -> None:
    store = tmp_path / "chosen-by-a-repo"
    ws = config_store_workspace(tmp_path, store)
    args = ["add-user", "alice", "--password-stdin", "--workspace", str(ws)]
    if how == "flag":
        args += ["--auth-dir", str(store)]
    else:
        monkeypatch.setenv("AO_AUTH_DIR", str(store))
    result = invoke(*args, input=PW + "\n")
    assert result.exit_code == 0, result.output
    assert stat.S_IMODE(store.stat().st_mode) == 0o700


# ---------------------------------------------------------------------------
# AC 6: enrollment-token notes
# ---------------------------------------------------------------------------


def test_enrollment_token_note_when_not_required(
    auth_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    add(auth_dir)
    monkeypatch.setenv("AO_UI_AUTH_TOTP", "optional")
    result = invoke("enrollment-token", "alice", "--auth-dir", str(auth_dir))
    assert result.exit_code == 0 and len(tokens_in(result.stdout)) == 1
    assert "not currently required" in result.stdout
    assert "env:AO_UI_AUTH_TOTP" in result.stdout


def test_enrollment_token_note_when_blocked(
    auth_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    add(auth_dir, "alice", "--require-totp")
    monkeypatch.setenv("AO_UI_AUTH_TOTP", "off")
    result = invoke("enrollment-token", "alice", "--auth-dir", str(auth_dir))
    assert result.exit_code == 0 and len(tokens_in(result.stdout)) == 1
    assert "cannot log in until" in result.stdout


def test_enrollment_token_has_no_note_when_enrollment_is_expected(
    auth_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    add(auth_dir)
    monkeypatch.setenv("AO_UI_AUTH_TOTP", "required")
    result = invoke("enrollment-token", "alice", "--auth-dir", str(auth_dir))
    assert result.exit_code == 0 and "NOTE" not in result.stdout


def test_enrollment_token_unknown_user_exits_1(auth_dir: Path) -> None:
    add(auth_dir)
    result = invoke("enrollment-token", "ghost", "--auth-dir", str(auth_dir))
    assert result.exit_code == 1 and "no such user: ghost" in result.stderr


# ---------------------------------------------------------------------------
# Command edge cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "set-password",
        "remove-user",
        "enable-2fa",
        "disable-2fa",
        "reset-2fa",
        "unlock",
        "revoke-sessions",
    ],
)
def test_unknown_user_exits_1(auth_dir: Path, command: str) -> None:
    add(auth_dir)
    extra = ["--yes"] if command in {"remove-user", "disable-2fa", "reset-2fa"} else []
    result = invoke(command, "ghost", *extra, "--auth-dir", str(auth_dir))
    assert result.exit_code == 1 and "no such user: ghost" in result.stderr


def test_add_user_validates_and_normalizes_the_name(auth_dir: Path) -> None:
    bad = invoke(
        "add-user", "bad name!", "--password-stdin", "--auth-dir", str(auth_dir), input=PW + "\n"
    )
    assert bad.exit_code == 1 and "invalid username" in bad.stderr
    add(auth_dir, "  Ａlice  ")  # fullwidth A, padded: NFKC -> strip -> lower
    assert list(records(auth_dir)) == ["alice"]
    assert invoke("revoke-sessions", "ALICE", "--auth-dir", str(auth_dir)).exit_code == 0


def test_add_user_notes_that_auth_is_off(auth_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert "ao ui --auth" in add(auth_dir).stdout
    monkeypatch.setenv("AO_UI_AUTH", "1")
    assert "ao ui --auth" not in add(auth_dir, "bob").stdout


def test_add_user_with_require_totp_under_policy_off_prints_the_blocked_note(
    auth_dir: Path,
) -> None:
    result = add(auth_dir, "alice", "--require-totp")
    assert "cannot log in until" in result.stdout


def test_remove_user_asks_for_confirmation(auth_dir: Path) -> None:
    add(auth_dir)
    declined = invoke("remove-user", "alice", "--auth-dir", str(auth_dir), input="n\n")
    assert declined.exit_code == 1 and "alice" in records(auth_dir)
    accepted = invoke("remove-user", "alice", "--auth-dir", str(auth_dir), input="y\n")
    assert accepted.exit_code == 0 and records(auth_dir) == {}


def test_remove_user_survives_a_corrupt_lockouts_file(auth_dir: Path) -> None:
    add(auth_dir)
    add(auth_dir, "bob")
    (auth_dir / "state" / LOCKOUTS_FILENAME).write_text("{not json")
    result = invoke("remove-user", "bob", "--yes", "--auth-dir", str(auth_dir))
    assert result.exit_code == 0, result.output
    assert "WARNING: could not clear the lockout entry" in result.stderr
    assert "bob" not in records(auth_dir)


def test_corrupt_lockouts_warn_and_unlock_repairs(auth_dir: Path) -> None:
    add(auth_dir)
    (auth_dir / "state" / LOCKOUTS_FILENAME).write_text("{not json")
    result = invoke(
        "set-password", "alice", "--auth-dir", str(auth_dir), input=f"{NEW_PW}\n{NEW_PW}\n"
    )
    assert result.exit_code == 0
    assert "lockouts.json is unusable" in result.stderr
    assert invoke("list-users", "--auth-dir", str(auth_dir)).stdout.count(" ?") == 1
    assert invoke("unlock", "alice", "--auth-dir", str(auth_dir)).exit_code == 0
    repaired = json.loads((auth_dir / "state" / LOCKOUTS_FILENAME).read_text())
    assert re.fullmatch(r"[0-9a-f]{64}", repaired["name_key_hex"])


def test_list_users_with_no_store(auth_dir: Path) -> None:
    result = invoke("list-users", "--auth-dir", str(auth_dir))
    assert result.exit_code == 0 and "(no users)" in result.stdout


def test_list_users_shows_recovery_codes_left(auth_dir: Path, fixed_secret: bytes) -> None:
    add(auth_dir)
    assert (
        invoke(
            "enable-2fa", "alice", "--auth-dir", str(auth_dir), input=current_code() + "\n"
        ).exit_code
        == 0
    )
    row = invoke("list-users", "--auth-dir", str(auth_dir)).stdout.splitlines()[3]
    assert row.split()[:4] == ["alice", "yes", "no", "10"]


def test_disable_2fa_clears_a_requirement_without_enrollment(auth_dir: Path) -> None:
    add(auth_dir, "alice", "--require-totp")
    result = invoke("disable-2fa", "alice", "--yes", "--auth-dir", str(auth_dir))
    assert result.exit_code == 0, result.output
    record = records(auth_dir)["alice"]
    assert record["totp_required"] is False and record["enrollment_token"] is None


def test_disable_2fa_neither_enrolled_nor_required_exits_1(auth_dir: Path) -> None:
    add(auth_dir)
    result = invoke("disable-2fa", "alice", "--yes", "--auth-dir", str(auth_dir))
    assert result.exit_code == 1 and "not enrolled" in result.stderr


def test_disable_2fa_warns_under_a_required_policy_and_asks_to_confirm(
    auth_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    add(auth_dir, "alice", "--require-totp")
    monkeypatch.setenv("AO_UI_AUTH_TOTP", "required")
    result = invoke("disable-2fa", "alice", "--auth-dir", str(auth_dir), input="n\n")
    assert result.exit_code == 1 and "forced to enroll again" in result.stdout
    assert records(auth_dir)["alice"]["totp_required"] is True


def test_reset_2fa_is_idempotent_and_confirmed(auth_dir: Path) -> None:
    add(auth_dir)
    assert invoke("reset-2fa", "alice", "--auth-dir", str(auth_dir), input="n\n").exit_code == 1
    first = invoke("reset-2fa", "alice", "--yes", "--auth-dir", str(auth_dir))
    epoch = records(auth_dir)["alice"]["credential_epoch"]
    second = invoke("reset-2fa", "alice", "--yes", "--auth-dir", str(auth_dir))
    assert first.exit_code == second.exit_code == 0
    assert records(auth_dir)["alice"]["credential_epoch"] == epoch  # nothing changed: no bump


def test_enable_2fa_notes_the_off_policy_and_uses_the_issuer(
    auth_dir: Path, fixed_secret: bytes
) -> None:
    add(auth_dir)
    result = invoke("enable-2fa", "alice", "--auth-dir", str(auth_dir), input=current_code() + "\n",
                    env={"AO_UI_AUTH_TOTP_ISSUER": "my-issuer"})  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "enrollment is sticky" in result.stdout
    assert "otpauth://totp/my-issuer:alice?" in result.stdout
    assert "next code" in result.stdout


def test_enable_2fa_gives_up_after_three_bad_codes(auth_dir: Path, fixed_secret: bytes) -> None:
    add(auth_dir)
    now = time.time()
    valid = {totp.hotp(RFC_KEY, totp.totp_step(now) + delta) for delta in range(-2, 3)}
    bad = [c for c in ("000000", "111111", "222222", "333333") if c not in valid][:3]
    epoch = records(auth_dir)["alice"]["credential_epoch"]
    result = invoke(
        "enable-2fa", "alice", "--auth-dir", str(auth_dir), input="\n".join([*bad, "x"]) + "\n"
    )
    assert result.exit_code == 1 and "nothing was changed" in result.stderr
    assert records(auth_dir)["alice"]["totp"] is None
    assert records(auth_dir)["alice"]["credential_epoch"] == epoch


def test_enable_2fa_with_closed_stdin_exits_1(auth_dir: Path, fixed_secret: bytes) -> None:
    add(auth_dir)
    result = invoke("enable-2fa", "alice", "--auth-dir", str(auth_dir), input="")
    assert result.exit_code == 1 and records(auth_dir)["alice"]["totp"] is None


def test_enable_2fa_detects_a_concurrent_account_change(
    auth_dir: Path, fixed_secret: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    add(auth_dir)
    real_prompt: Callable[..., Any] = typer.prompt

    def prompt_then_revoke(*args: Any, **kwargs: Any) -> Any:
        answer = real_prompt(*args, **kwargs)
        assert invoke("revoke-sessions", "alice", "--auth-dir", str(auth_dir)).exit_code == 0
        return answer

    monkeypatch.setattr(typer, "prompt", prompt_then_revoke)
    result = runner.invoke(
        auth_cli.app,
        ["enable-2fa", "alice", "--auth-dir", str(auth_dir)],
        input=current_code() + "\n",
    )
    assert result.exit_code == 1 and "account changed during enrollment; retry" in result.stderr
    assert records(auth_dir)["alice"]["totp"] is None


def test_remove_last_user_is_allowed_when_auth_is_off(auth_dir: Path) -> None:
    add(auth_dir)
    result = invoke("remove-user", "alice", "--yes", "--auth-dir", str(auth_dir))
    assert result.exit_code == 0 and records(auth_dir) == {}


def test_workspace_default_comes_from_the_environment(
    tmp_path: Path, auth_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    add(auth_dir)
    ws = write_workspace(tmp_path, "    enabled: false\n")
    monkeypatch.setenv("AO_WORKSPACE_ROOT", str(ws))
    result = invoke("status", "--auth-dir", str(auth_dir))
    assert "disabled_by_config" in result.stdout


def test_default_store_follows_xdg(tmp_path: Path) -> None:
    result = invoke("add-user", "alice", "--password-stdin", input=PW + "\n")
    assert result.exit_code == 0, result.output
    expected = tmp_path / "xdg-config" / "ao" / "auth"
    assert (expected / USERS_FILENAME).exists()
    assert (tmp_path / "xdg-state" / "ao" / "auth" / "audit.jsonl").exists()
    assert "(default)" in invoke("status").stdout


# ---------------------------------------------------------------------------
# Error mapping unit cases (the multi-process store-busy test belongs to T-U2ERMo)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "code", "needle"),
    [
        (
            StoreLockTimeoutError("timed out after 5s waiting for lock /x/users.lock"),
            1,
            "users.lock",
        ),
        (StoreUnavailableError(cause_for_log="disk on fire"), 1, "disk on fire"),
        (StoreCorruptError("users.json: not valid JSON"), 1, "not valid JSON"),
        (StoreMissingError("/x/users.json does not exist"), 1, "ao auth add-user"),
        (fsutil.UnsafePathError("mode 0777; run chmod 700 /x"), EXIT_CONFIG, "chmod 700"),
    ],
)
def test_error_boundary_maps_exit_codes(error: Exception, code: int, needle: str) -> None:
    mapped = auth_cli._classify(error)
    assert mapped is not None and mapped[1] == code and needle in mapped[0]
    if isinstance(error, StoreLockTimeoutError):
        assert "busy" in mapped[0]


def test_unmapped_errors_propagate() -> None:
    assert auth_cli._classify(RuntimeError("bug")) is None
    with pytest.raises(RuntimeError), auth_cli._errors():
        raise RuntimeError("bug")


def test_lock_timeout_surfaces_as_exit_1(auth_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    add(auth_dir)
    from agent_orchestrator.auth import store as store_module

    real_init = store_module.UserStore.__init__

    def quick_timeout(self: Any, paths: Any, **kwargs: Any) -> None:
        kwargs["lock_timeout"] = 0.1
        real_init(self, paths, **kwargs)

    monkeypatch.setattr(store_module.UserStore, "__init__", quick_timeout)
    with fsutil.FileLock(auth_dir / "users.lock", timeout=1):
        result = invoke(
            "set-password", "alice", "--auth-dir", str(auth_dir), input=f"{NEW_PW}\n{NEW_PW}\n"
        )
    assert result.exit_code == 1
    assert "busy" in result.stderr and "users.lock" in result.stderr


# ---------------------------------------------------------------------------
# Registration in the root app
# ---------------------------------------------------------------------------


def test_auth_is_registered_in_the_root_app(auth_dir: Path) -> None:
    result = runner.invoke(root_cli.app, ["auth", "--help"])
    assert result.exit_code == 0
    for command in ("add-user", "enrollment-token", "revoke-sessions", "status"):
        assert command in result.output
    listed = runner.invoke(root_cli.app, ["auth", "list-users", "--auth-dir", str(auth_dir)])
    assert listed.exit_code == 0 and listed.stdout.splitlines()[0] == f"store: {auth_dir}"
    assert TotpPolicy.OFF.value == "off"  # the policy vocabulary the notes quote
