"""Smoke test: package imports and version."""

import agent_orchestrator


def test_version() -> None:
    assert agent_orchestrator.__version__ == "0.1.0"
