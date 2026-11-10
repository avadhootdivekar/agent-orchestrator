"""T-Un0Lm6: ``until`` termination of recurring schedules (HLD §6.8). Fixed clocks throughout.

Unit tier drives ``until_satisfied`` / ``ScheduleEngine.evaluate`` directly; the e2e tier goes
through ``CliRunner`` (``add`` / ``daemon --once`` / ``enable`` / ``list``) with a fixed clock.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from agent_orchestrator.cli import app
from agent_orchestrator.schedules import cli as sched_cli
from agent_orchestrator.schedules.models import ScheduleBinding, UntilSpec
from agent_orchestrator.service.fire_store import ScheduleState, ScheduleStateStore
from agent_orchestrator.service.schedule_engine import (
    DEFAULT_UNTIL_MAX_RUNS,
    until_condition,
    until_satisfied,
)

from ..schedules._fixtures import minimal_limits
from ..schedules.test_cli_e2e import Env as CliEnv
from .test_schedule_engine import T0, env_for

runner = CliRunner()
E2E_T0 = datetime(2026, 3, 4, 5, 6, 7, tzinfo=UTC)


def ub(**until: Any) -> ScheduleBinding:
    return ScheduleBinding(
        id="s",
        kind="interval",
        interval_seconds=60,
        workflow="w.json",
        limits=minimal_limits(),
        until=UntilSpec(**until),
    )


class Events:
    """Collects emitted events (the engine's ScheduleEventLog interface: ``emit``)."""

    def __init__(self) -> None:
        self.items: list[tuple[str, dict[str, Any]]] = []

    def emit(self, event: str, level: int | None = None, **fields: Any) -> None:
        self.items.append((event, {"level": level, **fields}))

    def names(self) -> list[str]:
        return [n for n, _ in self.items]


def gate(tmp_path: Path, content: str | bytes | None, name: str = "gate.json") -> None:
    p = tmp_path / name
    if content is None:
        return
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content)


# -- AC10: one test per condition -------------------------------------------------------------


@pytest.mark.parametrize("runs,expected", [(2, False), (3, True), (4, True)])
def test_max_runs_reached_exactly(tmp_path: Path, runs: int, expected: bool) -> None:
    b = ub(max_runs=3)
    assert until_satisfied(tmp_path, b, ScheduleState(runs_count=runs)) is expected
    assert (until_condition(tmp_path, b, ScheduleState(runs_count=runs)) == "max_runs") is expected


def test_artifact_exists_present_and_absent(tmp_path: Path) -> None:
    b = ub(artifact_exists="out/DONE")
    assert until_satisfied(tmp_path, b, ScheduleState()) is False
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "DONE").write_text("")
    assert until_condition(tmp_path, b, ScheduleState()) == "artifact_exists"


@pytest.mark.parametrize(
    "content,expected,unreadable",
    [
        ('{"done": true}', True, False),
        ('{"done": false}', False, False),
        ('{"other": true}', False, False),  # field absent
        ('{"done": "true"}', False, False),  # wrong type: str vs bool
        ('{"done": 1}', False, False),  # True == 1 must not end a loop
        ('{"done": 1.0}', False, False),
        ('{"done": {"x": 1}}', False, False),
        ('{"done": null}', False, False),
        ("[true]", False, False),  # not an object
        ("{not json", False, True),  # malformed
        (b"\xff\xfe\x00bad", False, True),  # undecodable
        (None, False, False),  # missing file: not done, not an error
    ],
)
def test_gate_file_variants(
    tmp_path: Path, content: str | bytes | None, expected: bool, unreadable: bool
) -> None:
    gate(tmp_path, content)
    ev = Events()
    assert until_satisfied(tmp_path, ub(gate_file="gate.json"), ScheduleState(), ev) is expected
    assert ("schedule.until_unreadable" in ev.names()) is unreadable
    if unreadable:
        level, err = ev.items[0][1]["level"], ev.items[0][1]["error"]
        assert level == 30 and err  # WARNING, with the error text


def test_gate_unreadable_when_path_is_a_directory(tmp_path: Path) -> None:
    (tmp_path / "gate.json").mkdir()
    ev = Events()
    # a directory is "not a file": absent, not an error
    assert until_satisfied(tmp_path, ub(gate_file="gate.json"), ScheduleState(), ev) is False


