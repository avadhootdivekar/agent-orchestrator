"""T-SnXAcc (non-live part): EPIC §11 e2e acceptance matrix.

Two jobs:

* **Traceability** -- ``MATRIX`` maps every §11 e2e scenario to the concrete tests that exercise it;
  ``test_matrix_targets_exist`` fails if a referenced test is renamed/removed, so a scenario can
  never silently lose coverage.
* **Gap fills** -- scenarios the per-task suites only covered at the unit layer are exercised here
  end-to-end on a temp workspace with a scripted executor (no live LLM, no real sleeps beyond the
  engine's own capped tick wait): the FR-12 reject-rule matrix through the real inbox, the NFR-1
  disabled == absent byte-identical golden, and crash+resume of an ``inject`` mutation.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from agent_orchestrator.engine import Orchestrator
from agent_orchestrator.models import SentinelSpec, TaskContext
from agent_orchestrator.sentinel_settings import resolve_sentinel_settings
from tests.test_sentinel_tick import _run_dir, _setup, _wait_for, _write_entry
from tests.test_soft_barrier import (
    INSTR,
    WF,
    Exec,
    T,
    _processed,
    _rejected_rule,
    sentinel,
)
from tests.test_soft_barrier import _fast_wait as _fast_wait  # noqa: F401  (autouse fixture)

# scenario -> [(test module, test name)]; ``Class.method`` allowed for class-based tests.
MATRIX: dict[str, list[tuple[str, str]]] = {
    "tick-while-busy+metrics": [
        ("tests.test_sentinel_tick", "test_tick_runs_while_all_workers_blocked"),
        ("tests.test_sentinel_tick", "test_metrics_on_scripted_timeline"),
    ],
    "stall-kill-retry-escalate-fail+systemic-alarm": [
        ("tests.test_sentinel_recovery", "test_idle_kill_retry_escalate_then_fail"),
        ("tests.test_sentinel_recovery", "test_systemic_alarm_stops_kills"),
        ("tests.test_sentinel_recovery", "test_real_subprocess_tree_killed_within_2s_no_orphans"),
    ],
    "ctl-round-trip+live-max-parallel": [
        ("tests.test_ctl_cli", "test_live_round_trip_set_pause_note_kill_cancel"),
        ("tests.test_sentinel_tick", "test_set_max_parallel_up_to_ceiling"),
    ],
    "dashboard-control-api-auth-csrf-actor": [
        (
            "tests.ui.test_sentinel_api",
            "TestSecurity.test_cross_origin_post_is_rejected_and_writes_nothing",
        ),
        ("tests.ui.test_sentinel_api", "test_actor_is_the_authenticated_principal"),
        (
            "tests.ui.test_sentinel_e2e",
            "test_dashboard_set_max_parallel_pause_and_status_follow_live_engine",
        ),
    ],
    "heavy-tail-idle-slot-nfr13": [
        ("tests.test_sentinel_backlog", "test_heavy_tail_idle_slot_seconds_reduced_at_least_half"),
    ],
    "backlog-release+expiry+early-checkpoint-carry-over": [
        ("tests.test_sentinel_backlog", "test_priority_order_and_entries_released_once"),
        ("tests.test_sentinel_backlog", "test_expired_entries_never_released"),
        ("tests.test_sentinel_backlog", "test_early_checkpoint_runs_while_straggler_continues"),
    ],
    "pending-edit-matrix+stage-gating": [
        ("tests.test_graph_check", "test_stage_matrix"),
        ("tests.test_sentinel_e2e_matrix", "test_pending_edit_reject_matrix_through_inbox"),
    ],
    "soft-barrier-in-flight": [
        ("tests.test_soft_barrier", "test_soft_emitter_overlaps_three_unrelated_tasks"),
    ],
    "llm-rotation-ungraceful-budget-autopause": [
        (
            "tests.test_sentinel_generations",
            "test_e2e_rotation_three_generations_handoff_carried_ungraceful_covered",
        ),
        ("tests.test_sentinel_generations", "test_e2e_budget_cap_stops_generations_ticks_continue"),
        ("tests.test_sentinel_generations", "test_e2e_consecutive_rejections_auto_pause"),
    ],
    "llm-powers-advise-vs-act-kill-limits-directive-ack": [
        ("tests.test_sentinel_powers", "test_e2e_advise_power_quarantines_inject"),
        ("tests.test_sentinel_powers", "test_kill_ok_then_rate_limited"),
        ("tests.test_sentinel_powers", "test_r12b_missing_ack_fails_and_ack_passes"),
    ],
    "routed-runner-planner-injects-sentinel-unit": [
        ("tests.test_sentinel_powers", "test_e2e_routed_planner_idle_slots_inject_sentinel_origin"),
    ],
    "crash-resume-at-mutation-points": [
        (
            "tests.test_sentinel_recovery",
            "test_resume_mid_ladder_preserves_recovery_no_double_kill",
        ),
        ("tests.test_soft_barrier", "test_consumed_edit_replayed_after_crash_is_not_applied_twice"),
        ("tests.test_soft_barrier", "test_resume_does_not_revert_edits"),
        ("tests.test_sentinel_backlog", "test_early_checkpoint_edit_replays_on_resume"),
        (
            "tests.test_sentinel_generations",
            "test_e2e_resume_restores_counter_memory_handoff_no_duplicate_proposals",
        ),
        ("tests.test_sentinel_tick", "test_resume_restores_overrides"),
        ("tests.test_sentinel_e2e_matrix", "test_inject_survives_crash_and_resume_exactly_once"),
    ],
    "lane-exception-runs-as-disabled": [
        ("tests.test_sentinel_tick", "test_tick_exception_runs_as_if_disabled"),
        ("tests.test_sentinel_generations", "test_e2e_lane_error_disables_only_llm_lane"),
    ],
    "nfr1-disabled-byte-identical": [
        ("tests.test_sentinel_tick", "test_disabled_is_inert"),
        ("tests.test_sentinel_e2e_matrix", "test_disabled_sentinel_equals_absent_state_golden"),
    ],
}


def _resolve(mod: str, name: str) -> Any:
    obj: Any = importlib.import_module(mod)
    for part in name.split("."):
        obj = getattr(obj, part, None)
        if obj is None:
            return None
    return obj


@pytest.mark.parametrize("scenario", sorted(MATRIX))
def test_matrix_targets_exist(scenario: str) -> None:
    missing = [f"{m}::{n}" for m, n in MATRIX[scenario] if not callable(_resolve(m, n))]
    assert not missing, f"{scenario}: referenced tests vanished: {missing}"


# ---- FR-12 reject-rule matrix through the real inbox ------------------------------------------
def test_pending_edit_reject_matrix_through_inbox(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    wf = WF([T("a"), T("b"), T("c"), T("d", depends_on=["b"])])
    state0 = rs.new_run(wf)
    run_dir = _run_dir(rs, state0)

    bad: dict[str, tuple[dict, str]] = {
        "ctl-bad-unknown": ({"priority": {"zzz": 1}}, "GE-UNKNOWN-TASK"),
        "ctl-bad-notpend": ({"skip": ["a"]}, "GE-NOT-PENDING"),
        "ctl-bad-cycle01": ({"add_edges": [{"from": "d", "to": "b"}]}, "GE-CYCLE"),
        "ctl-bad-intorun": ({"add_edges": [{"from": "b", "to": "a"}]}, "GE-NOT-PENDING"),
        "ctl-bad-field01": ({"bogus": 1}, "GE-PAYLOAD"),
        "ctl-bad-empty01": ({}, "GE-PAYLOAD"),
        "ctl-bad-prio001": ({"priority": {"b": 10_000}}, "GE-PAYLOAD"),
        "ctl-bad-both001": ({"cancel": ["c"], "skip": ["c"]}, "GE-PAYLOAD"),
    }

    def first(ex: Exec, ctx: TaskContext) -> None:
        for eid, (payload, _) in bad.items():
            _write_entry(run_dir, eid, "edit_pending", payload)
        _write_entry(run_dir, "ctl-good-skip01", "edit_pending", {"skip": ["c"]})
        for eid in bad:
            assert _wait_for(
                lambda eid=eid: (run_dir / "control" / "inbox-rejected" / f"{eid}.json").exists()
            ), eid
        assert _wait_for(lambda: _processed(run_dir, "ctl-good-skip01").exists())

    ex = Exec({"a": first})
    state = Orchestrator(ex, store, rs, max_parallel=1, sentinel=sentinel()).run(
        wf, reposets, agents, run_state=state0
    )
    assert state.status == "succeeded"
    for eid, (_, rule) in bad.items():
        assert _rejected_rule(run_dir, eid) == rule, eid
    # all-or-nothing: only the one good edit landed
    assert [e.op for e in state.graph_edits] == ["skip"]
    assert state.tasks["c"].status == "skipped" and "c" not in ex.order


# ---- crash + resume of an inject mutation -----------------------------------------------------
def test_inject_survives_crash_and_resume_exactly_once(tmp_path: Path) -> None:
    store, rs, reposets, agents = _setup(tmp_path)
    wf = WF([T("a"), T("b", depends_on=["a"])])
    state0 = rs.new_run(wf)
    run_dir = _run_dir(rs, state0)

    def first(ex: Exec, ctx: TaskContext) -> None:
        _write_entry(
            run_dir,
            "ctl-inj-crash001",
            "inject",
            {"tasks": [{"id": "extra", "agent": "ag", "instruction": INSTR, "depends_on": ["a"]}]},
        )
        assert _wait_for(lambda: _processed(run_dir, "ctl-inj-crash001").exists())
        raise RuntimeError("crash a")

    with pytest.raises(RuntimeError, match="crash a"):
        Orchestrator(Exec({"a": first}), store, rs, max_parallel=1, sentinel=sentinel()).run(
            wf, reposets, agents, run_state=state0
        )
    loaded = rs.load(state0.run_id)
    assert "extra" in loaded.tasks and loaded.tasks["extra"].origin == "sentinel"

    wf2 = WF([T("a"), T("b", depends_on=["a"])])
    loaded = rs.prepare_resume(loaded, wf2)
    ex2 = Exec()
    final = Orchestrator(ex2, store, rs, max_parallel=1, sentinel=sentinel()).run(
        wf2, reposets, agents, run_state=loaded
    )
    assert final.status == "succeeded"
    assert ex2.order.count("extra") == 1 and sorted(ex2.order) == ["a", "b", "extra"]
    assert sum(1 for s in final.spawned_by.values() if s.parent_task_id.startswith("ctl:")) == 1
    assert (
        final.sentinel is not None and final.sentinel.inbox_consumed.count("ctl-inj-crash001") == 1
    )


# ---- NFR-1 golden: disabled == absent ----------------------------------------------------------
_VOLATILE = {"run_id", "started_at", "finished_at", "updated_at", "created_at", "at", "ts"}


def _scrub(obj: Any, root: str = "") -> Any:
    if isinstance(obj, dict):
        return {k: _scrub(v, root) for k, v in sorted(obj.items()) if k not in _VOLATILE}
    if isinstance(obj, list):
        return [_scrub(v, root) for v in obj]
    if isinstance(obj, str) and root and root in obj:
        return obj.replace(root, "<ws>")
    if isinstance(obj, str) and obj[:2] == "20" and "T" in obj and obj.endswith(("Z", "+00:00")):
        return "<ts>"
    return obj


def _run_plain(tmp_path: Path, sentinel_arg: Any) -> tuple[dict, list[str], set[str]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    store, rs, reposets, agents = _setup(tmp_path)
    wf = WF([T("a"), T("b", depends_on=["a"]), T("c", depends_on=["a"])])
    state0 = rs.new_run(wf)
    ex = Exec()
    kw: dict[str, Any] = {} if sentinel_arg is None else {"sentinel": sentinel_arg}
    final = Orchestrator(ex, store, rs, max_parallel=2, **kw).run(
        wf, reposets, agents, run_state=state0
    )
    assert final.status == "succeeded"
    run_dir = _run_dir(rs, state0)
    files = {str(p.relative_to(run_dir)) for p in run_dir.rglob("*") if p.is_file()}
    return json.loads(rs._path(final.run_id).read_text()), sorted(ex.order), files


def test_disabled_sentinel_equals_absent_state_golden(tmp_path: Path) -> None:
    absent_state, absent_order, absent_files = _run_plain(tmp_path / "absent", None)
    off = resolve_sentinel_settings(workflow=SentinelSpec(enabled=False))
    off_state, off_order, off_files = _run_plain(tmp_path / "off", off)
    assert absent_order == off_order
    assert not any(f.startswith("control") for f in off_files)
    assert absent_files == off_files
    assert _scrub(absent_state, str(tmp_path / "absent")) == _scrub(
        off_state, str(tmp_path / "off")
    )
    assert off_state.get("sentinel") is None
