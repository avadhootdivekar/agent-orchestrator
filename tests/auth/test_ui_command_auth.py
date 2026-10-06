"""``ao ui`` with auth (T-jVqH8w; AC-3, AC-45 ``ao ui`` part, security L3/L4/M3; HLD 16).

Drives the real CLI through ``CliRunner`` with a recording stand-in for uvicorn, so what is
asserted is how the server is configured, not that a socket opens.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agent_orchestrator.auth.errors import AuthConfigError
from agent_orchestrator.auth.runtime import runtime_of
from agent_orchestrator.cli import UI_DEFAULT_HOST, UI_DEFAULT_PORT, app
from agent_orchestrator.errors import EXIT_CONFIG
from agent_orchestrator.ui.app import (
    FACTORY_DEFAULT_HOST,
    FACTORY_DEFAULT_PORT,
    create_app_from_env,
)
from tests.auth.helpers.launch import (
    LaunchEnv,
    RecordingUvicorn,
    audit_events,
    auth_block,
    make_launch_env,
    write_ws_config,
)

runner = CliRunner()
PORT = 8765
UNAUTHENTICATED = "UNAUTHENTICATED"
RELOAD_ENV_KEYS = (
    "AO_UI_WORKSPACE",
    "AO_UI_BOUND_HOST",
    "AO_UI_BOUND_PORT",
    "AO_UI_AUTH",
    "AO_UI_AUTH_TOTP",
    "AO_AUTH_DIR",
)


@pytest.fixture()
def fake_uvicorn(monkeypatch: pytest.MonkeyPatch) -> RecordingUvicorn:
    stub = RecordingUvicorn()
    monkeypatch.setitem(sys.modules, "uvicorn", stub)
    return stub


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    # ``ao ui --reload`` writes these into ``os.environ``; make monkeypatch restore them.
    for key in RELOAD_ENV_KEYS:
        monkeypatch.setenv(key, "x")
        monkeypatch.delenv(key)


@pytest.fixture()
def lenv(tmp_path: Path) -> LaunchEnv:
    return make_launch_env(tmp_path / "launch")


def ui(lenv: LaunchEnv, *args: str) -> object:
    return runner.invoke(app, ["ui", "--workspace", str(lenv.workspace), *args])


def serving_app(uvicorn: RecordingUvicorn) -> FastAPI:
    [(args, _kwargs)] = uvicorn.calls
    served = args[0]
    assert isinstance(served, FastAPI)
    return served


# --- AC 1: auth off is unchanged ---


def test_auth_off_uvicorn_kwargs_are_exactly_host_and_port(
    tmp_path: Path, fake_uvicorn: RecordingUvicorn
) -> None:
    ws = tmp_path / "plain"
    ws.mkdir()
    result = runner.invoke(app, ["ui", "--workspace", str(ws)])
    assert result.exit_code == 0, result.output
    _args, kwargs = fake_uvicorn.calls[0]
    assert kwargs == {"host": "127.0.0.1", "port": PORT}
    assert runtime_of(serving_app(fake_uvicorn)) is None


def test_non_loopback_without_auth_keeps_the_warning_and_adds_the_deprecation(
    tmp_path: Path, fake_uvicorn: RecordingUvicorn
) -> None:
    ws = tmp_path / "plain"
    ws.mkdir()
    result = runner.invoke(app, ["ui", "--workspace", str(ws), "--host", "0.0.0.0"])
    assert result.exit_code == 0
    assert (
        "WARNING: binding 0.0.0.0 exposes an UNAUTHENTICATED dashboard that can browse "
        "files and start runs. Only do this on a trusted network."
    ) in result.output
    assert "deprecated" in result.output


# --- AC 4: refuse to start ---


def test_auth_with_an_empty_store_exits_78_before_serving(
    tmp_path: Path, fake_uvicorn: RecordingUvicorn
) -> None:
    empty = make_launch_env(tmp_path / "empty", users=())
    result = ui(empty, "--auth", "--auth-dir", str(empty.store_dir))
    assert result.exit_code == EXIT_CONFIG == 78
    assert "ERROR:" in result.output and "ao auth add-user" in result.output
    assert fake_uvicorn.calls == []
    assert audit_events(empty.store_dir / "state") == ["auth.startup.refused"]


@pytest.fixture()
def read_only_store(tmp_path: Path) -> Iterator[LaunchEnv]:
    """A store dir (with a user) at mode 0500 and no state dir yet: creating the derived
    ``<store>/state`` raises ``PermissionError``."""
    env = make_launch_env(tmp_path / "ro", users=("alice",))
    env.state_dir.rmdir()
    env.store_dir.chmod(0o500)
    try:
        yield env
    finally:
        env.store_dir.chmod(0o700)


def test_h_code_1_a_read_only_store_exits_78_not_a_traceback(
    read_only_store: LaunchEnv, fake_uvicorn: RecordingUvicorn
) -> None:
    result = ui(read_only_store, "--auth", "--auth-dir", str(read_only_store.store_dir))
    assert result.exit_code == EXIT_CONFIG == 78, result.output
    assert "Traceback" not in result.output
    assert isinstance(result.exception, SystemExit)  # not a raw PermissionError
    assert fake_uvicorn.calls == []


def test_the_same_refusal_through_the_environment_and_the_config(
    tmp_path: Path, fake_uvicorn: RecordingUvicorn, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = make_launch_env(tmp_path / "empty", users=())
    for key, value in empty.env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("AO_UI_AUTH", "1")
    result = ui(empty)
    assert result.exit_code == 78 and "ao auth add-user" in result.output

    monkeypatch.delenv("AO_UI_AUTH")
    write_ws_config(empty.workspace, auth_block("enabled: true"))
    result = ui(empty)
    assert result.exit_code == 78 and "ao auth add-user" in result.output
    assert fake_uvicorn.calls == []


def test_auth_with_port_zero_exits_78(lenv: LaunchEnv, fake_uvicorn: RecordingUvicorn) -> None:
    result = ui(lenv, "--auth", "--auth-dir", str(lenv.store_dir), "--port", "0")
    assert result.exit_code == 78
    assert "--port 0" in result.output
    assert fake_uvicorn.calls == []


# --- AC 5: the enabled path ---


def test_auth_with_one_user_serves_an_authenticated_app(
    lenv: LaunchEnv, fake_uvicorn: RecordingUvicorn
) -> None:
    result = ui(lenv, "--auth", "--auth-dir", str(lenv.store_dir))
    assert result.exit_code == 0, result.output
    _args, kwargs = fake_uvicorn.calls[0]
    assert kwargs == {"host": "127.0.0.1", "port": PORT, "proxy_headers": False}
    assert runtime_of(serving_app(fake_uvicorn)) is not None


def test_trusted_proxies_reach_uvicorn(
    lenv: LaunchEnv, fake_uvicorn: RecordingUvicorn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AO_UI_AUTH_TRUSTED_PROXIES", "127.0.0.1")
    result = ui(lenv, "--auth", "--auth-dir", str(lenv.store_dir))
    assert result.exit_code == 0, result.output
    _args, kwargs = fake_uvicorn.calls[0]
    assert kwargs["proxy_headers"] is True and kwargs["forwarded_allow_ips"] == "127.0.0.1"


def test_non_loopback_with_auth_warns_plain_http_not_unauthenticated(
    lenv: LaunchEnv, fake_uvicorn: RecordingUvicorn
) -> None:
    result = ui(lenv, "--auth", "--auth-dir", str(lenv.store_dir), "--host", "0.0.0.0")
    assert result.exit_code == 0, result.output
    assert "not encrypted" in result.output
    assert UNAUTHENTICATED not in result.output


def test_the_served_service_denies_the_store_and_state_directories(
    lenv: LaunchEnv, fake_uvicorn: RecordingUvicorn, monkeypatch: pytest.MonkeyPatch
) -> None:
    import agent_orchestrator.ui.service as service_module

    real_service = service_module.DashboardService
    seen: list[list[str] | None] = []

    def spy(*args: object, **kwargs: object) -> object:
        seen.append(kwargs.get("denied_paths"))  # type: ignore[arg-type]
        return real_service(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(service_module, "DashboardService", spy)
    result = ui(lenv, "--auth", "--auth-dir", str(lenv.store_dir))
    assert result.exit_code == 0, result.output
    [denied] = seen
    assert denied is not None
    assert str(lenv.store_dir.resolve()) in denied
    assert str((lenv.store_dir / "state").resolve()) in denied


# --- AC 6: precedence ---


def test_no_auth_beats_the_environment(
    lenv: LaunchEnv, fake_uvicorn: RecordingUvicorn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AO_UI_AUTH", "1")
    monkeypatch.setenv("AO_AUTH_DIR", str(lenv.store_dir))
    monkeypatch.setenv("AO_AUTH_STATE_DIR", str(lenv.state_dir))
    result = ui(lenv, "--no-auth")
    assert result.exit_code == 0, result.output
    assert runtime_of(serving_app(fake_uvicorn)) is None
    assert "Note: dashboard authentication is disabled" in result.output


def test_env_zero_beats_the_config(
    lenv: LaunchEnv, fake_uvicorn: RecordingUvicorn, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_ws_config(lenv.workspace, auth_block("enabled: true"))
    for key, value in lenv.env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("AO_UI_AUTH", "0")
    result = ui(lenv)
    assert result.exit_code == 0, result.output
    assert runtime_of(serving_app(fake_uvicorn)) is None
    assert "Note: dashboard authentication is disabled" in result.output


# --- AC 7: --reload (security L3) ---


def test_reload_passes_the_same_kwargs_and_relays_the_cli_flags(
    lenv: LaunchEnv, fake_uvicorn: RecordingUvicorn
) -> None:
    result = ui(
        lenv, "--reload", "--auth", "--auth-totp", "required", "--auth-dir", str(lenv.store_dir)
    )
    assert result.exit_code == 0, result.output
    args, kwargs = fake_uvicorn.calls[0]
    assert args == ("agent_orchestrator.ui.app:create_app_from_env",)
    assert kwargs["reload"] is True and kwargs["factory"] is True
    assert kwargs["proxy_headers"] is False
    assert os.environ["AO_UI_AUTH"] == "1"
    assert os.environ["AO_UI_AUTH_TOTP"] == "required"
    assert os.environ["AO_AUTH_DIR"] == str(lenv.store_dir)
    assert os.environ["AO_UI_BOUND_PORT"] == str(PORT)


def test_reload_and_plain_kwargs_are_equal_with_trusted_proxies(
    lenv: LaunchEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AO_UI_AUTH_TRUSTED_PROXIES", "127.0.0.1")
    common = ("--auth", "--auth-dir", str(lenv.store_dir))
    plain, reloading = RecordingUvicorn(), RecordingUvicorn()
    monkeypatch.setitem(sys.modules, "uvicorn", plain)
    assert ui(lenv, *common).exit_code == 0
    monkeypatch.setitem(sys.modules, "uvicorn", reloading)
    assert ui(lenv, "--reload", *common).exit_code == 0
    reload_kwargs = reloading.calls[0][1]
    assert reload_kwargs["proxy_headers"] is True
    assert reload_kwargs["forwarded_allow_ips"] == "127.0.0.1"
    extras = {"reload", "factory"}
    assert {k: v for k, v in reload_kwargs.items() if k not in extras} == plain.calls[0][1]


def test_the_reloaded_child_builds_an_authenticated_app(
    lenv: LaunchEnv, fake_uvicorn: RecordingUvicorn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AO_UI_ALLOWED_HOSTS", "testserver")
    port = 9123
    result = ui(lenv, "--reload", "--auth", "--auth-dir", str(lenv.store_dir), "--port", str(port))
    assert result.exit_code == 0, result.output
    child_app = create_app_from_env()  # what uvicorn's reloader would import
    runtime = runtime_of(child_app)
    assert runtime is not None
    assert runtime.realm.cookie_name(secure=False) == f"ao_sid_{port}"
    with TestClient(child_app) as client:
        assert client.get("/api/runs").status_code == 401


def test_the_factory_requires_the_bound_port_when_auth_is_on(
    lenv: LaunchEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    for key, value in {
        **lenv.env,
        "AO_UI_AUTH": "1",
        "AO_UI_WORKSPACE": str(lenv.workspace),
    }.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(AuthConfigError, match="AO_UI_BOUND_PORT"):
        create_app_from_env()
    monkeypatch.setenv("AO_UI_BOUND_PORT", "not-a-port")
    with pytest.raises(AuthConfigError, match="AO_UI_BOUND_PORT"):
        create_app_from_env()


def test_the_factory_without_the_port_is_unchanged_when_auth_is_off(
    lenv: LaunchEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AO_UI_WORKSPACE", str(lenv.workspace))
    assert runtime_of(create_app_from_env()) is None


# --- AC 8: invalid config through ao ui ---


@pytest.mark.parametrize(
    ("block", "needle"),
    [
        (auth_block("enable: true"), "ui.auth.enable"),
        (auth_block("trusted_proxies: ['127.0.0.1']"), "AO_UI_AUTH_TRUSTED_PROXIES"),
        (auth_block("session_idle_minutes: 600"), "would weaken"),
    ],
    ids=["typo", "proxies-in-config", "loosening"],
)
def test_invalid_config_exits_78(
    lenv: LaunchEnv, fake_uvicorn: RecordingUvicorn, block: str, needle: str
) -> None:
    write_ws_config(lenv.workspace, block)
    result = ui(lenv, "--auth-dir", str(lenv.store_dir))
    assert result.exit_code == 78, result.output
    assert needle in result.output
    assert fake_uvicorn.calls == []


def test_unparseable_config_refuses_with_users_and_warns_without(
    tmp_path: Path, lenv: LaunchEnv, fake_uvicorn: RecordingUvicorn
) -> None:
    write_ws_config(lenv.workspace, "ui: [unclosed")
    refused = ui(lenv, "--auth-dir", str(lenv.store_dir))
    assert refused.exit_code == 78 and fake_uvicorn.calls == []

    empty = make_launch_env(tmp_path / "empty", users=())
    write_ws_config(empty.workspace, "ui: [unclosed")
    started = ui(empty, "--auth-dir", str(empty.store_dir))
    assert started.exit_code == 0, started.output
    assert "cannot be parsed" in started.output


# --- AC 9: the config flip (AC-45; S29) ---


def test_a_config_flip_to_disabled_is_refused_and_audited(
    lenv: LaunchEnv, fake_uvicorn: RecordingUvicorn
) -> None:
    config = write_ws_config(lenv.workspace, auth_block("enabled: true"))
    assert ui(lenv, "--auth-dir", str(lenv.store_dir)).exit_code == 0
    assert runtime_of(serving_app(fake_uvicorn)) is not None
    fake_uvicorn.calls.clear()

    write_ws_config(lenv.workspace, auth_block("enabled: false"))  # the `git pull` case
    result = ui(lenv, "--auth-dir", str(lenv.store_dir))
    assert result.exit_code == 78
    assert str(config) in result.output and "--no-auth" in result.output
    assert "AO_UI_AUTH=0" in result.output
    assert fake_uvicorn.calls == []
    assert audit_events(lenv.store_dir / "state") == ["auth.startup.disabled_by_config"]


def test_an_explicit_disable_after_the_flip_starts_with_the_note(
    lenv: LaunchEnv, fake_uvicorn: RecordingUvicorn, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_ws_config(lenv.workspace, auth_block("enabled: false"))
    flag = ui(lenv, "--no-auth", "--auth-dir", str(lenv.store_dir))
    assert flag.exit_code == 0 and "Note: dashboard authentication is disabled" in flag.output
    fake_uvicorn.calls.clear()

    for key, value in lenv.env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("AO_UI_AUTH", "0")
    env = ui(lenv)
    assert env.exit_code == 0 and "Note: dashboard authentication is disabled" in env.output


def test_a_config_disable_with_no_accounts_starts_unauthenticated(
    tmp_path: Path, fake_uvicorn: RecordingUvicorn
) -> None:
    empty = make_launch_env(tmp_path / "empty", users=())
    write_ws_config(empty.workspace, auth_block("enabled: false"))
    result = ui(empty, "--auth-dir", str(empty.store_dir))
    assert result.exit_code == 0, result.output
    assert runtime_of(serving_app(fake_uvicorn)) is None


def test_a_corrupt_store_with_a_config_disable_is_refused(
    lenv: LaunchEnv, fake_uvicorn: RecordingUvicorn
) -> None:
    write_ws_config(lenv.workspace, auth_block("enabled: false"))
    (lenv.store_dir / "users.json").write_text("{not json", encoding="utf-8")
    result = ui(lenv, "--auth-dir", str(lenv.store_dir))
    assert result.exit_code == 78 and "an unknown number of" in result.output
    assert fake_uvicorn.calls == []


def test_a_config_only_totp_downgrade_warns_and_audits(
    lenv: LaunchEnv, fake_uvicorn: RecordingUvicorn
) -> None:
    write_ws_config(lenv.workspace, auth_block("enabled: true", "totp: optional"))
    result = ui(lenv, "--auth-dir", str(lenv.store_dir))
    assert result.exit_code == 0, result.output
    assert "comes only from" in result.output
    assert audit_events(lenv.store_dir / "state") == ["auth.startup.totp_downgraded_by_config"]


def test_the_config_env_block_cannot_disable_auth(
    lenv: LaunchEnv, fake_uvicorn: RecordingUvicorn
) -> None:
    write_ws_config(lenv.workspace, 'env:\n  AO_UI_AUTH: "0"\n' + auth_block("enabled: true"))
    result = ui(lenv, "--auth-dir", str(lenv.store_dir))
    assert result.exit_code == 0, result.output
    assert runtime_of(serving_app(fake_uvicorn)) is not None


def test_the_factory_defaults_mirror_the_cli_defaults() -> None:
    """``ui/app.py`` cannot import ``cli``; this pins the mirrored constants (T-2wE08U)."""
    assert (FACTORY_DEFAULT_HOST, FACTORY_DEFAULT_PORT) == (UI_DEFAULT_HOST, UI_DEFAULT_PORT)
