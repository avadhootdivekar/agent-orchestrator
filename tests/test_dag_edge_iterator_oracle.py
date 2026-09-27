"""Oracle-equality gate for the `dag.py` edge-derivation refactor (T-mzT3BW, ADR-0017 D3).

`_oracle_build_dag` below is a FROZEN, byte-for-byte copy of `build_dag`'s pre-refactor body
(dag.py @ commit 191da69, before `iter_dependency_edges` was extracted). It exists ONLY as a
fixed reference to diff the new, iterator-consuming `build_dag` against — do NOT "clean it up"
to match the new code, and do NOT delete it once the refactor lands.

Why this file is mandatory (ADR-0017 Risk R-2, HLD §8.3.1): `Graph.adjacency()`'s list order
feeds `Graph.topological_order()` (Kahn's algorithm pops in sorted order but ties/insertion
order still shape scheduling), which is production task dispatch order. A subtle reordering
in the extracted `iter_dependency_edges` would silently change which task the engine dispatches
first among otherwise-equal candidates. So every workflow spec this repo can load (AC-2) is run
through BOTH implementations and their `adj` / `output_to_task` are asserted equal, INCLUDING
list order (`json.dumps` equality, not set equality) — this is this task's hard merge gate
(Gate G1), reviewed sign-off required before merge (see STATUS.md).

Covers TASK.md AC-2 (oracle equality over every loadable spec), AC-3 (synthetic edge-kind /
dedup / phantom-node cases), AC-4 (byte-identical inferred-edge warning text), and AC-5
(`topological_order()` equality over the same spec set as AC-2).
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

import pytest

from agent_orchestrator.dag import (
    EDGE_KIND_EXPLICIT,
    EDGE_KIND_INFERRED,
    EDGE_KIND_LOOP,
    DependencyEdge,
    Graph,
    _resolve_loop_dep,
    build_dag,
    iter_dependency_edges,
)
from agent_orchestrator.errors import CycleError
from agent_orchestrator.models import LoopSpec, TaskSpec, WorkflowSpec
from agent_orchestrator.spec import load_workflow
from agent_orchestrator.templates import instantiate, load_template

_REPO_ROOT = Path(__file__).resolve().parents[1]

# A logger distinct from `agent_orchestrator.dag`'s own, so AC-4's byte-identity assertion
# genuinely compares two independently-produced log records rather than the same call site.
_ORACLE_LOGGER = logging.getLogger(f"{__name__}.oracle")


# ---------------------------------------------------------------------------
# The frozen oracle (verbatim pre-refactor `build_dag` body — see module docstring)
# ---------------------------------------------------------------------------


def _oracle_build_dag(workflow: WorkflowSpec) -> Graph:
    """Build a Graph from a WorkflowSpec.

    Edges are derived from:
    1. Explicit ``depends_on`` declarations.
       If a dep matches a loop id, it is resolved to the last materialized
       iteration's last task (HLD §4.5, ADR-001).
    2. Inferred edges when task A's output path matches task B's input path
       (logged as a warning if the dependency is not declared explicitly).
    """
    tasks = workflow.tasks

    # Set of loop ids for fast lookup
    loop_ids = {lp.id for lp in workflow.loops}

    # Map output path -> producing task id
    output_to_task: dict[str, str] = {}
    for task in tasks:
        for out in task.outputs:
            output_to_task[out] = task.id

    # adj[A] = [B, C] means A must complete before B and C can run (A -> B, A -> C)
    adj: dict[str, list[str]] = {t.id: [] for t in tasks}

    for task in tasks:
        # Explicit depends_on: dep must run before task
        for dep in task.depends_on:
            # Resolve loop-id deps to the final iteration's last task
            resolved_dep = _resolve_loop_dep(dep, workflow) if dep in loop_ids else dep
            if resolved_dep not in adj:
                # Unknown dep — leave as-is so cycle/missing detection fires
                adj[resolved_dep] = []
            if task.id not in adj[resolved_dep]:
                adj[resolved_dep].append(task.id)

    # Inferred edges from input/output path matching
    for task in tasks:
        for inp in task.inputs:
            producer = output_to_task.get(inp)
            if producer and producer != task.id:
                if task.id not in adj[producer]:
                    adj[producer].append(task.id)
                    if producer not in task.depends_on:
                        _ORACLE_LOGGER.warning(
                            "Inferred edge %s -> %s (input %s matches output) "
                            "not declared in depends_on",
                            producer,
                            task.id,
                            inp,
                        )

    return Graph(adj, tasks, output_to_task=output_to_task)


# ---------------------------------------------------------------------------
# AC-2 spec discovery: specs/, tests/ fixtures, and the two builtin templates
# ---------------------------------------------------------------------------


def _glob_spec_files(root: Path) -> list[Path]:
    return sorted(
        set(root.glob("**/*.json")) | set(root.glob("**/*.yaml")) | set(root.glob("**/*.yml"))
    )


def _loadable_workflow_specs(root: Path) -> list[tuple[str, WorkflowSpec]]:
    """Every spec/fixture file under *root* that `load_workflow` accepts (AC-2's wording:
    "every workflow spec loadable from ..."). Files that fail schema/model validation
    (schemas themselves, agents.json, reposet.json, non-workflow fixtures) are silently
    skipped — they are not workflow specs, by definition of "loadable"."""
    cases: list[tuple[str, WorkflowSpec]] = []
    for path in _glob_spec_files(root):
        try:
            wf = load_workflow(path)
        except Exception:
            continue
        cases.append((str(path.relative_to(_REPO_ROOT)), wf))
    return cases


def _rendered_builtin_template_specs() -> list[tuple[str, WorkflowSpec]]:
    """Render the two shipped builtin templates via the REAL `instantiate()` path (the same
    one `ao new` uses) with only the one param every template requires (`repo_set`; every
    other param has a manifest default) — the most production-faithful way to get "the
    builtin templates' rendered example specs" (TASK.md AC-2) without hand-duplicating a
    dummy-values table that could drift from the manifests' own defaults."""
    cases: list[tuple[str, WorkflowSpec]] = []
    for name in ("routed-runner", "overseer-runner"):
        workspace_root = tempfile.mkdtemp(prefix=f"oracle-builtin-{name}-")
        info = load_template(name, workspace_root, None)
        result = instantiate(
            info,
            workspace_root,
            slug_or_id="sample-slug",
            params={"repo_set": "default-set"},
        )
        wf = load_workflow(result.workflow_path)
        cases.append((f"builtin-template:{name}", wf))
    return cases


