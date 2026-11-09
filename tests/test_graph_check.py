"""T-Sn6Brr: shared mechanical checker -- FR-11 structural/stage rules, FR-12 edit rules."""

from __future__ import annotations

from typing import Any

import pytest

from agent_orchestrator import graph_check as gc
from agent_orchestrator.dag import build_dag, compute_cones
from agent_orchestrator.models import (
    GraphEdit,
    LoopSpec,
    RouterSpec,
    RouteSpec,
    RunState,
    TaskRunState,
    TaskSpec,
    WorkflowSpec,
)

INSTR = "specs/examples/instructions/design.md"
KW: dict[str, Any] = {"actor": "ctl:me", "at": "2026-11-09T00:00:00+00:00", "edit_id": "e1"}


def t(tid: str, deps: list[str] | None = None, **kw: Any) -> TaskSpec:
    return TaskSpec(id=tid, agent="ag", instruction=INSTR, depends_on=deps or [], **kw)


def wf(tasks: list[TaskSpec], **kw: Any) -> WorkflowSpec:
    return WorkflowSpec(version="1.0", id="wf", tasks=tasks, **kw)


def st(**statuses: str) -> RunState:
    s = RunState(run_id="r", workflow_id="wf", started_at="x", updated_at="x")
    for k, v in statuses.items():
        s.tasks[k] = TaskRunState(status=v)  # type: ignore[arg-type]
    return s


def membership(w: WorkflowSpec) -> Any:
    return compute_cones(w, build_dag(w))[1]


def edit(w: WorkflowSpec, s: RunState, **payload: Any) -> gc.EditPlan:
    return gc.plan_edit_pending(w, s, payload, membership=membership(w), **KW)


def rule(fn: Any) -> str:
    with pytest.raises(gc.EditRejected) as ei:
        fn()
    return ei.value.rule_id


# ---- additive manifest / inject (FR-11) ----------------------------------------------------
def base() -> tuple[WorkflowSpec, RunState]:
    w = wf([t("run1", outputs=["o/run1"]), t("p1"), t("p2", ["p1"])])
    return w, st(run1="running", p1="pending", p2="pending")


def test_additive_ok() -> None:
    w, s = base()
    gc.check_additive_manifest(w, s, [t("new1", ["run1"])])


def test_dup_id_and_pattern() -> None:
    w, s = base()
    assert rule(lambda: gc.check_additive_manifest(w, s, [t("p1")])) == gc.RULE_ID_DUP
    assert rule(lambda: gc.check_additive_manifest(w, s, [t("a b")])) == gc.RULE_ID_PATTERN
    assert rule(lambda: gc.check_additive_manifest(w, s, [t("x__iter2")])) == gc.RULE_ID_PATTERN


def test_dup_producer() -> None:
    w, s = base()
    new = [t("dup", outputs=["o/run1"])]
    assert rule(lambda: gc.check_additive_manifest(w, s, new)) == gc.RULE_PRODUCER_DUP


def test_inferred_edge_into_running_task_rejected() -> None:
    w = wf([t("slow", inputs=["x/in"]), t("p1")])
    s = st(slow="running", p1="pending")
    new = [t("late", outputs=["x/in"])]
    assert rule(lambda: gc.check_additive_manifest(w, s, new)) == gc.RULE_EDGE_INTO_NONPENDING


def test_inferred_edge_into_pending_task_ok() -> None:
    w = wf([t("slow", inputs=["x/in"]), t("p1")])
    s = st(slow="pending", p1="pending")
    gc.check_additive_manifest(w, s, [t("late", outputs=["x/in"])])


def test_cycle_in_manifest() -> None:
    w, s = base()
    new = [t("a", ["b"]), t("b", ["a"])]
    assert rule(lambda: gc.check_additive_manifest(w, s, new)) == gc.RULE_CYCLE


def test_unknown_dependency_rejected_not_raised() -> None:
    w, s = base()
    assert rule(lambda: gc.check_additive_manifest(w, s, [t("a", ["ghost"])])) == gc.RULE_SPEC


