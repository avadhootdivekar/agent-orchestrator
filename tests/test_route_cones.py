"""Tests for route cone computation (T-c4w6p1, epic E-rc7k2v). Pure algorithmic
core only — no activation/not_taken logic, join resolution, or `ao validate`
rules are exercised here; those land in downstream tickets (T-m2h5t7, T-w6p2c8).

Covers TASK.md acceptance criteria 1-6:
  1. Two-route workflow sharing a head + declared convergence tail -> disjoint
     exclusive cones, tail excluded from both (shared).
  2. An INFERRED (path-matching, non-depends_on) edge extends a cone -- proves
     cones reflect the runtime graph build_dag returns, not depends_on alone
     (memory `build-dag-infers-edges-from-paths`).
  3. membership[t] lists every (router_id, route_id) reaching t; a task
     reachable from 2 routes of ONE router has both entries.
  4. Determinism across repeated calls (sets, so order never matters).
  5. Producer-map accessor (Graph.output_to_task / Graph.producer_of) exposed
     and exercised.
  6. Empty branches, single-router multi-route, two independent routers, and
     the inferred-edge extension are all covered.
"""

from __future__ import annotations

from agent_orchestrator.dag import Graph, build_dag, compute_cones, forward_closure
from agent_orchestrator.models import RouterSpec, RouteSpec, TaskSpec, WorkflowSpec


def _workflow(
    tasks: list[TaskSpec],
    branches: list[RouterSpec] | None = None,
    wf_id: str = "test-wf",
) -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0",
        id=wf_id,
        repo_set="rs",
        tasks=tasks,
        branches=branches or [],
    )


def _task(
    tid: str,
    depends_on: list[str] | None = None,
    inputs: list[str] | None = None,
    outputs: list[str] | None = None,
) -> TaskSpec:
    return TaskSpec(
        id=tid,
        agent="agent",
        instruction="instr.md",
        depends_on=depends_on or [],
        inputs=inputs or [],
        outputs=outputs or [],
    )


class TestEmptyBranches:
    def test_no_branches_returns_empty_structures(self) -> None:
        """AC6: no workflow.branches at all -> compute_cones returns empty
        cones/membership without error."""
        tasks = [_task("a"), _task("b", depends_on=["a"])]
        workflow = _workflow(tasks)
        graph = build_dag(workflow)

        cones, membership = compute_cones(workflow, graph)

        assert cones == {}
        assert membership == {}


class TestSharedConvergenceTail:
    """AC1 + AC3: two routes off one router sharing a head + a declared
    convergence tail."""

    def _build(self) -> tuple[WorkflowSpec, Graph]:
        tasks = [
            _task("classify", outputs=["out/verdict.json"]),
            _task("bug-fix", depends_on=["classify"]),
            _task("epic-plan", depends_on=["classify"]),
            _task("converge", depends_on=["bug-fix", "epic-plan"]),
        ]
        router = RouterSpec(
            id="classify-router",
            router_task_id="classify",
            verdict_path="out/verdict.json",
            routes={
                "bug": RouteSpec(entry=["bug-fix"]),
                "epic": RouteSpec(entry=["epic-plan"]),
            },
        )
        workflow = _workflow(tasks, branches=[router])
        graph = build_dag(workflow)
        return workflow, graph

    def test_exclusive_cones_are_disjoint_and_exclude_shared_tail(self) -> None:
        workflow, graph = self._build()
        cones, _ = compute_cones(workflow, graph)

        assert cones["classify-router"]["bug"] == {"bug-fix"}
        assert cones["classify-router"]["epic"] == {"epic-plan"}
        # Shared/convergence tail must not appear in EITHER exclusive cone.
        assert "converge" not in cones["classify-router"]["bug"]
        assert "converge" not in cones["classify-router"]["epic"]
        # Cones are disjoint.
        assert cones["classify-router"]["bug"].isdisjoint(cones["classify-router"]["epic"])

    def test_membership_lists_both_routes_for_the_shared_tail(self) -> None:
        """AC3: a task reachable from 2 routes of ONE router has both entries
        in membership."""
        workflow, graph = self._build()
        _, membership = compute_cones(workflow, graph)

        assert membership["converge"] == {
            ("classify-router", "bug"),
            ("classify-router", "epic"),
        }
        # Exclusive tasks have exactly one membership entry.
        assert membership["bug-fix"] == {("classify-router", "bug")}
        assert membership["epic-plan"] == {("classify-router", "epic")}


class TestInferredEdgeExtendsCone:
    """AC2: the memory-pitfall regression test. A route reaches a task purely
    through an edge INFERRED from input/output path matching (no depends_on) --
    the cone must include it, proving compute_cones uses build_dag's runtime
    adjacency (graph.adjacency()), not workflow depends_on alone."""

    def test_inferred_edge_extends_the_route_cone(self) -> None:
        tasks = [
            _task("router-task", outputs=["out/verdict.json"]),
            _task("route-entry", depends_on=["router-task"], outputs=["out/mid.txt"]),
            # Deliberately no depends_on: reachable from route-entry ONLY via
            # the inferred input/output path-matching edge.
            _task("inferred-downstream", inputs=["out/mid.txt"]),
        ]
        router = RouterSpec(
            id="r",
            router_task_id="router-task",
            verdict_path="out/verdict.json",
            routes={"only": RouteSpec(entry=["route-entry"])},
        )
        workflow = _workflow(tasks, branches=[router])
        graph = build_dag(workflow)

        cones, membership = compute_cones(workflow, graph)

        assert cones["r"]["only"] == {"route-entry", "inferred-downstream"}
        assert membership["inferred-downstream"] == {("r", "only")}

        # Load-bearing check: a depends_on-only adjacency (ignoring build_dag's
        # inferred edges entirely) must NOT reach "inferred-downstream" -- this
        # is exactly the bug this test guards against (computing cones over
        # depends_on alone instead of graph.adjacency()).
        declared_only_adj: dict[str, list[str]] = {t.id: [] for t in tasks}
        for t in tasks:
            for dep in t.depends_on:
                declared_only_adj[dep].append(t.id)
        declared_reach = forward_closure(declared_only_adj, ["route-entry"])
        assert "inferred-downstream" not in declared_reach


