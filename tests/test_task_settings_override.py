"""Tests for per-task model/effort/max_turns overrides (ADR-0003 decision 2) and the
"xhigh" effort tier.

Covers:
- Unit: EFFORT_MAX_TURNS["xhigh"] mapping; `resolve_effective_agent` precedence
  (task > agent, per-field fill-in not clobber, and the "no override" regression path).
- Validate-layer (E-3JTmVu learning: test feature acceptance where `ao validate` actually
  checks it -- schema load + pydantic construction -- not just raw jsonschema): workflow/
  agents specs accept the new fields and "xhigh"; a bad effort value is rejected.
- Integration: engine dispatch resolves task > agent settings and they reach the executor
  (via `FakeExecutor.resolved_agents`), including for a task injected at run time through
  an `emit_tasks` manifest (Area 2 dynamic injection).
- E2E (CliRunner): `ao validate` accepts/rejects task-level effort at the real CLI boundary.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.config import load_agents
from agent_orchestrator.errors import SpecValidationError
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import (
    EFFORT_MAX_TURNS,
    AgentSpec,
    TaskSpec,
    WorkflowSpec,
    resolve_effective_agent,
)
from agent_orchestrator.spec import load_workflow

runner = CliRunner()

# ---------------------------------------------------------------------------
# Unit: EFFORT_MAX_TURNS xhigh mapping
# ---------------------------------------------------------------------------


class TestXhighEffortTier:
    def test_xhigh_max_turns_is_120(self) -> None:
        assert EFFORT_MAX_TURNS["xhigh"] == 120

    def test_xhigh_above_high(self) -> None:
        assert EFFORT_MAX_TURNS["xhigh"] > EFFORT_MAX_TURNS["high"]

    def test_agent_spec_accepts_xhigh(self) -> None:
        agent = AgentSpec(executor="claude_cli", effort="xhigh")
        assert agent.effort == "xhigh"

    def test_task_spec_accepts_xhigh(self) -> None:
        task = TaskSpec(id="t", agent="ag", instruction="i.md", effort="xhigh")
        assert task.effort == "xhigh"


# ---------------------------------------------------------------------------
# Unit: resolve_effective_agent precedence
# ---------------------------------------------------------------------------


def _agent(**kwargs) -> AgentSpec:
    return AgentSpec(executor="claude_cli", **kwargs)


def _task(**kwargs) -> TaskSpec:
    base = {"id": "t1", "agent": "ag", "instruction": "i.md"}
    base.update(kwargs)
    return TaskSpec(**base)


class TestResolveEffectiveAgent:
    def test_no_task_overrides_returns_same_agent_object(self) -> None:
        """Regression: a task declaring no overrides must not allocate a new AgentSpec,
        and the agent's own fields must pass through untouched."""
        agent = _agent(model="agent-model", effort="high", max_turns=60)
        task = _task()
        result = resolve_effective_agent(task, agent)
        assert result is agent
        assert result.model == "agent-model"
        assert result.effort == "high"
        assert result.max_turns == 60

    def test_task_model_wins_over_agent_model(self) -> None:
        agent = _agent(model="agent-model")
        task = _task(model="task-model")
        result = resolve_effective_agent(task, agent)
        assert result.model == "task-model"

    def test_task_effort_wins_over_agent_effort(self) -> None:
        agent = _agent(effort="low")
        task = _task(effort="xhigh")
        result = resolve_effective_agent(task, agent)
        assert result.effort == "xhigh"

    def test_task_max_turns_wins_over_agent_max_turns(self) -> None:
        agent = _agent(max_turns=15)
        task = _task(max_turns=200)
        result = resolve_effective_agent(task, agent)
        assert result.max_turns == 200

    def test_unset_task_fields_fall_back_to_agent_per_field(self) -> None:
        """Fill-in, not clobber: overriding one field must not blank out the others."""
        agent = _agent(model="agent-model", effort="high", max_turns=60)
        task = _task(effort="xhigh")  # only effort overridden
        result = resolve_effective_agent(task, agent)
        assert result.model == "agent-model"
        assert result.effort == "xhigh"
        assert result.max_turns == 60

    def test_task_overrides_do_not_mutate_original_agent(self) -> None:
        agent = _agent(model="agent-model")
        task = _task(model="task-model")
        resolve_effective_agent(task, agent)
        assert agent.model == "agent-model"

    def test_agent_only_fields_untouched_by_resolution(self) -> None:
        """working_dir/disallowed_tools have no task-level override; must pass through."""
        agent = _agent(working_dir="sub", disallowed_tools=["KillShell"])
        task = _task(model="task-model")
        result = resolve_effective_agent(task, agent)
        assert result.working_dir == "sub"
        assert result.disallowed_tools == ["KillShell"]


# ---------------------------------------------------------------------------
# Validate layer: load_workflow / load_agents (schema + pydantic, what `ao validate`
# actually calls -- E-3JTmVu learning)
# ---------------------------------------------------------------------------


