"""Task-level model selection: id validation and precedence over a baked-in `--model`."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_orchestrator.models import AgentSpec, TaskSpec, resolve_effective_agent, strip_model_flag

_BASE = {"id": "t1", "agent": "a", "instruction": "i.md"}


@pytest.mark.parametrize(
    "model",
    ["opus", "claude-opus-4-8", "claude-haiku-4-5-20251001", "sonnet[1m]", "claude-sonnet-5-5"],
)
def test_task_model_accepts_ids_and_aliases(model: str) -> None:
    assert TaskSpec(**_BASE, model=model).model == model


@pytest.mark.parametrize("model", ["--dangerously-skip-permissions", "a b", "x;rm -rf", "", "-m"])
def test_task_model_rejects_flag_like_or_malformed(model: str) -> None:
    with pytest.raises(ValidationError):
        TaskSpec(**_BASE, model=model)


def test_task_model_overrides_baked_model_flag() -> None:
    agent = AgentSpec(
        executor="claude_cli",
        command_template=["claude", "-p", "{prompt}", "--model", "claude-opus-5-5"],
    )
    eff = resolve_effective_agent(TaskSpec(**_BASE, model="claude-sonnet-4-5"), agent)
    assert eff.model == "claude-sonnet-4-5"
    assert "--model" not in eff.command_template and "claude-opus-5-5" not in eff.command_template
    assert agent.command_template[-1] == "claude-opus-5-5"  # original untouched


def test_unset_task_model_keeps_baked_flag() -> None:
    agent = AgentSpec(
        executor="claude_cli", command_template=["claude", "--model", "claude-opus-5-5"]
    )
    assert resolve_effective_agent(TaskSpec(**_BASE), agent) is agent


def test_strip_model_flag_variants() -> None:
    assert strip_model_flag(["c", "--model=x", "-m", "y", "z"]) == ["c", "z"]