def test_gate_unreadable_permission(tmp_path: Path) -> None:
    p = tmp_path / "gate.json"
    p.write_text('{"done": true}')
    p.chmod(0)
    ev = Events()
    try:
        got = until_satisfied(tmp_path, ub(gate_file="gate.json"), ScheduleState(), ev)
    finally:
        p.chmod(0o600)
    if got:  # running as root: chmod 0 is not enforced; nothing to assert about the error path
        pytest.skip("permission bits not enforced (root)")
    assert "schedule.until_unreadable" in ev.names()


def test_gate_custom_field_and_values(tmp_path: Path) -> None:
    gate(tmp_path, '{"state": "finished", "n": 5}')
    assert until_satisfied(
        tmp_path,
        ub(gate_file="gate.json", gate_field="state", gate_equals="finished"),
        ScheduleState(),
    )
    assert until_satisfied(
        tmp_path, ub(gate_file="gate.json", gate_field="n", gate_equals=5), ScheduleState()
    )
    assert not until_satisfied(
        tmp_path, ub(gate_file="gate.json", gate_field="n", gate_equals=6), ScheduleState()
    )


def test_no_until_is_never_satisfied(tmp_path: Path) -> None:
    b = ScheduleBinding(
        id="s", kind="interval", interval_seconds=60, workflow="w.json", limits=minimal_limits()
    )
    assert until_satisfied(tmp_path, b, ScheduleState(runs_count=10**6)) is False


def test_symlink_escape_is_unreadable_not_satisfied(tmp_path: Path) -> None:
    """AC5: the workspace-root guard applies at read time (a symlink out of the root)."""
    outside = tmp_path / "outside.json"
    outside.write_text('{"done": true}')
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "gate.json").symlink_to(outside)
    ev = Events()
    assert until_satisfied(ws, ub(gate_file="gate.json"), ScheduleState(), ev) is False
    assert "schedule.until_unreadable" in ev.names()
    (ws / "link").symlink_to(outside)
    assert until_satisfied(ws, ub(artifact_exists="link"), ScheduleState()) is False


# -- AC11: ordering + call-site placement -----------------------------------------------------


def test_max_runs_beats_a_gate_that_says_continue(tmp_path: Path) -> None:
    gate(tmp_path, '{"done": false}')
    b = ub(max_runs=2, gate_file="gate.json")
    assert until_condition(tmp_path, b, ScheduleState(runs_count=2)) == "max_runs"
    assert until_condition(tmp_path, b, ScheduleState(runs_count=1)) is None


def test_max_runs_checked_before_gate_is_even_read(tmp_path: Path) -> None:
    gate(tmp_path, "{broken")  # would emit unreadable if read
    ev = Events()
    assert until_satisfied(
        tmp_path, ub(max_runs=1, gate_file="gate.json"), ScheduleState(runs_count=1), ev
    )
    assert ev.items == []


def test_evaluate_completes_without_next_fire(tmp_path: Path) -> None:
    env = env_for(tmp_path, [_engine_binding(max_runs=1)])
    env.anchor()
    env.engine.state_store.record_fire(
        env.root, "s", scheduled_for=T0, run_id="r0", fire_at=T0, fire_key="k0"
    )
    d = env.evaluate(now=T0 + timedelta(hours=1))
    assert (d.fire, d.reason) == (False, "until_satisfied")
    st = env.engine.state_store.get(env.root, "s")
    assert st is not None and st.status == "completed" and st.runs_count == 1
    assert st.disabled_reason == "until:max_runs"
    (ev,) = env.events("schedule.until_satisfied")
    assert ev["runs_count"] == 1 and ev["schedule_id"] == "s"
    # the binding on disk is untouched
    assert env.binding("s").until is not None and env.binding("s").enabled
    # next evaluation reports "completed" from the status check
    assert env.evaluate(now=T0 + timedelta(hours=2)).reason == "completed"


def test_satisfied_schedule_does_not_drain_queued_fire(tmp_path: Path) -> None:
    env = env_for(tmp_path, [_engine_binding(max_runs=1)])
    env.engine.state_store.record_fire(
        env.root, "s", scheduled_for=T0, run_id="r0", fire_at=T0, fire_key="k0"
    )
    env.end_run("r0")
    env.engine.state_store.set_queued(env.root, "s", "queued-key", T0)
    d = env.evaluate(now=T0 + timedelta(hours=1))
    assert (d.fire, d.reason) == (False, "until_satisfied")
    st = env.engine.state_store.get(env.root, "s")
    assert st is not None and st.queued_fire_key == "queued-key"  # not drained, not cleared