@pytest.mark.parametrize(
    ("stage", "kind", "work_item", "expect"),
    [
        ("explore", "implement", "new", None),
        ("converge", "implement", "known", None),
        ("converge", "implement", "new", gc.RULE_STAGE_CONVERGE),
        ("stabilize", "verify", "known", None),
        ("stabilize", "document", "known", None),
        ("stabilize", "stabilize", "known", None),
        ("stabilize", "implement", "known", gc.RULE_STAGE_STABILIZE),
        ("closeout", "verify", "known", gc.RULE_STAGE_CLOSEOUT),
    ],
)
def test_stage_matrix(stage: str, kind: str, work_item: str, expect: str | None) -> None:
    w, s = base()
    pol = gc.CheckPolicy(
        stage=stage,
        known_work_items=frozenset({"known"}),
        task_meta={"n1": {"kind": kind, "work_item": work_item}},
    )
    if expect is None:
        gc.check_additive_manifest(w, s, [t("n1")], pol)
    else:
        assert rule(lambda: gc.check_additive_manifest(w, s, [t("n1")], pol)) == expect


def test_closeout_allows_edit_only_and_limit() -> None:
    w, s = base()
    assert (
        rule(
            lambda: gc.check_additive_manifest(
                w, s, [t("a"), t("b")], gc.CheckPolicy(max_new_tasks=1)
            )
        )
        == gc.RULE_LIMIT
    )


# ---- edit_pending (FR-12) -----------------------------------------------------------------
def test_add_remove_edge_ok_and_ledgered() -> None:
    w = wf([t("a"), t("b"), t("c", ["a"])])
    s = st()
    plan = edit(w, s, add_edges=[{"from": "b", "to": "c"}], remove_edges=[{"from": "a", "to": "c"}])
    (e,) = plan.edits
    assert e.op == "set_edges" and e.task_id == "c" and e.depends_on == ["b"]
    assert e.source == "ctl:me" and e.edit_id == "e1"


def test_cycle_rejected() -> None:
    w = wf([t("a"), t("b", ["a"])])
    assert rule(lambda: edit(w, st(), add_edges=[{"from": "b", "to": "a"}])) == gc.RULE_CYCLE
    assert rule(lambda: edit(w, st(), add_edges=[{"from": "a", "to": "a"}])) == gc.RULE_CYCLE


def test_touching_running_or_finished_rejected() -> None:
    w = wf([t("a"), t("b"), t("c")])
    s = st(a="running", b="succeeded")
    for tid in ("a", "b"):
        assert rule(lambda tid=tid: edit(w, s, cancel=[tid])) == gc.RULE_NOT_PENDING
    assert rule(lambda: edit(w, s, add_edges=[{"from": "c", "to": "a"}])) == gc.RULE_NOT_PENDING
    assert rule(lambda: edit(w, s, priority={"a": 3})) == gc.RULE_NOT_PENDING
    assert rule(lambda: edit(w, s, skip=["ghost"])) == gc.RULE_UNKNOWN_TASK


def test_remove_edge_with_declared_input_needs_drop_inputs() -> None:
    w = wf([t("a", outputs=["o/a"]), t("b", ["a"], inputs=["o/a"])])
    assert (
        rule(lambda: edit(w, st(), remove_edges=[{"from": "a", "to": "b"}])) == gc.RULE_INPUT_EDGE
    )
    plan = edit(w, st(), remove_edges=[{"from": "a", "to": "b", "drop_inputs": True}])
    assert plan.edits[0].depends_on == [] and plan.edits[0].inputs == []


def test_cancel_orphaning_input_rejected_unless_dependent_cancelled() -> None:
    w = wf([t("a", outputs=["o/a"]), t("b", ["a"], inputs=["o/a"]), t("c", ["a"])])
    assert rule(lambda: edit(w, st(), cancel=["a"])) == gc.RULE_ORPHAN_INPUT
    plan = edit(w, st(), cancel=["a", "b"])
    assert plan.removed == ["a", "b"]
    assert [x.id for x in plan.tasks_after] == ["c"] and plan.tasks_after[0].depends_on == []


