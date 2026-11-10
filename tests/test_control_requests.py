"""T-Sn5Ui1: shared control-request validation, outcome probe and the extended status document."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agent_orchestrator import control, sentinel_tick
from agent_orchestrator.models import (
    EffectiveSetting,
    RunState,
    SentinelKill,
    SentinelNote,
    SentinelState,
    TaskRecovery,
    TaskRunState,
)

NOW = datetime(2026, 11, 9, 12, 0, 0, tzinfo=UTC)
STAMP = "2026-11-09T10:00:00+00:00"


def _state(**sent: object) -> RunState:
    state = RunState(
        run_id="wf-1",
        workflow_id="wf",
        status="running",
        started_at=STAMP,
        updated_at=STAMP,
    )
    state.tasks["t0"] = TaskRunState(status="running", started_at="2026-11-09T11:00:00+00:00")
    state.tasks["t1"] = TaskRunState(status="pending")
    state.effective_settings["sentinel.max_parallel_ceiling"] = EffectiveSetting(
        value=4, source="cli"
    )
    state.sentinel = SentinelState(**sent)  # type: ignore[arg-type]
    return state


# -- validate_request -------------------------------------------------------------------------


def test_set_values_are_typed_and_bounded_by_ceiling() -> None:
    state = _state()
    payload, rationale = control.validate_request(
        "set", {"values": {"max_parallel": 3}}, " why ", state
    )
    assert payload == {"values": {"max_parallel": 3}} and rationale == "why"
    for bad in (0, 5, True, "x", 2.5):
        with pytest.raises(control.ControlError):
            control.validate_request("set", {"values": {"max_parallel": bad}}, None, state)
    with pytest.raises(control.ControlError, match="not live-overridable"):
        control.validate_request("set", {"values": {"tick_seconds": 5}}, None, state)
    with pytest.raises(control.ControlError):
        control.validate_request("set", {"values": {}}, None, state)
    with pytest.raises(control.ControlError):
        control.validate_request("set", {}, None, state)


def test_kill_requires_reason_and_a_running_task() -> None:
    state = _state()
    payload, rationale = control.validate_request(
        "kill", {"task_id": "t0", "no_retry": True}, "stuck", state
    )
    assert payload == {"task_id": "t0", "no_retry": True} and rationale == "stuck"
    assert control.validate_request("kill", {"task_id": "t0"}, "r", state)[0]["no_retry"] is False
    for payload_in, why in (
        ({"task_id": "t0"}, None),  # reason required
        ({"task_id": "t0"}, "   "),
        ({"task_id": "t1"}, "r"),  # pending, not running
        ({"task_id": "nope"}, "r"),
        ({}, "r"),
        ({"task_id": "t0", "no_retry": "yes"}, "r"),
    ):
        with pytest.raises(control.ControlError):
            control.validate_request("kill", payload_in, why, state)
    with pytest.raises(control.ControlError, match="at most"):
        control.validate_request("kill", {"task_id": "t0"}, "x" * 300, state)


def test_note_simple_kinds_and_unknown_kind() -> None:
    state = _state()
    assert control.validate_request("note", {"text": " hi "}, None, state)[0] == {"text": "hi"}
    with pytest.raises(control.ControlError):
        control.validate_request("note", {"text": ""}, None, state)
    with pytest.raises(control.ControlError):
        control.validate_request(
            "note", {"text": "x" * (sentinel_tick.NOTE_TEXT_MAX + 1)}, None, state
        )
    for kind in ("pause_llm", "resume_llm", "request_cancel"):
        assert control.validate_request(kind, {"junk": 1}, None, state)[0] == {}
    assert control.validate_request("release_backlog", {"ids": ["a"]}, None, state)[0] == {
        "ids": ["a"]
    }
    for bad in ("inject", "directive", "edit_pending", "request_hold", "cancel", "", "set "):
        with pytest.raises(control.ControlError, match="unsupported"):
            control.validate_request(bad, {}, None, state)


def test_dashboard_actor() -> None:
    assert control.dashboard_actor("alice") == "dashboard:alice"
    assert control.dashboard_actor(None) == "dashboard:local"
    assert control.dashboard_actor("  ") == "dashboard:local"


# -- entry_outcome ----------------------------------------------------------------------------


def test_entry_outcome_pending_consumed_rejected(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    ctl = run_dir / sentinel_tick.CONTROL_DIR
    assert control.entry_outcome(run_dir, "note-20261109t000000-abcd1234") == {"outcome": "pending"}
    (ctl / sentinel_tick.PROCESSED_DIR).mkdir(parents=True)
    (ctl / sentinel_tick.PROCESSED_DIR / "a-20261109t000000-aaaa.json").write_text("{}")
    assert control.entry_outcome(run_dir, "a-20261109t000000-aaaa")["outcome"] == "consumed"
    (ctl / sentinel_tick.REJECTED_DIR).mkdir(parents=True)
    (ctl / sentinel_tick.REJECTED_DIR / "b-20261109t000000-bbbb.reason.json").write_text(
        json.dumps({"rule_id": "CTL-CEILING", "detail": "too big"})
    )
    assert control.entry_outcome(run_dir, "b-20261109t000000-bbbb") == {
        "outcome": "rejected",
        "rule_id": "CTL-CEILING",
        "detail": "too big",
    }
    for bad in ("../x", "A/b", "x", ""):
        with pytest.raises(control.ControlError):
            control.entry_outcome(run_dir, bad)


# -- status -----------------------------------------------------------------------------------


def test_status_old_run_has_no_sentinel() -> None:
    state = RunState(
        run_id="old-1",
        workflow_id="wf",
        status="succeeded",
        started_at=STAMP,
        updated_at=STAMP,
    )
    doc = control.build_status(state, Path("/nonexistent"), now=NOW)
    assert doc["sentinel"]["enabled"] is False
    assert doc["llm"] == {
        "generation": None,
        "spend_usd": None,
        "paused": False,
        "pause_reason": None,
        "budget_exhausted": False,
        "enabled": None,
        "powers": None,
    }
    assert doc["ladder"] == {} and doc["recent_actions"] == []


def test_status_liveness_ladder_pause_reason_and_actions() -> None:
    state = _state(
        llm_paused=True,
        llm_pause_reason="5 consecutive rejected proposals",
        systemic_alarm=True,
        systemic_alarm_at="2026-11-09T11:30:00+00:00",
        generation=2,
        notes=[SentinelNote(id="n1", actor="cli:a", at="2026-11-09T11:00:00+00:00", text="hello")],
        kill_log=[
            SentinelKill(
                task_id="t9", at="2026-11-09T11:10:00+00:00", reason="idle", source="mechanical"
            )
        ],
    )
    state.effective_settings["sentinel.llm.powers"] = EffectiveSetting(value="act", source="cli")
    state.effective_settings["sentinel.llm.enabled"] = EffectiveSetting(value=True, source="cli")
    state.tasks["t0"].last_activity_at = "2026-11-09T11:58:30+00:00"
    state.tasks["t0"].recovery = TaskRecovery(
        kills=1, last_reason="idle", last_source="mechanical", escalation_step=1
    )
    doc = control.build_status(state, Path("/nonexistent"), now=NOW)
    assert doc["tasks"]["running"] == [{"id": "t0", "liveness_age_s": 90.0}]
    assert doc["ladder"] == {
        "t0": {
            "kills": 1,
            "last_reason": "idle",
            "last_source": "mechanical",
            "escalation_step": 1,
            "no_retry": False,
        }
    }
    assert doc["sentinel"]["systemic_alarm"] is True
    assert doc["llm"]["pause_reason"] == "5 consecutive rejected proposals"
    assert doc["llm"]["powers"] == "act" and doc["llm"]["enabled"] is True
    assert doc["llm"]["generation"] == 2
    assert [a["kind"] for a in doc["recent_actions"]] == ["note", "kill"]
    assert doc["recent_actions"][1]["text"] == "t9: idle"
    json.dumps(doc)  # serialisable as-is


def test_cli_kill_no_retry_matches_engine_payload_key(tmp_path: Path) -> None:
    """The engine reads ``payload.no_retry`` (sentinel_tick); the CLI used to send ``retry``."""
    from typer.testing import CliRunner

    from agent_orchestrator.cli import app

    run_dir = tmp_path / ".orchestrator" / "runs" / "wf-1"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(_state().model_dump_json())
    res = CliRunner().invoke(
        app,
        ["ctl", "kill", "wf-1", "t0", "--reason", "r", "--no-retry", "--json", "-w", str(tmp_path)],
    )
    assert res.exit_code == 0, res.output
    entry = json.loads(next((run_dir / "control" / "inbox").glob("kill-*.json")).read_text())
    assert entry["payload"] == {"task_id": "t0", "no_retry": True}