def test_not_satisfied_still_fires(tmp_path: Path) -> None:
    env = env_for(tmp_path, [_engine_binding(max_runs=5)])
    env.anchor()
    d = env.evaluate(now=T0 + timedelta(seconds=60))
    assert d.fire and d.reason == "due"


def test_disabled_check_precedes_until(tmp_path: Path) -> None:
    env = env_for(tmp_path, [_engine_binding(max_runs=1, enabled=False)])
    env.engine.state_store.record_fire(
        env.root, "s", scheduled_for=T0, run_id="r0", fire_at=T0, fire_key="k0"
    )
    assert env.evaluate().reason == "disabled"
    assert env.events("schedule.until_satisfied") == []


def test_bad_gate_does_not_stop_the_schedule(tmp_path: Path) -> None:
    env = env_for(tmp_path, [_engine_binding(gate_file="gate.json")])
    Path(env.root, "gate.json").write_text("{nope")
    env.anchor()
    d = env.evaluate(now=T0 + timedelta(seconds=60))
    assert d.fire  # the next fire proceeds
    assert len(env.events("schedule.until_unreadable")) == 1


# -- AC14 coverage close-out: branches the coverage report showed unexercised -------------------


@pytest.mark.parametrize("content", ["{nope", b"\xff\xfe\x00bad"])
def test_unreadable_gate_without_event_sink_is_silent_and_never_satisfied(
    tmp_path: Path, content: str | bytes
) -> None:
    """``events=None`` (the default) skips the ``until_unreadable`` emit: no raise, no stop."""
    gate(tmp_path, content)
    assert until_condition(tmp_path, ub(gate_file="gate.json"), ScheduleState()) is None
    assert until_satisfied(tmp_path, ub(gate_file="gate.json"), ScheduleState()) is False


def test_plain_schedule_without_until_fires_through_the_engine(tmp_path: Path) -> None:
    """The engine's ``binding.until is None`` branch skips the until check entirely."""
    plain = _engine_binding(max_runs=1).model_copy(update={"until": None})
    env = env_for(tmp_path, [plain])
    env.anchor()
    d = env.evaluate(now=T0 + timedelta(seconds=60))
    assert d.fire and d.reason == "due"
    assert env.events("schedule.until_satisfied") == []


def _engine_binding(**kw: Any) -> ScheduleBinding:
    enabled = kw.pop("enabled", True)
    b = ub(**kw)
    return b.model_copy(update={"enabled": enabled})


# -- AC8/AC9/AC12: CLI e2e (CliRunner, fixed clock, daemon --once) ---------------------------


@pytest.fixture()
def cenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CliEnv:
    e = CliEnv(tmp_path)
    monkeypatch.setenv("AO_SERVICE_CONFIG", str(tmp_path / "service.yaml"))
    monkeypatch.setenv("AO_SERVICE_STATE_DIR", str(e.state))
    monkeypatch.chdir(e.ws)
    monkeypatch.setattr(sched_cli, "_now", lambda: E2E_T0)
    monkeypatch.setattr(sched_cli, "_SUPERVISOR_FACTORY", e.sup)
    return e


def _daemon_once(env: CliEnv) -> str:
    res = runner.invoke(app, ["schedule", "daemon", "--once", "--workspace", str(env.ws)])
    assert res.exit_code == 0, res.output
    return res.stdout.strip()


def _until_row(env: CliEnv) -> dict[str, Any]:
    row = env.data()["schedules"][0]
    assert isinstance(row, dict)
    return row


def test_add_supplies_default_max_runs_and_says_so(cenv: CliEnv) -> None:
    out = cenv.run("add", "loop", "--interval", "60", "--workflow", "w.json", "--quota", "day=100",
                   "--until-gate", "control/status.json").output  # fmt: skip
    assert str(DEFAULT_UNTIL_MAX_RUNS) in out and "--max-runs" in out
    until = _until_row(cenv)["until"]
    assert until["gate_file"] == "control/status.json"
    assert until["max_runs"] == DEFAULT_UNTIL_MAX_RUNS


