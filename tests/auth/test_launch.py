"""``prepare_auth`` (T-jVqH8w; HLD 11.20, 11.3.4 rows 9, 14, 15; AC-2, AC-25, AC-45 part)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

import agent_orchestrator.auth.launch as launch_mod
from agent_orchestrator.auth.audit import AuditLog
from agent_orchestrator.auth.errors import AuthConfigError, AuthNotReadyError
from agent_orchestrator.auth.launch import (
    DEPRECATION_NOTICE,
    PLAIN_HTTP_WARNING,
    AuthLaunch,
    exit_code_for,
    is_loopback_bind,
    prepare_auth,
)
from agent_orchestrator.auth.model import TotpPolicy
from agent_orchestrator.auth.settings import AuthCliOverrides, ConfigRisk
from agent_orchestrator.errors import EXIT_CONFIG
from tests.auth.helpers.launch import (
    LaunchEnv,
    audit_events,
    auth_block,
    make_launch_env,
    write_ws_config,
)

PORT = 8765
LOOPBACK = "127.0.0.1"
CLI_ON = AuthCliOverrides(enabled=True)


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated ``$HOME`` (the default store/state and ``service.env`` live under it)."""
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    return home_dir


@pytest.fixture()
def lenv(tmp_path: Path, home: Path) -> LaunchEnv:
    return make_launch_env(tmp_path / "launch")


