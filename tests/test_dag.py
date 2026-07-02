"""Tests for DAG construction, cycle detection, and topological ordering."""

from __future__ import annotations

import pytest

from agent_orchestrator.dag import build_dag
from agent_orchestrator.errors import CycleError, MissingInputError
from agent_orchestrator.models import TaskSpec, WorkflowSpec


def _workflow(tasks: list[TaskSpec], wf_id: str = "test-wf") -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0",
        id=wf_id,
        repo_set="rs",
        tasks=tasks,
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


class TestTopologicalOrder:
    def test_linear_chain(self) -> None:
        """A -> B -> C must be ordered [A, B, C]."""
        tasks = [
            _task("a"),
            _task("b", depends_on=["a"]),
            _task("c", depends_on=["b"]),
        ]
        graph = build_dag(_workflow(tasks))
        order = graph.topological_order()
        assert order == ["a", "b", "c"]

    def test_diamond(self) -> None:
        """Diamond: A -> B, A -> C, B -> D, C -> D.
        Valid orders are [A, B, C, D] or [A, C, B, D] — D must be last, A must be first.
        """
        tasks = [
            _task("a"),
            _task("b", depends_on=["a"]),
            _task("c", depends_on=["a"]),
            _task("d", depends_on=["b", "c"]),
        ]
        graph = build_dag(_workflow(tasks))
        order = graph.topological_order()
        assert order[0] == "a"
        assert order[-1] == "d"
        assert set(order) == {"a", "b", "c", "d"}
        # b and c must both precede d
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_single_node(self) -> None:
        tasks = [_task("a")]
        graph = build_dag(_workflow(tasks))
        assert graph.topological_order() == ["a"]


class TestCycleDetection:
    def test_self_cycle(self) -> None:
        """A depending on itself must raise CycleError."""
        tasks = [_task("a", depends_on=["a"])]
        # build_dag produces the adj; cycle detected in topological_order
        graph = build_dag(_workflow(tasks))
        with pytest.raises(CycleError) as exc_info:
            graph.topological_order()
        assert "a" in exc_info.value.nodes

    def test_three_node_cycle(self) -> None:
        """A -> B -> C -> A must raise CycleError."""
        tasks = [
            _task("a", depends_on=["c"]),
            _task("b", depends_on=["a"]),
            _task("c", depends_on=["b"]),
        ]
        graph = build_dag(_workflow(tasks))
        with pytest.raises(CycleError):
            graph.topological_order()

    def test_cycle_error_nodes(self) -> None:
        tasks = [
            _task("x", depends_on=["y"]),
            _task("y", depends_on=["x"]),
        ]
        graph = build_dag(_workflow(tasks))
        with pytest.raises(CycleError) as exc_info:
            graph.topological_order()
        assert sorted(exc_info.value.nodes) == ["x", "y"]


class TestInferredEdges:
    def test_inferred_edge_from_output_input_match(self) -> None:
        """If A.outputs=[x] and B.inputs=[x], edge A->B is inferred even without depends_on."""
        tasks = [
            _task("a", outputs=["output/x.txt"]),
            _task("b", inputs=["output/x.txt"]),
        ]
        graph = build_dag(_workflow(tasks))
        order = graph.topological_order()
        assert order.index("a") < order.index("b")


class TestValidateInputs:
    def test_missing_input_raises(self) -> None:
        """B.inputs=[x] but no task produces x and it doesn't exist -> MissingInputError."""
        tasks = [
            _task("a"),
            _task("b", inputs=["missing/file.txt"], depends_on=["a"]),
        ]
        graph = build_dag(_workflow(tasks))
        with pytest.raises(MissingInputError) as exc_info:
            graph.validate_inputs(lambda p: False)
        assert exc_info.value.task_id == "b"
        assert exc_info.value.path == "missing/file.txt"

    def test_input_resolved_from_prior_output(self) -> None:
        """B.inputs=[x], A.outputs=[x] -> validates OK (no filesystem check needed)."""
        tasks = [
            _task("a", outputs=["output/x.txt"]),
            _task("b", inputs=["output/x.txt"]),
        ]
        graph = build_dag(_workflow(tasks))
        # Should not raise; x is produced by a
        graph.validate_inputs(lambda p: False)

    def test_input_exists_on_disk(self) -> None:
        """B.inputs=[x] and exists_fn says x exists -> validates OK."""
        tasks = [
            _task("b", inputs=["existing/file.txt"]),
        ]
        graph = build_dag(_workflow(tasks))
        graph.validate_inputs(lambda p: p == "existing/file.txt")

    def test_deterministic_ordering(self) -> None:
        """Topological order is deterministic across repeated calls."""
        tasks = [
            _task("d", depends_on=["b", "c"]),
            _task("c", depends_on=["a"]),
            _task("b", depends_on=["a"]),
            _task("a"),
        ]
        graph = build_dag(_workflow(tasks))
        orders = [graph.topological_order() for _ in range(5)]
        # All 5 runs must produce the same ordering
        assert all(o == orders[0] for o in orders)
        assert orders[0][0] == "a"
        assert orders[0][-1] == "d"