def test_add_explicit_options_round_trip(cenv: CliEnv) -> None:
    out = cenv.run("add", "loop", "--interval", "60", "--workflow", "w.json", "--quota", "day=100",
                   "--until-gate", "g.json", "--until-field", "state", "--until-equals", "fin",
                   "--until-artifact", "DONE", "--max-runs", "7").output  # fmt: skip
    assert "bounding" not in out
    until = _until_row(cenv)["until"]
    assert until == {
        "artifact_exists": "DONE", "gate_file": "g.json", "gate_field": "state",
        "gate_equals": "fin", "max_runs": 7,
    }  # fmt: skip


@pytest.mark.parametrize("raw,typed", [("true", True), ("False", False), ("3", 3), ("x", "x")])
def test_until_equals_typing(cenv: CliEnv, raw: str, typed: object) -> None:
    cenv.add("loop", "--until-gate", "g.json", "--until-equals", raw)
    assert _until_row(cenv)["until"]["gate_equals"] == typed


def test_add_plain_schedule_has_no_until(cenv: CliEnv) -> None:
    out = cenv.run(
        "add", "plain", "--interval", "60", "--workflow", "w.json", "--quota", "day=100"
    ).output
    assert "until" not in _until_row(cenv) and "bounding" not in out


def test_add_rejects_field_without_gate_and_bad_path(cenv: CliEnv) -> None:
    cenv.run("add", "x", "--interval", "60", "--workflow", "w.json", "--quota", "day=100",
             "--until-field", "f", ok=False)  # fmt: skip
    res = cenv.run("add", "y", "--interval", "60", "--workflow", "w.json", "--quota", "day=100",
                   "--until-artifact", "/etc/passwd", ok=False)  # fmt: skip
    assert "relative" in res.output.lower() or "absolute" in res.output.lower()
    assert not cenv.sched_file.exists() or cenv.data()["schedules"] == []