def _discover_oracle_cases() -> list[tuple[str, WorkflowSpec]]:
    cases: list[tuple[str, WorkflowSpec]] = []
    cases.extend(_loadable_workflow_specs(_REPO_ROOT / "specs"))
    cases.extend(_loadable_workflow_specs(_REPO_ROOT / "tests"))
    cases.extend(_rendered_builtin_template_specs())
    return cases


_ORACLE_CASES = _discover_oracle_cases()

# AC-2: "at least 10 specs are covered (record the count in STATUS)".
assert len(_ORACLE_CASES) >= 10, (
    f"oracle coverage dropped below the AC-2 floor of 10 specs: got {len(_ORACLE_CASES)}"
)

_ORACLE_CASE_IDS = [name for name, _ in _ORACLE_CASES]


# ---------------------------------------------------------------------------
# AC-2 / AC-5: oracle equality over every discovered spec
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spec_name,workflow", _ORACLE_CASES, ids=_ORACLE_CASE_IDS)
class TestOracleEquality:
    def test_adjacency_matches_oracle_including_order(
        self, spec_name: str, workflow: WorkflowSpec
    ) -> None:
        oracle = _oracle_build_dag(workflow)
        new = build_dag(workflow)
        # `json.dumps` (not `==`) deliberately: dict equality would ignore key insertion
        # order and list-vs-list `==` already checks element order, but AC-2 asks for the
        # adjacency structure to be compared byte-for-byte the same way a snapshot diff
        # would catch a reordering.
        assert json.dumps(new.adjacency()) == json.dumps(oracle.adjacency()), spec_name
        assert json.dumps(new.output_to_task()) == json.dumps(oracle.output_to_task()), spec_name

    def test_topological_order_matches_oracle(self, spec_name: str, workflow: WorkflowSpec) -> None:
        oracle = _oracle_build_dag(workflow)
        new = build_dag(workflow)
        try:
            oracle_order = oracle.topological_order()
        except CycleError as exc:
            with pytest.raises(CycleError) as new_exc:
                new.topological_order()
            assert sorted(new_exc.value.nodes) == sorted(exc.nodes), spec_name
        else:
            assert new.topological_order() == oracle_order, spec_name


# ---------------------------------------------------------------------------
# AC-3: synthetic edge-kind / dedup / phantom-node cases
# ---------------------------------------------------------------------------


