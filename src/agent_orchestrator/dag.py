"""DAG construction, cycle detection, and topological ordering."""

from __future__ import annotations

import logging
from collections.abc import Callable

from .errors import CycleError, MissingInputError
from .models import WorkflowSpec

logger = logging.getLogger(__name__)


class Graph:
    """Directed graph with deterministic topological ordering (Kahn's algorithm)."""

    def __init__(self, adj: dict[str, list[str]], tasks: list) -> None:
        # adj[node] = list of successor node ids (nodes that depend on `node`)
        self._adj = adj
        self._tasks = {t.id: t for t in tasks}

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

    return Graph(adj, tasks)
