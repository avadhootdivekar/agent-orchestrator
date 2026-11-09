"""T-f0d7gm: turn_budget alias matrix, `ao migrate effort-keys`, autocompact."""

# ruff: noqa: E501

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from agent_orchestrator import cli, models
from agent_orchestrator.cli import app
from agent_orchestrator.executors.claude_cli import build_claude_argv
from agent_orchestrator.models import (
    EFFORT_MAX_TURNS,
    AgentSpec,
    TaskSpec,
    parse_autocompact,
    resolve_effective_agent,
)
from agent_orchestrator.project_config import ProjectConfig

from .test_run_options import _launch, _state

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for n in ("AO_EFFORT", "AO_TURN_BUDGET", "AO_AUTOCOMPACT", "AO_THINKING_EFFORT"):
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setattr(models, "_warned_legacy_effort", False)


def _task(**kw) -> TaskSpec:
    return TaskSpec(id="t", agent="a", instruction="i.md", outputs=["o.md"], **kw)


# --- spec alias matrix ---------------------------------------------------------------------------


@pytest.mark.parametrize("cls", ["agent", "task"])
def test_spec_legacy_effort_maps_to_turn_budget_with_warning(cls, caplog):
    make = (lambda **k: AgentSpec(executor="fake", **k)) if cls == "agent" else _task
    with caplog.at_level(logging.WARNING):
        spec = make(effort="high")
    assert spec.turn_budget == "high" and spec.effort == "high"
    assert "deprecated" in caplog.text.lower() and "turn_budget" in caplog.text


@pytest.mark.parametrize("cls", ["agent", "task"])
def test_spec_turn_budget_no_warning_and_conflict_error(cls, caplog):
    make = (lambda **k: AgentSpec(executor="fake", **k)) if cls == "agent" else _task
    with caplog.at_level(logging.WARNING):
        assert make(turn_budget="low").turn_budget == "low"
        assert make(turn_budget="low", effort="low").turn_budget == "low"  # same value: ok
    assert "deprecated" in caplog.text.lower()  # only from the effort= call
    with pytest.raises(ValidationError, match="conflicting"):
        make(turn_budget="low", effort="high")


def test_spec_turn_budget_validates_values():
    with pytest.raises(ValidationError):
        AgentSpec(executor="fake", turn_budget="max")


@pytest.mark.parametrize(
    ("tier", "turns"), [("low", 15), ("medium", 30), ("high", 60), ("xhigh", 120)]
)
def test_turn_budget_injects_max_turns(tier, turns):
    assert EFFORT_MAX_TURNS[tier] == turns
    argv = build_claude_argv(AgentSpec(executor="claude_cli", turn_budget=tier), "P")
    assert argv[argv.index("--max-turns") + 1] == str(turns)


# --- CLI / env / config matrix (_resolve_run_settings) -------------------------------------------


def _resolve(monkeypatch, cfg=None, **kw):
    monkeypatch.setattr(cli, "_load_project_config_or_exit", lambda: cfg or ProjectConfig())
    monkeypatch.delenv("AO_MAX_PARALLEL", raising=False)
    return cli._resolve_run_settings(
        None, None, None, kw.get("effort"), None, None, None, turn_budget=kw.get("turn_budget")
    )[3]


def test_cli_turn_budget_flag_env_config_precedence(monkeypatch):
    assert _resolve(monkeypatch, ProjectConfig(turn_budget="low")) == "low"
    monkeypatch.setenv("AO_TURN_BUDGET", "medium")
    assert _resolve(monkeypatch, ProjectConfig(turn_budget="low")) == "medium"
    assert _resolve(monkeypatch, ProjectConfig(turn_budget="low"), turn_budget="xhigh") == "xhigh"


