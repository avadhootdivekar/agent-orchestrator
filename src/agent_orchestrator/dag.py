"""DAG construction, cycle detection, and topological ordering."""

from __future__ import annotations

import logging
from collections.abc import Callable

from .errors import CycleError, MissingInputError
from .models import RouterSpec, WorkflowSpec

logger = logging.getLogger(__name__)


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


def build_dag(workflow: WorkflowSpec) -> Graph:
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
                        logger.warning(
                            "Inferred edge %s -> %s (input %s matches output) "
                            "not declared in depends_on",
                            producer,
                            task.id,
                            inp,
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
