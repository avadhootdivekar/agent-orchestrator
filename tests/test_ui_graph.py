"""Unit tests for the pure run-graph builder (T-M4qboy, HLD §8.3.2, ADR-0017 D3/D4).

Every fixture uses fixed timestamps (CLAUDE.md's deterministic-clock rule) -- nothing here
touches a real clock. `ui.graph` itself does no I/O and imports no web framework, so this
file (unlike `tests/ui/*`) needs no `[ui]` extra and no fixtures from `tests/ui/conftest.py`.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, fields, is_dataclass
from pathlib import Path

import pytest

from agent_orchestrator.models import (
    LoopSpec,
    RouterSpec,
    RouteSpec,
    RunState,
    SpawnRecord,
    SpecSession,
    TaskRunState,
    TaskSpec,
    WorkflowSnapshot,
    WorkflowSpec,
)
from agent_orchestrator.ui import graph as graph_mod
from agent_orchestrator.ui.graph import (
    GRAPH_LABEL_MAX_CHARS,
    GRAPH_MAX_NODES,
    GRAPH_SCHEMA_VERSION,
    GRAPH_SOURCE_SNAPSHOT,
    GRAPH_SOURCE_UNAVAILABLE,
    GRAPH_VERSION_HEX_CHARS,
    GraphDependencyEdge,
    GraphLoop,
    GraphNode,
    GraphRouter,
    GraphSpawnEdge,
    RunGraph,
    build_run_graph,
    compute_graph_version,
    display_text,
)

# ---------------------------------------------------------------------------
# Fixed clock values (CLAUDE.md: no direct clock calls; every timestamp is pinned).
# ---------------------------------------------------------------------------

T0 = "2026-09-27T10:00:00+00:00"
T1 = "2026-09-27T10:00:01+00:00"
SHA1 = "1" * 64
SHA2 = "2" * 64


def task(
    task_id: str,
    *,
    depends_on: list[str] | None = None,
    emit_tasks: bool = False,
) -> TaskSpec:
    return TaskSpec(
        id=task_id,
        agent="worker",
        instruction="instructions/do.md",
        depends_on=depends_on or [],
        emit_tasks=emit_tasks,
    )


def workflow(
    tasks: list[TaskSpec],
    *,
    loops: list[LoopSpec] | None = None,
    branches: list[RouterSpec] | None = None,
    workflow_id: str = "wf",
) -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0",
        id=workflow_id,
        repo_set="demo-repos",
        tasks=tasks,
        loops=loops or [],
        branches=branches or [],
    )


def snapshot_of(
    wf: WorkflowSpec, *, run_id: str = "run-1", sha: str = SHA1, written_at: str = T0
) -> WorkflowSnapshot:
    return WorkflowSnapshot(run_id=run_id, spec_sha256=sha, written_at=written_at, workflow=wf)


def spawn(
    parent_task_id: str,
    *,
    origin: str = "injected",
    injected_at: str = T0,
    cycle: int = 0,
    loop_id: str | None = None,
    iteration: int | None = None,
) -> SpawnRecord:
    return SpawnRecord(
        parent_task_id=parent_task_id,
        parent_dispatch_cycle=cycle,
        origin=origin,
        injected_at=injected_at,
        loop_id=loop_id,
        iteration=iteration,
    )


def ts(
    *,
    status: str = "succeeded",
    started_at: str | None = None,
    origin: str = "static",
    route: str | None = None,
) -> TaskRunState:
    return TaskRunState(status=status, started_at=started_at, origin=origin, route=route)


def run_state(
    *,
    run_id: str = "run-1",
    tasks_map: dict[str, TaskRunState] | None = None,
    injected_tasks: list[TaskSpec] | None = None,
    spawned_by: dict[str, SpawnRecord] | None = None,
    spec_sessions: list[SpecSession] | None = None,
    loop_iterations: dict[str, int] | None = None,
    route_decisions: dict[str, list[str]] | None = None,
) -> RunState:
    return RunState(
        run_id=run_id,
        workflow_id="wf",
        repo_set="demo-repos",
        started_at=T0,
        updated_at=T0,
        tasks=tasks_map or {},
        injected_tasks=injected_tasks or [],
        spawned_by=spawned_by or {},
        spec_sessions=spec_sessions or [],
        loop_iterations=loop_iterations or {},
        route_decisions=route_decisions or {},
    )


def node_map(g: RunGraph) -> dict[str, GraphNode]:
    return {n.id: n for n in g.nodes}


def dep_pairs(g: RunGraph) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in g.dependency_edges}


def spawn_pairs(g: RunGraph) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in g.spawn_edges}


# ---------------------------------------------------------------------------
# AC-1: dataclasses + constants.
# ---------------------------------------------------------------------------


class TestDataclassesAndConstants:
    def test_constants_have_the_exact_values_task_md_requires(self) -> None:
        assert GRAPH_SCHEMA_VERSION == 1
        assert GRAPH_MAX_NODES == 5000
        assert GRAPH_LABEL_MAX_CHARS == 200
        assert GRAPH_VERSION_HEX_CHARS == 16
        assert GRAPH_SOURCE_SNAPSHOT == "snapshot"
        assert GRAPH_SOURCE_UNAVAILABLE == "unavailable"

    @pytest.mark.parametrize(
        "cls",
        [RunGraph, GraphNode, GraphDependencyEdge, GraphSpawnEdge, GraphLoop, GraphRouter],
    )
    def test_every_graph_dataclass_is_frozen(self, cls: type) -> None:
        assert is_dataclass(cls)
        assert cls.__dataclass_params__.frozen is True  # type: ignore[attr-defined]

    def test_run_graph_field_names_match_hld_14_2(self) -> None:
        names = [f.name for f in fields(RunGraph)]
        assert names == [
            "schema_version",
            "run_id",
            "graph_version",
            "source",
            "spawn_data",
            "truncated",
            "warnings",
            "nodes",
            "dependency_edges",
            "spawn_edges",
            "loops",
            "routers",
        ]

    def test_graph_node_field_names_match_hld_14_2(self) -> None:
        names = [f.name for f in fields(GraphNode)]
        assert names == [
            "id",
            "label",
            "label_sanitized",
            "origin",
            "parent_task_id",
            "route",
            "loop_id",
            "iteration",
            "spawn_depth",
            "children_count",
            "is_emitter",
            "is_router",
            "is_loop_gate",
            "exec_ordinal",
            "missing",
        ]

    def test_run_graph_is_asdict_and_json_serializable(self) -> None:
        wf = workflow([task("a"), task("b", depends_on=["a"])])
        g = build_run_graph(run_state(), snapshot_of(wf))
        dumped = json.dumps(asdict(g))
        assert isinstance(dumped, str)
        assert json.loads(dumped)["nodes"][0]["id"] == "a"


# ---------------------------------------------------------------------------
# AC-2: edges come ONLY from dag.iter_dependency_edges (DRY, ADR-0017 D3).
# ---------------------------------------------------------------------------


class TestNoReimplementedEdgeDerivation:
    def test_no_own_dependency_iteration_or_output_input_matching(self) -> None:
        source = Path(graph_mod.__file__).read_text(encoding="utf-8")
        forbidden = ["depends_on", "output_to_task"]
        for token in forbidden:
            assert token not in source, f"ui/graph.py must not re-derive edges via {token!r}"

    def test_build_run_graph_calls_the_shared_iterator(self) -> None:
        source = Path(graph_mod.__file__).read_text(encoding="utf-8")
        assert "iter_dependency_edges(" in source


# ---------------------------------------------------------------------------
# AC-3: fixture scenarios (a)-(k).
# ---------------------------------------------------------------------------


class TestFixtureScenarios:
    def test_a_static_linear_chain_with_snapshot(self) -> None:
        wf = workflow([task("a"), task("b", depends_on=["a"]), task("c", depends_on=["b"])])
        state = run_state(spec_sessions=[SpecSession(session=1, started_at=T0, spec_sha256=SHA1)])
        g = build_run_graph(state, snapshot_of(wf, sha=SHA1))

        assert [n.id for n in g.nodes] == ["a", "b", "c"]
        assert [(e.source, e.target) for e in g.dependency_edges] == [("a", "b"), ("b", "c")]
        assert g.spawn_edges == []
        assert g.spawn_data == "none"
        assert g.source == GRAPH_SOURCE_SNAPSHOT
        assert g.warnings == []
        assert g.truncated is False

    def test_b_overseer_shape_dependency_and_spawn_edges_differ(self) -> None:
        static_wf = workflow([task("cp1", emit_tasks=True)])
        injected = [
            task("u1"),
            task("u2"),
            task("u3"),
            task("cp2", depends_on=["u1", "u2", "u3"]),
        ]
        state = run_state(
            injected_tasks=injected,
            spawned_by={
                "u1": spawn("cp1"),
                "u2": spawn("cp1"),
                "u3": spawn("cp1"),
                "cp2": spawn("cp1"),
            },
            spec_sessions=[SpecSession(session=1, started_at=T0, spec_sha256=SHA1)],
        )
        g = build_run_graph(state, snapshot_of(static_wf, sha=SHA1))

        assert dep_pairs(g) == {("u1", "cp2"), ("u2", "cp2"), ("u3", "cp2")}
        assert spawn_pairs(g) == {("cp1", "u1"), ("cp1", "u2"), ("cp1", "u3"), ("cp1", "cp2")}
        assert dep_pairs(g) != spawn_pairs(g), "U-4: the two edge sets must differ"

    def test_c_nested_emit_produces_spawn_depths_0_1_2(self) -> None:
        static_wf = workflow([task("a", emit_tasks=True)])
        injected = [task("b", emit_tasks=True), task("c")]
        state = run_state(
            injected_tasks=injected,
            spawned_by={"b": spawn("a", injected_at=T0), "c": spawn("b", injected_at=T1)},
            spec_sessions=[SpecSession(session=1, started_at=T0, spec_sha256=SHA1)],
        )
        g = build_run_graph(state, snapshot_of(static_wf, sha=SHA1))
        nodes = node_map(g)
        assert nodes["a"].spawn_depth == 0
        assert nodes["b"].spawn_depth == 1
        assert nodes["c"].spawn_depth == 2

    def test_d_loop_times_3_iteration_and_loop_id_and_loop_edge(self) -> None:
        loop = LoopSpec(
            id="L",
            body=["dev", "gate"],
            gate_task_id="gate",
            gate_output_path="out/gate.json",
            max_iterations=5,
        )
        static_wf = workflow(
            [
                task("dev"),
                task("gate", depends_on=["dev"]),
                task("downstream", depends_on=["L"]),
            ],
            loops=[loop],
        )
        injected = [
            task("dev__iter2", depends_on=["gate"]),
            task("gate__iter2", depends_on=["dev__iter2"]),
            task("dev__iter3", depends_on=["gate__iter2"]),
            task("gate__iter3", depends_on=["dev__iter3"]),
        ]
        state = run_state(
            injected_tasks=injected,
            spawned_by={
                "dev__iter2": spawn(
                    "gate", origin="loop", loop_id="L", iteration=2, injected_at=T0
                ),
                "gate__iter2": spawn(
                    "gate", origin="loop", loop_id="L", iteration=2, injected_at=T1
                ),
                "dev__iter3": spawn(
                    "gate__iter2", origin="loop", loop_id="L", iteration=3, injected_at=T1
                ),
                "gate__iter3": spawn(
                    "gate__iter2", origin="loop", loop_id="L", iteration=3, injected_at=T1
                ),
            },
            loop_iterations={"L": 3},
            spec_sessions=[SpecSession(session=1, started_at=T0, spec_sha256=SHA1)],
        )
        g = build_run_graph(state, snapshot_of(static_wf, sha=SHA1))
        nodes = node_map(g)

        assert nodes["dev"].iteration == 1 and nodes["dev"].loop_id == "L"
        assert nodes["gate"].iteration == 1 and nodes["gate"].loop_id == "L"
        assert nodes["dev__iter2"].iteration == 2
        assert nodes["dev__iter2"].loop_id == "L"
        assert nodes["dev__iter2"].parent_task_id == "gate"
        assert nodes["gate__iter3"].iteration == 3
        assert nodes["gate__iter3"].parent_task_id == "gate__iter2"
        assert nodes["gate"].is_loop_gate is True
        assert nodes["gate__iter2"].is_loop_gate is True

        loop_edges = [e for e in g.dependency_edges if e.kind == "loop"]
        assert any(
            e.source == "gate__iter3" and e.target == "downstream" and e.via == "L"
            for e in loop_edges
        )

        assert g.loops == [
            GraphLoop(
                id="L",
                body=["dev", "gate"],
                gate_task_id="gate",
                max_iterations=5,
                iterations_materialized=3,
            )
        ]

    def test_e_router_with_not_taken_cone(self) -> None:
        router = RouterSpec(
            id="classify",
            router_task_id="triage",
            verdict_path="out/verdict.json",
            routes={"bug": RouteSpec(entry=["fixer"]), "docs": RouteSpec(entry=["writer"])},
        )
        static_wf = workflow(
            [
                task("triage"),
                task("fixer", depends_on=["triage"]),
                task("writer", depends_on=["triage"]),
            ],
            branches=[router],
        )
        state = run_state(
            tasks_map={
                "triage": ts(),
                "fixer": ts(),
                "writer": ts(status="not_taken", route="classify:docs"),
            },
            route_decisions={"classify": ["bug"]},
            spec_sessions=[SpecSession(session=1, started_at=T0, spec_sha256=SHA1)],
        )
        g = build_run_graph(state, snapshot_of(static_wf, sha=SHA1))
        nodes = node_map(g)

        assert nodes["triage"].is_router is True
        assert nodes["fixer"].is_router is False
        assert g.routers == [GraphRouter(id="classify", router_task_id="triage", selected=["bug"])]

    def test_f_unknown_dependency_yields_phantom_node_and_warning(self) -> None:
        static_wf = workflow([task("a"), task("b", depends_on=["a", "zzz"])])
        state = run_state(spec_sessions=[SpecSession(session=1, started_at=T0, spec_sha256=SHA1)])
        g = build_run_graph(state, snapshot_of(static_wf, sha=SHA1))
        nodes = node_map(g)

        assert "zzz" in nodes
        assert nodes["zzz"].missing is True
        assert nodes["zzz"].origin == "static"
        assert any(e.source == "zzz" and e.target == "b" for e in g.dependency_edges)
        assert g.warnings == [
            "1 dependency id(s) reference unknown tasks and are shown as missing nodes."
        ]

    def test_g_legacy_no_spec_sessions_no_spawned_by_injected_tasks_present(self) -> None:
        state = run_state(injected_tasks=[task("d"), task("e", depends_on=["d"])])
        g = build_run_graph(state, None)

        assert g.source == GRAPH_SOURCE_UNAVAILABLE
        assert g.spawn_data == "not_recorded"
        assert dep_pairs(g) == {("d", "e")}
        assert len(g.warnings) == 2
        assert "predates workflow snapshots" in g.warnings[0]
        assert "predates spawn tracking" in g.warnings[1]

    def test_h_spec_sessions_present_but_snapshot_none(self) -> None:
        state = run_state(spec_sessions=[SpecSession(session=1, started_at=T0, spec_sha256=SHA1)])
        g = build_run_graph(state, None)

        assert g.source == GRAPH_SOURCE_UNAVAILABLE
        assert len(g.warnings) == 1
        assert "missing or unreadable" in g.warnings[0]

    def test_i_spec_changed_mid_run_warning_names_latest_session(self) -> None:
        static_wf = workflow([task("a")])
        state = run_state(
            spec_sessions=[
                SpecSession(session=1, started_at=T0, spec_sha256=SHA1),
                SpecSession(session=2, started_at=T1, spec_sha256=SHA2),
            ]
        )
        g = build_run_graph(state, snapshot_of(static_wf, sha=SHA2))
        assert any("session 2" in w for w in g.warnings)

    def test_j_execution_ordinal_by_time_then_id_then_none(self) -> None:
        static_wf = workflow(
            [task("alpha"), task("beta"), task("gamma"), task("delta"), task("echo"), task("zeta")]
        )
        state = run_state(
            tasks_map={
                "alpha": ts(started_at="2026-09-27T10:00:05+00:00"),
                "beta": ts(started_at="2026-09-27T10:00:01+00:00"),
                "gamma": ts(started_at="2026-09-27T10:00:09+00:00"),
                "delta": ts(started_at="2026-09-27T10:00:07+00:00"),
                "echo": ts(started_at="2026-09-27T10:00:07+00:00"),
                "zeta": ts(status="pending", started_at=None),
            },
            spec_sessions=[SpecSession(session=1, started_at=T0, spec_sha256=SHA1)],
        )
        g = build_run_graph(state, snapshot_of(static_wf, sha=SHA1))
        nodes = node_map(g)

        assert nodes["beta"].exec_ordinal == 1
        assert nodes["alpha"].exec_ordinal == 2
        assert nodes["delta"].exec_ordinal == 3
        assert nodes["echo"].exec_ordinal == 4
        assert nodes["gamma"].exec_ordinal == 5
        assert nodes["zeta"].exec_ordinal is None

    def test_k_origin_fallback_record_then_task_run_state_then_static(self) -> None:
        static_wf = workflow([task("root", depends_on=["ghost"])])
        state = run_state(
            tasks_map={"orphan_task": ts(origin="loop")},
            injected_tasks=[task("child")],
            spawned_by={"child": spawn("root", origin="injected")},
            spec_sessions=[SpecSession(session=1, started_at=T0, spec_sha256=SHA1)],
        )
        g = build_run_graph(state, snapshot_of(static_wf, sha=SHA1))
        nodes = node_map(g)

        assert nodes["child"].origin == "injected"  # from the spawned_by record
        assert nodes["orphan_task"].origin == "loop"  # from TaskRunState, no spawned_by record
        assert nodes["ghost"].origin == "static"  # phantom: neither record nor task state
        assert nodes["ghost"].missing is True


# ---------------------------------------------------------------------------
# AC-4: display_text sanitizer.
# ---------------------------------------------------------------------------


class TestDisplayTextSanitizer:
    def test_strips_bidi_override_and_zero_width(self) -> None:
        raw = "a‮b​c"
        cleaned, changed = display_text(raw)
        assert cleaned == "abc"
        assert changed is True

    def test_1000_char_id_truncates_with_ellipsis_and_flags_sanitized(self) -> None:
        raw = "x" * 1000
        cleaned, changed = display_text(raw)
        assert len(cleaned) == GRAPH_LABEL_MAX_CHARS
        assert cleaned.endswith("…")
        assert changed is True

    def test_plain_id_passes_through_unchanged(self) -> None:
        assert display_text("plain-id") == ("plain-id", False)

    def test_raw_id_field_never_sanitized_only_label_is(self) -> None:
        weird_id = "leaf​"
        static_wf = workflow([task(weird_id)])
        g = build_run_graph(run_state(), snapshot_of(static_wf))
        node = g.nodes[0]
        assert node.id == weird_id  # untouched -- it is the join key
        assert node.label == "leaf"
        assert node.label_sanitized is True


# ---------------------------------------------------------------------------
# AC-5: early size cap.
# ---------------------------------------------------------------------------


class TestEarlyCap:
    def test_50000_tasks_builds_fast_and_caps_at_graph_max_nodes(self) -> None:
        n = 50_000
        shared_ts = ts()
        ids = [f"t{i:06d}" for i in range(n)]
        state = run_state(tasks_map=dict.fromkeys(ids, shared_ts))

        started = time.perf_counter()
        g = build_run_graph(state, None)
        elapsed = time.perf_counter() - started

        assert elapsed < 3.0, f"early cap must bound the work; took {elapsed:.3f}s"
        assert len(g.nodes) == GRAPH_MAX_NODES
        assert g.truncated is True
        assert all(not n.missing for n in g.nodes), "must not invent phantoms from cut tasks"

    def test_truncation_splits_merged_then_orphans(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(graph_mod, "GRAPH_MAX_NODES", 4)
        static_wf = workflow([task("a"), task("b"), task("c")])  # 3 merged
        state = run_state(
            tasks_map=dict.fromkeys(["a", "b", "c", "o1", "o2", "o3", "o4", "o5"], ts()),
            spec_sessions=[SpecSession(session=1, started_at=T0, spec_sha256=SHA1)],
        )
        g = build_run_graph(state, snapshot_of(static_wf, sha=SHA1))

        assert g.truncated is True
        assert len(g.nodes) == 4
        ids = [n.id for n in g.nodes]
        assert ids[:3] == ["a", "b", "c"]  # merged kept in full (3 < cap)
        assert ids[3] == "o1"  # exactly one orphan admitted (cap - merged = 1)


# ---------------------------------------------------------------------------
# AC-6: forged spawn cycle.
# ---------------------------------------------------------------------------


class TestForgedSpawnCycle:
    def test_two_node_cycle_terminates_with_none_depths(self) -> None:
        static_wf = workflow([task("a"), task("b")])
        state = run_state(
            spawned_by={"a": spawn("b"), "b": spawn("a")},
            spec_sessions=[SpecSession(session=1, started_at=T0, spec_sha256=SHA1)],
        )
        g = build_run_graph(state, snapshot_of(static_wf, sha=SHA1))  # must not raise
        nodes = node_map(g)
        assert nodes["a"].spawn_depth is None
        assert nodes["b"].spawn_depth is None

    def test_three_node_cycle_with_no_root_leaves_every_member_unreached(self) -> None:
        """a->b->c->a: every member has exactly one (cyclic) incoming spawn edge, so
        there is no root at all -- the whole cycle is absent from the depth map."""
        static_wf = workflow([task("a"), task("b"), task("c")])
        state = run_state(
            spawned_by={"a": spawn("c"), "b": spawn("a"), "c": spawn("b")},
            spec_sessions=[SpecSession(session=1, started_at=T0, spec_sha256=SHA1)],
        )
        g = build_run_graph(state, snapshot_of(static_wf, sha=SHA1))  # must not raise
        nodes = node_map(g)
        assert nodes["a"].spawn_depth is None
        assert nodes["b"].spawn_depth is None
        assert nodes["c"].spawn_depth is None


# ---------------------------------------------------------------------------
# AC-7: determinism.
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_building_twice_from_identical_inputs_is_byte_identical(self) -> None:
        loop = LoopSpec(
            id="L", body=["dev", "gate"], gate_task_id="gate", gate_output_path="out/gate.json"
        )
        router = RouterSpec(
            id="classify",
            router_task_id="triage",
            verdict_path="out/v.json",
            routes={"bug": RouteSpec(entry=["fixer"])},
        )
        static_wf = workflow(
            [
                task("dev"),
                task("gate", depends_on=["dev"]),
                task("triage"),
                task("fixer", depends_on=["triage", "L"]),
            ],
            loops=[loop],
            branches=[router],
        )
        state = run_state(
            tasks_map={"triage": ts(started_at=T0), "fixer": ts(started_at=T1)},
            injected_tasks=[task("dev__iter2", depends_on=["gate"])],
            spawned_by={"dev__iter2": spawn("gate", origin="loop", loop_id="L", iteration=2)},
            loop_iterations={"L": 2},
            route_decisions={"classify": ["bug"]},
            spec_sessions=[SpecSession(session=1, started_at=T0, spec_sha256=SHA1)],
        )
        snap = snapshot_of(static_wf, sha=SHA1)

        first = json.dumps(asdict(build_run_graph(state, snap)), sort_keys=False)
        second = json.dumps(asdict(build_run_graph(state, snap)), sort_keys=False)
        assert first == second


# ---------------------------------------------------------------------------
# AC-8: compute_graph_version sensitivity.
# ---------------------------------------------------------------------------


class TestGraphVersionSensitivity:
    def _base_state(self) -> RunState:
        return run_state(
            tasks_map={"a": ts(status="running", started_at=T0)},
            spec_sessions=[SpecSession(session=1, started_at=T0, spec_sha256=SHA1)],
        )

    def test_changes_on_new_injected_task(self) -> None:
        base = self._base_state()
        changed = base.model_copy(update={"injected_tasks": [task("new-one")]})
        assert compute_graph_version(base) != compute_graph_version(changed)

    def test_changes_on_new_spec_session_sha(self) -> None:
        base = self._base_state()
        changed = base.model_copy(
            update={
                "spec_sessions": [
                    *base.spec_sessions,
                    SpecSession(session=2, started_at=T1, spec_sha256=SHA2),
                ]
            }
        )
        assert compute_graph_version(base) != compute_graph_version(changed)

    def test_changes_on_loop_iterations_change(self) -> None:
        base = self._base_state()
        changed = base.model_copy(update={"loop_iterations": {"L": 2}})
        assert compute_graph_version(base) != compute_graph_version(changed)

    def test_unchanged_on_status_attempts_started_at_cost_change(self) -> None:
        base = self._base_state()
        changed = base.model_copy(
            update={
                "tasks": {
                    "a": ts(status="succeeded", started_at=T1).model_copy(
                        update={"attempts": 3, "cumulative_cost_usd": 12.5}
                    )
                }
            }
        )
        assert compute_graph_version(base) == compute_graph_version(changed)


# ---------------------------------------------------------------------------
# AC-9: perf envelope (200 nodes / 500 edges).
# ---------------------------------------------------------------------------


def _generate_edges(n_nodes: int, n_edges: int) -> dict[int, list[int]]:
    """Deterministic layered edge generator: no `random` (CLAUDE.md determinism rule)."""
    deps: dict[int, list[int]] = {i: [] for i in range(n_nodes)}
    count = 0
    offset = 1
    while count < n_edges and offset < n_nodes:
        progressed = False
        for i in range(offset, n_nodes):
            if count >= n_edges:
                break
            deps[i].append(i - offset)
            count += 1
            progressed = True
        offset += 1
        if not progressed:
            break
    return deps


def _perf_state_and_snapshot(
    n_nodes: int = 200, n_edges: int = 500
) -> tuple[RunState, WorkflowSnapshot]:
    deps = _generate_edges(n_nodes, n_edges)
    tasks = [task(f"t{i:04d}", depends_on=[f"t{j:04d}" for j in deps[i]]) for i in range(n_nodes)]
    static_wf = workflow(tasks)
    state = run_state(spec_sessions=[SpecSession(session=1, started_at=T0, spec_sha256=SHA1)])
    return state, snapshot_of(static_wf, sha=SHA1)


class TestPerfEnvelope:
    def test_generator_produces_exactly_200_nodes_and_500_edges(self) -> None:
        state, snap = _perf_state_and_snapshot()
        g = build_run_graph(state, snap)
        assert len(g.nodes) == 200
        assert len(g.dependency_edges) == 500

    def test_build_under_ci_safe_450ms_budget(self) -> None:
        state, snap = _perf_state_and_snapshot()
        build_run_graph(state, snap)  # warm-up (regex/module already loaded either way)
        samples_ms = []
        for _ in range(3):
            started = time.perf_counter()
            build_run_graph(state, snap)
            samples_ms.append((time.perf_counter() - started) * 1000)
        best = min(samples_ms)
        # HLD §18/§14.2 NFR-3: 150 ms dev-machine target, 450 ms CI-safe (3x headroom).
        # best-of-3 to avoid a one-off scheduling hiccup flaking the assertion.
        assert best <= 450, f"build_run_graph best-of-3 took {best:.1f}ms, budget is 450ms"


# ---------------------------------------------------------------------------
# AC-10: no fastapi import, no file I/O, importable standalone.
# ---------------------------------------------------------------------------


class TestSpawnDepthHelperDirectly:
    """White-box coverage of `_compute_spawn_depths`'s own contract: any iterable of
    (parent, child) pairs, which is broader than the one-parent-per-child shape
    `RunState.spawned_by: dict[str, SpawnRecord]` can express through the public API.
    """

    def test_diamond_shape_exercises_the_already_visited_guard(self) -> None:
        # Two roots feed the same child -- the one shape that pushes a node into the BFS
        # queue twice before it is dequeued, exercising the visited-set guard HLD §8.3.2's
        # pseudocode calls for.
        edges = [("r1", "x"), ("r2", "x"), ("x", "y")]
        depth = graph_mod._compute_spawn_depths(edges)
        assert depth == {"r1": 0, "r2": 0, "x": 1, "y": 2}


class TestParseStartedAtDefensive:
    def test_malformed_started_at_is_treated_as_never_started(self) -> None:
        static_wf = workflow([task("a")])
        state = run_state(
            tasks_map={"a": ts(started_at="not-a-timestamp")},
            spec_sessions=[SpecSession(session=1, started_at=T0, spec_sha256=SHA1)],
        )
        g = build_run_graph(state, snapshot_of(static_wf, sha=SHA1))
        assert node_map(g)["a"].exec_ordinal is None


class TestPureModuleBoundary:
    def test_no_web_framework_import_and_no_file_io(self) -> None:
        source = Path(graph_mod.__file__).read_text(encoding="utf-8")
        assert "fastapi" not in source
        for banned in ("open(", "read_text(", "write_text(", "Path("):
            assert banned not in source, f"ui/graph.py must do no file I/O ({banned!r} found)"

    def test_module_has_no_pathlib_or_os_import(self) -> None:
        source = Path(graph_mod.__file__).read_text(encoding="utf-8")
        assert "import os" not in source
        assert "from pathlib" not in source
        assert "import pathlib" not in source