def test_legacy_effort_alias_warns_on_every_layer(monkeypatch, capsys):
    assert _resolve(monkeypatch, effort="high") == "high"
    assert "DEPRECATED" in capsys.readouterr().err
    monkeypatch.setenv("AO_EFFORT", "low")
    assert _resolve(monkeypatch) == "low"
    assert "DEPRECATED" in capsys.readouterr().err
    monkeypatch.delenv("AO_EFFORT")
    assert _resolve(monkeypatch, ProjectConfig(effort="medium")) == "medium"
    assert "DEPRECATED" in capsys.readouterr().err


def test_no_deprecation_warning_for_canonical_name(monkeypatch, capsys):
    assert _resolve(monkeypatch, turn_budget="high") == "high"
    assert "DEPRECATED" not in capsys.readouterr().err


def test_conflict_between_effort_and_turn_budget_is_an_error(monkeypatch):
    with pytest.raises(Exception) as ei:
        _resolve(monkeypatch, turn_budget="low", effort="high")
    assert getattr(ei.value, "exit_code", None) == 1
    monkeypatch.setenv("AO_EFFORT", "high")
    with pytest.raises(Exception):
        _resolve(monkeypatch, ProjectConfig(turn_budget="low"))
    assert _resolve(monkeypatch, turn_budget="high") == "high"  # same value: no conflict


def test_cli_e2e_turn_budget_applied_and_recorded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = _launch(tmp_path, "--turn-budget", "xhigh")
    assert r.exit_code == 0, r.output
    st = _state(tmp_path)
    lim = st.tasks["task_a"].effective_limits
    assert (lim.turn_budget, lim.max_turns) == ("xhigh", 120)


def test_cli_e2e_legacy_effort_flag_warns_and_conflict_exits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = _launch(tmp_path, "--effort", "low")
    assert r.exit_code == 0 and "DEPRECATED" in r.output
    assert _state(tmp_path).tasks["task_a"].effective_limits.turn_budget == "low"


def test_cli_e2e_conflict_exits_nonzero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = _launch(tmp_path, "--effort", "low", "--turn-budget", "high")
    assert r.exit_code == 1 and "conflicting" in r.output


def test_help_texts():
    out = runner.invoke(app, ["run", "--help"], terminal_width=200).output
    flat = " ".join(out.replace("│", " ").split())
    assert "low=15, medium=30, high=60, xhigh=120" in flat
    assert "low, medium, high, xhigh or max" in flat  # --thinking-effort lists the full range
    assert "DEPRECATED alias of --turn-budget" in flat and "--autocompact" in flat


# --- migrate effort-keys ---


