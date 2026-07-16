"""Tests for config loading + agents.schema.json ↔ AgentSpec parity."""

from __future__ import annotations

import json

import pytest

from agent_orchestrator.config import load_agents
from agent_orchestrator.errors import SpecValidationError


def _write_agents(tmp_path, agent: dict) -> str:
    p = tmp_path / "agents.json"
    p.write_text(json.dumps({"version": "1.0", "agents": {"dev": agent}}))
    return str(p)


class TestLoadAgentsSchema:
    """agents.schema.json has additionalProperties:false, so every AgentSpec field
    it omits is silently rejected at `ao validate` time — these guard schema↔model parity."""

    def test_accepts_max_turns_working_dir_disallowed_tools(self, tmp_path) -> None:
        # Regression: max_turns/working_dir were on AgentSpec but MISSING from the
        # schema, so a valid spec using them was wrongly rejected (fixed 2026-07-15).
        path = _write_agents(
            tmp_path,
            {
                "executor": "claude_cli",
                "max_turns": 42,
                "working_dir": "sub/dir",
                "disallowed_tools": ["BashOutput", "KillShell", "KillBash"],
            },
        )
        spec = load_agents(path)["dev"]
        assert spec.max_turns == 42
        assert spec.working_dir == "sub/dir"
        assert spec.disallowed_tools == ["BashOutput", "KillShell", "KillBash"]

    def test_disallowed_tools_defaults_to_allow_all(self, tmp_path) -> None:
        path = _write_agents(tmp_path, {"executor": "claude_cli"})
        assert load_agents(path)["dev"].disallowed_tools == []

    def test_rejects_unknown_field(self, tmp_path) -> None:
        # additionalProperties:false must stay enforced after the field additions.
        path = _write_agents(tmp_path, {"executor": "claude_cli", "not_a_real_field": 1})
        with pytest.raises(SpecValidationError):
            load_agents(path)
