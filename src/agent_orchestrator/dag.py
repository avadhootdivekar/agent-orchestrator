"""DAG construction, cycle detection, and topological ordering."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import NamedTuple

from .errors import CycleError, MissingInputError
from .models import RouterSpec, WorkflowSpec

logger = logging.getLogger(__name__)

# Dependency-edge kinds yielded by `iter_dependency_edges` (ADR-0017 D3). Named constants
# rather than a closed `Literal` on `DependencyEdge.kind` so a future edge kind (like
# `SpawnRecord.origin`, ADR-0017 D1) can't make an older reader reject the whole tuple —
# `kind` is compared by value (`==`), never pattern-matched exhaustively.
EDGE_KIND_EXPLICIT = "explicit"
EDGE_KIND_LOOP = "loop"
EDGE_KIND_INFERRED = "inferred"


class DependencyEdge(NamedTuple):
    """One dependency edge derived from a workflow spec (HLD §8.3.1, ADR-0017 D3).

    The SINGLE typed representation of "A must settle before B" — shared by
    `build_dag` (engine/scheduling) and the dashboard's graph builder (`ui/graph.py`,
    T-M4qboy) so the two can never independently derive (or order) edges differently.
    This is scheduling-critical: `build_dag`'s adjacency-list order feeds
    `Graph.topological_order`, which is production task dispatch order (ADR-0017 Risk R-2).
    """

    source: str  # upstream: must complete before `target` can run
    target: str  # downstream
    kind: str  # EDGE_KIND_EXPLICIT | EDGE_KIND_LOOP | EDGE_KIND_INFERRED
    via: str | None  # explicit: None; loop: the loop id; inferred: the matching artifact path


class Graph:
    """Directed graph with deterministic topological ordering (Kahn's algorithm)."""

    def __init__(
        self,
        adj: dict[str, list[str]],
        tasks: list,
        output_to_task: dict[str, str] | None = None,
    ) -> None:
        # adj[node] = list of successor node ids (nodes that depend on `node`)
        self._adj = adj
        self._tasks = {t.id: t for t in tasks}
        # inp path -> producing task id (built by build_dag; see output_to_task()).
        self._output_to_task = output_to_task if output_to_task is not None else {}

    def adjacency(self) -> dict[str, list[str]]:
        """Return the graph's adjacency map (node id -> successor node ids).

        Includes BOTH declared ``depends_on`` edges and inferred input/output
        path-matching edges (see ``build_dag``) — this is the runtime graph, not
        just declared dependencies. Returned by reference (matches this class's
        existing accessor style, e.g. ``topological_order``/``validate_inputs``
        reading ``self._adj`` directly); callers must not mutate the result.
        """
        return self._adj

    def output_to_task(self) -> dict[str, str]:
        """Return the producer map (output path -> producing task id).

        Built once in ``build_dag`` from every task's declared outputs. Exposed
        so downstream code (e.g. join-input relaxation, LLD §5.4a) can resolve
        which task produced a given input path without recomputing the map.
        """
        return self._output_to_task

    def producer_of(self, inp: str) -> str | None:
        """Return the id of the task that produces output path *inp*, if any."""
        return self._output_to_task.get(inp)

    def topological_order(self) -> list[str]:
        """Return a deterministic topological ordering using Kahn's algorithm.

        Raises CycleError if a cycle is detected.
        """
        # Compute in-degrees from the adjacency list
        indeg: dict[str, int] = {nid: 0 for nid in self._adj}
        for nid in self._adj:
            for successor in self._adj[nid]:
                indeg[successor] = indeg.get(successor, 0) + 1

        # Start with nodes that have no predecessors, sorted for determinism
        queue: list[str] = sorted(nid for nid, d in indeg.items() if d == 0)
        order: list[str] = []

        while queue:
            nid = queue.pop(0)
            order.append(nid)
            for successor in sorted(self._adj.get(nid, [])):
                indeg[successor] -= 1
                if indeg[successor] == 0:
                    # Insert in sorted order to maintain determinism
                    queue.append(successor)
                    queue.sort()

        leftover = [nid for nid, d in indeg.items() if d > 0]
        if leftover:
            raise CycleError(sorted(leftover))

        return order

    def validate_inputs(self, exists_fn: Callable[[str], bool]) -> None:
        """Walk the topo order and confirm every task input either:
        - is produced by a prior task's outputs, OR
        - already exists on disk (via exists_fn).

        Raises MissingInputError on the first unresolvable input.
        """
        produced: set[str] = set()
        for tid in self.topological_order():
            task = self._tasks[tid]
            for inp in task.inputs:
                if inp not in produced and not exists_fn(inp):
                    raise MissingInputError(tid, inp)
            produced.update(task.outputs)


def _resolve_loop_dep(loop_id: str, workflow: WorkflowSpec) -> str:
    """Return the last task id of the highest materialized iteration for *loop_id*.

    Iteration 1 = un-suffixed authored body; iteration N uses ``__iterN`` suffix.
    The last task id is ``body[-1]`` (iter 1) or ``body[-1]__iter{N}`` (iter N > 1).

    If the loop is not found, the original id is returned unchanged (cycle detection
    will then raise on the unknown dep).
    """
    loop = next((lp for lp in workflow.loops if lp.id == loop_id), None)
    if loop is None:
        return loop_id  # unknown; let cycle/missing-dep detection handle it

    last_body_task = loop.body[-1]

    # Find the highest iteration suffix present in the live task list
    max_iter = 1
    for t in workflow.tasks:
        tid = t.id
        prefix = f"{last_body_task}__iter"
        if tid.startswith(prefix):
            try:
                n = int(tid[len(prefix) :])
                if n > max_iter:
                    max_iter = n
            except ValueError:
                pass  # malformed suffix — ignore

    if max_iter == 1:
        return last_body_task
    return f"{last_body_task}__iter{max_iter}"


def iter_dependency_edges(workflow: WorkflowSpec) -> Iterator[DependencyEdge]:
    """Yield every dependency edge derived from *workflow*, in `build_dag`'s historical
    precedence and order (HLD §8.3.1, ADR-0017 D3) — the ONE place this logic lives, so
    `build_dag` and the dashboard's graph builder can never disagree:

    1. Explicit ``depends_on`` declarations, in authored task order. A dep matching a
       loop id is resolved to the last materialized iteration's last task (HLD §4.5,
       ADR-001) and yielded with ``kind=EDGE_KIND_LOOP``; every other explicit dep is
       ``kind=EDGE_KIND_EXPLICIT``.
    2. Inferred edges from output/input path matching, in authored task order (second,
       same precedence as today).

    A ``(source, target)`` pair is yielded at most once — first-writer-wins, matching
    `build_dag`'s historical ``if task.id not in adj[...]`` dedup — so an explicit (or
    loop) edge and an inferred edge on the same pair collapse to a single explicit/loop
    edge, and a dep listed twice yields one edge.

    Pure and deterministic: depends only on *workflow*; no I/O, no clock, no mutation.
    An unknown ``depends_on`` id is still yielded as an edge (its source is not a task
    id) — cycle/missing-dependency detection is `Graph`'s job, not this function's.
    """
    tasks = workflow.tasks
    loop_ids = {lp.id for lp in workflow.loops}

    # Map output path -> producing task id (last writer wins, same as today)
    output_to_task: dict[str, str] = {}
    for task in tasks:
        for out in task.outputs:
            output_to_task[out] = task.id

    seen: set[tuple[str, str]] = set()

    # 1. Explicit depends_on, in authored task order.
    for task in tasks:
        for dep in task.depends_on:
            if dep in loop_ids:
                # Resolve loop-id deps to the final iteration's last task
                source = _resolve_loop_dep(dep, workflow)
                kind = EDGE_KIND_LOOP
                via: str | None = dep
            else:
                source = dep
                kind = EDGE_KIND_EXPLICIT
                via = None
            pair = (source, task.id)
            if pair not in seen:
                seen.add(pair)
                yield DependencyEdge(source, task.id, kind, via)

    # 2. Inferred edges from input/output path matching, second.
    for task in tasks:
        for inp in task.inputs:
            producer = output_to_task.get(inp)
            if producer and producer != task.id:
                pair = (producer, task.id)
                if pair not in seen:
                    seen.add(pair)
                    yield DependencyEdge(producer, task.id, EDGE_KIND_INFERRED, inp)


def build_dag(workflow: WorkflowSpec) -> Graph:
    """Build a Graph from a WorkflowSpec.

    Edges are derived from:
    1. Explicit ``depends_on`` declarations.
       If a dep matches a loop id, it is resolved to the last materialized
       iteration's last task (HLD §4.5, ADR-001).
    2. Inferred edges when task A's output path matches task B's input path
       (logged as a warning if the dependency is not declared explicitly).

    Edge derivation itself is `iter_dependency_edges` (HLD §8.3.1, ADR-0017 D3) — this
    function only assembles the resulting edges into the adjacency map `Graph` needs and
    emits the inferred-but-undeclared warning. Behavior is byte-identical to the
    pre-refactor implementation, including adjacency list order (see the oracle-equality
    test, `tests/test_dag_edge_iterator_oracle.py` — ADR-0017 Risk R-2: adjacency order
    feeds `Graph.topological_order`, i.e. production task dispatch order).
    """
    tasks = workflow.tasks
    tasks_by_id = {t.id: t for t in tasks}

    # Map output path -> producing task id
    output_to_task: dict[str, str] = {}
    for task in tasks:
        for out in task.outputs:
            output_to_task[out] = task.id

    # adj[A] = [B, C] means A must complete before B and C can run (A -> B, A -> C)
    adj: dict[str, list[str]] = {t.id: [] for t in tasks}

    for edge in iter_dependency_edges(workflow):
        # Unknown dep sources get a phantom key here (preserves today's behavior so
        # cycle/missing-dependency detection fires downstream, in Graph).
        adj.setdefault(edge.source, [])
        adj[edge.source].append(edge.target)
        if edge.kind == EDGE_KIND_INFERRED:
            target_task = tasks_by_id[edge.target]
            if edge.source not in target_task.depends_on:
                logger.warning(
                    "Inferred edge %s -> %s (input %s matches output) not declared in depends_on",
                    edge.source,
                    edge.target,
                    edge.via,
                )

    return Graph(adj, tasks, output_to_task=output_to_task)


def forward_closure(adj: dict[str, list[str]], entries: list[str]) -> set[str]:
    """Return the forward transitive closure of *adj* starting from *entries*.

    Plain iterative BFS over an adjacency map (node id -> successor ids); entries
    are included in the result. Pure and deterministic — no clock, no randomness
    — though the returned ``set`` is itself unordered (callers needing a stable
    order must sort it, per NFR-2).
    """
    visited: set[str] = set()
    queue: list[str] = list(entries)
    while queue:
        node = queue.pop(0)
        if node in visited:
            continue
        visited.add(node)
        for successor in adj.get(node, []):
            if successor not in visited:
                queue.append(successor)
    return visited


def compute_cones(
    workflow: WorkflowSpec, graph: Graph
) -> tuple[dict[str, dict[str, set[str]]], dict[str, set[tuple[str, str]]]]:
    """Compute each router's exclusive route cones and the task->routes membership map.

    See LLD §4.1-4.2 (`docs-md/lld-run-control-routing-breakers.md`). Cones are
    computed over ``graph.adjacency()`` — the full runtime graph ``build_dag``
    returns, including edges *inferred* from input/output path matching — never
    over ``depends_on`` alone (memory ``build-dag-infers-edges-from-paths``): an
    inferred edge can extend a route's reach exactly like a declared one.

    Returns ``(cones, membership)``:
      - ``cones[router_id][route_id]`` = set of task ids EXCLUSIVELY reachable
        from that route's ``entry`` list — i.e. no *other* route of the SAME
        router also reaches them. A task reachable from >=2 routes of one
        router is shared/convergent and appears in neither route's cone.
      - ``membership[task_id]`` = set of ``(router_id, route_id)`` pairs for
        every route (across every router) that reaches ``task_id``.

    Pure and deterministic: depends only on ``workflow.branches`` and
    ``graph.adjacency()``; no clock, no I/O, no mutation of either argument.
    Two calls on the same inputs return identical cones/membership (NFR-2);
    results are sets, so order never matters.

    This is the algorithmic core only — activation/``not_taken`` logic, join
    resolution, and ``ao validate`` rules (LLD §4.4) are separate downstream
    tickets (T-m2h5t7, T-w6p2c8) and are intentionally not implemented here.
    """
    adj = graph.adjacency()
    cones: dict[str, dict[str, set[str]]] = {}
    membership: dict[str, set[tuple[str, str]]] = {}

    router: RouterSpec
    for router in workflow.branches:
        reach_by_route: dict[str, set[str]] = {
            route_id: forward_closure(adj, route.entry) for route_id, route in router.routes.items()
        }

        for route_id, reached in reach_by_route.items():
            for task_id in reached:
                membership.setdefault(task_id, set()).add((router.id, route_id))

        # A task is exclusive to a route iff no OTHER route of THIS router also
        # reaches it (count over this router's routes only, not all routers).
        router_cones: dict[str, set[str]] = {}
        for route_id, reached in reach_by_route.items():
            router_cones[route_id] = {
                task_id
                for task_id in reached
                if sum(1 for other in reach_by_route.values() if task_id in other) == 1
            }
        cones[router.id] = router_cones

    return cones, membership