def test_route_sink_and_cone_and_loop_rejected() -> None:
    w = wf(
        [t("r"), t("x"), t("y"), t("z"), t("g"), t("body")],
        branches=[
            RouterSpec(
                id="rt",
                router_task_id="r",
                verdict_path="v.json",
                routes={"one": RouteSpec(entry=["x"]), "two": RouteSpec(entry=["y"])},
            )
        ],
        loops=[
            LoopSpec(
                id="lp",
                body=["body", "g"],
                gate_task_id="g",
                gate_output_path="g.json",
                gate_field="again",
                max_iterations=2,
            )
        ],
    )
    # make y's cone contain z
    w.task("z").depends_on = ["y"]
    assert rule(lambda: edit(w, st(), cancel=["r"])) == gc.RULE_ROUTE_SINK
    assert rule(lambda: edit(w, st(), skip=["x"])) == gc.RULE_ROUTE_SINK
    assert rule(lambda: edit(w, st(), priority={"z": 1})) == gc.RULE_CONE
    assert rule(lambda: edit(w, st(), cancel=["g"])) == gc.RULE_CONE


def test_atomic_all_or_nothing_and_payload_rules() -> None:
    w = wf([t("a"), t("b", ["a"]), t("c")])
    s = st(c="running")
    # one bad op (c running) sinks the whole edit; workflow untouched
    before = [x.model_dump() for x in w.tasks]
    assert (
        rule(lambda: edit(w, s, add_edges=[{"from": "a", "to": "b"}], skip=["c"]))
        == gc.RULE_NOT_PENDING
    )
    assert [x.model_dump() for x in w.tasks] == before
    assert rule(lambda: edit(w, st())) == gc.RULE_PAYLOAD
    assert rule(lambda: edit(w, st(), bogus=1)) == gc.RULE_PAYLOAD
    assert rule(lambda: edit(w, st(), priority={"a": True})) == gc.RULE_PAYLOAD
    assert rule(lambda: edit(w, st(), priority={"a": 5000})) == gc.RULE_PAYLOAD
    assert rule(lambda: edit(w, st(), cancel=["a"], skip=["a"])) == gc.RULE_PAYLOAD
    assert rule(lambda: edit(w, st(), add_edges=[{"from": "a"}])) == gc.RULE_PAYLOAD


def test_commit_and_replay_roundtrip() -> None:
    w = wf([t("a"), t("b", ["a"]), t("c"), t("d")])
    s = st()
    plan = edit(
        w, s, add_edges=[{"from": "c", "to": "b"}], cancel=["d"], skip=["a"], priority={"c": 7}
    )
    gc.commit_plan(w, s, plan, parent="ctl:me", at=KW["at"])
    assert [x.id for x in w.tasks] == ["a", "b", "c"] and s.tasks["a"].status == "skipped"
    assert gc.priorities(s) == {"c": 7}
    # resume: fresh static workflow + persisted state -> same graph
    w2 = wf([t("a"), t("b", ["a"]), t("c"), t("d")])
    s2 = RunState.model_validate_json(s.model_dump_json())
    s2.tasks["a"] = TaskRunState(status="pending")
    gc.replay_edits(w2, s2)
    assert [x.id for x in w2.tasks] == ["a", "b", "c"]
    assert w2.task("b").depends_on == ["a", "c"]
    assert s2.tasks["a"].status == "skipped"
    gc.replay_edits(w2, s2)  # idempotent
    assert w2.task("b").depends_on == ["a", "c"]


def test_inject_commit_sets_sentinel_origin() -> None:
    w, s = base()
    plan = gc.plan_inject(
        w, s, {"tasks": [{"id": "n1", "agent": "ag", "instruction": INSTR}]}, **KW
    )
    gc.commit_plan(w, s, plan, parent="sentinel-g03", at=KW["at"])
    assert s.tasks["n1"].origin == "sentinel"
    assert s.spawned_by["n1"].parent_task_id == "sentinel-g03"
    assert s.spawned_by["n1"].origin == "sentinel"
    assert [x.id for x in s.injected_tasks] == ["n1"]
    assert rule(lambda: gc.plan_inject(w, s, {"tasks": []}, **KW)) == gc.RULE_PAYLOAD
    assert rule(lambda: gc.plan_inject(w, s, {"tasks": [{"id": "zz"}]}, **KW)) == gc.RULE_SPEC


def test_graphedit_json_roundtrip() -> None:
    e = GraphEdit(op="priority", task_id="a", value=3)
    assert GraphEdit.model_validate_json(e.model_dump_json()) == e
