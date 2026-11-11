"""T-Rk4Wn7 D4: the LLM lane's default spawn can write its outputs; denial is a distinct alarm."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from agent_orchestrator import sentinel_driver as sd
from agent_orchestrator import sentinel_tick
from agent_orchestrator.cli import app
from agent_orchestrator.models import AgentSpec
from tests.test_sentinel_generations import T0, ScriptedDriver, _drive, _lane


def _spawn_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, agent: AgentSpec | None
) -> list[str]:
    seen: list[list[str]] = []

    class FakePopen:
        def __init__(self, argv: list[str], **_: Any) -> None:
            seen.append(argv)

    monkeypatch.setattr(subprocess, "Popen", FakePopen)
    pack = tmp_path / "g01" / "pack"
    pack.mkdir(parents=True)
    drv = sd.ClaudeCliSentinelDriver(agent)
    drv.start(
        str(pack), sd.GenerationLimits(60, 1000, generation=1, inbox_dir=str(tmp_path / "inbox"))
    )
    return seen[0]


def test_default_spawn_is_least_privilege_and_scoped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    argv = _spawn_argv(monkeypatch, tmp_path, None)
    assert "--dangerously-skip-permissions" not in argv
    assert argv[argv.index("--permission-mode") + 1] == "acceptEdits"
    allowed = argv[argv.index("--allowedTools") + 1]
    assert "Write" in allowed and "Bash(mv:*)" in allowed and "Bash(*)" not in allowed
    dirs = [argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"]
    assert dirs == [str(tmp_path / "g01"), str(tmp_path / "inbox")]


def test_explicit_agent_permission_flags_are_not_overridden(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    agent = AgentSpec(
        executor="claude_cli",
        command_template=["claude", "--dangerously-skip-permissions", "-p", "{prompt}"],
    )
    argv = _spawn_argv(monkeypatch, tmp_path, agent)
    assert "--permission-mode" not in argv and "--allowedTools" not in argv


def test_bare_explicit_agent_is_not_given_lane_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An explicit llm.agent is never altered, so the startup warning stays truthful.
    bare = AgentSpec(executor="claude_cli")
    argv = _spawn_argv(monkeypatch, tmp_path, bare)
    assert "--permission-mode" not in argv and "--allowedTools" not in argv
    assert "--add-dir" not in argv
    assert sd.lane_permission_warnings("act", bare, "a")


def test_warning_for_act_agent_without_permission_flags() -> None:
    bare = AgentSpec(executor="claude_cli")
    assert sd.lane_permission_warnings("act", bare, "a")
    assert not sd.lane_permission_warnings("advise", bare, "a")
    assert not sd.lane_permission_warnings("act", None)
    ok = AgentSpec(
        executor="claude_cli",
        command_template=["claude", "--permission-mode=acceptEdits", "-p", "{prompt}"],
    )
    assert not sd.lane_permission_warnings("act", ok, "a")


@pytest.mark.parametrize(
    "tpl",
    [
        ["env", "FOO=1", "claude", "-p", "{prompt}"],
        ["npx", "-y", "@anthropic-ai/claude-code", "-p", "{prompt}"],
        ["/usr/local/bin/claude", "-p", "{prompt}"],
    ],
)
def test_warning_for_wrapped_claude(tpl: list[str]) -> None:
    bare = AgentSpec(executor="claude_cli", command_template=tpl)
    assert sd.lane_permission_warnings("act", bare, "a")
    ok = AgentSpec(executor="claude_cli", command_template=[*tpl, "--dangerously-skip-permissions"])
    assert not sd.lane_permission_warnings("act", ok, "a")
    other = AgentSpec(executor="claude_cli", command_template=["env", "FOO=1", "other", "-p"])
    assert not sd.lane_permission_warnings("act", other, "a")


def test_detect_permission_denial() -> None:
    ev = {"type": "result", "permission_denials": [{"tool_name": "Write"}, {"tool_name": "Bash"}]}
    assert "2 tool call(s) denied: Bash, Write" == sd.detect_permission_denial(
        "noise\n" + json.dumps(ev) + "\n"
    )
    assert (
        sd.detect_permission_denial(json.dumps({"type": "result", "permission_denials": []}))
        is None
    )
    assert sd.detect_permission_denial("not json") is None


class DeniedDriver(ScriptedDriver):
    def poll(self, handle: sd.Handle) -> sd.DriverStatus:
        st = super().poll(handle)
        if not st.alive:
            st.permission_denied = "1 tool call(s) denied: Write"
        return st


def test_permission_denial_raises_distinct_alarm_and_pauses(tmp_path: Path) -> None:
    drv = DeniedDriver({1: {"polls": 1, "rc": 1, "handoff": False}})
    lane, st = _lane(tmp_path, drv)
    _drive(lane, st, T0, 6)
    assert st.sentinel is not None and st.sentinel.llm_paused
    alarms = [
        json.loads(x)
        for x in (tmp_path / sentinel_tick.CONTROL_DIR / sentinel_tick.ALARMS_FILE)
        .read_text()
        .splitlines()
    ]
    assert [a["kind"] for a in alarms] == ["llm_permission_denied"]
    events = [
        json.loads(x)["event"]
        for x in (tmp_path / "sentinel" / "ledger.jsonl").read_text().splitlines()
    ]
    assert "sentinel_permission_denied" in events and "sentinel_ungraceful" in events
    assert drv.started == [1]  # paused: no identical retry generations


def test_validate_warns_on_act_agent_without_permissions(tmp_path: Path) -> None:
    wf = {
        "version": "1.0",
        "id": "w",
        "tasks": [
            {"id": "t", "agent": "bare", "instruction": "i.md", "outputs": ["o.md"]},
        ],
        "sentinel": {"enabled": True, "llm": {"enabled": True, "powers": "act", "agent": "bare"}},
    }
    agents = {"version": "1.0", "agents": {"bare": {"executor": "claude_cli"}}}
    (tmp_path / "wf.json").write_text(json.dumps(wf))
    (tmp_path / "ag.json").write_text(json.dumps(agents))
    res = CliRunner().invoke(
        app,
        [
            "validate",
            "--workflow",
            str(tmp_path / "wf.json"),
            "--agents",
            str(tmp_path / "ag.json"),
        ],
    )
    assert "sentinel.llm.agent 'bare' has no permission flag" in res.output, res.output
