"""Layered auth settings (T-PlEROT; HLD section 11.3, rows 1-5, 10, 12, 14, 15)."""

from __future__ import annotations

import dataclasses
import socket
from collections.abc import Callable, Mapping
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent_orchestrator.auth.constants import (
    DEFAULT_ADDRESS_THRESHOLD,
    DEFAULT_LOCKOUT_BASE_SECONDS,
    DEFAULT_LOCKOUT_MAX_SECONDS,
    DEFAULT_LOCKOUT_THRESHOLD,
    DEFAULT_MIN_PASSWORD_LENGTH,
    DEFAULT_SESSION_ABSOLUTE_HOURS,
    DEFAULT_SESSION_IDLE_MINUTES,
    MAX_QUOTED_CONFIG_CHARS,
    STATE_SUBDIR,
)
from agent_orchestrator.auth.errors import AuthConfigError
from agent_orchestrator.auth.model import TotpPolicy
from agent_orchestrator.auth.settings import (
    TIGHTEN_RULES,
    AuthCliOverrides,
    AuthSettings,
    ConfigProblem,
    ConfigProblemKind,
    ConfigRisk,
    Decision,
    DecisionAction,
    UIAuthConfig,
    decide,
    load_auth_block,
    resolve_auth_settings,
)

SENTINEL = "ZZ-SENTINEL-VALUE-ZZ"
CliKw = AuthCliOverrides


@pytest.fixture()
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / ".git").mkdir(parents=True)
    return root


@pytest.fixture()
def xdg_env(tmp_path: Path) -> dict[str, str]:
    return {
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
        "XDG_STATE_HOME": str(tmp_path / "xdg-state"),
    }


