"""Run-options registry (T-zP28k9): behaviour-preservation oracle, allow-list, schema snapshot,
and the effective-settings / per-task-limits state snapshot."""

from __future__ import annotations

import itertools
import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator import cli, run_options
from agent_orchestrator.cli import app
from agent_orchestrator.models import RunState, TaskRunState
from agent_orchestrator.project_config import MonitoringConfig, ProjectConfig
from agent_orchestrator.ui.processes import ALLOWED_BOOL_OPTIONS, ALLOWED_OPTIONS, _render_options

from .test_e2e_cli_max_parallel import (
    _write_instructions,
    _write_simple_agents,
    _write_simple_reposet,
    _write_small_workflow,
)

SNAPSHOT = Path(__file__).parent / "fixtures" / "run_options_schema.json"
runner = CliRunner()


# --- legacy oracle: verbatim copies of the pre-registry `or` chains -------------------------


def _int_env(name: str) -> int | None:
    v = os.environ.get(name)
    try:
        return int(v) if v else None
    except ValueError:
        return None


def _legacy_run_settings(cfg, max_attempts, max_turns, model, effort, qw, qp, mp):
    return (
        max_attempts or _int_env("AO_MAX_ATTEMPTS") or (cfg.max_attempts if cfg else None),
        max_turns or _int_env("AO_MAX_TURNS") or (cfg.max_turns if cfg else None),
        model or os.environ.get("AO_MODEL") or (cfg.model if cfg else None),
        effort or os.environ.get("AO_EFFORT") or (cfg.effort if cfg else None),
        int(
            qw
            or _int_env("AO_QUOTA_MAX_WAIT_SECONDS")
            or (cfg.quota_max_wait_seconds if cfg else None)
            or 21600
        ),
        int(
            qp
            or _int_env("AO_QUOTA_POLL_SECONDS")
            or (cfg.quota_poll_seconds if cfg else None)
            or 900
        ),
        int(mp or _int_env("AO_MAX_PARALLEL") or (cfg.max_parallel if cfg else None) or 1),
    )


def _legacy_self_heal(cfg, flag):
    v = os.environ.get("AO_SELF_HEAL")
    env = None if not v else v.strip().lower() in ("1", "true", "yes", "on")
    base = cfg.monitoring if cfg else MonitoringConfig()
    return flag if flag is not None else env if env is not None else base.self_heal


def _legacy_git_heads(cfg, flag):
    if flag is not None:
        return flag
    env = os.environ.get("AO_RECORD_GIT_HEADS")
    if env and env.strip():
        return env.strip().lower() in ("1", "true", "yes", "on")
    if cfg is not None and cfg.record_git_heads is not None:
        return cfg.record_git_heads
    return True


CLI_INT = [None, 0, 7]
ENV_INT = [None, "", "bogus", "0", "5"]
CFG_INT = [None, 0, 3]


@pytest.mark.parametrize("cli_v,env_v,cfg_v", list(itertools.product(CLI_INT, ENV_INT, CFG_INT)))
def test_run_settings_match_legacy_chain(monkeypatch, cli_v, env_v, cfg_v):
    for name in (
        "AO_MAX_ATTEMPTS",
        "AO_MAX_TURNS",
        "AO_QUOTA_MAX_WAIT_SECONDS",
        "AO_QUOTA_POLL_SECONDS",
        "AO_MAX_PARALLEL",
    ):
        if env_v is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, env_v)
    for name in ("AO_MODEL", "AO_EFFORT"):
        monkeypatch.delenv(name, raising=False)
    cfg = ProjectConfig(
        max_attempts=cfg_v,
        max_turns=cfg_v,
        quota_max_wait_seconds=cfg_v,
        quota_poll_seconds=cfg_v,
        max_parallel=cfg_v,
    )
    monkeypatch.setattr(cli, "_load_project_config_or_exit", lambda: cfg)
    expected = _legacy_run_settings(cfg, cli_v, cli_v, None, None, cli_v, cli_v, cli_v)
    if expected[6] < 1:
        with pytest.raises(Exception):  # typer.Exit
            cli._resolve_run_settings(cli_v, cli_v, None, None, cli_v, cli_v, cli_v)
        return
    assert cli._resolve_run_settings(cli_v, cli_v, None, None, cli_v, cli_v, cli_v) == expected