def _workspace(root: Path) -> dict[str, str]:
    files = {
        "specs/agents.json": '{\n  "agents": {\n    "dev": {"executor": "claude_cli", "effort": "high",'
        ' "thinking_effort": "max"}\n  }\n}\n',
        "workflows/routed-runner/workflow.json": '{"tasks": [{"id": "a", "effort": "low"}]}\n',
        "workflows/routed-runner/runs/r1/workflow.json": '{"tasks": [{"id": "a", "effort": "low"}]}\n',
        "workflows/routed-runner/runs/r1/outputs/x.json": '{"effort": "high"}\n',
        ".ao/config.yaml": "# effort: kept comment\nmodel: sonnet\neffort: medium\nthinking_effort: high\n",
        "specs/other/agents.json.tmpl": '{"a": {"effort": "{{ e }}"}}\n',
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return files


def _snapshot(root: Path) -> dict[str, str]:
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*")) if p.is_file()}


def test_migrate_dry_run_prints_diff_and_changes_nothing(tmp_path):
    _workspace(tmp_path)
    before = _snapshot(tmp_path)
    r = runner.invoke(app, ["migrate", "effort-keys", "--workspace", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert '-    "dev": {"executor": "claude_cli", "effort": "high"' in r.output
    assert '+    "dev": {"executor": "claude_cli", "turn_budget": "high"' in r.output
    assert "re-run with --apply" in r.output
    assert _snapshot(tmp_path) == before


def test_migrate_apply_rewrites_only_effort_keys_and_is_idempotent(tmp_path):
    _workspace(tmp_path)
    r = runner.invoke(app, ["migrate", "effort-keys", "-w", str(tmp_path), "--apply"])
    assert r.exit_code == 0, r.output
    snap = _snapshot(tmp_path)
    agents = json.loads(snap["specs/agents.json"])["agents"]["dev"]
    assert agents == {"executor": "claude_cli", "turn_budget": "high", "thinking_effort": "max"}
    assert '"turn_budget": "low"' in snap["workflows/routed-runner/workflow.json"]
    assert (
        '"turn_budget": "low"' in snap["workflows/routed-runner/runs/r1/workflow.json"]
    )  # instance
    assert (
        snap["workflows/routed-runner/runs/r1/outputs/x.json"] == '{"effort": "high"}\n'
    )  # outputs
    assert snap[".ao/config.yaml"] == (
        "# effort: kept comment\nmodel: sonnet\nturn_budget: medium\nthinking_effort: high\n"
    )
    assert '"turn_budget": "{{ e }}"' in snap["specs/other/agents.json.tmpl"]
    again = runner.invoke(app, ["migrate", "effort-keys", "-w", str(tmp_path), "--apply"])
    assert again.exit_code == 0 and "nothing to migrate" in again.output
    assert _snapshot(tmp_path) == snap


def test_migrate_conflict_left_alone_and_exit_1(tmp_path):
    p = tmp_path / "specs" / "agents.json"
    p.parent.mkdir()
    text = '{"agents": {"a": {"effort": "low", "turn_budget": "high"}}}\n'
    p.write_text(text)
    r = runner.invoke(app, ["migrate", "effort-keys", "-w", str(tmp_path), "--apply"])
    assert r.exit_code == 1 and "CONFLICT" in r.output
    assert p.read_text() == text


def test_migrated_workspace_loads_without_deprecation(tmp_path, caplog):
    _workspace(tmp_path)
    runner.invoke(app, ["migrate", "effort-keys", "-w", str(tmp_path), "--apply"])
    with caplog.at_level(logging.WARNING):
        AgentSpec.model_validate(
            json.loads((tmp_path / "specs/agents.json").read_text())["agents"]["dev"]
        )
    assert "deprecated" not in caplog.text.lower()


# --- autocompact ---


@pytest.mark.parametrize(
    ("raw", "want"),
    [("auto", "auto"), ("AUTO", "auto"), (100000, 100000), ("250000", 250000), (1000000, 1000000)],
)
def test_parse_autocompact_ok(raw, want):
    assert parse_autocompact(raw) == want


@pytest.mark.parametrize("raw", [99999, 1000001, "bogus", True, 1.5, "-5", ""])
def test_parse_autocompact_rejects(raw):
    with pytest.raises(ValueError):
        parse_autocompact(raw)


def test_autocompact_precedence_task_over_agent_over_run():
    agent = AgentSpec(executor="claude_cli", autocompact=200000)
    assert resolve_effective_agent(_task(), agent).autocompact == 200000
    assert resolve_effective_agent(_task(autocompact="auto"), agent).autocompact == "auto"
    # run level only fills agents without their own value
    agents = {
        "a": AgentSpec(executor="claude_cli"),
        "b": AgentSpec(executor="claude_cli", autocompact=300000),
    }
    cli._apply_agent_run_settings(agents, None, None, 150000)
    assert (agents["a"].autocompact, agents["b"].autocompact) == (150000, 300000)


def test_run_level_resolution_chain(monkeypatch):
    monkeypatch.setattr(
        cli, "_load_project_config_or_exit", lambda: ProjectConfig(autocompact=200000)
    )
    assert cli._resolve_autocompact(None) == 200000
    monkeypatch.setenv("AO_AUTOCOMPACT", "auto")
    assert cli._resolve_autocompact(None) == "auto"
    assert cli._resolve_autocompact("500000") == 500000
    with pytest.raises(Exception) as ei:
        cli._resolve_autocompact("5")
    assert getattr(ei.value, "exit_code", None) == 1


def test_claude_argv_autocompact_mapping_and_default_unchanged():
    base = build_claude_argv(AgentSpec(executor="claude_cli"), "P")
    assert "--autocompact" not in base  # default: byte-identical argv
    argv = build_claude_argv(AgentSpec(executor="claude_cli", autocompact=250000), "P")
    assert argv[argv.index("--autocompact") + 1] == "250000"
    argv = build_claude_argv(AgentSpec(executor="claude_cli", autocompact="auto"), "P")
    assert argv[argv.index("--autocompact") + 1] == "auto"


def test_claude_argv_extra_args_autocompact_wins_with_warning(caplog):
    agent = AgentSpec(
        executor="claude_cli", autocompact=250000, extra_args=["--autocompact", "180000"]
    )
    with caplog.at_level(logging.WARNING):
        argv = build_claude_argv(agent, "P")
    assert argv.count("--autocompact") == 1 and argv[argv.index("--autocompact") + 1] == "180000"
    assert "extra_args wins" in caplog.text


def test_executor_supported_settings():
    from agent_orchestrator.executors import (
        EXECUTOR_SUPPORTED_SETTINGS,
        ClaudeCliExecutor,
        FakeExecutor,
    )

    assert ClaudeCliExecutor.supported_settings == {"thinking_effort", "autocompact"}
    assert FakeExecutor.supported_settings == frozenset()
    assert EXECUTOR_SUPPORTED_SETTINGS["claude_cli"] >= {"autocompact"}


def _write_unsupported(tmp_path: Path, agent_autocompact: bool = True) -> tuple[Path, Path, Path]:
    from .test_e2e_cli_max_parallel import (
        _write_instructions,
        _write_simple_reposet,
        _write_small_workflow,
    )

    wf, rs = _write_small_workflow(tmp_path), _write_simple_reposet(tmp_path)
    agent: dict[str, object] = {"executor": "fake"}
    if agent_autocompact:
        agent["autocompact"] = 200000
    ag = tmp_path / "agents.json"
    ag.write_text(json.dumps({"version": "1.0", "agents": {"ag": agent}}))
    _write_instructions(tmp_path)
    return wf, rs, ag


def test_validate_warns_for_unsupported_executor_but_succeeds(tmp_path):
    wf, rs, ag = _write_unsupported(tmp_path)
    r = runner.invoke(
        app, ["validate", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)]
    )
    assert r.exit_code == 0 and "OK: all specs valid" in r.output, r.output
    assert "WARNING: autocompact is set for agent 'ag'" in r.output and "'fake'" in r.output


def test_validate_silent_when_no_autocompact(tmp_path):
    wf, rs, ag = _write_unsupported(tmp_path, agent_autocompact=False)
    r = runner.invoke(
        app, ["validate", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)]
    )
    assert r.exit_code == 0 and "WARNING" not in r.output


def test_run_with_unsupported_executor_warns_once_and_succeeds(tmp_path, monkeypatch, caplog):
    monkeypatch.chdir(tmp_path)
    with caplog.at_level(logging.WARNING):
        r = _launch(tmp_path, "--autocompact", "300000")
    assert r.exit_code == 0, r.output
    assert caplog.text.count("autocompact is set for agent 'ag'") == 1
    st = _state(tmp_path)
    assert st.effective_settings["autocompact"].value == "300000"
    assert st.effective_settings["autocompact"].source == "cli"
    assert st.tasks["task_a"].effective_limits.autocompact == 300000


def test_run_rejects_out_of_range_autocompact(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r = _launch(tmp_path, "--autocompact", "10")
    assert r.exit_code == 1 and "autocompact" in r.output


def test_registry_providers_metadata():
    from agent_orchestrator import run_options

    by = {o["name"]: o for o in run_options.options_schema()["options"]}
    assert by["autocompact"]["providers"] == ["claude-cli"]
    assert by["thinking_effort"]["providers"] == ["claude-cli"]
    assert by["turn_budget"]["providers"] == []
    assert by["turn_budget"]["choices"] == ["low", "medium", "high", "xhigh"]
