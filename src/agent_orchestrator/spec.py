"""Load and validate workflow spec files."""

from __future__ import annotations

import re
from collections import defaultdict
from itertools import product
from pathlib import Path

from .breakers import BREAKER_REGISTRY
from .config import _load_file, _validate_against_schema
from .dag import Graph, compute_cones, forward_closure
from .errors import SpecValidationError
from .models import BudgetSpec, TaskSpec, WorkflowSpec

# Suffix used for loop iteration cloning — authors must not use this in task ids.
_ITER_SUFFIX_MARKER = "__iter"

# Router/route/breaker id pattern (mirrors specs/workflow.schema.json's task/router/
# circuitBreaker id patterns). JSON Schema already enforces this for `router.id` and
# `circuit_breakers[].id` at load time, but NOT for route ids (dict keys of
# `router.routes`, which the schema leaves unconstrained) — validate_run_control
# checks all three so route ids get the same guarantee (rule 11).
_ROUTING_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-_]*$")

# The implemented circuit-breaker conditions. The schema's `condition` enum accepts the
# full HLD §6 catalog so specs may reference not-yet-implemented conditions ahead of
# time; this validator rejects those with a clean, named error rather than letting them
# reach the engine as a silent no-op. Derived from BREAKER_REGISTRY (the engine's actual
# implementations) instead of a hand-maintained copy: a previous hardcoded set here
# silently drifted when E-3JTmVu added `run_active_seconds` to the registry but not to
# this list, making `ao validate` reject a fully-implemented condition.
_MVP_BREAKER_CONDITIONS = frozenset(BREAKER_REGISTRY)

# Safety cap on the route-selection combinations enumerated for rule 7's any-join
# satisfiability check (product of route counts across all routers). Real specs have
# a handful of routers/routes; this guards against a pathological spec turning
# `ao validate` into a combinatorial hang (NFR "safe by default").
_MAX_ANY_JOIN_COMBINATIONS = 4096


def load_workflow(path: str | Path) -> WorkflowSpec:
    """Load a workflow JSON/YAML file, validate against JSON Schema, parse into WorkflowSpec."""
    data = _load_file(path)
    _validate_against_schema(data, "workflow.schema.json")
    try:
        return WorkflowSpec(**data)
    except Exception as exc:
        raise SpecValidationError(f"Workflow model error: {exc}", path=str(path)) from exc