class TestSingleRouterMultiRoute:
    def test_three_routes_each_get_exclusive_cone(self) -> None:
        tasks = [
            _task("head"),
            _task("a1", depends_on=["head"]),
            _task("a2", depends_on=["a1"]),
            _task("b1", depends_on=["head"]),
            _task("c1", depends_on=["head"]),
        ]
        router = RouterSpec(
            id="router",
            router_task_id="head",
            verdict_path="out/verdict.json",
            routes={
                "a": RouteSpec(entry=["a1"]),
                "b": RouteSpec(entry=["b1"]),
                "c": RouteSpec(entry=["c1"]),
            },
        )
        workflow = _workflow(tasks, branches=[router])
        graph = build_dag(workflow)

        cones, membership = compute_cones(workflow, graph)

        assert cones["router"]["a"] == {"a1", "a2"}
        assert cones["router"]["b"] == {"b1"}
        assert cones["router"]["c"] == {"c1"}
        assert membership["a2"] == {("router", "a")}


class TestTwoIndependentRouters:
    def test_routers_do_not_cross_contaminate_cones_or_membership(self) -> None:
        """Two routers, each with routes reusing the same route ids ('a'/'b'),
        must not be confused with each other -- keyed by (router_id, route_id)."""
        tasks = [
            _task("head1"),
            _task("r1-a", depends_on=["head1"]),
            _task("r1-b", depends_on=["head1"]),
            _task("head2"),
            _task("r2-a", depends_on=["head2"]),
            _task("r2-b", depends_on=["head2"]),
        ]
        router1 = RouterSpec(
            id="router1",
            router_task_id="head1",
            verdict_path="out/v1.json",
            routes={"a": RouteSpec(entry=["r1-a"]), "b": RouteSpec(entry=["r1-b"])},
        )
        router2 = RouterSpec(
            id="router2",
            router_task_id="head2",
            verdict_path="out/v2.json",
            routes={"a": RouteSpec(entry=["r2-a"]), "b": RouteSpec(entry=["r2-b"])},
        )
        workflow = _workflow(tasks, branches=[router1, router2])
        graph = build_dag(workflow)

        cones, membership = compute_cones(workflow, graph)

        assert cones["router1"]["a"] == {"r1-a"}
        assert cones["router1"]["b"] == {"r1-b"}
        assert cones["router2"]["a"] == {"r2-a"}
        assert cones["router2"]["b"] == {"r2-b"}

        assert membership["r1-a"] == {("router1", "a")}
        assert membership["r2-a"] == {("router2", "a")}


class TestDeterminism:
    def test_repeated_calls_return_identical_cones_and_membership(self) -> None:
        """AC4/NFR-2: two calls on the same spec return identical results."""
        tasks = [
            _task("classify", outputs=["out/verdict.json"]),
            _task("bug-fix", depends_on=["classify"]),
            _task("epic-plan", depends_on=["classify"]),
            _task("converge", depends_on=["bug-fix", "epic-plan"]),
        ]
        router = RouterSpec(
            id="classify-router",
            router_task_id="classify",
            verdict_path="out/verdict.json",
            routes={
                "bug": RouteSpec(entry=["bug-fix"]),
                "epic": RouteSpec(entry=["epic-plan"]),
            },
        )
        workflow = _workflow(tasks, branches=[router])
        graph = build_dag(workflow)

        results = [compute_cones(workflow, graph) for _ in range(5)]
        first = results[0]
        assert all(r == first for r in results)


class TestForwardClosure:
    def test_entries_included_and_transitive(self) -> None:
        adj = {"a": ["b"], "b": ["c"], "c": []}
        assert forward_closure(adj, ["a"]) == {"a", "b", "c"}

    def test_disconnected_entries_stay_separate_unless_joined(self) -> None:
        adj = {"a": ["b"], "b": [], "x": ["y"], "y": []}
        assert forward_closure(adj, ["a"]) == {"a", "b"}
        assert forward_closure(adj, ["a", "x"]) == {"a", "b", "x", "y"}

    def test_unknown_entry_with_no_adjacency_entry_is_included_alone(self) -> None:
        adj: dict[str, list[str]] = {}
        assert forward_closure(adj, ["z"]) == {"z"}


class TestProducerMapAccessor:
    """AC5: the producer-map accessor is exposed and reusable."""

    def test_output_to_task_and_producer_of(self) -> None:
        tasks = [
            _task("a", outputs=["out/x.txt"]),
            _task("b", inputs=["out/x.txt"]),
        ]
        graph = build_dag(_workflow(tasks))

        assert graph.output_to_task() == {"out/x.txt": "a"}
        assert graph.producer_of("out/x.txt") == "a"
        assert graph.producer_of("no/such/path") is None

    def test_adjacency_accessor_matches_internal_state_and_includes_inferred_edges(
        self,
    ) -> None:
        tasks = [
            _task("a", outputs=["out/x.txt"]),
            _task("b", inputs=["out/x.txt"]),
        ]
        graph = build_dag(_workflow(tasks))

        adj = graph.adjacency()
        assert adj["a"] == ["b"]
        assert adj["b"] == []