def write_config(ws: Path, text: str) -> Path:
    path = ws / ".ao" / "config.yaml"
    path.parent.mkdir(exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path.resolve()


def auth_block(*lines: str) -> str:
    return "ui:\n  auth:\n" + "".join(f"    {line}\n" for line in lines)


def counts(value: int | None) -> Callable[[Path], int | None]:
    return lambda _dir: value


class CountSpy:
    def __init__(self, value: int | None = 0) -> None:
        self.value = value
        self.calls: list[Path] = []

    def __call__(self, store_dir: Path) -> int | None:
        self.calls.append(store_dir)
        return self.value


def resolve(
    ws: Path | None,
    env: Mapping[str, str],
    cli: AuthCliOverrides | None = None,
    count: Callable[[Path], int | None] | None = None,
) -> AuthSettings:
    return resolve_auth_settings(
        cli=cli or AuthCliOverrides(),
        env=env,
        workspace_root=ws,
        count_users=count or counts(0),
    )


# ---------------------------------------------------------------------------
# AC 1: precedence and sources
# ---------------------------------------------------------------------------


def test_defaults_everywhere(ws: Path, xdg_env: dict[str, str]) -> None:
    s = resolve(ws, xdg_env)
    assert s.enabled is False
    assert s.totp is TotpPolicy.OFF
    assert s.session_idle_seconds == DEFAULT_SESSION_IDLE_MINUTES * 60
    assert s.session_absolute_seconds == DEFAULT_SESSION_ABSOLUTE_HOURS * 3600
    assert s.lockout_threshold == DEFAULT_LOCKOUT_THRESHOLD
    assert s.lockout_base_seconds == DEFAULT_LOCKOUT_BASE_SECONDS
    assert s.lockout_max_seconds == DEFAULT_LOCKOUT_MAX_SECONDS
    assert s.address_threshold == DEFAULT_ADDRESS_THRESHOLD
    assert s.min_password_length == DEFAULT_MIN_PASSWORD_LENGTH
    assert s.trusted_proxies == ()
    assert s.config_path is None
    assert s.config_risks == frozenset()
    assert s.warnings == ()
    assert set(s.sources.values()) == {"default"}


def test_enabled_precedence_cli_env_config_default(ws: Path, xdg_env: dict[str, str]) -> None:
    cfg = write_config(ws, auth_block("enabled: true"))
    # config only
    s = resolve(ws, xdg_env)
    assert (s.enabled, s.sources["enabled"]) == (True, f"config:{cfg}")
    # env beats config
    s = resolve(ws, {**xdg_env, "AO_UI_AUTH": "0"})
    assert (s.enabled, s.sources["enabled"]) == (False, "env:AO_UI_AUTH")
    # cli beats env
    s = resolve(ws, {**xdg_env, "AO_UI_AUTH": "0"}, CliKw(enabled=True))
    assert (s.enabled, s.sources["enabled"]) == (True, "cli")


def test_totp_precedence(ws: Path, xdg_env: dict[str, str]) -> None:
    cfg = write_config(ws, auth_block("totp: optional"))
    s = resolve(ws, xdg_env)
    assert (s.totp, s.sources["totp"]) == (TotpPolicy.OPTIONAL, f"config:{cfg}")
    s = resolve(ws, {**xdg_env, "AO_UI_AUTH_TOTP": "REQUIRED"})
    assert (s.totp, s.sources["totp"]) == (TotpPolicy.REQUIRED, "env:AO_UI_AUTH_TOTP")
    s = resolve(ws, {**xdg_env, "AO_UI_AUTH_TOTP": "required"}, CliKw(totp=TotpPolicy.OFF))
    assert (s.totp, s.sources["totp"]) == (TotpPolicy.OFF, "cli")


def test_store_dir_precedence(ws: Path, tmp_path: Path, xdg_env: dict[str, str]) -> None:
    cfg_store, env_store, cli_store = (tmp_path / n for n in ("c-store", "e-store", "k-store"))
    cfg = write_config(ws, auth_block(f"store_dir: {cfg_store}"))
    s = resolve(ws, xdg_env)
    assert (s.store_dir, s.sources["store_dir"]) == (cfg_store.resolve(), f"config:{cfg}")
    s = resolve(ws, {**xdg_env, "AO_AUTH_DIR": str(env_store)})
    assert (s.store_dir, s.sources["store_dir"]) == (env_store.resolve(), "env:AO_AUTH_DIR")
    s = resolve(ws, {**xdg_env, "AO_AUTH_DIR": str(env_store)}, CliKw(store_dir=str(cli_store)))
    assert (s.store_dir, s.sources["store_dir"]) == (cli_store.resolve(), "cli")


@pytest.mark.parametrize(
    ("field", "var", "env_value", "attr", "expected_env"),
    [
        ("session_idle_minutes", "AO_UI_AUTH_IDLE_MINUTES", "20", "session_idle_seconds", 1200),
        (
            "session_absolute_hours",
            "AO_UI_AUTH_ABSOLUTE_HOURS",
            "6",
            "session_absolute_seconds",
            6 * 3600,
        ),
        ("lockout_threshold", "AO_UI_AUTH_LOCKOUT_THRESHOLD", "3", "lockout_threshold", 3),
        (
            "lockout_base_seconds",
            "AO_UI_AUTH_LOCKOUT_BASE_SECONDS",
            "60",
            "lockout_base_seconds",
            60,
        ),
        (
            "lockout_max_seconds",
            "AO_UI_AUTH_LOCKOUT_MAX_SECONDS",
            "1800",
            "lockout_max_seconds",
            1800,
        ),
        ("address_threshold", "AO_UI_AUTH_ADDRESS_THRESHOLD", "10", "address_threshold", 10),
        ("min_password_length", "AO_UI_AUTH_MIN_PASSWORD_LENGTH", "16", "min_password_length", 16),
    ],
)
def test_numeric_precedence_env_over_config(
    ws: Path,
    xdg_env: dict[str, str],
    field: str,
    var: str,
    env_value: str,
    attr: str,
    expected_env: int,
) -> None:
    # A tightening config value in the scale of each field (lower-stricter and higher-stricter).
    stricter_lower = dict(TIGHTEN_RULES)[field].value == "lower"
    cfg_value = {
        "session_idle_minutes": 10,
        "session_absolute_hours": 4,
        "lockout_threshold": 2,
        "lockout_base_seconds": 45,
        "lockout_max_seconds": 1200,
        "address_threshold": 5,
        "min_password_length": 14,
    }[field]
    assert stricter_lower == (
        field in {"session_idle_minutes", "session_absolute_hours"} or "threshold" in field
    )
    cfg = write_config(ws, auth_block(f"{field}: {cfg_value}"))
    s = resolve(ws, xdg_env)
    assert s.sources[field] == f"config:{cfg}"
    s = resolve(ws, {**xdg_env, var: env_value})
    assert getattr(s, attr) == expected_env
    assert s.sources[field] == f"env:{var}"
    # The CLI has no flag for these fields: env is the top of the chain.


def test_hub_never_reads_cwd_config(
    ws: Path, xdg_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    write_config(ws, auth_block("enabled: true", "totp: required"))
    monkeypatch.chdir(ws)
    s = resolve(None, xdg_env)
    assert s.enabled is False
    assert s.config_path is None
    assert set(s.sources.values()) == {"default"}


def test_config_path_is_found_by_walking_up(ws: Path, xdg_env: dict[str, str]) -> None:
    cfg = write_config(ws, auth_block("enabled: true"))
    sub = ws / "a" / "b"
    sub.mkdir(parents=True)
    s = resolve(sub, xdg_env)
    assert s.config_path == cfg
    assert s.enabled is True


def test_config_without_auth_block_is_unaffected(ws: Path, xdg_env: dict[str, str]) -> None:
    cfg = write_config(ws, "defaults:\n  model: x\n")
    s = resolve(ws, xdg_env)
    assert s.config_path == cfg
    assert set(s.sources.values()) == {"default"}


# ---------------------------------------------------------------------------
# AC 2: env parsing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("word", ["1", "true", "yes", "on", "TRUE", "Yes", " On "])
def test_env_true_words(xdg_env: dict[str, str], word: str) -> None:
    assert resolve(None, {**xdg_env, "AO_UI_AUTH": word}).enabled is True


@pytest.mark.parametrize("word", ["0", "false", "no", "off", "FALSE", "No", " OFF "])
def test_env_false_words(xdg_env: dict[str, str], word: str) -> None:
    assert resolve(None, {**xdg_env, "AO_UI_AUTH": word}).enabled is False


@pytest.mark.parametrize("bad", ["", " ", "maybe"])
def test_env_bad_bool_names_variable(xdg_env: dict[str, str], bad: str) -> None:
    with pytest.raises(AuthConfigError, match="AO_UI_AUTH") as err:
        resolve(None, {**xdg_env, "AO_UI_AUTH": bad})
    if bad.strip():
        assert "true" in str(err.value) and "false" in str(err.value)  # lists accepted words


@pytest.mark.parametrize(
    "var",
    [
        "AO_UI_AUTH_TOTP",
        "AO_AUTH_DIR",
        "AO_AUTH_STATE_DIR",
        "AO_UI_AUTH_IDLE_MINUTES",
        "AO_UI_AUTH_TRUSTED_PROXIES",
        "AO_UI_AUTH_TOTP_ISSUER",
    ],
)
def test_every_env_var_rejects_empty(xdg_env: dict[str, str], var: str) -> None:
    with pytest.raises(AuthConfigError, match=f"{var} is set but empty"):
        resolve(None, {**xdg_env, var: "  "})


@pytest.mark.parametrize(
    ("var", "bad"),
    [
        ("AO_UI_AUTH_IDLE_MINUTES", "0"),
        ("AO_UI_AUTH_IDLE_MINUTES", "1441"),
        ("AO_UI_AUTH_ABSOLUTE_HOURS", "721"),
        ("AO_UI_AUTH_LOCKOUT_THRESHOLD", "101"),
        ("AO_UI_AUTH_LOCKOUT_BASE_SECONDS", "3601"),
        ("AO_UI_AUTH_LOCKOUT_MAX_SECONDS", "86401"),
        ("AO_UI_AUTH_ADDRESS_THRESHOLD", "10001"),
        ("AO_UI_AUTH_MIN_PASSWORD_LENGTH", "7"),
        ("AO_UI_AUTH_MIN_PASSWORD_LENGTH", "257"),
        ("AO_UI_AUTH_IDLE_MINUTES", "-5"),
        ("AO_UI_AUTH_IDLE_MINUTES", "ten"),
        ("AO_UI_AUTH_IDLE_MINUTES", "1_0"),
        ("AO_UI_AUTH_IDLE_MINUTES", "1.5"),
    ],
)
def test_env_integers_out_of_range_or_malformed(
    xdg_env: dict[str, str], var: str, bad: str
) -> None:
    with pytest.raises(AuthConfigError, match=var):
        resolve(None, {**xdg_env, var: bad})


def test_env_integer_bounds_are_inclusive(xdg_env: dict[str, str]) -> None:
    s = resolve(
        None, {**xdg_env, "AO_UI_AUTH_IDLE_MINUTES": "1440", "AO_UI_AUTH_MIN_PASSWORD_LENGTH": "8"}
    )
    assert s.session_idle_seconds == 1440 * 60
    assert s.min_password_length == 8


def test_env_totp_invalid_lists_policies(xdg_env: dict[str, str]) -> None:
    with pytest.raises(AuthConfigError, match="AO_UI_AUTH_TOTP") as err:
        resolve(None, {**xdg_env, "AO_UI_AUTH_TOTP": "sometimes"})
    assert "off, optional, required" in str(err.value)


def test_trusted_proxies_env_is_normalized(xdg_env: dict[str, str]) -> None:
    s = resolve(None, {**xdg_env, "AO_UI_AUTH_TRUSTED_PROXIES": "127.0.0.1, ::1"})
    assert s.trusted_proxies == ("127.0.0.1", "::1")
    assert s.sources["trusted_proxies"] == "env:AO_UI_AUTH_TRUSTED_PROXIES"


def test_trusted_proxies_ipv6_is_canonicalized(xdg_env: dict[str, str]) -> None:
    s = resolve(None, {**xdg_env, "AO_UI_AUTH_TRUSTED_PROXIES": "0:0:0:0:0:0:0:1"})
    assert s.trusted_proxies == ("::1",)


@pytest.mark.parametrize("bad", ["10.0.0.0/8", "not-an-ip", "127.0.0.1,,::1", "127.0.0.1,"])
def test_trusted_proxies_rejects_non_ip(xdg_env: dict[str, str], bad: str) -> None:
    with pytest.raises(AuthConfigError, match="CIDR"):
        resolve(None, {**xdg_env, "AO_UI_AUTH_TRUSTED_PROXIES": bad})


def test_long_bad_value_is_quoted_with_a_cap(xdg_env: dict[str, str]) -> None:
    for var in ("AO_UI_AUTH", "AO_UI_AUTH_TOTP", "AO_UI_AUTH_IDLE_MINUTES"):
        with pytest.raises(AuthConfigError) as err:
            resolve(None, {**xdg_env, var: "q" * 200})
        message = str(err.value)
        assert "q" * MAX_QUOTED_CONFIG_CHARS in message
        assert "q" * (MAX_QUOTED_CONFIG_CHARS + 1) not in message
    with pytest.raises(AuthConfigError) as err:
        resolve(None, {**xdg_env, "AO_UI_AUTH_TRUSTED_PROXIES": "q" * 200})
    assert "q" * (MAX_QUOTED_CONFIG_CHARS + 1) not in str(err.value)


def test_env_errors_win_even_with_auth_explicitly_disabled(
    ws: Path, xdg_env: dict[str, str]
) -> None:
    write_config(ws, auth_block("enabled: true"))
    with pytest.raises(AuthConfigError, match="AO_UI_AUTH_TOTP"):
        resolve(ws, {**xdg_env, "AO_UI_AUTH": "0", "AO_UI_AUTH_TOTP": "bogus"})


def test_issuer_from_env_and_length_cap(xdg_env: dict[str, str]) -> None:
    s = resolve(None, {**xdg_env, "AO_UI_AUTH_TOTP_ISSUER": "MyCo"})
    assert (s.totp_issuer, s.sources["totp_issuer"]) == ("MyCo", "env:AO_UI_AUTH_TOTP_ISSUER")
    with pytest.raises(AuthConfigError, match="AO_UI_AUTH_TOTP_ISSUER"):
        resolve(None, {**xdg_env, "AO_UI_AUTH_TOTP_ISSUER": "x" * 65})


# ---------------------------------------------------------------------------
# AC 3: YAML `off`
# ---------------------------------------------------------------------------


def test_yaml_bare_off_is_the_off_policy(ws: Path, xdg_env: dict[str, str]) -> None:
    write_config(ws, auth_block("totp: off"))
    assert resolve(ws, xdg_env).totp is TotpPolicy.OFF


def test_yaml_quoted_policy_values(ws: Path, xdg_env: dict[str, str]) -> None:
    write_config(ws, auth_block('totp: "required"'))
    assert resolve(ws, xdg_env).totp is TotpPolicy.REQUIRED
    write_config(ws, auth_block('totp: "off"'))
    assert resolve(ws, xdg_env).totp is TotpPolicy.OFF


def test_yaml_bare_on_is_refused(ws: Path, xdg_env: dict[str, str]) -> None:
    write_config(ws, auth_block("totp: on"))
    with pytest.raises(AuthConfigError, match=r"ui\.auth\.totp"):
        resolve(ws, xdg_env)


# ---------------------------------------------------------------------------
# AC 4: decide()
# ---------------------------------------------------------------------------

UNPARSEABLE = ConfigProblem(ConfigProblemKind.UNPARSEABLE, "bad yaml")
INVALID = ConfigProblem(ConfigProblemKind.INVALID, "ui.auth.enable: nope")


@pytest.mark.parametrize("problem", [UNPARSEABLE, INVALID])
@pytest.mark.parametrize("count", [0, 3, None])
def test_decide_explicit_false_ignores_the_config(
    problem: ConfigProblem, count: int | None
) -> None:
    d = decide(False, problem, count)
    assert d.action is DecisionAction.IGNORE_CONFIG
    assert "explicitly disabled" in d.message


@pytest.mark.parametrize("problem", [UNPARSEABLE, INVALID])
@pytest.mark.parametrize("count", [0, 3, None])
def test_decide_explicit_true_refuses(problem: ConfigProblem, count: int | None) -> None:
    assert decide(True, problem, count).action is DecisionAction.REFUSE


def test_decide_unparseable_with_no_accounts_continues_with_a_warning() -> None:
    d = decide(None, UNPARSEABLE, 0)
    assert d.action is DecisionAction.CONTINUE_WITHOUT_CONFIG
    assert d.message


@pytest.mark.parametrize("count", [3, None])
def test_decide_unparseable_with_accounts_or_unknown_count_refuses(count: int | None) -> None:
    assert decide(None, UNPARSEABLE, count).action is DecisionAction.REFUSE


@pytest.mark.parametrize("count", [0, 3, None])
def test_decide_invalid_refuses(count: int | None) -> None:
    assert decide(None, INVALID, count).action is DecisionAction.REFUSE


@pytest.mark.parametrize("explicit", [True, False, None])
@pytest.mark.parametrize("count", [0, 3, None])
def test_decide_no_problem_uses_config(explicit: bool | None, count: int | None) -> None:
    assert decide(explicit, None, count) == Decision(DecisionAction.USE_CONFIG)


BROKEN_YAML = "ui: [unclosed\n  auth: {\n"
INVALID_BLOCK = auth_block("enable: true")


@pytest.mark.parametrize("count", [0, 3, None])
def test_e2e_unparseable_without_explicit_value(
    ws: Path, xdg_env: dict[str, str], count: int | None
) -> None:
    cfg = write_config(ws, BROKEN_YAML)
    spy = CountSpy(count)
    if count == 0:
        s = resolve(ws, xdg_env, count=spy)
        assert s.enabled is False
        assert s.config_path == cfg
        assert any(str(cfg) in w and "cannot be parsed" in w for w in s.warnings)
    else:
        with pytest.raises(AuthConfigError, match="cannot be parsed") as err:
            resolve(ws, xdg_env, count=spy)
        assert str(cfg) in str(err.value)
        assert "--no-auth" in str(err.value)
        if count is None:
            assert "unknown" in str(err.value)
    # the probe looked at the default store (no CLI/env override here)
    assert spy.calls == [Path(xdg_env["XDG_CONFIG_HOME"]).resolve() / "ao" / "auth"]


def test_e2e_unparseable_probe_uses_cli_then_env_store(
    ws: Path, tmp_path: Path, xdg_env: dict[str, str]
) -> None:
    write_config(ws, BROKEN_YAML)
    spy = CountSpy(0)
    resolve(ws, {**xdg_env, "AO_AUTH_DIR": str(tmp_path / "e")}, count=spy)
    resolve(
        ws,
        {**xdg_env, "AO_AUTH_DIR": str(tmp_path / "e")},
        CliKw(store_dir=str(tmp_path / "k")),
        count=spy,
    )
    assert spy.calls == [(tmp_path / "e").resolve(), (tmp_path / "k").resolve()]


@pytest.mark.parametrize("count", [0, 3, None])
@pytest.mark.parametrize("text", [BROKEN_YAML, INVALID_BLOCK])
def test_e2e_explicit_disable_ignores_problem_with_warning_naming_source(
    ws: Path, xdg_env: dict[str, str], count: int | None, text: str
) -> None:
    cfg = write_config(ws, text)
    spy = CountSpy(count)
    s = resolve(ws, {**xdg_env, "AO_UI_AUTH": "0"}, count=spy)
    assert s.enabled is False and s.config_risks == frozenset()
    assert any("AO_UI_AUTH" in w and str(cfg) in w for w in s.warnings)
    s = resolve(ws, xdg_env, CliKw(enabled=False), count=spy)
    assert any(w.endswith("by cli") for w in s.warnings)
    assert spy.calls == []  # an explicit value never needs the user count


@pytest.mark.parametrize("text", [BROKEN_YAML, INVALID_BLOCK])
def test_e2e_explicit_enable_with_problem_refuses(
    ws: Path, xdg_env: dict[str, str], text: str
) -> None:
    write_config(ws, text)
    spy = CountSpy(0)
    with pytest.raises(AuthConfigError):
        resolve(ws, {**xdg_env, "AO_UI_AUTH": "1"}, count=spy)
    with pytest.raises(AuthConfigError):
        resolve(ws, xdg_env, CliKw(enabled=True), count=spy)
    assert spy.calls == []


@pytest.mark.parametrize("count", [0, 3, None])
def test_e2e_invalid_block_refuses_whatever_the_count(
    ws: Path, xdg_env: dict[str, str], count: int | None
) -> None:
    write_config(ws, INVALID_BLOCK)
    spy = CountSpy(count)
    with pytest.raises(AuthConfigError, match=r"ui\.auth\.enable"):
        resolve(ws, xdg_env, count=spy)
    assert spy.calls == []  # INVALID never needs a count


# ---------------------------------------------------------------------------
# AC 5: unknown keys, malformed structure, value-free messages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "ui: 5\n",
        "ui:\n  - a\n",
        "ui:\n  auth: yes\n",
        "ui:\n  auth: [1, 2]\n",
    ],
)
def test_non_mapping_ui_or_auth_is_invalid(tmp_path: Path, text: str) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(text)
    cfg, problem = load_auth_block(path)
    assert cfg is None
    assert problem is not None and problem.kind is ConfigProblemKind.INVALID