def cross_validate(
    workflow: WorkflowSpec,
    reposets: dict,
    agents: dict,
) -> None:
    """Cross-validate workflow references against loaded reposets and agents.

    Raises SpecValidationError for:
    - Unknown repo_set
    - Task referencing an unknown agent
    - Task depends_on referencing an unknown task id or an unknown loop id not resolvable
    - emit_tasks / task_manifest_path pairing violations (Area 2)
    - LoopSpec body/gate/max_iterations constraints (Area 2)
    - Authored task ids or loop body ids containing '__iter' (reserved suffix, ADR-006)
    """
    if workflow.repo_set not in reposets:
        raise SpecValidationError(
            f"Unknown repo_set: {workflow.repo_set!r}",
            path="repo_set",
        )

    task_ids = {t.id for t in workflow.tasks}
    loop_ids = {lp.id for lp in workflow.loops}

    # Build set of valid dep targets: task ids + loop ids (loop id resolves to last iter last task)
    valid_dep_targets = task_ids | loop_ids

    # Check for reserved suffix in authored task ids
    for task in workflow.tasks:
        if _ITER_SUFFIX_MARKER in task.id:
            raise SpecValidationError(
                f"Task id {task.id!r} contains reserved suffix {_ITER_SUFFIX_MARKER!r}; "
                "authored ids must not use __iter",
                path=f"tasks.{task.id}.id",
            )

    for task in workflow.tasks:
        if task.agent not in agents:
            raise SpecValidationError(
                f"Task {task.id!r}: unknown agent {task.agent!r}",
                path=f"tasks.{task.id}.agent",
            )
        for dep in task.depends_on:
            if dep not in valid_dep_targets:
                raise SpecValidationError(
                    f"Task {task.id!r}: unknown depends_on {dep!r}",
                    path=f"tasks.{task.id}.depends_on",
                )

        # emit_tasks ⇔ task_manifest_path both set or both unset (§3.1 cross-validation)
        if task.emit_tasks and not task.task_manifest_path:
            raise SpecValidationError(
                f"Task {task.id!r}: emit_tasks=True requires task_manifest_path to be set",
                path=f"tasks.{task.id}.emit_tasks",
            )
        if task.task_manifest_path and not task.emit_tasks:
            raise SpecValidationError(
                f"Task {task.id!r}: task_manifest_path is set but emit_tasks=False",
                path=f"tasks.{task.id}.task_manifest_path",
            )

    # LoopSpec cross-validation (§3.2)
    body_task_to_loop: dict[str, str] = {}  # task_id -> loop_id
    for loop in workflow.loops:
        if not loop.body:
            raise SpecValidationError(
                f"Loop {loop.id!r}: body must not be empty",
                path=f"loops.{loop.id}.body",
            )

        if loop.max_iterations < 1:
            raise SpecValidationError(
                f"Loop {loop.id!r}: max_iterations must be >= 1, got {loop.max_iterations}",
                path=f"loops.{loop.id}.max_iterations",
            )

        for bid in loop.body:
            if bid not in task_ids:
                raise SpecValidationError(
                    f"Loop {loop.id!r}: body task {bid!r} not found in workflow tasks",
                    path=f"loops.{loop.id}.body",
                )
            if _ITER_SUFFIX_MARKER in bid:
                raise SpecValidationError(
                    f"Loop {loop.id!r}: body task id {bid!r} contains reserved suffix "
                    f"{_ITER_SUFFIX_MARKER!r}",
                    path=f"loops.{loop.id}.body",
                )
            if bid in body_task_to_loop:
                raise SpecValidationError(
                    f"Loop {loop.id!r}: task {bid!r} already belongs to loop "
                    f"{body_task_to_loop[bid]!r}; a task may be in at most one loop body",
                    path=f"loops.{loop.id}.body",
                )
            body_task_to_loop[bid] = loop.id

            # No nested loops: body task id must not equal any loop id
            if bid in loop_ids:
                raise SpecValidationError(
                    f"Loop {loop.id!r}: body task id {bid!r} matches a loop id (nested loops "
                    "are not supported in this release)",
                    path=f"loops.{loop.id}.body",
                )

        if loop.gate_task_id not in loop.body:
            raise SpecValidationError(
                f"Loop {loop.id!r}: gate_task_id {loop.gate_task_id!r} not in body",
                path=f"loops.{loop.id}.gate_task_id",
            )

        # Gate task must not itself be an emitter (a task cannot be both emitter and gate)
        gate_task = next(t for t in workflow.tasks if t.id == loop.gate_task_id)
        if gate_task.emit_tasks:
            raise SpecValidationError(
                f"Loop {loop.id!r}: gate_task_id {loop.gate_task_id!r} has emit_tasks=True; "
                "a task cannot be both an emitter and a gate",
                path=f"loops.{loop.id}.gate_task_id",
            )

    # Budget cross-validation (T-oh5gl5)
    if workflow.budget is not None:
        budget_cross_validate(workflow.budget)


def budget_cross_validate(budget: BudgetSpec) -> None:
    """Standalone budget cross-validation (called from CLI and cross_validate)."""
    b = budget
    if b.total_tokens is not None and b.total_tokens <= 0:
        raise SpecValidationError(
            "budget.total_tokens must be > 0",
            path="budget.total_tokens",
        )
    if b.rate is not None:
        has_window = b.rate.window is not None
        has_secs = b.rate.window_seconds is not None
        if has_window == has_secs:  # both set or neither set
            raise SpecValidationError(
                "budget.rate: set exactly one of window or window_seconds",
                path="budget.rate",
            )
        if b.rate.tokens <= 0:
            raise SpecValidationError(
                "budget.rate.tokens must be > 0",
                path="budget.rate.tokens",
            )
    if b.estimator.pessimism_buffer < 1.0:
        raise SpecValidationError(
            "budget.estimator.pessimism_buffer must be >= 1.0",
            path="budget.estimator.pessimism_buffer",
        )


# ---------------------------------------------------------------------------
# Run-control (routing + circuit breakers) static validation — LLD §4.4,
# epic E-rc7k2v, T-w6p2c8. Runs AFTER `build_dag` succeeds (needs the runtime
# graph, declared + inferred edges, for reachability/cone computation).
# ---------------------------------------------------------------------------


