"""Tests for AgentSpec.forbidden_task_models (per-agent guard on task-level `model` overrides).

Covers the static `cross_validate` path and the agent-authored `emit_tasks` injection path.
"""

from __future__ import annotations

import pytest

from agent_orchestrator.errors import SpecValidationError
from agent_orchestrator.executors.fake import FakeExecutor
from agent_orchestrator.models import AgentSpec, TaskSpec, WorkflowSpec
from agent_orchestrator.spec import cross_validate, validate_task_model_policy

HAIKU = "claude-haiku-4-5-20251001"


def _agents(forbidden: list[str]) -> dict:
    return {"ag": AgentSpec(executor="fake", forbidden_task_models=forbidden)}


def _task(model: str | None) -> TaskSpec:
    return TaskSpec(id="t1", agent="ag", instruction="i.md", model=model)


class TestValidateTaskModelPolicy:
    def test_forbidden_model_rejected(self) -> None:
        with pytest.raises(SpecValidationError, match="forbidden_task_models") as ei:
            validate_task_model_policy([_task(HAIKU)], _agents(["haiku"]))
        assert ei.value.path == "tasks.t1.model"

    def test_match_is_case_insensitive(self) -> None:
        with pytest.raises(SpecValidationError):
            validate_task_model_policy([_task("Claude-HAIKU-4-5")], _agents(["Haiku"]))

    def test_allowed_model_passes(self) -> None:
        validate_task_model_policy([_task("claude-sonnet-5-5")], _agents(["haiku"]))

    def test_no_override_inherits_and_passes(self) -> None:
        validate_task_model_policy([_task(None)], _agents(["haiku"]))

    def test_default_agent_has_no_restriction(self) -> None:
        validate_task_model_policy([_task(HAIKU)], _agents([]))

    def test_unknown_agent_skipped(self) -> None:
        # Unknown agents are cross_validate's job, not this rule's.
        validate_task_model_policy([_task(HAIKU)], {})


def test_cross_validate_rejects_forbidden_model() -> None:
    wf = WorkflowSpec(version="1.0", id="wf", repo_set="rs", tasks=[_task(HAIKU)])
    with pytest.raises(SpecValidationError, match="not allowed for agent"):
        cross_validate(wf, {"rs": object()}, _agents(["haiku"]))


class TestEmitTasksInjection:
    def _emit(self, make_workflow, make_orchestrator, model: str | None):
        wf = make_workflow(
            [
                {
                    "id": "emitter",
                    "emit_tasks": True,
                    "task_manifest_path": "output/m.json",
                    "outputs": [],
                }
            ]
        )
        emitted = {
            "id": "injected-a",
            "agent": "ag",
            "instruction": "specs/instructions/emitter.md",
            "outputs": ["output/injected.txt"],
            "depends_on": ["emitter"],
        }
        if model:
            emitted["model"] = model
        orch, reposets, agents = make_orchestrator(
            FakeExecutor(emit_payloads={"emitter": {"tasks": [emitted]}})
        )
        agents["ag"] = AgentSpec(executor="fake", forbidden_task_models=["haiku"])
        return orch.run(wf, reposets, agents)

    def test_forbidden_model_in_manifest_fails_run(self, make_workflow, make_orchestrator) -> None:
        state = self._emit(make_workflow, make_orchestrator, HAIKU)
        assert state.status == "failed"
        assert "injected-a" not in state.tasks

    def test_allowed_model_in_manifest_runs(self, make_workflow, make_orchestrator) -> None:
        state = self._emit(make_workflow, make_orchestrator, "claude-sonnet-5-5")
        assert state.status == "succeeded"
        assert state.tasks["injected-a"].status == "succeeded"


def test_recommended_agents_forbid_haiku_on_rigour_roles() -> None:
    import json
    from pathlib import Path

    import agent_orchestrator

    tmpl = Path(agent_orchestrator.__file__).parent / "templates/builtin/routed-runner"
    agents = json.loads((tmpl / "agents.recommended.json.tmpl").read_text())["agents"]
    protected = {
        "architect",
        "architect-opus",
        "reviewer-opus",
        "reviewer",
        "full-tester",
        "manager",
    }
    for role in protected:
        assert "haiku" in agents[role]["forbidden_task_models"], role
    # The roles Haiku may be assigned to must stay unrestricted.
    for role in ("developer", "tester"):
        assert "forbidden_task_models" not in agents[role], role
    contract = (tmpl / "breakdown-contract.md.tmpl").read_text()
    assert "claude-haiku-4-5-20251001" in contract and "Haiku tier" in contract
