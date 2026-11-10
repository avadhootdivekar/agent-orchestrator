"""Sentinel settings plumbing (T-Sn2Set): model, layered precedence, registry, CLI, templates."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from agent_orchestrator import run_options
from agent_orchestrator.cli import app
from agent_orchestrator.models import RunState, SentinelSpec, WorkflowSpec
from agent_orchestrator.sentinel_settings import (
    SentinelSettingsError,
    parse_sentinel_set,
    resolve_sentinel_settings,
)

from .test_e2e_cli_max_parallel import (
    _write_instructions,
    _write_simple_agents,
    _write_simple_reposet,
    _write_small_workflow,
)

runner = CliRunner()


# --- model -------------------------------------------------------------------------------------


def test_defaults_are_disabled_and_match_epic():
    s = SentinelSpec()
    assert s.enabled is False and s.tick_seconds == 15
    assert s.recovery.idle_kill_minutes == 15 and s.llm.enabled is False
    assert s.llm.powers == "act" and s.llm.budget_pct == 10


def test_unknown_key_rejected():
    with pytest.raises(ValidationError):
        SentinelSpec.model_validate({"recovery": {"idle_kil_minutes": 20}})


def test_idle_floor_needs_force():
    with pytest.raises(ValidationError, match="floor of 12"):
        SentinelSpec.model_validate({"recovery": {"idle_kill_minutes": 5}})
    ok = SentinelSpec.model_validate({"recovery": {"idle_kill_minutes": 5, "force": True}})
    assert ok.recovery.idle_kill_minutes == 5


def test_workflow_without_block_has_none():
    wf = WorkflowSpec.model_validate(
        {"version": "1.0", "id": "w", "tasks": [{"id": "a", "agent": "x", "instruction": "i"}]}
    )
    assert wf.sentinel is None


# --- precedence --------------------------------------------------------------------------------


def _spec(**kw) -> SentinelSpec:
    return SentinelSpec.model_validate(kw)


def test_precedence_workflow_cli_env_config_default():
    env = {"AO_SENTINEL_TICK_SECONDS": "30"}
    cfg = _spec(tick_seconds=40, inbox_batch_max=10)
    # config < env
    r = resolve_sentinel_settings(config=cfg, environ=env)
    assert r.spec.tick_seconds == 30 and r.sources["tick_seconds"].source == "env"
    assert r.spec.inbox_batch_max == 10 and r.sources["inbox_batch_max"].source == "config"
    assert r.sources["cancel_grace_seconds"].source == "default"
    # env < cli
    r = resolve_sentinel_settings(config=cfg, environ=env, cli={"tick_seconds": 20})
    assert r.spec.tick_seconds == 20 and r.sources["tick_seconds"].source == "cli"
    # cli < workflow
    r = resolve_sentinel_settings(
        config=cfg, environ=env, cli={"tick_seconds": 20}, workflow=_spec(tick_seconds=60)
    )
    assert r.spec.tick_seconds == 60 and r.sources["tick_seconds"].source == "workflow"


def test_block_only_overrides_explicit_keys():
    r = resolve_sentinel_settings(
        config=_spec(tick_seconds=40), workflow=_spec(enabled=True), environ={}
    )
    assert r.spec.tick_seconds == 40  # workflow block did not pin the default
    assert r.sources["enabled"].source == "workflow"


def test_false_is_a_real_value():
    r = resolve_sentinel_settings(config=_spec(enabled=True), cli={"enabled": False}, environ={})
    assert r.spec.enabled is False and r.sources["enabled"].source == "cli"


def test_bad_env_value():
    with pytest.raises(SentinelSettingsError, match="AO_SENTINEL_TICK_SECONDS"):
        resolve_sentinel_settings(environ={"AO_SENTINEL_TICK_SECONDS": "abc"})


def test_merged_validation_floor():
    with pytest.raises(SentinelSettingsError, match="floor"):
        resolve_sentinel_settings(cli={"recovery.idle_kill_minutes": 3}, environ={})
    r = resolve_sentinel_settings(
        cli={"recovery.idle_kill_minutes": 3, "recovery.force": True}, environ={}
    )
    assert r.spec.recovery.idle_kill_minutes == 3


# --- --sentinel-set parsing --------------------------------------------------------------------


def test_parse_set_values():
    got = parse_sentinel_set(
        [
            "recovery.idle_kill_minutes=20",
            "llm.powers=advise",
            "llm.enabled=true",
            'recovery.escalate=[{"effort":"max"}]',
            "llm.agent=null",
        ]
    )
    assert got == {
        "recovery.idle_kill_minutes": 20,
        "llm.powers": "advise",
        "llm.enabled": True,
        "recovery.escalate": [{"effort": "max"}],
        "llm.agent": None,
    }


@pytest.mark.parametrize(
    "item", ["nokey", "bogus.key=1", "tick_seconds=abc", "llm.powers=godmode", "=1"]
)
def test_parse_set_errors(item):
    with pytest.raises(SentinelSettingsError):
        parse_sentinel_set([item])


# --- registry ----------------------------------------------------------------------------------


def test_registry_has_every_scalar_leaf_and_flags():
    names = {o.name for o in run_options.OPTIONS if o.name.startswith("sentinel.")}
    assert "sentinel.recovery.idle_kill_minutes" in names and "sentinel.llm.budget_usd" in names
    assert "sentinel.recovery.escalate" not in names  # list leaf: config/workflow/--sentinel-set
    o = run_options.get_option("sentinel.enabled")
    assert (o.flag, o.no_flag, o.env) == ("--sentinel", "--no-sentinel", "AO_SENTINEL_ENABLED")
    assert run_options.get_option("sentinel.max_parallel_ceiling").flag == "--max-parallel-ceiling"
    # T-Sn5Ui1: only the flagged leaves are forwardable from the dashboard (schema-driven form);
    # tuning knobs stay on config / env / the workflow block.
    ui = {o.name for o in run_options.OPTIONS if o.ui and o.name.startswith("sentinel.")}
    assert ui == {
        "sentinel.enabled",
        "sentinel.max_parallel_ceiling",
        "sentinel.llm.enabled",
        "sentinel.llm.powers",
        "sentinel.llm.budget_usd",
    }
    assert all(run_options.get_option(n).flag for n in ui)


# --- CLI e2e -----------------------------------------------------------------------------------


def _launch(tmp_path: Path, *extra: str, cmd: str = "run", env=None, wf_extra=None):
    wf = _write_small_workflow(tmp_path)
    if wf_extra:
        data = json.loads(wf.read_text())
        data.update(wf_extra)
        wf.write_text(json.dumps(data))
    rs = _write_simple_reposet(tmp_path)
    ag = _write_simple_agents(tmp_path)
    _write_instructions(tmp_path)
    args = [cmd, "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag), *extra]
    return runner.invoke(app, args, env={"AO_WORKSPACE_ROOT": str(tmp_path), **(env or {})})


def _state(tmp_path: Path) -> RunState:
    (path,) = list((tmp_path / ".orchestrator" / "runs").glob("*/state.json"))
    return RunState.model_validate_json(path.read_text())


def test_run_flags_recorded_with_sources(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = _launch(
        tmp_path,
        "--sentinel",
        "--sentinel-llm",
        "--sentinel-powers",
        "advise",
        "--max-parallel-ceiling",
        "4",
        "--sentinel-set",
        "tick_seconds=25",
        env={"AO_SENTINEL_TICK_SECONDS": "99", "AO_SENTINEL_INBOX_BATCH_MAX": "7"},
        wf_extra={"sentinel": {"llm": {"powers": "act"}}},
    )
    assert r.exit_code == 0, r.output
    s = _state(tmp_path).effective_settings
    got = {k: (v.value, v.source) for k, v in s.items() if k.startswith("sentinel.")}
    assert got["sentinel.enabled"] == (True, "cli")
    assert got["sentinel.llm.enabled"] == (True, "cli")
    assert got["sentinel.llm.powers"] == ("act", "workflow")  # workflow > CLI
    assert got["sentinel.max_parallel_ceiling"] == (4, "cli")
    assert got["sentinel.tick_seconds"] == (25, "cli")  # --sentinel-set > env
    assert got["sentinel.inbox_batch_max"] == (7, "env")
    assert got["sentinel.cancel_grace_seconds"] == (30, "default")


def test_resume_accepts_flags(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _launch(tmp_path).exit_code == 0
    st = _state(tmp_path)
    r = _launch(tmp_path, "--run-id", st.run_id, "--sentinel", cmd="resume")
    assert r.exit_code == 0, r.output
    assert _state(tmp_path).effective_settings["sentinel.enabled"].source == "cli"


@pytest.mark.parametrize(
    "bad", ["bogus=1", "tick_seconds=abc", "llm.powers=x", "recovery.idle_kill_minutes=3", "oops"]
)
def test_bad_sentinel_set_exits_2(tmp_path, monkeypatch, bad):
    monkeypatch.chdir(tmp_path)
    r = _launch(tmp_path, "--sentinel-set", bad)
    assert r.exit_code == 2, r.output
    assert "ERROR" in r.output


def test_no_sentinel_flags_keeps_defaults_and_ignores_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert _launch(tmp_path).exit_code == 0
    s = _state(tmp_path).effective_settings
    assert s["sentinel.enabled"].value is False and s["sentinel.enabled"].source == "default"


def test_validate_rejects_bad_block(tmp_path):
    wf = _write_small_workflow(tmp_path)
    data = json.loads(wf.read_text())
    data["sentinel"] = {"recovery": {"idle_kill_minutes": 2}}
    wf.write_text(json.dumps(data))
    rs = _write_simple_reposet(tmp_path)
    ag = _write_simple_agents(tmp_path)
    r = runner.invoke(
        app, ["validate", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)]
    )
    assert r.exit_code != 0 and "floor" in r.output


# --- templates ---------------------------------------------------------------------------------


@pytest.mark.parametrize("template", ["routed-runner", "overseer-runner"])
def test_template_renders_block_only_when_enabled(tmp_path, template):
    from agent_orchestrator.templates import instantiate, load_template

    def render(slug, **params):
        info = load_template(template, str(tmp_path), None)
        res = instantiate(info, str(tmp_path), slug_or_id=slug, params=params)
        return Path(res.workflow_path).read_text()

    base = render("plain")
    assert '"sentinel"' not in base
    assert "sentinel" not in render("off", sentinel_enabled="false")
    # the disabled fragment is empty, so the rendered line is exactly what it was before the param
    assert '"name": "' + template + ": " in base and '",\n  "repo_set"' in base
    on = render("on", sentinel_enabled="true", sentinel_llm="true", sentinel_powers="advise")
    block = json.loads(on)["sentinel"]
    assert block == {"enabled": True, "llm": {"enabled": True, "powers": "advise"}}
    WorkflowSpec.model_validate(json.loads(on))  # still a valid spec


def test_new_run_passes_sentinel_options_through(tmp_path, monkeypatch):
    """`ao new <tpl> --param sentinel_enabled=true --run`: block rendered, flags forwarded."""
    import agent_orchestrator.cli as cli

    seen: dict[str, object] = {}

    def fake_invoke(workflow_path, reposets, agents, input_mode=None, extra_args=None):
        seen["workflow"] = json.loads(Path(workflow_path).read_text())
        seen["extra"] = extra_args
        return 0

    monkeypatch.setattr(cli, "_invoke_run_in_process", fake_invoke)
    monkeypatch.setattr(cli, "_load_all", lambda *a, **k: None)  # agents not under test
    monkeypatch.chdir(tmp_path)
    r = runner.invoke(
        app,
        [
            "new", "routed-runner", "sx", "--param", "sentinel_enabled=true",
            "--sentinel-llm", "--sentinel-set", "tick_seconds=20", "--max-parallel-ceiling", "8",
            "--run",
        ],
        env={"AO_WORKSPACE_ROOT": str(tmp_path)},
    )  # fmt: skip
    assert r.exit_code == 0, r.output
    assert seen["workflow"]["sentinel"] == {"enabled": True}  # type: ignore[index]
    assert seen["extra"] == [
        "--sentinel-llm", "--max-parallel-ceiling", "8", "--sentinel-set", "tick_seconds=20",
    ]  # fmt: skip