@pytest.mark.parametrize(
    "cli_v,env_v,cfg_v",
    list(itertools.product([None, "opus"], [None, "", "haiku"], [None, "sonnet"])),
)
def test_str_settings_match_legacy_chain(monkeypatch, cli_v, env_v, cfg_v):
    # AO_EFFORT is now validated (turn-budget tiers), so feed it a tier, not the model string.
    for name, val in (("AO_MODEL", env_v), ("AO_EFFORT", env_v and "medium")):
        if val is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, val)
    cfg = ProjectConfig(model=cfg_v, effort=cfg_v and "low")
    monkeypatch.setattr(cli, "_load_project_config_or_exit", lambda: cfg)
    monkeypatch.delenv("AO_MAX_PARALLEL", raising=False)
    got = cli._resolve_run_settings(None, None, cli_v, cli_v and "high", None, None, None)
    want = _legacy_run_settings(cfg, None, None, cli_v, cli_v and "high", None, None, None)
    assert got == want


@pytest.mark.parametrize(
    "cli_v,env_v,cfg_v",
    itertools.product(
        [None, True, False],
        [None, "", "  ", "1", "TRUE", "on", "0", "no", "garbage"],
        [None, True, False],
    ),
)
def test_bool_settings_match_legacy_chain(monkeypatch, cli_v, env_v, cfg_v):
    for name in ("AO_SELF_HEAL", "AO_RECORD_GIT_HEADS"):
        if env_v is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, env_v)
    cfg = ProjectConfig(
        record_git_heads=cfg_v,
        monitoring=MonitoringConfig(self_heal=bool(cfg_v)),
    )
    monkeypatch.setattr(cli, "_load_project_config_or_exit", lambda: cfg)
    assert cli._resolve_monitoring_settings(cli_v).self_heal == _legacy_self_heal(cfg, cli_v)
    assert cli._resolve_record_git_heads(cli_v) == _legacy_git_heads(cfg, cli_v)


@pytest.mark.parametrize(
    "cli_v,env_v,cfg_v",
    list(itertools.product([None, "", "max"], [None, "", "high"], [None, "low"])),
)
def test_thinking_effort_matches_legacy_chain(monkeypatch, cli_v, env_v, cfg_v):
    if env_v is None:
        monkeypatch.delenv("AO_THINKING_EFFORT", raising=False)
    else:
        monkeypatch.setenv("AO_THINKING_EFFORT", env_v)
    cfg = ProjectConfig(thinking_effort=cfg_v)
    monkeypatch.setattr(cli, "_load_project_config_or_exit", lambda: cfg)
    want = cli_v or env_v or cfg_v
    assert cli._resolve_thinking_effort(cli_v) == (want if want else want or None)


def test_sources_are_reported(monkeypatch):
    monkeypatch.setenv("AO_MAX_PARALLEL", "4")
    monkeypatch.delenv("AO_MODEL", raising=False)
    cfg = ProjectConfig(model="sonnet")
    opt = run_options.get_option
    assert run_options.resolve_option(opt("max_parallel"), 9, cfg) == (
        run_options.Resolved(9, "cli")
    )
    assert run_options.resolve_option(opt("max_parallel"), None, cfg).source == "env"
    assert run_options.resolve_option(opt("model"), None, cfg).source == "config"
    assert run_options.resolve_option(
        opt("quota_poll_interval"), None, cfg
    ) == run_options.Resolved(900, "default")
    assert run_options.resolve_option(opt("max_turns"), None, cfg) == run_options.Resolved(
        None, "unset"
    )


# --- allow-list ------------------------------------------------------------------------------


def test_allow_list_unchanged_and_registry_derived():
    assert ALLOWED_OPTIONS == {
        "model": "--model",
        "turn_budget": "--turn-budget",
        "effort": "--effort",  # deprecated alias; stays until the dashboard form moves (T-pMpo4O)
        "autocompact": "--autocompact",
        "thinking_effort": "--thinking-effort",
        "max_attempts": "--max-attempts",
        "max_turns": "--max-turns",
        "max_parallel": "--max-parallel",
        "budget_total": "--budget-total",
        "quota_max_wait": "--quota-max-wait",
        "quota_poll_interval": "--quota-poll-interval",
    }
    assert ALLOWED_BOOL_OPTIONS == {"self_heal": ("--self-heal", "--no-self-heal")}
    assert ALLOWED_OPTIONS == run_options.allowed_options()