def _check_reserved_id(value: str, path: str) -> None:
    """Rule 11: router/route/breaker ids must match the id pattern and must not
    contain the reserved loop-iteration marker.

    JSON Schema already enforces the pattern for `router.id` and
    `circuit_breakers[].id` at load time; this is defense-in-depth for those two
    plus the ONLY enforcement point for route ids (dict keys of `router.routes`,
    which the schema leaves unconstrained).
    """
    if not _ROUTING_ID_PATTERN.match(value):
        raise SpecValidationError(
            f"{path}: id {value!r} must match pattern '^[a-z0-9][a-z0-9-_]*$'",
            path=path,
        )
    if _ITER_SUFFIX_MARKER in value:
        raise SpecValidationError(
            f"{path}: id {value!r} contains reserved suffix {_ITER_SUFFIX_MARKER!r}",
            path=path,
        )


def _effective_deps(task: TaskSpec, graph: Graph) -> set[str]:
    """A task's declared depends_on plus any inferred producer of its declared
    inputs (LLD §4.4 rule 5/7's "effective = depends_on ∪ inferred-producer")."""
    deps = set(task.depends_on)
    for inp in task.inputs:
        producer = graph.producer_of(inp)
        if producer is not None:
            deps.add(producer)
    return deps


def _simulate_route_selection(
    tasks_by_id: dict[str, TaskSpec],
    graph: Graph,
    cones: dict[str, dict[str, set[str]]],
    membership: dict[str, set[tuple[str, str]]],
    order: list[str],
    selection: dict[str, str],
) -> dict[str, bool]:
    """Pure static simulation of not_taken propagation for ONE route selection
    (router_id -> selected route_id, one per router).

    Mirrors the engine's runtime activation rules (LLD §5.2/§5.4) at validation
    time: a task is inactive if it sits in an exclusive cone whose route was NOT
    selected; otherwise it follows `join` semantics over its effective
    dependencies (all => every dep active; any => at least one dep active).
    No I/O, no clock — deterministic given (tasks, graph, cones, membership,
    selection). Used only by rule 7 (any-join satisfiability).
    """
    active: dict[str, bool] = {}
    for tid in order:
        task = tasks_by_id.get(tid)
        if task is None:
            # Not an authored task (e.g. a resolved loop-iteration clone id) —
            # outside routing's static concern; treat as non-blocking.
            active[tid] = True
            continue

        forced_inactive = False
        for router_id, route_id in membership.get(tid, ()):
            if (
                tid in cones.get(router_id, {}).get(route_id, ())
                and selection.get(router_id) != route_id
            ):
                forced_inactive = True
                break
        if forced_inactive:
            active[tid] = False
            continue

        deps = _effective_deps(task, graph)
        if not deps:
            active[tid] = True
            continue
        dep_active = [active.get(d, True) for d in deps]
        active[tid] = all(dep_active) if task.join == "all" else any(dep_active)
    return active


def _check_any_join_satisfiability(
    workflow: WorkflowSpec,
    graph: Graph,
    cones: dict[str, dict[str, set[str]]],
    membership: dict[str, set[tuple[str, str]]],
    join_any_tasks: list[TaskSpec],
) -> None:
    """Rule 7: for each `join="any"` task, brute-force every possible route
    selection (one route per router) and check whether the task is ever active.

    Raises SpecValidationError if a task is dead (never active) under EVERY
    possible verdict combination — i.e. no selection can ever activate it.
    """
    tasks_by_id = {t.id: t for t in workflow.tasks}
    order = graph.topological_order()
    router_ids = [r.id for r in workflow.branches]
    route_options = [list(r.routes.keys()) for r in workflow.branches]

    total_combinations = 1
    for opts in route_options:
        total_combinations *= max(len(opts), 1)
    if total_combinations > _MAX_ANY_JOIN_COMBINATIONS:
        # Pathological number of routers/routes — skip exhaustive enforcement
        # rather than hang `ao validate` (safe-by-default: a missed check here
        # is far less harmful than a stuck CLI).
        return

    ever_active = dict.fromkeys(t.id for t in join_any_tasks)
    for combo in product(*route_options):
        selection = dict(zip(router_ids, combo, strict=True))
        active = _simulate_route_selection(tasks_by_id, graph, cones, membership, order, selection)
        for t in join_any_tasks:
            if active.get(t.id):
                ever_active[t.id] = True
        if all(ever_active.values()):
            break  # every join="any" task already proven satisfiable

    for t in join_any_tasks:
        if not ever_active[t.id]:
            deps = sorted(_effective_deps(t, graph))
            raise SpecValidationError(
                f"Task {t.id!r}: join='any' is unsatisfiable — every effective "
                f"dependency {deps} sits in a route-cone that can never "
                "co-activate with any cone reachable by this task under any "
                "verdict selection",
                path=f"tasks.{t.id}.join",
            )