def _write_json(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data))
    return path


def _minimal_workflow_dict(task_extra: dict) -> dict:
    task = {"id": "t1", "agent": "ag", "instruction": "specs/instructions/t1.md"}
    task.update(task_extra)
    return {
        "version": "1.0",
        "id": "settings-wf",
        "repo_set": "rs",
        "tasks": [task],
    }


class TestValidateLayerAcceptsTaskSettings:
    def test_load_workflow_accepts_task_model_effort_max_turns(self, tmp_path: Path) -> None:
        wf_path = _write_json(
            tmp_path / "workflow.json",
            _minimal_workflow_dict(
                {"model": "claude-opus-4-8", "effort": "xhigh", "max_turns": 150}
            ),
        )
        wf = load_workflow(wf_path)
        task = wf.tasks[0]
        assert task.model == "claude-opus-4-8"
        assert task.effort == "xhigh"
        assert task.max_turns == 150

    def test_load_workflow_rejects_bad_effort_value(self, tmp_path: Path) -> None:
        wf_path = _write_json(
            tmp_path / "workflow.json", _minimal_workflow_dict({"effort": "ultra"})
        )
        with pytest.raises(SpecValidationError):
            load_workflow(wf_path)

    def test_load_workflow_task_without_overrides_still_valid(self, tmp_path: Path) -> None:
        """Regression: a task declaring none of the new fields still loads fine."""
        wf_path = _write_json(tmp_path / "workflow.json", _minimal_workflow_dict({}))
        wf = load_workflow(wf_path)
        task = wf.tasks[0]
        assert task.model is None
        assert task.effort is None
        assert task.max_turns is None

    def test_load_agents_accepts_xhigh(self, tmp_path: Path) -> None:
        ag_path = _write_json(
            tmp_path / "agents.json",
            {"version": "1.0", "agents": {"ag": {"executor": "claude_cli", "effort": "xhigh"}}},
        )
        agents = load_agents(ag_path)
        assert agents["ag"].effort == "xhigh"

    def test_load_agents_rejects_bad_effort_value(self, tmp_path: Path) -> None:
        ag_path = _write_json(
            tmp_path / "agents.json",
            {"version": "1.0", "agents": {"ag": {"executor": "claude_cli", "effort": "ultra"}}},
        )
        with pytest.raises(SpecValidationError):
            load_agents(ag_path)


# ---------------------------------------------------------------------------
# Integration: engine dispatch resolution reaches the executor
# ---------------------------------------------------------------------------


def _settings_workflow(workspace: Path, tasks: list[TaskSpec], wf_id: str = "settings-wf"):
    """Build a WorkflowSpec from TaskSpec objects, writing stub instruction files.

    Bypasses the `make_workflow` conftest fixture (which does not forward
    model/effort/max_turns) -- same pattern `test_dynamic_injection.py` uses for
    WorkflowSpec shapes the shared factory doesn't cover.
    """
    instr_dir = workspace / "specs" / "instructions"
    instr_dir.mkdir(parents=True, exist_ok=True)
    for t in tasks:
        p = workspace / t.instruction
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"instruction for {t.id}")
    return WorkflowSpec(version="1.0", id=wf_id, repo_set="rs", tasks=tasks)


class TestEngineDispatchResolution:
    def test_task_overrides_reach_executor(self, make_orchestrator, workspace: Path) -> None:
        """Task-level model/effort/max_turns win over the agent's own, all the way through
        to the TaskContext the executor actually receives."""
        executor = FakeExecutor()
        orch, reposets, agents = make_orchestrator(executor)
        agents["ag"] = AgentSpec(executor="fake", model="agent-model", effort="low", max_turns=15)
        task = TaskSpec(
            id="t1",
            agent="ag",
            instruction="specs/instructions/t1.md",
            model="task-model",
            effort="xhigh",
            max_turns=111,
        )
        wf = _settings_workflow(workspace, [task])

        state = orch.run(wf, reposets, agents)

        assert state.status == "succeeded"
        resolved = executor.resolved_agents["t1"]
        assert resolved.model == "task-model"
        assert resolved.effort == "xhigh"
        assert resolved.max_turns == 111

    def test_task_without_overrides_falls_back_to_agent(
        self, make_orchestrator, workspace: Path
    ) -> None:
        """Regression: a task with no overrides dispatches with the agent's own settings
        unchanged."""
        executor = FakeExecutor()
        orch, reposets, agents = make_orchestrator(executor)
        agents["ag"] = AgentSpec(executor="fake", model="agent-model", effort="high", max_turns=60)
        task = TaskSpec(id="t1", agent="ag", instruction="specs/instructions/t1.md")
        wf = _settings_workflow(workspace, [task])

        state = orch.run(wf, reposets, agents)

        assert state.status == "succeeded"
        resolved = executor.resolved_agents["t1"]
        assert resolved.model == "agent-model"
        assert resolved.effort == "high"
        assert resolved.max_turns == 60

    def test_task_partial_override_falls_back_per_field(
        self, make_orchestrator, workspace: Path
    ) -> None:
        executor = FakeExecutor()
        orch, reposets, agents = make_orchestrator(executor)
        agents["ag"] = AgentSpec(executor="fake", model="agent-model", effort="high", max_turns=60)
        task = TaskSpec(id="t1", agent="ag", instruction="specs/instructions/t1.md", effort="xhigh")
        wf = _settings_workflow(workspace, [task])

        state = orch.run(wf, reposets, agents)

        assert state.status == "succeeded"
        resolved = executor.resolved_agents["t1"]
        assert resolved.model == "agent-model"  # unset on task -> agent's value
        assert resolved.effort == "xhigh"  # task wins
        assert resolved.max_turns == 60  # unset on task -> agent's value