def test_every_registry_flag_is_a_real_cli_flag():
    help_text = ""
    for cmd in ("run", "resume"):
        help_text += runner.invoke(
            app, [cmd, "--help"], env={"COLUMNS": "400", "TERM": "dumb"}
        ).output
    for o in run_options.OPTIONS:
        for f in (o.flag, o.no_flag):
            if f:
                assert f in help_text, f


@pytest.mark.parametrize(
    "key",
    ["--workspace", "workflow", "__class__", "model --dangerous", "--model", "cwd", "env", ""],
)
def test_unknown_keys_dropped_and_values_never_become_flags(key):
    assert _render_options({key: "x"}) == []


def test_argv_injection_value_stays_one_token():
    argv = _render_options({"model": "opus --dangerously-skip-permissions", "bogus": 1})
    assert argv == ["--model", "opus --dangerously-skip-permissions"]  # single argv element


# --- schema snapshot ---------------------------------------------------------------------------


def test_schema_matches_snapshot():
    got = run_options.options_schema()
    if not SNAPSHOT.exists():  # first run only: commit the generated file
        SNAPSHOT.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n")
    assert got == json.loads(SNAPSHOT.read_text())


# --- state snapshot ----------------------------------------------------------------------------


def _launch(tmp_path: Path, *extra: str, cmd: str = "run", run_id: str | None = None, env=None):
    wf = _write_small_workflow(tmp_path)
    rs = _write_simple_reposet(tmp_path)
    ag = _write_simple_agents(tmp_path)
    _write_instructions(tmp_path)
    args = [cmd, "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag), *extra]
    if run_id:
        args += ["--run-id", run_id]
    return runner.invoke(app, args, env={"AO_WORKSPACE_ROOT": str(tmp_path), **(env or {})})


def _state(tmp_path: Path) -> RunState:
    (path,) = list((tmp_path / ".orchestrator" / "runs").glob("*/state.json"))
    return RunState.model_validate_json(path.read_text())


def test_effective_settings_written_on_start_and_appended_on_resume(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = _launch(tmp_path, "--max-parallel", "2", "--model", "opus", env={"AO_MAX_ATTEMPTS": "3"})
    assert r.exit_code == 0, r.output
    st = _state(tmp_path)
    s = st.effective_settings
    assert (s["max_parallel"].value, s["max_parallel"].source) == (2, "cli")
    assert (s["model"].value, s["model"].source) == ("opus", "cli")
    assert (s["max_attempts"].value, s["max_attempts"].source) == (3, "env")
    assert s["quota_poll_interval"].source == "default"
    assert [h.kind for h in st.effective_settings_history] == ["start"]

    r = _launch(tmp_path, "--max-parallel", "1", cmd="resume", run_id=st.run_id)
    assert r.exit_code == 0, r.output
    st2 = _state(tmp_path)
    assert [h.kind for h in st2.effective_settings_history] == ["start", "resume"]
    assert st2.effective_settings_history[0].settings["max_parallel"].value == 2
    assert st2.effective_settings["max_parallel"].value == 1
    assert st2.effective_settings["model"].source == "unset"


def test_per_task_limits_recorded_at_dispatch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _launch(tmp_path, "--max-attempts", "4").exit_code == 0
    st = _state(tmp_path)
    lim = st.tasks["task_a"].effective_limits
    assert lim is not None
    assert lim.max_attempts == 4  # --max-attempts fill-in is what dispatch enforces
    assert lim.timeout_seconds == 1800
    dumped = lim.model_dump()
    assert set(dumped) == {
        "thinking_effort",
        "turn_budget",
        "max_turns",
        "autocompact",
        "timeout_seconds",
        "task_cost_cap_usd",
        "max_attempts",
    }


def test_old_state_without_new_fields_loads():
    old = {
        "run_id": "r",
        "workflow_id": "w",
        "started_at": "t",
        "updated_at": "t",
        "tasks": {"a": {"status": "succeeded"}},
    }
    st = RunState.model_validate(old)
    assert st.effective_settings == {} and st.effective_settings_history == []
    assert st.tasks["a"].effective_limits is None
    assert TaskRunState().effective_limits is None