@pytest.mark.parametrize(
    "text", ["", "# only a comment\n", "ui:\n", "ui:\n  auth:\n", "other: 1\n"]
)
def test_absent_block_is_neither_config_nor_problem(tmp_path: Path, text: str) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(text)
    assert load_auth_block(path) == (None, None)


def test_unparseable_variants(tmp_path: Path) -> None:
    cases: list[Path] = []
    for name, data in {
        "yaml": b"ui: [unclosed\n",
        "latin": b"ui:\n  auth:\n    totp_issuer: \xff\xfe\n",
        "list": b"- a\n- b\n",
        "scalar": b"just text\n",
    }.items():
        path = tmp_path / f"{name}.yaml"
        path.write_bytes(data)
        cases.append(path)
    cases.append(tmp_path / "missing.yaml")
    cases.append(tmp_path)  # a directory: OSError on read
    for path in cases:
        cfg, problem = load_auth_block(path)
        assert cfg is None
        assert problem is not None and problem.kind is ConfigProblemKind.UNPARSEABLE, path


def test_yaml_syntax_error_message_has_position_not_content(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(f"ui: [{SENTINEL}\nother: {SENTINEL} {{\n")
    _cfg, problem = load_auth_block(path)
    assert problem is not None
    assert SENTINEL not in problem.message
    assert "line" in problem.message


def test_typo_key_error_names_the_key(ws: Path, xdg_env: dict[str, str]) -> None:
    write_config(ws, auth_block("enable: true"))
    with pytest.raises(AuthConfigError) as err:
        resolve(ws, xdg_env)
    assert "ui.auth.enable" in str(err.value)


@pytest.mark.parametrize(
    "line",
    [
        f"enabled: {SENTINEL}",
        f"totp: {SENTINEL}",
        f"session_idle_minutes: {SENTINEL}",
        f"lockout_threshold: {SENTINEL * 3}",
        f"store_dir: [{SENTINEL}]",
        f"totp_issuer: [{SENTINEL}]",
        f"trusted_proxies: {SENTINEL}",
        f"{SENTINEL}: 1",
        "totp: on",
        "session_idle_minutes: 99999",
    ],
)
def test_invalid_values_are_never_echoed(tmp_path: Path, line: str) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(auth_block(line))
    cfg, problem = load_auth_block(path)
    assert cfg is None and problem is not None
    key = line.split(":")[0]
    if key != SENTINEL:  # an unknown key's *name* is reported on purpose (it is the typo)
        assert SENTINEL not in problem.message
    assert problem.message.startswith("ui.auth.")


def test_validation_error_does_not_echo_input() -> None:
    with pytest.raises(ValidationError) as err:
        UIAuthConfig.model_validate({"enabled": SENTINEL, "lockout_threshold": SENTINEL})
    assert SENTINEL not in str(err.value)
    assert all("input" not in e for e in err.value.errors(include_input=False))


def test_model_rejects_unknown_keys() -> None:
    with pytest.raises(ValidationError):
        UIAuthConfig.model_validate({"bogus": 1})


@pytest.mark.parametrize("value", ["", "a" * 65])
def test_issuer_bounds_in_config(tmp_path: Path, value: str) -> None:
    path = tmp_path / "c.yaml"
    path.write_text(auth_block(f'totp_issuer: "{value}"'))
    cfg, problem = load_auth_block(path)
    assert cfg is None and problem is not None


# ---------------------------------------------------------------------------
# AC 6: tighten-only (AC-38, row 12)
# ---------------------------------------------------------------------------

LOOSER = {
    "session_idle_minutes": 31,
    "session_absolute_hours": 13,
    "lockout_threshold": 6,
    "address_threshold": 21,
    "lockout_base_seconds": 29,
    "lockout_max_seconds": 899,
    "min_password_length": 11,
}
TIGHTER = {
    "session_idle_minutes": 29,
    "session_absolute_hours": 11,
    "lockout_threshold": 4,
    "address_threshold": 19,
    "lockout_base_seconds": 31,
    "lockout_max_seconds": 901,
    "min_password_length": 13,
}
DEFAULT_OF = {
    "session_idle_minutes": DEFAULT_SESSION_IDLE_MINUTES,
    "session_absolute_hours": DEFAULT_SESSION_ABSOLUTE_HOURS,
    "lockout_threshold": DEFAULT_LOCKOUT_THRESHOLD,
    "address_threshold": DEFAULT_ADDRESS_THRESHOLD,
    "lockout_base_seconds": DEFAULT_LOCKOUT_BASE_SECONDS,
    "lockout_max_seconds": DEFAULT_LOCKOUT_MAX_SECONDS,
    "min_password_length": DEFAULT_MIN_PASSWORD_LENGTH,
}
ENV_OF = {
    "session_idle_minutes": "AO_UI_AUTH_IDLE_MINUTES",
    "session_absolute_hours": "AO_UI_AUTH_ABSOLUTE_HOURS",
    "lockout_threshold": "AO_UI_AUTH_LOCKOUT_THRESHOLD",
    "address_threshold": "AO_UI_AUTH_ADDRESS_THRESHOLD",
    "lockout_base_seconds": "AO_UI_AUTH_LOCKOUT_BASE_SECONDS",
    "lockout_max_seconds": "AO_UI_AUTH_LOCKOUT_MAX_SECONDS",
    "min_password_length": "AO_UI_AUTH_MIN_PASSWORD_LENGTH",
}
FIELDS = [field for field, _ in TIGHTEN_RULES]


def test_tighten_rules_cover_exactly_the_seven_fields() -> None:
    assert set(FIELDS) == set(LOOSER) == set(TIGHTER) == set(DEFAULT_OF) == set(ENV_OF)
    assert len(TIGHTEN_RULES) == 7


@pytest.mark.parametrize("field", FIELDS)
def test_loosening_config_is_refused_naming_field_default_and_alternative(
    ws: Path, xdg_env: dict[str, str], field: str
) -> None:
    write_config(ws, auth_block(f"{field}: {LOOSER[field]}"))
    with pytest.raises(AuthConfigError) as err:
        resolve(ws, xdg_env)
    message = str(err.value)
    assert f"ui.auth.{field}" in message
    assert f"({DEFAULT_OF[field]})" in message
    assert "env/CLI" in message
    assert ENV_OF[field] in message


@pytest.mark.parametrize("field", FIELDS)
def test_equal_and_tightening_config_values_are_accepted(
    ws: Path, xdg_env: dict[str, str], field: str
) -> None:
    for value in (DEFAULT_OF[field], TIGHTER[field]):
        write_config(ws, auth_block(f"{field}: {value}"))
        assert resolve(ws, xdg_env).sources[field].startswith("config:")


@pytest.mark.parametrize("field", FIELDS)
def test_the_same_loosening_value_is_accepted_through_env(
    xdg_env: dict[str, str], field: str
) -> None:
    s = resolve(None, {**xdg_env, ENV_OF[field]: str(LOOSER[field])})
    assert s.sources[field] == f"env:{ENV_OF[field]}"


def test_loose_config_is_refused_even_when_env_overrides_the_field(
    ws: Path, xdg_env: dict[str, str]
) -> None:
    write_config(ws, auth_block("lockout_threshold: 50"))
    with pytest.raises(AuthConfigError, match="ui.auth.lockout_threshold"):
        resolve(ws, {**xdg_env, "AO_UI_AUTH_LOCKOUT_THRESHOLD": "3"})


@pytest.mark.parametrize("value", ["[]", "['127.0.0.1']", "['::1', '10.0.0.1']"])
def test_trusted_proxies_in_config_is_refused_for_any_value(
    ws: Path, xdg_env: dict[str, str], value: str
) -> None:
    write_config(ws, auth_block(f"trusted_proxies: {value}"))
    with pytest.raises(AuthConfigError, match="AO_UI_AUTH_TRUSTED_PROXIES"):
        resolve(ws, xdg_env)


def test_config_enabled_true_and_totp_required_are_accepted(
    ws: Path, xdg_env: dict[str, str]
) -> None:
    cfg = write_config(ws, auth_block("enabled: true", "totp: required"))
    s = resolve(ws, xdg_env)
    assert (s.enabled, s.totp) == (True, TotpPolicy.REQUIRED)
    assert s.sources["enabled"] == s.sources["totp"] == f"config:{cfg}"


# ---------------------------------------------------------------------------
# AC 7: store_dir (row 10)
# ---------------------------------------------------------------------------


def test_config_relative_store_dir_is_refused(ws: Path, xdg_env: dict[str, str]) -> None:
    write_config(ws, auth_block("store_dir: auth-store"))
    with pytest.raises(AuthConfigError, match="absolute"):
        resolve(ws, xdg_env)


def test_config_store_dir_inside_workspace_is_refused(ws: Path, xdg_env: dict[str, str]) -> None:
    write_config(ws, auth_block(f"store_dir: {ws / 'secrets'}"))
    with pytest.raises(AuthConfigError, match="inside the workspace"):
        resolve(ws, xdg_env)


def test_config_store_dir_reached_through_a_symlink_into_the_workspace_is_refused(
    ws: Path, tmp_path: Path, xdg_env: dict[str, str]
) -> None:
    (ws / "real").mkdir()
    link = tmp_path / "outside-link"
    link.symlink_to(ws / "real")
    write_config(ws, auth_block(f"store_dir: {link}"))
    with pytest.raises(AuthConfigError, match="inside the workspace"):
        resolve(ws, xdg_env)


def test_config_store_dir_equal_to_the_workspace_is_refused(
    ws: Path, xdg_env: dict[str, str]
) -> None:
    write_config(ws, auth_block(f"store_dir: {ws}"))
    with pytest.raises(AuthConfigError, match="inside the workspace"):
        resolve(ws, xdg_env)


def test_config_store_dir_outside_the_workspace_is_accepted_and_flagged_as_config(
    ws: Path, tmp_path: Path, xdg_env: dict[str, str]
) -> None:
    write_config(ws, auth_block(f"store_dir: {tmp_path / 'safe'}"))
    s = resolve(ws, xdg_env)
    assert s.store_dir_from_config is True
    assert s.store_dir == (tmp_path / "safe").resolve()


def test_non_config_store_dirs_are_not_flagged_as_config(
    ws: Path, tmp_path: Path, xdg_env: dict[str, str]
) -> None:
    assert resolve(ws, xdg_env).store_dir_from_config is False
    s = resolve(ws, {**xdg_env, "AO_AUTH_DIR": str(tmp_path / "e")})
    assert s.store_dir_from_config is False


def test_cli_and_env_store_dir_inside_the_workspace_only_warn(
    ws: Path, xdg_env: dict[str, str]
) -> None:
    inside = ws / "store"
    for s in (
        resolve(ws, {**xdg_env, "AO_AUTH_DIR": str(inside)}),
        resolve(ws, xdg_env, CliKw(store_dir=str(inside))),
    ):
        assert any("inside the served workspace" in w for w in s.warnings)


def test_relative_cli_store_dir_resolves_against_cwd(
    ws: Path, xdg_env: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    s = resolve(None, xdg_env, CliKw(store_dir="rel-store"))
    assert s.store_dir == (tmp_path / "rel-store").resolve()


def test_default_store_inside_a_home_workspace_warns_not_errors(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    (home / ".git").mkdir(parents=True)
    env = {"XDG_CONFIG_HOME": str(home / ".config"), "XDG_STATE_HOME": str(home / ".state")}
    s = resolve(home, env)
    assert s.store_dir == (home / ".config" / "ao" / "auth").resolve()
    assert any("inside the served workspace" in w for w in s.warnings)


def test_default_store_follows_xdg_config_home_in_the_env_mapping(
    xdg_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "/definitely/not/used")
    s = resolve(None, xdg_env)
    assert s.store_dir == Path(xdg_env["XDG_CONFIG_HOME"]).resolve() / "ao" / "auth"


def test_tilde_in_env_store_dir_is_expanded(
    xdg_env: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    s = resolve(None, {**xdg_env, "AO_AUTH_DIR": "~/x"})
    assert s.store_dir == (tmp_path / "x").resolve()


def test_tilde_in_config_store_dir_counts_as_absolute(
    ws: Path, xdg_env: dict[str, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    write_config(ws, auth_block("store_dir: ~/cfg-store"))
    s = resolve(ws, xdg_env)
    assert s.store_dir == (tmp_path / "cfg-store").resolve()


def test_unknown_user_tilde_is_a_config_error(
    xdg_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self: Path) -> Path:
        raise RuntimeError("no home")

    monkeypatch.setattr(Path, "expanduser", boom)
    with pytest.raises(AuthConfigError, match="home directory"):
        resolve(None, {**xdg_env, "AO_AUTH_DIR": "~nobody/x"})


# ---------------------------------------------------------------------------
# AC 8: state_dir
# ---------------------------------------------------------------------------


def test_state_dir_from_env(xdg_env: dict[str, str], tmp_path: Path) -> None:
    s = resolve(None, {**xdg_env, "AO_AUTH_STATE_DIR": str(tmp_path / "st")})
    assert s.state_dir == (tmp_path / "st").resolve()
    assert s.sources["state_dir"] == "env:AO_AUTH_STATE_DIR"


def test_state_dir_env_wins_over_an_overridden_store(
    xdg_env: dict[str, str], tmp_path: Path
) -> None:
    env = {
        **xdg_env,
        "AO_AUTH_DIR": str(tmp_path / "s"),
        "AO_AUTH_STATE_DIR": str(tmp_path / "st"),
    }
    assert resolve(None, env).state_dir == (tmp_path / "st").resolve()


@pytest.mark.parametrize("via", ["cli", "env", "config"])
def test_overridden_store_puts_state_under_it(
    ws: Path, tmp_path: Path, xdg_env: dict[str, str], via: str
) -> None:
    store = tmp_path / "store"
    env, cli = dict(xdg_env), AuthCliOverrides()
    if via == "cli":
        cli = CliKw(store_dir=str(store))
    elif via == "env":
        env["AO_AUTH_DIR"] = str(store)
    else:
        write_config(ws, auth_block(f"store_dir: {store}"))
    s = resolve(ws, env, cli)
    assert s.state_dir == store.resolve() / STATE_SUBDIR
    assert s.sources["state_dir"] == "derived"


def test_default_state_dir_follows_xdg_state_home_from_the_env_mapping(
    xdg_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", "/definitely/not/used")
    s = resolve(None, xdg_env)
    assert s.state_dir == Path(xdg_env["XDG_STATE_HOME"]).resolve() / "ao" / "auth"
    assert s.sources["state_dir"] == "default"


# ---------------------------------------------------------------------------
# AC 9: cross-field
# ---------------------------------------------------------------------------


def test_lockout_base_above_max_is_refused(xdg_env: dict[str, str]) -> None:
    env = {
        **xdg_env,
        "AO_UI_AUTH_LOCKOUT_BASE_SECONDS": "1000",
        "AO_UI_AUTH_LOCKOUT_MAX_SECONDS": "999",
    }
    with pytest.raises(AuthConfigError, match="lockout_base_seconds"):
        resolve(None, env)


def test_lockout_base_equal_to_max_is_allowed(xdg_env: dict[str, str]) -> None:
    env = {
        **xdg_env,
        "AO_UI_AUTH_LOCKOUT_BASE_SECONDS": "900",
        "AO_UI_AUTH_LOCKOUT_MAX_SECONDS": "900",
    }
    assert resolve(None, env).lockout_base_seconds == 900


@pytest.mark.parametrize("issuer", ["ao:evil", "bad\x07bell", "tab\there"])
def test_bad_issuer_is_refused(xdg_env: dict[str, str], issuer: str) -> None:
    with pytest.raises(AuthConfigError, match="totp_issuer"):
        resolve(None, {**xdg_env, "AO_UI_AUTH_TOTP_ISSUER": issuer})


def test_bad_issuer_from_config_is_refused(ws: Path, xdg_env: dict[str, str]) -> None:
    write_config(ws, auth_block('totp_issuer: "a:b"'))
    with pytest.raises(AuthConfigError, match="totp_issuer"):
        resolve(ws, xdg_env)


def test_default_issuer_uses_the_short_hostname(
    xdg_env: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: "devbox.example.com")
    s = resolve(None, xdg_env)
    assert s.totp_issuer == "ao@devbox"
    assert s.sources["totp_issuer"] == "default"


@pytest.mark.parametrize(
    ("hostname", "expected"),
    [
        ("", "ao@localhost"),
        (".weird", "ao@localhost"),
        ("a:b", "ao@a-b"),
        ("h" * 80, "ao@" + "h" * 61),
    ],
)
def test_default_issuer_is_always_valid(
    xdg_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, hostname: str, expected: str
) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: hostname)
    assert resolve(None, xdg_env).totp_issuer == expected


def test_issuer_from_config(ws: Path, xdg_env: dict[str, str]) -> None:
    cfg = write_config(ws, auth_block("totp_issuer: Acme"))
    s = resolve(ws, xdg_env)
    assert (s.totp_issuer, s.sources["totp_issuer"]) == ("Acme", f"config:{cfg}")


# ---------------------------------------------------------------------------
# AC 10: pure data
# ---------------------------------------------------------------------------


def test_settings_are_frozen_and_launch_free(xdg_env: dict[str, str]) -> None:
    s = resolve(None, xdg_env)
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.enabled = True  # type: ignore[misc]
    with pytest.raises(TypeError):
        s.sources["enabled"] = "x"  # type: ignore[index]
    for name in ("uvicorn_kwargs", "cli_env_overrides", "protected_paths", "child_env"):
        assert not hasattr(s, name)
    assert isinstance(s.config_risks, frozenset)


def test_resolution_is_deterministic(ws: Path, xdg_env: dict[str, str]) -> None:
    write_config(ws, auth_block("enabled: true", "totp: required", "lockout_threshold: 3"))
    env = {**xdg_env, "AO_UI_AUTH_TRUSTED_PROXIES": "127.0.0.1"}
    assert resolve(ws, env) == resolve(ws, env)


def test_env_mapping_is_not_mutated(ws: Path, xdg_env: dict[str, str]) -> None:
    before = dict(xdg_env)
    resolve(ws, xdg_env)
    assert xdg_env == before


# ---------------------------------------------------------------------------
# AC 11: no I/O beyond the inputs
# ---------------------------------------------------------------------------


def test_count_users_is_not_called_in_quiet_paths(ws: Path, xdg_env: dict[str, str]) -> None:
    spy = CountSpy(5)
    write_config(ws, auth_block("enabled: true", 'totp: "required"', "session_idle_minutes: 10"))
    resolve(ws, xdg_env, count=spy)
    resolve(None, {**xdg_env, "AO_UI_AUTH": "1", "AO_UI_AUTH_TOTP": "optional"}, count=spy)
    resolve(None, xdg_env, CliKw(enabled=False), count=spy)
    resolve(None, xdg_env, count=spy)
    assert spy.calls == []


def test_count_users_is_called_once_per_step5_case(ws: Path, xdg_env: dict[str, str]) -> None:
    spy = CountSpy(2)
    write_config(ws, auth_block("enabled: false"))
    resolve(ws, xdg_env, count=spy)
    write_config(ws, auth_block("enabled: true", "totp: optional"))
    resolve(ws, xdg_env, count=spy)
    assert len(spy.calls) == 2
    # both weak at once -> impossible (weak_enabled needs enabled False, weak_totp True), so
    # "once" is per resolution:
    assert all(c == spy.calls[0] for c in spy.calls)


def test_default_count_probe_reads_the_real_store(
    ws: Path, tmp_path: Path, xdg_env: dict[str, str]
) -> None:
    store = tmp_path / "real-store"
    write_config(ws, auth_block("enabled: false"))
    env = {**xdg_env, "AO_AUTH_DIR": str(store)}
    # `enabled: false` from config + a corrupt users.json (unknown count): the default probe
    # reports it as a risk.
    store.mkdir()
    (store / "users.json").write_text("{not json")
    s = resolve_auth_settings(cli=AuthCliOverrides(), env=env, workspace_root=ws)
    assert s.config_risks == {ConfigRisk.DISABLED_BY_CONFIG}
    # and a missing store counts as zero accounts
    env = {**xdg_env, "AO_AUTH_DIR": str(tmp_path / "nope")}
    s = resolve_auth_settings(cli=AuthCliOverrides(), env=env, workspace_root=ws)
    assert s.config_risks == frozenset()


# ---------------------------------------------------------------------------
# AC 12: ConfigRisk detection (v2.1; rows 14-15)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("count", "risky"), [(0, False), (3, True), (None, True)])
def test_config_only_disable_is_a_risk_when_accounts_exist_or_unknown(
    ws: Path, xdg_env: dict[str, str], count: int | None, risky: bool
) -> None:
    write_config(ws, auth_block("enabled: false"))
    s = resolve(ws, xdg_env, count=counts(count))  # never raises
    assert s.enabled is False
    assert s.config_risks == ({ConfigRisk.DISABLED_BY_CONFIG} if risky else frozenset())
    assert not any("comes only from" in w for w in s.warnings)


@pytest.mark.parametrize("count", [0, 3, None])
def test_explicit_disable_is_never_a_config_risk(
    ws: Path, xdg_env: dict[str, str], count: int | None
) -> None:
    write_config(ws, auth_block("enabled: false"))
    assert resolve(ws, {**xdg_env, "AO_UI_AUTH": "0"}, count=counts(count)).config_risks == set()
    assert resolve(ws, xdg_env, CliKw(enabled=False), count=counts(count)).config_risks == set()


@pytest.mark.parametrize("count", [0, 3, None])
def test_default_disabled_is_not_a_config_risk(
    ws: Path, xdg_env: dict[str, str], count: int | None
) -> None:
    assert resolve(ws, xdg_env, count=counts(count)).config_risks == set()


@pytest.mark.parametrize("policy", ["optional", "off"])
@pytest.mark.parametrize("enabled_via", ["config", "env", "cli"])
@pytest.mark.parametrize(("count", "risky"), [(0, False), (3, True), (None, True)])
def test_config_only_totp_downgrade_is_a_risk_with_a_pin_it_warning(
    ws: Path,
    xdg_env: dict[str, str],
    policy: str,
    enabled_via: str,
    count: int | None,
    risky: bool,
) -> None:
    lines = [f"totp: {policy}"]
    env, cli = dict(xdg_env), AuthCliOverrides()
    if enabled_via == "config":
        lines.append("enabled: true")
    elif enabled_via == "env":
        env["AO_UI_AUTH"] = "yes"
    else:
        cli = CliKw(enabled=True)
    cfg = write_config(ws, auth_block(*lines))
    s = resolve(ws, env, cli, count=counts(count))
    assert s.enabled is True
    if not risky:
        assert s.config_risks == set()
        assert not any("comes only from" in w for w in s.warnings)
        return
    assert s.config_risks == {ConfigRisk.TOTP_DOWNGRADED_BY_CONFIG}
    warning = next(w for w in s.warnings if "comes only from" in w)
    assert f"totp={policy}" in warning
    assert str(cfg) in warning
    assert "AO_UI_AUTH_TOTP" in warning and "--auth-totp" in warning


@pytest.mark.parametrize("count", [0, 3, None])
def test_required_totp_from_config_is_no_risk(
    ws: Path, xdg_env: dict[str, str], count: int | None
) -> None:
    write_config(ws, auth_block("enabled: true", "totp: required"))
    assert resolve(ws, xdg_env, count=counts(count)).config_risks == set()


@pytest.mark.parametrize("count", [0, 3, None])
@pytest.mark.parametrize("pin", ["env", "cli"])
def test_pinned_totp_is_no_risk(
    ws: Path, xdg_env: dict[str, str], count: int | None, pin: str
) -> None:
    write_config(ws, auth_block("enabled: true", "totp: required"))
    env, cli = dict(xdg_env), AuthCliOverrides()
    if pin == "env":
        env["AO_UI_AUTH_TOTP"] = "optional"
    else:
        cli = CliKw(totp=TotpPolicy.OPTIONAL)
    assert resolve(ws, env, cli, count=counts(count)).config_risks == set()


@pytest.mark.parametrize("count", [0, 3, None])
def test_totp_below_required_with_auth_disabled_is_no_totp_risk(
    ws: Path, xdg_env: dict[str, str], count: int | None
) -> None:
    write_config(ws, auth_block("totp: optional"))  # enabled stays the default False
    s = resolve(ws, xdg_env, count=counts(count))
    assert ConfigRisk.TOTP_DOWNGRADED_BY_CONFIG not in s.config_risks
    s = resolve(ws, {**xdg_env, "AO_UI_AUTH": "0"}, count=counts(count))
    assert s.config_risks == set()


def test_risk_messages_contain_no_config_value_beyond_the_policy(
    ws: Path, xdg_env: dict[str, str]
) -> None:
    write_config(ws, auth_block("enabled: true", "totp: optional", f"totp_issuer: {SENTINEL}"))
    s = resolve(ws, xdg_env, count=counts(3))
    assert s.config_risks == {ConfigRisk.TOTP_DOWNGRADED_BY_CONFIG}
    assert all(SENTINEL not in w for w in s.warnings)


def test_both_risks_are_not_simultaneously_possible_but_each_calls_the_probe_once(
    ws: Path, xdg_env: dict[str, str]
) -> None:
    spy = CountSpy(1)
    write_config(ws, auth_block("enabled: true", "totp: optional"))
    resolve(ws, xdg_env, count=spy)
    assert len(spy.calls) == 1


def test_risk_probe_uses_the_resolved_store_dir(
    ws: Path, tmp_path: Path, xdg_env: dict[str, str]
) -> None:
    spy = CountSpy(1)
    write_config(ws, auth_block("enabled: false"))
    resolve(ws, {**xdg_env, "AO_AUTH_DIR": str(tmp_path / "pinned")}, count=spy)
    assert spy.calls == [(tmp_path / "pinned").resolve()]


def test_config_risk_values() -> None:
    assert ConfigRisk.DISABLED_BY_CONFIG.value == "disabled_by_config"
    assert ConfigRisk.TOTP_DOWNGRADED_BY_CONFIG.value == "totp_downgraded_by_config"


@pytest.mark.parametrize("exc", [RecursionError(), ValueError(SENTINEL)])
def test_other_loader_failures_are_unparseable_and_value_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    def boom(_stream: object) -> object:
        raise exc

    monkeypatch.setattr("agent_orchestrator.auth.settings.yaml.safe_load", boom)
    path = tmp_path / "c.yaml"
    path.write_text("ui: {}\n")
    cfg, problem = load_auth_block(path)
    assert cfg is None
    assert problem is not None and problem.kind is ConfigProblemKind.UNPARSEABLE
    assert SENTINEL not in problem.message