# ---------------------------------------------------------------------------
# Integration: emit_tasks manifest carries effort/model to an injected task's dispatch
# ---------------------------------------------------------------------------


class TestInjectedTaskSettingsReachExecutor:
    def test_emitted_task_effort_model_reach_executor(
        self, make_orchestrator, workspace: Path
    ) -> None:
        """A task injected at run time via emit_tasks (Area 2) with `effort`/`model` in its
        manifest entry must have those fields resolved through to the executor exactly like
        a statically-declared task -- proves TaskSpec(**t) in read_task_manifest round-trips
        the new fields (they skip jsonschema entirely, per the injected-manifest learning)."""
        manifest_path = "output/task-manifest.json"
        emitted_task = {
            "id": "injected-a",
            "agent": "ag",
            "instruction": "specs/instructions/injected-a.md",
            "depends_on": ["emitter"],
            "effort": "xhigh",
            "model": "claude-opus-4-8",
        }
        executor = FakeExecutor(emit_payloads={"emitter": {"tasks": [emitted_task]}})
        orch, reposets, agents = make_orchestrator(executor)
        agents["ag"] = AgentSpec(executor="fake", model="agent-model", effort="low")

        task = TaskSpec(
            id="emitter",
            agent="ag",
            instruction="specs/instructions/emitter.md",
            emit_tasks=True,
            task_manifest_path=manifest_path,
        )
        wf = _settings_workflow(workspace, [task])

        state = orch.run(wf, reposets, agents)

        assert state.status == "succeeded"
        assert state.tasks["injected-a"].status == "succeeded"
        resolved = executor.resolved_agents["injected-a"]
        assert resolved.effort == "xhigh"
        assert resolved.model == "claude-opus-4-8"


# ---------------------------------------------------------------------------
# E2E (CliRunner): `ao validate` accepts/rejects task-level effort at the CLI boundary
# ---------------------------------------------------------------------------


def _write_reposets(tmp_path: Path) -> Path:
    return _write_json(
        tmp_path / "reposets.json",
        {
            "version": "1.0",
            "repo_sets": {
                "rs": {
                    "workspace_root": str(tmp_path),
                    "repos": [{"id": "core", "path": ".", "role": "primary"}],
                }
            },
        },
    )


def _write_agents(tmp_path: Path) -> Path:
    return _write_json(
        tmp_path / "agents.json", {"version": "1.0", "agents": {"ag": {"executor": "fake"}}}
    )


class TestValidateCliBoundary:
    def test_ao_validate_accepts_task_level_xhigh_effort(self, tmp_path: Path) -> None:
        instr_dir = tmp_path / "specs" / "instructions"
        instr_dir.mkdir(parents=True)
        (instr_dir / "t1.md").write_text("do the thing")

        wf = _write_json(
            tmp_path / "workflow.json",
            _minimal_workflow_dict(
                {
                    "effort": "xhigh",
                    "model": "claude-opus-4-8",
                    "outputs": ["out/t1.txt"],
                }
            ),
        )
        rs = _write_reposets(tmp_path)
        ag = _write_agents(tmp_path)

        result = runner.invoke(
            app,
            ["validate", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )

        assert result.exit_code == 0, result.output
        assert "OK" in result.output

    def test_ao_validate_rejects_bad_task_level_effort(self, tmp_path: Path) -> None:
        instr_dir = tmp_path / "specs" / "instructions"
        instr_dir.mkdir(parents=True)
        (instr_dir / "t1.md").write_text("do the thing")

        wf = _write_json(
            tmp_path / "workflow.json", _minimal_workflow_dict({"effort": "ultra-mega"})
        )
        rs = _write_reposets(tmp_path)
        ag = _write_agents(tmp_path)

        result = runner.invoke(
            app,
            ["validate", "--workflow", str(wf), "--reposets", str(rs), "--agents", str(ag)],
            env={"AO_WORKSPACE_ROOT": str(tmp_path)},
        )

        assert result.exit_code == 1
        assert "ERROR" in result.output
