"""thinking_effort: provider-neutral reasoning-depth knob (claude_cli -> ``--effort``)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_orchestrator.executors.claude_cli import build_claude_argv
from agent_orchestrator.models import AgentSpec, TaskSpec, resolve_effective_agent


def _agent(**kw: object) -> AgentSpec:
    return AgentSpec(name="a", executor="claude_cli", **kw)  # type: ignore[arg-type]


def test_unset_injects_no_effort_flag() -> None:
    assert "--effort" not in build_claude_argv(_agent(effort="high"), "p")


@pytest.mark.parametrize("level", ["low", "medium", "high", "xhigh", "max"])
def test_flag_injected(level: str) -> None:
    argv = build_claude_argv(_agent(thinking_effort=level), "p")
    assert argv[argv.index("--effort") + 1] == level


def test_explicit_flag_in_extra_args_wins() -> None:
    argv = build_claude_argv(_agent(thinking_effort="max", extra_args=["--effort", "low"]), "p")
    assert argv.count("--effort") == 1 and argv[argv.index("--effort") + 1] == "low"


def test_turn_cap_effort_independent_of_thinking_effort() -> None:
    argv = build_claude_argv(_agent(effort="low", thinking_effort="max"), "p")
    assert argv[argv.index("--max-turns") + 1] == "15"
    assert argv[argv.index("--effort") + 1] == "max"


def test_invalid_level_rejected() -> None:
    with pytest.raises(ValidationError):
        _agent(thinking_effort="ultra")


def test_task_overrides_agent_and_falls_through() -> None:
    agent = _agent(thinking_effort="low")
    assert resolve_effective_agent(TaskSpec(id="t", agent="a", instruction="i.md"), agent) is agent
    t = TaskSpec(id="t", agent="a", instruction="i.md", thinking_effort="max")
    assert resolve_effective_agent(t, agent).thinking_effort == "max"


def test_unset_thinking_effort_leaves_cache_key_doc_unchanged() -> None:
    from agent_orchestrator.cache.constants import AGENT_KEY_FIELDS, AGENT_KEY_OMIT_WHEN_NONE

    assert "thinking_effort" in AGENT_KEY_FIELDS and "thinking_effort" in AGENT_KEY_OMIT_WHEN_NONE
