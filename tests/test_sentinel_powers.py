"""T-Sn9Pow: LLM powers (advise/act, kill limits), directives + OV-R12b, routed-runner planner."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator import graph_check
from agent_orchestrator import sentinel_gen as sg
from agent_orchestrator import sentinel_powers as sp
from agent_orchestrator.models import (
    RunMetrics,
    RunState,
    SentinelLlmSpec,
    SentinelState,
    TaskRunState,
)
from tests.test_overseer_tool_checker_semantic import (
    make_args,
    ov,
    rule_ids_of,
    setup_verdict_baseline,
    write_digest,
    write_verdict,
)
from tests.test_sentinel_generations import (  # noqa: F401  (fixture re-export)
    ScriptedDriver,
    _fast_wait,
    _run,
    _settings,
    _wf,
)

T0 = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
LONG = "x" * 45


def _entry(kind: str, payload: dict[str, Any] | None = None, **kw: Any) -> dict[str, Any]:
    return {
        "schema": "ao.ctl/v1",
        "id": kw.pop("id", "sentinel-g01-abcdef"),
        "kind": kind,
        "actor": kw.pop("actor", "sentinel-g01"),
        "created_at": T0.isoformat(),
        "payload": payload or {},
        **kw,
    }


def _state(running_for_min: float = 30, tid: str = "t1") -> RunState:
    st = RunState(run_id="r", workflow_id="w", started_at=T0.isoformat(), updated_at=T0.isoformat())
    st.tasks[tid] = TaskRunState(status="running", running_seconds=running_for_min * 60)
    return st


def _gate(tmp_path: Path, protected: frozenset[str] = frozenset(), **spec: Any) -> sp.PowerGate:
    return sp.PowerGate(SentinelLlmSpec(**spec), tmp_path, lambda t: t in protected)


def _rule(gate: sp.PowerGate, entry: dict[str, Any], state: RunState | None = None) -> str | None:
    try:
        gate(entry, state or _state(), T0)
    except graph_check.EditRejected as rej:
        return rej.rule_id
    return None


KILL = {"task_id": "t1"}


# ---- powers: advise vs act -----------------------------------------------------------------


@pytest.mark.parametrize("kind", ["inject", "edit_pending", "kill"])
def test_advise_refuses_act_kinds(tmp_path: Path, kind: str) -> None:
    g = _gate(tmp_path, powers="advise")
    assert _rule(g, _entry(kind, KILL, rationale=LONG)) == sp.RULE_ADVISE


@pytest.mark.parametrize("kind", ["note", "request_hold", "request_cancel"])
def test_advise_allows_advisory_kinds(tmp_path: Path, kind: str) -> None:
    assert _rule(_gate(tmp_path, powers="advise"), _entry(kind, {"text": "x"})) is None


@pytest.mark.parametrize("kind", ["set", "resume_llm", "release_backlog", "cancel", "pause_llm"])
def test_operator_only_kinds_refused_even_for_act(tmp_path: Path, kind: str) -> None:
    assert _rule(_gate(tmp_path, powers="act"), _entry(kind)) == sp.RULE_KIND


def test_human_actor_bypasses_the_gate(tmp_path: Path) -> None:
    g = _gate(tmp_path, powers="advise", allow_kill=False)
    assert _rule(g, _entry("kill", KILL, actor="ctl:me")) is None


# ---- allow_graph_edits / injection rate ----------------------------------------------------


def test_allow_graph_edits_flag(tmp_path: Path) -> None:
    g = _gate(tmp_path, allow_graph_edits=False)
    assert _rule(g, _entry("inject", {"tasks": [{}]})) == graph_check.RULE_DISABLED
    assert _rule(g, _entry("edit_pending", {})) == graph_check.RULE_DISABLED
    assert _rule(_gate(tmp_path), _entry("edit_pending", {})) is None


def test_injection_rate_cap_per_hour(tmp_path: Path) -> None:
    g = _gate(tmp_path, max_injections_per_hour=2)
    assert _rule(g, _entry("inject", {"tasks": [{}, {}]})) is None
    assert _rule(g, _entry("inject", {"tasks": [{}]})) == sp.RULE_INJECT_RATE
    later = T0 + timedelta(hours=1, seconds=1)  # window slid
    g(_entry("inject", {"tasks": [{}]}), _state(), later)


# ---- kill limits ---------------------------------------------------------------------------


def test_kill_ok_then_rate_limited(tmp_path: Path) -> None:
    g = _gate(tmp_path, max_llm_kills_per_hour=1)
    st = _state()
    st.tasks["t2"] = TaskRunState(status="running", running_seconds=3600)
    assert _rule(g, _entry("kill", KILL, rationale=LONG), st) is None
    e2 = _entry("kill", {"task_id": "t2"}, rationale=LONG, id="sentinel-g01-bbbbbb")
    assert _rule(g, e2, st) == sp.RULE_KILL_RATE


def test_kill_flag_rationale_target_protected_young(tmp_path: Path) -> None:
    assert _rule(_gate(tmp_path, allow_kill=False), _entry("kill", KILL, rationale=LONG)) == (
        sp.RULE_NO_KILL
    )
    g = _gate(tmp_path, protected=frozenset({"bar"}))
    assert _rule(g, _entry("kill", KILL, rationale="too short")) == sp.RULE_KILL_RATIONALE
    assert _rule(g, _entry("kill", KILL)) == sp.RULE_KILL_RATIONALE
    assert _rule(g, _entry("kill", {"task_id": "nope"}, rationale=LONG)) == sp.RULE_KILL_TARGET
    st = _state()
    st.tasks["bar"] = TaskRunState(status="running", running_seconds=9999)
    assert _rule(g, _entry("kill", {"task_id": "bar"}, rationale=LONG), st) == (
        sp.RULE_KILL_PROTECTED
    )
    young = _state(running_for_min=9)
    assert _rule(g, _entry("kill", KILL, rationale=LONG), young) == sp.RULE_KILL_YOUNG
    assert (
        _rule(
            _gate(tmp_path, llm_kill_min_running_minutes=5),
            _entry("kill", KILL, rationale=LONG),
            young,
        )
        is None
    )


def test_rejected_kill_is_not_counted_against_the_cap(tmp_path: Path) -> None:
    g = _gate(tmp_path, max_llm_kills_per_hour=1)
    assert _rule(g, _entry("kill", KILL, rationale="short")) == sp.RULE_KILL_RATIONALE
    assert _rule(g, _entry("kill", KILL, rationale=LONG)) is None


# ---- directives ----------------------------------------------------------------------------


def test_directive_validation_and_store(tmp_path: Path) -> None:
    ctl = tmp_path / "control"
    good = _entry("directive", {"text": "re-scope A2", "severity": "warn"}, rationale="because")
    sp.append_directive(ctl, good, T0.isoformat())
    sp.append_directive(ctl, good, T0.isoformat())  # replay: de-duplicated on read
    recs = sp.read_directives(ctl)
    assert [r["id"] for r in recs] == ["sentinel-g01-abcdef"] and recs[0]["severity"] == "warn"
    for bad in (
        _entry("directive", {"text": ""}, rationale="r"),
        _entry("directive", {"text": "t", "severity": "boom"}, rationale="r"),
        _entry("directive", {"text": "t"}),
    ):
        with pytest.raises(graph_check.EditRejected) as ei:
            sp.append_directive(ctl, bad, T0.isoformat())
        assert ei.value.rule_id == sp.RULE_DIRECTIVE


# ---- planner trigger + contract ------------------------------------------------------------


def _lane_state(idle: float) -> RunState:
    st = _state()
    st.sentinel = SentinelState()
    st.metrics = RunMetrics(slot_seconds_idle_no_ready=idle)
    return st


def test_planner_trigger_needs_optin_baseline_growth_and_empty_backlog(tmp_path: Path) -> None:
    drv = ScriptedDriver()
    mk = lambda **kw: sg.GenerationLane(  # noqa: E731
        spec=SentinelLlmSpec(enabled=True, planner_idle_seconds=60, **kw),
        driver=drv,
        run_dir=tmp_path,
        enabled_kinds=frozenset({"note"}),
        run_cost_cap_usd=None,
        log=__import__("logging").getLogger("t"),
    )
    off = mk()
    assert off._planner_idle_hit(_lane_state(0)) is False
    lane = mk(planner=True)
    assert lane._planner_idle_hit(_lane_state(100)) is False  # first sight = baseline
    assert lane._planner_idle_hit(_lane_state(130)) is False  # +30 < 60
    assert lane._planner_idle_hit(_lane_state(170)) is True  # +70
    assert lane._planner_idle_hit(_lane_state(175)) is False  # mark moved
    (tmp_path / "control").mkdir()
    (tmp_path / "control" / "backlog.json").write_text(json.dumps({"items": [{"id": "b"}]}))
    assert lane._planner_idle_hit(_lane_state(900)) is False  # backlog non-empty
    lane2 = mk(planner=True, powers="advise")
    assert lane2._planner_idle_hit(_lane_state(0)) is False  # planner needs act


def test_contract_planner_section_only_when_planner() -> None:
    plain = sg.render_contract(frozenset({"note"}), {})
    planner = sg.render_contract(frozenset({"note"}), {}, planner=True)
    assert "Planner role" not in plain and "Planner role" in planner
    assert "idle_slots_no_backlog" in planner


# ---- overseer: digest sentinel block + OV-R12b ---------------------------------------------


def _directive_digest(inst: Path, k: int, ids: list[str]) -> None:
    write_digest(inst, k)
    path = ov.digest_path(inst, k)
    d = json.loads(path.read_text())
    d["sentinel"] = {"directives": [{"id": i, "severity": "warn", "text": "t"} for i in ids]}
    path.write_text(json.dumps(d))


def test_r12b_missing_ack_fails_and_ack_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst, _cfg, _em, k, _ch = setup_verdict_baseline(tmp_path, tmp_path, monkeypatch)
    _directive_digest(inst, k, ["sentinel-g01-dir001"])
    with pytest.raises(ov.CheckViolations) as ei:
        ov.ckpt_check(make_args(tmp_path))
    assert "R12b" in rule_ids_of(ei.value.violations)
    ack = {"directive_id": "sentinel-g01-dir001", "response": "adopt", "rationale": "ok"}
    write_verdict(inst, k, directive_responses=[ack])
    # write_verdict resets other fields to a bare verdict; restore the baseline-compatible ones
    base = json.loads(ov.verdict_path(inst, k).read_text())
    base["alignment"] = [
        {"ask_id": "A1", "status": "on_track", "evidence": "parser progressing"},
        {"ask_id": "A2", "status": "on_track", "evidence": "docs progressing"},
    ]
    base["criteria"] = [{"id": "A1.1", "status": "unmet"}, {"id": "A2.1", "status": "unmet"}]
    ov.verdict_path(inst, k).write_text(json.dumps(base))
    ov.ckpt_check(make_args(tmp_path))


@pytest.mark.parametrize(
    "responses",
    [
        [{"directive_id": "d1abcd", "response": "maybe"}],
        [{"directive_id": "d1abcd", "response": "reject", "rationale": "no"}],
        [
            {"directive_id": "d1abcd", "response": "adopt"},
            {"directive_id": "d1abcd", "response": "adopt"},
        ],
    ],
)
def test_r12b_bad_responses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, responses: list[dict]
) -> None:
    inst, _cfg, _em, k, _ch = setup_verdict_baseline(tmp_path, tmp_path, monkeypatch)
    _directive_digest(inst, k, ["d1abcd"])
    d = json.loads(ov.verdict_path(inst, k).read_text())
    d["directive_responses"] = responses
    ov.verdict_path(inst, k).write_text(json.dumps(d))
    with pytest.raises(ov.CheckViolations) as ei:
        ov.ckpt_check(make_args(tmp_path))
    assert "R12b" in rule_ids_of(ei.value.violations)


def test_r12b_reject_with_rationale_passes_and_no_block_is_unaffected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    inst, _cfg, _em, k, _ch = setup_verdict_baseline(tmp_path, tmp_path, monkeypatch)
    ov.ckpt_check(make_args(tmp_path))  # no sentinel block: baseline untouched
    _directive_digest(inst, k, ["d1abcd"])
    d = json.loads(ov.verdict_path(inst, k).read_text())
    d["directive_responses"] = [
        {"directive_id": "d1abcd", "response": "reject", "rationale": "r" * 45}
    ]
    ov.verdict_path(inst, k).write_text(json.dumps(d))
    ov.ckpt_check(make_args(tmp_path))


def test_digest_sentinel_block_absent_without_footprint_and_tracks_acks(tmp_path: Path) -> None:
    run_dir, inst = tmp_path / "run", tmp_path / "inst"
    inst.mkdir()
    run_dir.mkdir()
    assert ov.sentinel_digest_block(run_dir, inst, 1) is None
    (run_dir / "control").mkdir()
    (run_dir / "control" / "directives.jsonl").write_text(
        "".join(
            json.dumps({"id": i, "at": "2026-11-09T12:00:00+00:00"}) + "\n" for i in ("da", "db")
        )
    )
    (run_dir / "sentinel").mkdir()
    (run_dir / "sentinel" / "powers.jsonl").write_text(
        json.dumps({"kind": "kill", "at": "2026-11-09T12:01:00+00:00", "task_id": "t1"}) + "\n"
    )
    blk = ov.sentinel_digest_block(run_dir, inst, 1)
    assert blk is not None and [d["id"] for d in blk["directives"]] == ["da", "db"]
    assert [x["task_id"] for x in blk["kills"]] == ["t1"]
    assert set(blk) >= {"generation", "directives", "injected_since_last_ckpt", "kills", "edits"}
    write_verdict(inst, 1, directive_responses=[{"directive_id": "da", "response": "adopt"}])
    blk2 = ov.sentinel_digest_block(run_dir, inst, 2)
    assert blk2 is not None and [d["id"] for d in blk2["directives"]] == ["db"]


# ---- engine e2e (scripted driver, temp workspace) ------------------------------------------


def test_e2e_directive_lands_in_store_and_bad_llm_proposals_quarantined(tmp_path: Path) -> None:
    props = [
        ("g1-dir-0001", "directive", {"text": "slow unit", "severity": "warn"}),
        ("g1-kill-001", "kill", {"task_id": "t1"}),  # no rationale / not long running
    ]
    drv = ScriptedDriver({1: {"proposals": props}})
    # directives need a rationale: rewrite the scripted entry to carry one
    orig = ScriptedDriver._propose

    def propose(inbox: Path, gen: int, pid: str, kind: str, payload: dict[str, Any]) -> None:
        orig(inbox, gen, pid, kind, payload)
        f = inbox / f"{pid}.json"
        d = json.loads(f.read_text())
        d["rationale"] = "evidence: unit exceeded its peers"
        f.write_text(json.dumps(d))

    drv._propose = staticmethod(propose)  # type: ignore[method-assign]
    state, rs = _run(tmp_path, drv, _settings(), wf=_wf(5))
    run_dir = Path(rs._path(state.run_id)).parent
    assert [d["id"] for d in sp.read_directives(run_dir / "control")] == ["g1-dir-0001"]
    reasons = list((run_dir / "control" / "inbox-rejected").glob("*.reason.json"))
    assert any(json.loads(r.read_text())["rule_id"].startswith("PWR-KILL") for r in reasons)


def test_e2e_advise_power_quarantines_inject(tmp_path: Path) -> None:
    inj = ("g1-inj-0001", "inject", {"tasks": [{"id": "x1", "agent": "ag", "instruction": "i.md"}]})
    state, rs = _run(
        tmp_path, ScriptedDriver({1: {"proposals": [inj]}}), _settings(powers="advise"), wf=_wf(5)
    )
    run_dir = Path(rs._path(state.run_id)).parent
    side = json.loads((run_dir / "control/inbox-rejected/g1-inj-0001.reason.json").read_text())
    assert side["rule_id"] == sp.RULE_ADVISE
    assert "x1" not in state.tasks


def test_e2e_routed_planner_idle_slots_inject_sentinel_origin(tmp_path: Path) -> None:
    inj = (
        "g1-plan-0001",
        "inject",
        {
            "tasks": [
                {
                    "id": "planned-1",
                    "agent": "ag",
                    "instruction": "specs/examples/instructions/design.md",
                    "depends_on": ["t0"],
                }
            ]
        },
    )
    drv = ScriptedDriver({1: {"proposals": [inj]}})
    # heartbeat off by a huge interval: only the planner trigger can start a generation
    settings = _settings(planner=True, planner_idle_seconds=1, heartbeat_minutes=100000)
    from agent_orchestrator import engine as engine_mod
    from agent_orchestrator.engine import Orchestrator
    from tests.test_sentinel_generations import SlowExecutor, StepClock, _env

    store, rs, reposets, agents = _env(tmp_path)
    orch = Orchestrator(
        SlowExecutor(0.25),
        store,
        rs,
        max_parallel=3,  # chain of 6: 1 ready at a time => idle slots, nothing ready
        clock=StepClock(),
        sentinel=settings,
        sentinel_driver_factory=lambda _s: drv,
    )
    del engine_mod
    state = orch.run(_wf(6), reposets, agents)
    assert drv.started, "idle_slots_no_backlog never started a generation"
    assert (
        "idle_slots_no_backlog"
        in (Path(rs._path(state.run_id)).parent / "sentinel/g01/triggers.jsonl").read_text()
    )
    assert state.tasks["planned-1"].origin == "sentinel"
    assert state.tasks["planned-1"].status == "succeeded"


def test_e2e_planner_off_never_fires_idle_trigger(tmp_path: Path) -> None:
    drv = ScriptedDriver()
    state, _rs = _run(tmp_path, drv, _settings(heartbeat_minutes=100000), wf=_wf(5))
    assert drv.started == []
    assert state.sentinel is not None and state.sentinel.generation == 0