def _wf(
    tasks: list[TaskSpec], loops: list[LoopSpec] | None = None, wf_id: str = "test-wf"
) -> WorkflowSpec:
    return WorkflowSpec(
        version="1.0",
        id=wf_id,
        repo_set="rs",
        tasks=tasks,
        loops=loops or [],
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


class TestSyntheticEdgeKinds:
    def test_explicit_dep(self) -> None:
        wf = _wf([_task("a"), _task("b", depends_on=["a"])])
        edges = list(iter_dependency_edges(wf))
        assert edges == [DependencyEdge("a", "b", EDGE_KIND_EXPLICIT, None)]

    def test_loop_dep_resolves_to_highest_materialized_iteration(self) -> None:
        """A dep on a loop id, with iterations materialized up to 3, resolves to
        body[-1]__iter3 with kind="loop" and via=<loop id> (TASK.md AC-3)."""
        loop = LoopSpec(
            id="qa-loop",
            body=["dev", "review"],
            gate_task_id="review",
            gate_output_path="output/gate.json",
        )
        tasks = [
            _task("dev"),
            _task("review", depends_on=["dev"]),
            _task("dev__iter2", depends_on=["review"]),
            _task("review__iter2", depends_on=["dev__iter2"]),
            _task("dev__iter3", depends_on=["review__iter2"]),
            _task("review__iter3", depends_on=["dev__iter3"]),
            _task("finalize", depends_on=["qa-loop"]),
        ]
        wf = _wf(tasks, loops=[loop])
        edges = [e for e in iter_dependency_edges(wf) if e.target == "finalize"]
        assert edges == [DependencyEdge("review__iter3", "finalize", EDGE_KIND_LOOP, "qa-loop")]

    def test_inferred_edge_from_output_input_match(self) -> None:
        wf = _wf([_task("a", outputs=["out/x.txt"]), _task("b", inputs=["out/x.txt"])])
        edges = list(iter_dependency_edges(wf))
        assert edges == [DependencyEdge("a", "b", EDGE_KIND_INFERRED, "out/x.txt")]

    def test_explicit_and_inferred_on_same_pair_collapses_to_one_explicit_edge(self) -> None:
        wf = _wf(
            [
                _task("a", outputs=["out/x.txt"]),
                _task("b", depends_on=["a"], inputs=["out/x.txt"]),
            ]
        )
        edges = list(iter_dependency_edges(wf))
        assert edges == [DependencyEdge("a", "b", EDGE_KIND_EXPLICIT, None)]

    def test_unknown_dep_still_yields_an_edge_and_build_dag_makes_a_phantom_key(self) -> None:
        wf = _wf([_task("b", depends_on=["ghost"])])
        edges = list(iter_dependency_edges(wf))
        assert edges == [DependencyEdge("ghost", "b", EDGE_KIND_EXPLICIT, None)]

        graph = build_dag(wf)
        assert graph.adjacency()["ghost"] == ["b"]

    def test_self_referential_input_output_yields_no_edge(self) -> None:
        wf = _wf([_task("a", inputs=["out/x.txt"], outputs=["out/x.txt"])])
        assert list(iter_dependency_edges(wf)) == []

    def test_duplicate_explicit_dep_collapses_to_one_edge(self) -> None:
        wf = _wf([_task("a"), _task("b", depends_on=["a", "a"])])
        edges = list(iter_dependency_edges(wf))
        assert edges == [DependencyEdge("a", "b", EDGE_KIND_EXPLICIT, None)]

    # Gate G1 NIT (reviewer): minimal-shape cases weren't covered by either the real
    # specs (AC-2) or the synthetic cases above. Both the iterator and build_dag
    # degenerate to a no-op identically for these -- pinned here rather than left as
    # a reviewer-asserted claim.
    def test_empty_workflow_yields_no_edges(self) -> None:
        wf = _wf([])
        assert list(iter_dependency_edges(wf)) == []
        graph = build_dag(wf)
        assert graph.adjacency() == {}
        assert graph.topological_order() == []

    def test_single_task_no_deps_yields_no_edges(self) -> None:
        wf = _wf([_task("a")])
        assert list(iter_dependency_edges(wf)) == []
        graph = build_dag(wf)
        assert graph.adjacency() == {"a": []}
        assert graph.topological_order() == ["a"]

    def test_fully_disconnected_tasks_yield_no_edges(self) -> None:
        wf = _wf([_task("a"), _task("b"), _task("c")])
        assert list(iter_dependency_edges(wf)) == []
        graph = build_dag(wf)
        assert graph.adjacency() == {"a": [], "b": [], "c": []}
        assert set(graph.topological_order()) == {"a", "b", "c"}


# ---------------------------------------------------------------------------
# AC-4: the inferred-but-undeclared warning text is byte-identical to the oracle's
# ---------------------------------------------------------------------------


class TestInferredWarningTextMatchesOracle:
    def test_warning_message_byte_identical(self, caplog: pytest.LogCaptureFixture) -> None:
        wf = _wf([_task("a", outputs=["out/x.txt"]), _task("b", inputs=["out/x.txt"])])

        with caplog.at_level(logging.WARNING):
            caplog.clear()
            _oracle_build_dag(wf)
            oracle_messages = [r.getMessage() for r in caplog.records]

            caplog.clear()
            build_dag(wf)
            new_messages = [r.getMessage() for r in caplog.records]

        assert new_messages == oracle_messages
        assert oracle_messages == [
            "Inferred edge a -> b (input out/x.txt matches output) not declared in depends_on"
        ]