def validate_run_control(workflow: WorkflowSpec, graph: Graph) -> list[str]:
    """Static routing + circuit-breaker validation (LLD §4.4, rules 1-11).

    MUST run after `build_dag` (and ideally after `graph.topological_order()`
    has confirmed the graph is acyclic) — cone/reachability computation needs
    the full runtime graph (declared + inferred edges).

    Raises `SpecValidationError` (naming the offending ids/path) for rules
    1-5 and 7-11. Rule 6 is non-fatal: it is returned as a list of warning
    strings for the caller to print (see `cli._load_all`).
    """
    warnings: list[str] = []
    task_ids = {t.id for t in workflow.tasks}
    tasks_by_id = {t.id: t for t in workflow.tasks}
    adj = graph.adjacency()

    # Rule 11 — checked first so a malformed id fails fast, before any
    # reachability computation that might otherwise behave oddly on garbage ids.
    for router in workflow.branches:
        _check_reserved_id(router.id, f"branches.{router.id}.id")
        for route_id in router.routes:
            _check_reserved_id(route_id, f"branches.{router.id}.routes.{route_id}")
    for breaker in workflow.circuit_breakers:
        _check_reserved_id(breaker.id, f"circuit_breakers.{breaker.id}.id")

    # Rules 1-4: per-router reference/reachability/disjointness checks.
    for router in workflow.branches:
        # Rule 1: unknown router task.
        if router.router_task_id not in task_ids:
            raise SpecValidationError(
                f"Router {router.id!r}: unknown router_task_id {router.router_task_id!r}",
                path=f"branches.{router.id}.router_task_id",
            )

        router_reach = forward_closure(adj, [router.router_task_id])
        reach_by_route: dict[str, set[str]] = {}
        for route_id, route in router.routes.items():
            for entry in route.entry:
                # Rule 2: unknown entry.
                if entry not in task_ids:
                    raise SpecValidationError(
                        f"Router {router.id!r} route {route_id!r}: unknown entry {entry!r}",
                        path=f"branches.{router.id}.routes.{route_id}.entry",
                    )
                # Rule 3: entry must be reachable from router_task_id, else it
                # can never be deactivated by this router.
                if entry != router.router_task_id and entry not in router_reach:
                    raise SpecValidationError(
                        f"Router {router.id!r}: entry {entry!r} of route {route_id!r} is "
                        f"not reachable from router_task_id {router.router_task_id!r}; it "
                        "could never be deactivated by this router",
                        path=f"branches.{router.id}.routes.{route_id}.entry",
                    )
            reach_by_route[route_id] = forward_closure(adj, route.entry)

        # Rule 4: route-entry disjointness — no entry of route A may be
        # reachable from route B's entries (selecting B would force A active).
        for route_id_a, route_a in router.routes.items():
            for route_id_b, reach_b in reach_by_route.items():
                if route_id_a == route_id_b:
                    continue
                for entry in route_a.entry:
                    if entry in reach_b:
                        raise SpecValidationError(
                            f"Router {router.id!r}: route {route_id_a!r} entry {entry!r} "
                            f"is reachable from route {route_id_b!r} (entries "
                            f"{router.routes[route_id_b].entry}) — routes are not "
                            "disjoint; selecting one route would force the other "
                            "active too",
                            path=f"branches.{router.id}.routes.{route_id_a}.entry",
                        )

    # Cones/membership over the full runtime graph (T-c4w6p1) — feeds rules 5-9.
    cones, membership = compute_cones(workflow, graph)

    # Rule 9: nested router rejection (MVP boundary) — a router_task_id must
    # not lie inside another router's exclusive cone.
    for router in workflow.branches:
        for other in workflow.branches:
            if other.id == router.id:
                continue
            for route_id, cone in cones.get(other.id, {}).items():
                if router.router_task_id in cone:
                    raise SpecValidationError(
                        f"Router {router.id!r}: router_task_id "
                        f"{router.router_task_id!r} lies inside router {other.id!r}'s "
                        f"exclusive cone (route {route_id!r}); nested routers are not "
                        "supported in this release",
                        path=f"branches.{router.id}.router_task_id",
                    )

    # Rules 5-6: convergence-task checks, grouped per-router (a task reachable
    # from >=2 routes of the SAME router is a convergence point).
    per_task_router_routes: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for tid, pairs in membership.items():
        for router_id, route_id in pairs:
            per_task_router_routes[tid][router_id].add(route_id)

    for tid, by_router in per_task_router_routes.items():
        converging_router_ids = sorted(rid for rid, routes in by_router.items() if len(routes) >= 2)
        if not converging_router_ids:
            continue
        task = tasks_by_id[tid]

        # Rule 5 (R2, the important one): ANY inferred incoming edge into a
        # multi-route convergence task is rejected outright, regardless of
        # `join` — legitimate convergence must be declared (depends_on + join).
        for inp in task.inputs:
            producer = graph.producer_of(inp)
            if producer is not None and producer not in task.depends_on:
                raise SpecValidationError(
                    f"Task {tid!r} is reachable from >=2 routes of router(s) "
                    f"{converging_router_ids} via an inferred edge from "
                    f"{producer!r} (shared path {inp!r}); give distinct output "
                    "paths or declare this dependency explicitly "
                    "(depends_on + join)",
                    path=f"tasks.{tid}.inputs",
                )

        # Rule 6 (WARN only): declared multi-route convergence left at the
        # default join="all". Pydantic can't distinguish "author explicitly
        # wrote all" from "left at default" (both are just `join == 'all'`), so
        # this always warns on a declared-edge multi-route convergence with
        # join=='all' — simplest interpretation that still surfaces the risk
        # without over-engineering an explicit-vs-default tracking mechanism.
        if task.join == "all":
            warnings.append(
                f"Task {tid!r} converges >=2 routes of router(s) "
                f"{converging_router_ids} via declared depends_on but join is "
                "'all' (default); set join explicitly (or add join=\"any\" if "
                "only one branch need succeed) if this convergence is intentional"
            )

    # Rule 7: any-join satisfiability — full route-selection simulation.
    join_any_tasks = [t for t in workflow.tasks if t.join == "any"]
    if join_any_tasks and workflow.branches:
        _check_any_join_satisfiability(workflow, graph, cones, membership, join_any_tasks)

    # Rule 8: unreachable endpoint — every route must contain >=1 sink (a task
    # with no successors) within its exclusive cone.
    for router in workflow.branches:
        for route_id, cone in cones.get(router.id, {}).items():
            if not any(not adj.get(tid) for tid in cone):
                raise SpecValidationError(
                    f"Router {router.id!r} route {route_id!r}: no sink (task with no "
                    "successors) in its exclusive cone; this route can never reach a "
                    "terminal endpoint",
                    path=f"branches.{router.id}.routes.{route_id}",
                )

    # Rule 10: breaker refs — unknown verdict.task_id, duplicate ids, and
    # not-yet-implemented conditions.
    seen_breaker_ids: set[str] = set()
    for breaker in workflow.circuit_breakers:
        if breaker.id in seen_breaker_ids:
            raise SpecValidationError(
                f"Duplicate circuit breaker id: {breaker.id!r}",
                path=f"circuit_breakers.{breaker.id}.id",
            )
        seen_breaker_ids.add(breaker.id)

        if breaker.condition not in _MVP_BREAKER_CONDITIONS:
            raise SpecValidationError(
                f"Circuit breaker {breaker.id!r}: condition {breaker.condition!r} not "
                "implemented in this release",
                path=f"circuit_breakers.{breaker.id}.condition",
            )
        if breaker.condition == "verdict" and breaker.task_id not in task_ids:
            raise SpecValidationError(
                f"Circuit breaker {breaker.id!r}: unknown verdict task_id {breaker.task_id!r}",
                path=f"circuit_breakers.{breaker.id}.task_id",
            )

    return warnings