@pytest.fixture()
def redaction_spy(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(launch_mod, "install_log_redaction", lambda: calls.append(1))
    return calls


def prepare(
    lenv: LaunchEnv,
    *,
    cli: AuthCliOverrides = CLI_ON,
    env: dict[str, str] | None = None,
    port: int = PORT,
    bind_host: str = LOOPBACK,
    **kwargs: Any,
) -> AuthLaunch:
    return prepare_auth(
        cli=cli,
        env={**lenv.env, **(env or {})},
        workspace_root=lenv.workspace,
        realm_kind="ui",
        port=port,
        bind_host=bind_host,
        **kwargs,
    )


def boom(*_args: object, **_kwargs: object) -> None:
    raise OSError("disk full")


# --- (a) disabled by default ---------------------------------------------------------------


def test_defaults_are_off_and_empty(tmp_path: Path, home: Path, redaction_spy: list[int]) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    launch = prepare_auth(
        cli=AuthCliOverrides(),
        env={},
        workspace_root=ws,
        realm_kind="ui",
        port=PORT,
        bind_host=LOOPBACK,
    )
    assert launch.runtime is None
    assert launch.uvicorn_kwargs == {}
    assert launch.child_env == {}
    assert launch.warnings == ()
    denied = set(launch.denied_paths)
    assert (home / ".config/ao/auth").resolve() in denied
    assert (home / ".local/state/ao/auth").resolve() in denied
    assert (home / ".config/ao/service.env").resolve() in denied
    assert redaction_spy == []  # not installed when disabled


# --- (b) enabled, empty store ---------------------------------------------------------------


def test_enabled_with_an_empty_store_refuses_and_audits(tmp_path: Path, home: Path) -> None:
    empty = make_launch_env(tmp_path / "empty", users=())
    with pytest.raises(AuthNotReadyError) as info:
        prepare(empty)
    text = str(info.value)
    assert "ao auth add-user" in text
    assert "--auth-dir" in text  # a non-default store is named
    assert audit_events(empty.state_dir) == ["auth.startup.refused"]
    assert exit_code_for(info.value) == EXIT_CONFIG


def test_a_failing_refusal_audit_does_not_change_the_error(
    tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = make_launch_env(tmp_path / "empty", users=())
    monkeypatch.setattr(AuditLog, "record", boom)
    with pytest.raises(AuthNotReadyError, match="ao auth add-user"):
        prepare(empty)


# --- (c)-(e) enabled ----------------------------------------------------------------------


def test_enabled_loopback_wires_the_runtime(lenv: LaunchEnv, redaction_spy: list[int]) -> None:
    launch = prepare(lenv)
    assert launch.runtime is not None
    realm_id = launch.runtime.realm.id
    assert realm_id.startswith("ui:") and len(realm_id) == len("ui:") + 12
    assert launch.uvicorn_kwargs == {"proxy_headers": False}
    assert not any("not encrypted" in line for line in launch.warnings)
    assert redaction_spy == [1]
    assert audit_events(lenv.state_dir) == []  # no event without a risk or a refusal


def test_enabled_non_loopback_without_proxies_warns_plain_http(lenv: LaunchEnv) -> None:
    launch = prepare(lenv, bind_host="0.0.0.0")
    assert any("not encrypted" in line and "0.0.0.0" in line for line in launch.warnings)
    assert "AO_UI_AUTH_TRUSTED_PROXIES" in PLAIN_HTTP_WARNING


def test_trusted_proxies_set_forwarded_allow_ips_and_drop_the_warning(lenv: LaunchEnv) -> None:
    launch = prepare(
        lenv, bind_host="0.0.0.0", env={"AO_UI_AUTH_TRUSTED_PROXIES": "127.0.0.1,10.0.0.2"}
    )
    assert launch.uvicorn_kwargs == {
        "proxy_headers": True,
        "forwarded_allow_ips": "127.0.0.1,10.0.0.2",
    }
    assert not any("not encrypted" in line for line in launch.warnings)


def test_warning_order_is_settings_then_transport(lenv: LaunchEnv) -> None:
    cfg = write_ws_config(lenv.workspace, auth_block("enabled: true", "totp: optional"))
    launch = prepare(lenv, cli=AuthCliOverrides(), bind_host="0.0.0.0")
    assert "comes only from" in launch.warnings[0] and str(cfg) in launch.warnings[0]
    assert "not encrypted" in launch.warnings[-1]


# --- (f), (g) disabled ----------------------------------------------------------------------


def test_disabled_non_loopback_prints_the_deprecation_notice(
    tmp_path: Path, home: Path, redaction_spy: list[int]
) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    launch = prepare_auth(
        cli=AuthCliOverrides(),
        env={},
        workspace_root=ws,
        realm_kind="ui",
        port=PORT,
        bind_host="0.0.0.0",
    )
    assert launch.warnings == (DEPRECATION_NOTICE.format(host="0.0.0.0"),)
    assert "deprecated" in launch.warnings[0]
    assert redaction_spy == []


def test_disabled_by_default_with_accounts_prints_the_note(lenv: LaunchEnv) -> None:
    launch = prepare(lenv, cli=AuthCliOverrides())
    assert launch.runtime is None
    [note] = launch.warnings
    assert note.startswith("Note:") and "1 account(s)" in note and str(lenv.store_dir) in note
    assert "from default" in note


def test_disabled_by_env_names_its_source(lenv: LaunchEnv) -> None:
    launch = prepare(lenv, cli=AuthCliOverrides(), env={"AO_UI_AUTH": "0"})
    [note] = launch.warnings
    assert "from env:AO_UI_AUTH" in note


def test_disabled_with_an_unknown_count_stays_silent(lenv: LaunchEnv) -> None:
    (lenv.store_dir / "users.json").write_text("{not json", encoding="utf-8")
    launch = prepare(lenv, cli=AuthCliOverrides(enabled=False))
    assert launch.warnings == ()


# --- (h) child_env ------------------------------------------------------------------------


def test_child_env_relays_only_cli_sourced_values(lenv: LaunchEnv) -> None:
    cli = AuthCliOverrides(enabled=True, totp=TotpPolicy.REQUIRED, store_dir=str(lenv.store_dir))
    assert prepare(lenv, cli=cli).child_env == {
        "AO_UI_AUTH": "1",
        "AO_UI_AUTH_TOTP": "required",
        "AO_AUTH_DIR": str(lenv.store_dir),
    }
    assert prepare(lenv, cli=AuthCliOverrides(enabled=False)).child_env == {"AO_UI_AUTH": "0"}
    assert prepare(lenv, cli=AuthCliOverrides(), env={"AO_UI_AUTH": "1"}).child_env == {}
    assert prepare(lenv, cli=AuthCliOverrides()).child_env == {}


# --- (i) denied_paths ---------------------------------------------------------------------


def test_denied_paths_cover_store_state_and_overrides(lenv: LaunchEnv, tmp_path: Path) -> None:
    launch = prepare(lenv)
    denied = launch.denied_paths
    assert lenv.store_dir.resolve() in denied and lenv.state_dir.resolve() in denied
    assert all(p == p.resolve() for p in denied)
    assert len(denied) == len(set(denied))


def test_denied_paths_include_a_derived_state_dir(tmp_path: Path, home: Path) -> None:
    store = tmp_path / "custom-store"
    store.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    launch = prepare_auth(
        cli=AuthCliOverrides(),
        env={"AO_AUTH_DIR": str(store)},
        workspace_root=ws,
        realm_kind="ui",
        port=PORT,
        bind_host=LOOPBACK,
    )
    assert store.resolve() in launch.denied_paths
    assert (store / "state").resolve() in launch.denied_paths


# --- (j), (k) ---------------------------------------------------------------------------------


def test_exit_code_for() -> None:
    assert exit_code_for(AuthConfigError("x")) == EXIT_CONFIG == 78
    assert exit_code_for(RuntimeError("x")) == 1


def test_port_zero_is_refused_before_the_runtime_is_built(
    lenv: LaunchEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    built: list[int] = []
    monkeypatch.setattr(launch_mod, "build_auth_runtime", lambda *a, **k: built.append(1))
    with pytest.raises(AuthConfigError, match=r"--port 0"):
        prepare(lenv, port=0)
    assert built == []


def test_port_zero_with_auth_off_is_unchanged(lenv: LaunchEnv) -> None:
    assert prepare(lenv, cli=AuthCliOverrides(enabled=False), port=0).runtime is None


# --- the hub realm --------------------------------------------------------------------------


def test_the_hub_realm_needs_no_workspace(lenv: LaunchEnv) -> None:
    launch = prepare_auth(
        cli=CLI_ON,
        env=lenv.env,
        workspace_root=None,
        realm_kind="hub",
        port=PORT,
        bind_host=LOOPBACK,
    )
    assert launch.runtime is not None and launch.runtime.realm.id == "hub"


# --- config risks (security M3; rows 14 and 15) ----------------------------------------------


def test_config_only_disable_with_accounts_is_refused_and_audited(lenv: LaunchEnv) -> None:
    cfg = write_ws_config(lenv.workspace, auth_block("enabled: false"))
    with pytest.raises(AuthConfigError) as info:
        prepare(lenv, cli=AuthCliOverrides())
    text = str(info.value)
    assert str(cfg) in text and "--no-auth" in text and "AO_UI_AUTH=0" in text
    assert "1 account(s)" in text and str(lenv.store_dir) in text
    assert not isinstance(info.value, AuthNotReadyError)
    assert audit_events(lenv.state_dir) == ["auth.startup.disabled_by_config"]


def test_h1_a_hostile_config_store_cannot_start_auth_off_while_accounts_exist(
    tmp_path: Path, home: Path
) -> None:
    """T-2wE08U H1: accounts in the default store + config `enabled: false` + an empty
    config-chosen store => refused (ConfigRisk), not started with auth off."""
    seed = make_launch_env(tmp_path / "seed")
    default_store = home / ".config" / "ao" / "auth"
    default_store.parent.mkdir(parents=True)
    seed.store_dir.rename(default_store)
    ws = tmp_path / "ws"
    (ws / ".git").mkdir(parents=True)
    hostile = tmp_path / "hostile-empty"
    write_ws_config(ws, auth_block("enabled: false", f"store_dir: {hostile}"))
    with pytest.raises(AuthConfigError) as info:
        prepare_auth(
            cli=AuthCliOverrides(),
            env={},
            workspace_root=ws,
            realm_kind="ui",
            port=PORT,
            bind_host=LOOPBACK,
        )
    assert exit_code_for(info.value) == EXIT_CONFIG
    assert "--no-auth" in str(info.value)


def test_a_failing_disable_audit_does_not_change_the_error(
    lenv: LaunchEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_ws_config(lenv.workspace, auth_block("enabled: false"))
    monkeypatch.setattr(AuditLog, "record", boom)
    with pytest.raises(AuthConfigError, match="refusing to start an unauthenticated dashboard"):
        prepare(lenv, cli=AuthCliOverrides())


def test_config_only_disable_with_an_unknown_count_says_so(lenv: LaunchEnv) -> None:
    write_ws_config(lenv.workspace, auth_block("enabled: false"))
    (lenv.store_dir / "users.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(AuthConfigError, match="an unknown number of account"):
        prepare(lenv, cli=AuthCliOverrides())


def test_config_only_disable_with_no_accounts_starts_unauthenticated(
    tmp_path: Path, home: Path
) -> None:
    empty = make_launch_env(tmp_path / "empty", users=())
    write_ws_config(empty.workspace, auth_block("enabled: false"))
    launch = prepare(empty, cli=AuthCliOverrides())
    assert launch.runtime is None and launch.warnings == ()
    assert audit_events(empty.state_dir) == []


@pytest.mark.parametrize(
    "override",
    [AuthCliOverrides(enabled=False), None],
    ids=["cli-no-auth", "env-zero"],
)
def test_an_explicit_disable_is_the_row_9_note_not_a_refusal(
    lenv: LaunchEnv, override: AuthCliOverrides | None
) -> None:
    write_ws_config(lenv.workspace, auth_block("enabled: false"))
    launch = (
        prepare(lenv, cli=override)
        if override is not None
        else prepare(lenv, cli=AuthCliOverrides(), env={"AO_UI_AUTH": "0"})
    )
    assert launch.runtime is None
    assert any(line.startswith("Note:") for line in launch.warnings)
    assert audit_events(lenv.state_dir) == []


def test_a_config_only_totp_downgrade_warns_and_audits(lenv: LaunchEnv) -> None:
    write_ws_config(lenv.workspace, auth_block("enabled: true", "totp: optional"))
    launch = prepare(lenv, cli=AuthCliOverrides())
    assert launch.runtime is not None
    assert ConfigRisk.TOTP_DOWNGRADED_BY_CONFIG in launch.settings.config_risks
    assert any("comes only from" in line for line in launch.warnings)
    assert audit_events(lenv.state_dir) == ["auth.startup.totp_downgraded_by_config"]


def test_a_failing_downgrade_audit_still_starts(
    lenv: LaunchEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_ws_config(lenv.workspace, auth_block("enabled: true", "totp: optional"))
    monkeypatch.setattr(AuditLog, "record", boom)
    assert prepare(lenv, cli=AuthCliOverrides()).runtime is not None


def test_a_failing_startup_audit_is_logged_at_warning(
    lenv: LaunchEnv, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """T-2wE08U: a lost security audit event must be visible at the default log level."""
    write_ws_config(lenv.workspace, auth_block("enabled: false"))
    monkeypatch.setattr(AuditLog, "record", boom)
    with caplog.at_level(logging.WARNING, logger=launch_mod.__name__):
        with pytest.raises(AuthConfigError):
            prepare(lenv, cli=AuthCliOverrides())
    assert [r.levelno for r in caplog.records if "audit event" in r.getMessage()] == [
        logging.WARNING
    ]


def test_a_pinned_totp_policy_records_no_risk(lenv: LaunchEnv) -> None:
    write_ws_config(lenv.workspace, auth_block("enabled: true", "totp: optional"))
    launch = prepare(lenv, cli=AuthCliOverrides(totp=TotpPolicy.REQUIRED))
    assert launch.settings.config_risks == frozenset()
    assert audit_events(lenv.state_dir) == []


def test_the_audit_event_carries_the_realm_and_a_reason(lenv: LaunchEnv) -> None:
    write_ws_config(lenv.workspace, auth_block("enabled: false"))
    with pytest.raises(AuthConfigError):
        prepare(lenv, cli=AuthCliOverrides())
    import json

    [line] = (lenv.state_dir / "audit.jsonl").read_text().splitlines()
    event = json.loads(line)
    assert event["realm"].startswith("ui:") and event["details"] == {"reason": "disabled_by_config"}
    assert event["outcome"] == "failure"


# --- is_loopback_bind -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("localhost", True),
        ("127.0.0.1", True),
        ("127.5.5.5", True),
        ("::1", True),
        ("0.0.0.0", False),
        ("192.168.1.2", False),
        ("::", False),
        ("example.com", False),
        ("", False),
    ],
)
def test_is_loopback_bind(host: str, expected: bool) -> None:
    assert is_loopback_bind(host) is expected