def test_lifecycle_satisfy_complete_enable_restart(
    cenv: CliEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC12: satisfy -> completed -> `enable` resets runs_count -> next tick fires again."""
    cenv.add("loop", "--max-runs", "1", "--until-gate", "control/status.json")
    store = ScheduleStateStore(cenv.state)
    store.advance_anchor(cenv.ws, "loop", E2E_T0 - timedelta(seconds=120))

    assert _daemon_once(cenv) == "1"  # first fire
    st = ScheduleStateStore(cenv.state).get(cenv.ws, "loop")
    assert st is not None and st.runs_count == 1 and st.status == "active"

    assert _daemon_once(cenv) == "0"  # max_runs reached: completes, no fire
    st = ScheduleStateStore(cenv.state).get(cenv.ws, "loop")
    assert st is not None and st.status == "completed" and st.runs_count == 1
    assert cenv.data()["schedules"][0]["id"] == "loop"  # binding intact

    listed = json.loads(cenv.run("list", "--json").stdout)["schedules"][0]
    assert listed["status"] == "completed" and listed["runs_count"] == 1
    table = cenv.run("list").stdout
    assert "completed" in table and "RUNS" in table

    assert "restarted" in cenv.run("enable", "loop").output
    st = ScheduleStateStore(cenv.state).get(cenv.ws, "loop")
    assert st is not None and st.runs_count == 0 and st.status == "active"

    # next tick fires again: the (fixed) clock moves on so a fresh instant is due
    later = E2E_T0 + timedelta(minutes=10)
    monkeypatch.setattr(sched_cli, "_now", lambda: later)
    assert _daemon_once(cenv) == "1"
    events = [e["event"] for e in json.loads(cenv.run("history", "loop", "--json").stdout)]
    assert "schedule.until_satisfied" in events


def test_lifecycle_gate_file_ends_loop_via_daemon(cenv: CliEnv) -> None:
    cenv.add("loop", "--until-gate", "control/status.json")
    ScheduleStateStore(cenv.state).advance_anchor(cenv.ws, "loop", E2E_T0 - timedelta(seconds=120))
    (cenv.ws / "control").mkdir()
    (cenv.ws / "control" / "status.json").write_text('{"done": false}')
    assert _daemon_once(cenv) == "1"
    (cenv.ws / "control" / "status.json").write_text('{"done": true}')
    assert _daemon_once(cenv) == "0"
    st = ScheduleStateStore(cenv.state).get(cenv.ws, "loop")
    assert st is not None and st.status == "completed"


def test_yaml_until_survives_enable(cenv: CliEnv) -> None:
    cenv.add("loop", "--max-runs", "2")
    cenv.run("disable", "loop")
    cenv.run("enable", "loop")
    assert yaml.safe_load(cenv.sched_file.read_text())["schedules"][0]["until"]["max_runs"] == 2


def test_float_gate_value_never_equals_an_int_gate(tmp_path: Path) -> None:
    """AC7: ``1.0 == 1`` in Python, but a float in the JSON must compare unequal."""
    gate(tmp_path, '{"n": 1.0}')
    b = ub(gate_file="gate.json", gate_field="n", gate_equals=1)
    assert until_satisfied(tmp_path, b, ScheduleState()) is False


# -- AC13: LoopSpec composition (characterization) --------------------------------------------


def _loop_workflow(make_workflow: Any) -> Any:
    return make_workflow(
        [
            {"id": "develop", "outputs": ["output/impl.md"]},
            {"id": "review", "inputs": ["output/impl.md"], "depends_on": ["develop"]},
            {"id": "finalize", "depends_on": ["dev-review-loop"], "outputs": ["output/final.md"]},
        ],
        wf_id="loop-wf",
        loops=[
            {
                "id": "dev-review-loop",
                "body": ["develop", "review"],
                "gate_task_id": "review",
                "gate_output_path": "output/review-verdict.json",
                "max_iterations": 5,
            }
        ],
    )


def test_loopspec_workflow_runs_unchanged_under_until_schedule(
    tmp_path: Path, make_workflow: Any, make_orchestrator: Any
) -> None:
    """AC13: ``until`` (inter-run) and ``LoopSpec`` (intra-run) compose without knowing about
    each other. One scheduled fire of a 2-iteration loop workflow is ONE ``runs_count`` tick
    (not one per iteration), the loop expands exactly as without a schedule, and ``until`` ends
    the schedule -- not the loop -- once ``max_runs`` is reached."""
    from agent_orchestrator.executors.fake import FakeExecutor

    # Engine side: a schedule with until.max_runs=2 fires once.
    env = env_for(tmp_path, [_engine_binding(max_runs=2)])
    env.anchor()
    decision = env.evaluate(now=T0 + timedelta(seconds=60))
    assert decision.fire and decision.reason == "due"
    record = env.engine.fire(decision)
    assert record is not None and record.status == "launched"
    state_row = env.engine.state_store.get(env.root, "s")
    assert state_row is not None and state_row.runs_count == 1

    # Run side: the fired workflow's loop is untouched by the schedule (gate True, then False).
    wf = _loop_workflow(make_workflow)
    orch, reposets, agents = make_orchestrator(
        FakeExecutor(gate_payloads={"review": [True, False]})
    )
    run = orch.run(wf, reposets, agents)
    assert run.status == "succeeded"
    assert {"review", "review__iter2"} <= set(run.tasks) and "review__iter3" not in run.tasks
    assert run.tasks["develop__iter2"].origin == "loop"
    assert run.tasks["finalize"].status == "succeeded"
    # `until` is not a workflow concern: the spec model carries no such field.
    assert "until" not in type(wf).model_fields

    # Inner iterations did not advance the schedule's counter; it is not yet satisfied.
    state_row = env.engine.state_store.get(env.root, "s")
    assert state_row is not None and state_row.runs_count == 1
    env.end_run("run-1")
    d2 = env.evaluate(now=T0 + timedelta(seconds=120))
    assert d2.fire and d2.reason == "due"
    env.engine.fire(d2)
    env.end_run("run-2")
    d3 = env.evaluate(now=T0 + timedelta(seconds=180))
    assert (d3.fire, d3.reason) == (False, "until_satisfied")


def test_loopspec_bad_gate_still_fails_the_run_while_until_gate_never_does(
    tmp_path: Path, make_workflow: Any, make_orchestrator: Any
) -> None:
    """The deliberate contrast (TASK 'Risks'): a malformed LoopSpec gate FAILS the run, whereas a
    malformed ``until`` gate never ends the schedule."""
    from agent_orchestrator.executors.fake import FakeExecutor

    wf = _loop_workflow(make_workflow)
    orch, reposets, agents = make_orchestrator(FakeExecutor(gate_payloads={"review": ["yes"]}))
    assert orch.run(wf, reposets, agents).status != "succeeded"
    # ... while the until side keeps going on the same kind of bad read:
    assert until_satisfied(tmp_path, ub(gate_file="g.json"), ScheduleState()) is False
