"""Shared mechanical checker + applier for run-time graph mutations (E-Sn7tL2 T-Sn6Brr).

One module is used by every mutation source that reshapes a *running* DAG: soft-barrier
``emit_tasks`` manifests, and the sentinel ``inject`` / ``edit_pending`` control entries (backlog,
early checkpoint, ``ao ctl``, LLM). FR-11 (structural + budget-stage rules) and FR-12 (pending-graph
edit rules) live here; charter-only rules (brief cites an ask, attempt caps) stay in the overseer
tool and are simply not applicable to a workflow without a charter.

Everything is pure over ``WorkflowSpec`` + ``RunState`` snapshots: a candidate task list is built on
deep copies, validated (``build_dag`` + topological order == cycle check, effective-edge diff), and
only then committed by :func:`commit_plan`. A rejection never mutates anything (spike invariant I5).
The accepted effect is recorded as ``GraphEdit`` rows on ``RunState.graph_edits`` in the same step,
and :func:`replay_edits` re-applies them on resume (spike invariant I1).

No clock and no I/O here: the caller passes ``at``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from pydantic import ValidationError

from .dag import build_dag, iter_dependency_edges
from .errors import CycleError
from .models import (
    SPAWN_ORIGIN_SENTINEL,
    GraphEdit,
    RunState,
    SpawnRecord,
    TaskRunState,
    TaskSpec,
    WorkflowSpec,
    strip_iter_suffix,
)

# ---- rule ids (inbox quarantine sidecar ``rule_id``) -----------------------------------------
RULE_PAYLOAD: Final = "GE-PAYLOAD"
RULE_ID_PATTERN: Final = "GE-ID-PATTERN"
RULE_ID_DUP: Final = "GE-ID-DUP"
RULE_PRODUCER_DUP: Final = "GE-PRODUCER-DUP"
RULE_EDGE_INTO_NONPENDING: Final = "GE-EDGE-INTO-NONPENDING"
RULE_CYCLE: Final = "GE-CYCLE"
RULE_UNKNOWN_TASK: Final = "GE-UNKNOWN-TASK"
RULE_NOT_PENDING: Final = "GE-NOT-PENDING"
RULE_INPUT_EDGE: Final = "GE-INPUT-EDGE"
RULE_ORPHAN_INPUT: Final = "GE-ORPHAN-INPUT"
RULE_ROUTE_SINK: Final = "GE-ROUTE-SINK"
RULE_CONE: Final = "GE-CONE"
RULE_SPEC: Final = "GE-SPEC"
RULE_STAGE_CLOSEOUT: Final = "GE-STAGE-CLOSEOUT"
RULE_STAGE_STABILIZE: Final = "GE-STAGE-STABILIZE"
RULE_STAGE_CONVERGE: Final = "GE-STAGE-CONVERGE"
RULE_LIMIT: Final = "GE-LIMIT"
RULE_DISABLED: Final = "GE-DISABLED"
RULE_EARLY: Final = "GE-EARLY"
PARENT_TICK: Final = "sentinel-tick"

#: Budget stages (mirrors the overseer tool's STAGE_ORDER; stage is supplied by the caller).
STAGE_EXPLORE: Final = "explore"
STAGE_CONVERGE: Final = "converge"
STAGE_STABILIZE: Final = "stabilize"
STAGE_CLOSEOUT: Final = "closeout"
STABILIZE_ALLOWED_KINDS: Final = frozenset({"stabilize", "verify", "document"})

TASK_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
"""Task ids a mutation may introduce (no path separators/whitespace; ``__iter`` is reserved)."""
RESERVED_ID_MARKER: Final = "__iter"
PRIORITY_MIN: Final = -1000
PRIORITY_MAX: Final = 1000
MAX_EDIT_ITEMS: Final = 200
"""Cap on list entries per edit payload section (keeps a hostile entry bounded)."""
MAX_INJECT_TASKS: Final = 100

PENDING: Final = "pending"


class EditRejected(Exception):
    """A mutation violated a checker rule; nothing was changed."""

    def __init__(self, rule_id: str, detail: str) -> None:
        super().__init__(detail)
        self.rule_id = rule_id
        self.detail = detail


@dataclass(frozen=True)
class CheckPolicy:
    """Optional policy inputs; the all-default policy applies only the structural rules.

    ``stage`` is the overseer budget stage (None => no stage gating, e.g. routed-runner).
    ``task_meta`` maps a *new* task id to ``{"kind": ..., "work_item": ...}`` taken from its
    brief; ``known_work_items`` is the ledger's existing work-item set (converge: no new scope).
    """

    stage: str | None = None
    known_work_items: frozenset[str] = frozenset()
    task_meta: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    max_new_tasks: int | None = None


@dataclass
class EditPlan:
    """A validated, not-yet-committed mutation."""

    new_tasks: list[TaskSpec] = field(default_factory=list)
    edits: list[GraphEdit] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    tasks_after: list[TaskSpec] = field(default_factory=list)
    """The full candidate ``workflow.tasks`` (what commit installs)."""


# ---------------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------------


def _is_pending(state: RunState, tid: str) -> bool:
    ts = state.tasks.get(tid)
    return ts is None or ts.status == PENDING


def _candidate(workflow: WorkflowSpec, tasks: list[TaskSpec]) -> WorkflowSpec:
    """A workflow view over *tasks* (shallow: only ``tasks`` replaced; nothing else is mutated)."""
    return workflow.model_copy(update={"tasks": tasks})


def _effective_edges(workflow: WorkflowSpec, tasks: list[TaskSpec]) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in iter_dependency_edges(_candidate(workflow, tasks))}


def _check_acyclic(workflow: WorkflowSpec, tasks: list[TaskSpec]) -> None:
    try:
        graph = build_dag(_candidate(workflow, tasks))
        graph.topological_order()
    except CycleError as exc:
        raise EditRejected(RULE_CYCLE, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 -- unknown dependency etc.: reject, never raise out
        raise EditRejected(RULE_SPEC, f"graph does not build: {exc}") from exc
    # build_dag keeps unknown dependency sources as phantom nodes; a run-time edit must not.
    ids = {t.id for t in tasks}
    phantom = sorted(set(graph.adjacency()) - ids)
    if phantom:
        raise EditRejected(RULE_SPEC, f"unknown dependency task id(s): {phantom}")


def _check_producers(tasks: list[TaskSpec]) -> None:
    owner: dict[str, str] = {}
    for t in tasks:
        for out in t.outputs:
            prev = owner.setdefault(out, t.id)
            if prev != t.id:
                raise EditRejected(
                    RULE_PRODUCER_DUP,
                    f"output {out!r} is produced by both {prev!r} and {t.id!r}",
                )


def _check_edges_into_nonpending(
    workflow: WorkflowSpec,
    before: list[TaskSpec],
    after: list[TaskSpec],
    state: RunState,
) -> None:
    """No *new* effective edge may point into a task that already left ``pending`` (I2/I4)."""
    old = _effective_edges(workflow, before)
    for src, dst in sorted(_effective_edges(workflow, after) - old):
        if not _is_pending(state, dst):
            raise EditRejected(
                RULE_EDGE_INTO_NONPENDING,
                f"edge {src!r} -> {dst!r} points into {dst!r}, which is "
                f"{state.tasks[dst].status!r} (only pending tasks may gain predecessors)",
            )


def _check_stage(new_tasks: list[TaskSpec], policy: CheckPolicy) -> None:
    if policy.stage is None or not new_tasks:
        return
    if policy.stage == STAGE_CLOSEOUT:
        raise EditRejected(RULE_STAGE_CLOSEOUT, "no new tasks may be added in the closeout stage")
    for t in new_tasks:
        meta = policy.task_meta.get(t.id, {})
        if policy.stage == STAGE_STABILIZE and meta.get("kind") not in STABILIZE_ALLOWED_KINDS:
            raise EditRejected(
                RULE_STAGE_STABILIZE,
                f"task {t.id!r} kind {meta.get('kind')!r} is not allowed in the stabilize stage "
                f"(must be one of {sorted(STABILIZE_ALLOWED_KINDS)})",
            )
        if policy.stage == STAGE_CONVERGE and meta.get("work_item") not in policy.known_work_items:
            raise EditRejected(
                RULE_STAGE_CONVERGE,
                f"task {t.id!r} targets work_item {meta.get('work_item')!r}, which is not an "
                "existing work item -- the converge stage may not open new scope",
            )


def _check_new_ids(existing: set[str], new_tasks: list[TaskSpec]) -> None:
    seen = set(existing)
    for t in new_tasks:
        if not TASK_ID_RE.match(t.id) or RESERVED_ID_MARKER in t.id:
            raise EditRejected(RULE_ID_PATTERN, f"task id {t.id!r} is not an allowed id")
        if t.id in seen:
            raise EditRejected(RULE_ID_DUP, f"task id {t.id!r} already exists")
        seen.add(t.id)


def _structural_ids(workflow: WorkflowSpec, membership: Mapping[str, Any]) -> dict[str, str]:
    """Task ids no run-time edit may touch -> rule id (router/loop/route cone bookkeeping)."""
    out: dict[str, str] = {}
    for router in workflow.branches:
        out[router.router_task_id] = RULE_ROUTE_SINK
        for route in router.routes.values():
            for entry in route.entry:
                out[entry] = RULE_ROUTE_SINK
    for loop in workflow.loops:
        out[loop.gate_task_id] = RULE_CONE
        for body in loop.body:
            out[body] = RULE_CONE
    for tid in membership:
        out.setdefault(tid, RULE_CONE)
    return out


def _guard_target(
    tid: str,
    workflow: WorkflowSpec,
    state: RunState,
    structural: dict[str, str],
    known: set[str],
) -> None:
    if tid not in known:
        raise EditRejected(RULE_UNKNOWN_TASK, f"unknown task {tid!r}")
    if not _is_pending(state, tid):
        raise EditRejected(
            RULE_NOT_PENDING, f"task {tid!r} is {state.tasks[tid].status!r}, not pending"
        )
    rule = structural.get(strip_iter_suffix(tid)) or structural.get(tid)
    if rule is not None:
        raise EditRejected(rule, f"task {tid!r} is part of a route/loop cone or route boundary")


def _need_list(payload: Mapping[str, Any], key: str) -> list[Any]:
    value = payload.get(key, [])
    if not isinstance(value, list):
        raise EditRejected(RULE_PAYLOAD, f"{key} must be a list")
    if len(value) > MAX_EDIT_ITEMS:
        raise EditRejected(RULE_PAYLOAD, f"{key} has more than {MAX_EDIT_ITEMS} entries")
    return value


def _edge_pair(item: Any) -> tuple[str, str, bool]:
    if (
        not isinstance(item, dict)
        or not isinstance(item.get("from"), str)
        or not isinstance(item.get("to"), str)
    ):
        raise EditRejected(RULE_PAYLOAD, 'each edge must be {"from": <task id>, "to": <task id>}')
    drop = item.get("drop_inputs", False)
    if not isinstance(drop, bool):
        raise EditRejected(RULE_PAYLOAD, "edge drop_inputs must be a boolean")
    return item["from"], item["to"], drop


def _need_ids(payload: Mapping[str, Any], key: str) -> list[str]:
    items = _need_list(payload, key)
    if not all(isinstance(i, str) for i in items):
        raise EditRejected(RULE_PAYLOAD, f"{key} must be a list of task ids")
    return list(items)


# ---------------------------------------------------------------------------------------------
# public checks
# ---------------------------------------------------------------------------------------------


def check_additive_manifest(
    workflow: WorkflowSpec,
    state: RunState,
    new_tasks: list[TaskSpec],
    policy: CheckPolicy | None = None,
) -> None:
    """Validate an additive set of new tasks against the live graph (soft barrier, ``inject``).

    Raises :class:`EditRejected`. Rules: id pattern/duplicates, no duplicate output producer, no
    cycle, no new effective edge into a non-pending task (explicit *or* inferred from
    inputs/outputs), plus the budget-stage rules of *policy*.
    """
    policy = policy or CheckPolicy()
    if policy.max_new_tasks is not None and len(new_tasks) > policy.max_new_tasks:
        raise EditRejected(
            RULE_LIMIT, f"{len(new_tasks)} new tasks exceed the limit {policy.max_new_tasks}"
        )
    _check_new_ids({t.id for t in workflow.tasks}, new_tasks)
    _check_stage(new_tasks, policy)
    after = [*workflow.tasks, *new_tasks]
    _check_producers(after)
    _check_acyclic(workflow, after)
    _check_edges_into_nonpending(workflow, workflow.tasks, after, state)


def plan_inject(
    workflow: WorkflowSpec,
    state: RunState,
    payload: Mapping[str, Any],
    *,
    actor: str,
    at: str,
    edit_id: str,
    policy: CheckPolicy | None = None,
) -> EditPlan:
    """Plan an ``inject`` entry (``payload.tasks`` = TaskSpec dicts)."""
    raw = _need_list(payload, "tasks")
    if not raw or len(raw) > MAX_INJECT_TASKS:
        raise EditRejected(RULE_PAYLOAD, f"inject needs 1..{MAX_INJECT_TASKS} tasks")
    try:
        new = [TaskSpec.model_validate(t) for t in raw]
    except ValidationError as exc:
        raise EditRejected(RULE_SPEC, f"invalid TaskSpec: {exc.error_count()} error(s)") from exc
    check_additive_manifest(workflow, state, new, policy)
    return EditPlan(new_tasks=new, tasks_after=[*workflow.tasks, *new])


def plan_edit_pending(
    workflow: WorkflowSpec,
    state: RunState,
    payload: Mapping[str, Any],
    *,
    membership: Mapping[str, Any],
    actor: str,
    at: str,
    edit_id: str,
) -> EditPlan:
    """Plan an ``edit_pending`` entry: ``add_edges[] remove_edges[] cancel[] skip[] priority{}``.

    All-or-nothing: every operation is validated against deep copies and the combined result is
    cycle/effective-edge checked once; any failure raises :class:`EditRejected`.
    """
    allowed = {"add_edges", "remove_edges", "cancel", "skip", "priority"}
    unknown = set(payload) - allowed
    if unknown:
        raise EditRejected(RULE_PAYLOAD, f"unknown edit_pending fields: {sorted(unknown)}")
    add_edges = [_edge_pair(i) for i in _need_list(payload, "add_edges")]
    rem_edges = [_edge_pair(i) for i in _need_list(payload, "remove_edges")]
    cancel = _need_ids(payload, "cancel")
    skip = _need_ids(payload, "skip")
    prio_raw = payload.get("priority", {})
    if not isinstance(prio_raw, dict) or len(prio_raw) > MAX_EDIT_ITEMS:
        raise EditRejected(RULE_PAYLOAD, "priority must be an object {task_id: int}")
    if not (add_edges or rem_edges or cancel or skip or prio_raw):
        raise EditRejected(RULE_PAYLOAD, "edit_pending carries no operations")
    for tid, val in prio_raw.items():
        if (
            isinstance(val, bool)
            or not isinstance(val, int)
            or not PRIORITY_MIN <= val <= PRIORITY_MAX
        ):
            raise EditRejected(
                RULE_PAYLOAD, f"priority[{tid!r}] must be an int in {PRIORITY_MIN}..{PRIORITY_MAX}"
            )
    if set(cancel) & set(skip):
        raise EditRejected(RULE_PAYLOAD, "a task cannot be both cancelled and skipped")

    known = {t.id for t in workflow.tasks}
    structural = _structural_ids(workflow, membership)
    touched = (
        {e[0] for e in add_edges}
        | {e[1] for e in add_edges}
        | {e[0] for e in rem_edges}
        | {e[1] for e in rem_edges}
        | set(cancel)
        | set(skip)
        | set(prio_raw)
    )
    for tid in sorted(touched):
        _guard_target(tid, workflow, state, structural, known)

    work: dict[str, TaskSpec] = {t.id: t.model_copy(deep=True) for t in workflow.tasks}
    touched_edges: set[str] = set()
    for src, dst, _ in add_edges:
        if src == dst:
            raise EditRejected(RULE_CYCLE, f"self edge on {src!r}")
        if src not in work[dst].depends_on:
            work[dst].depends_on.append(src)
        touched_edges.add(dst)
    for src, dst, drop in rem_edges:
        if src in work[dst].depends_on:
            work[dst].depends_on.remove(src)
        shared = [i for i in work[dst].inputs if i in work[src].outputs]
        if shared:
            if not drop:
                raise EditRejected(
                    RULE_INPUT_EDGE,
                    f"{src!r} produces declared input(s) {shared} of {dst!r}; removing the edge "
                    "needs drop_inputs=true (the inputs are removed too)",
                )
            work[dst].inputs = [i for i in work[dst].inputs if i not in shared]
        touched_edges.add(dst)

    cancel_set = set(cancel)
    for tid in cancel:
        gone = work.pop(tid)
        for other in work.values():
            if gone.outputs and any(i in gone.outputs for i in other.inputs):
                if other.id in cancel_set:
                    continue
                raise EditRejected(
                    RULE_ORPHAN_INPUT,
                    f"cancelling {tid!r} orphans input(s) of {other.id!r}; cancel it too or "
                    "remove the input",
                )
            if tid in other.depends_on:
                other.depends_on = [d for d in other.depends_on if d != tid]
                touched_edges.add(other.id)
    after = list(work.values())
    _check_acyclic(workflow, after)
    # `before` has to share the post-cancel id set for the diff to mean "new edges only".
    kept_before = [t for t in workflow.tasks if t.id in work]
    _check_edges_into_nonpending(workflow, kept_before, after, state)

    edits: list[GraphEdit] = []
    src_at: dict[str, Any] = {"source": actor, "at": at, "edit_id": edit_id}
    for tid in sorted(touched_edges - cancel_set):
        t = work[tid]
        edits.append(
            GraphEdit(
                op="set_edges",
                task_id=tid,
                depends_on=list(t.depends_on),
                inputs=list(t.inputs),
                **src_at,
            )
        )
    for tid in cancel:
        edits.append(GraphEdit(op="remove_task", task_id=tid, **src_at))
    for tid in skip:
        edits.append(GraphEdit(op="skip", task_id=tid, reason=f"skipped by {actor}", **src_at))
    for tid, val in sorted(prio_raw.items()):
        edits.append(GraphEdit(op="priority", task_id=tid, value=val, **src_at))
    return EditPlan(edits=edits, removed=list(cancel), skipped=list(skip), tasks_after=after)


def plan_early_checkpoint(
    workflow: WorkflowSpec,
    state: RunState,
    payload: Mapping[str, Any],
    *,
    actor: str,
    at: str,
    edit_id: str,
) -> EditPlan:
    """Plan the FR-10 early release of a pending soft checkpoint.

    ``payload = {checkpoint_id, carry_over[]}``: drops the (unsettled) carry-over predecessors
    from the checkpoint's ``depends_on`` and the ``inputs`` they produce. Internal source only
    (the tick); the checkpoint must be a pending soft ``emit_tasks`` task, each carry-over id a
    current predecessor that has not settled, and at least one predecessor must remain.
    """
    ck_id = payload.get("checkpoint_id")
    carry = payload.get("carry_over")
    if not isinstance(ck_id, str) or not isinstance(carry, list) or not carry:
        raise EditRejected(RULE_PAYLOAD, "early_checkpoint needs checkpoint_id and carry_over[]")
    by_id = {t.id: t for t in workflow.tasks}
    ck = by_id.get(ck_id)
    if ck is None:
        raise EditRejected(RULE_UNKNOWN_TASK, f"unknown checkpoint {ck_id!r}")
    if not _is_pending(state, ck_id):
        raise EditRejected(RULE_NOT_PENDING, f"checkpoint {ck_id!r} is not pending")
    if not ck.emit_tasks or ck.barrier != "soft":
        raise EditRejected(RULE_EARLY, f"{ck_id!r} is not a soft emitter checkpoint")
    settled_states = {"succeeded", "failed", "skipped", "cancelled"}
    for c in carry:
        if not isinstance(c, str) or c not in ck.depends_on:
            raise EditRejected(RULE_EARLY, f"carry-over {c!r} is not a predecessor of {ck_id!r}")
        if c in state.tasks and state.tasks[c].status in settled_states:
            raise EditRejected(RULE_EARLY, f"carry-over {c!r} already settled")
    new_deps = [d for d in ck.depends_on if d not in carry]
    if not new_deps:
        raise EditRejected(RULE_EARLY, "no settled predecessor would remain")
    dropped_out = (
        {o for c in carry for o in by_id[c].outputs} if all(c in by_id for c in carry) else set()
    )
    new_inputs = [i for i in ck.inputs if i not in dropped_out]
    work = {t.id: t.model_copy(deep=True) for t in workflow.tasks}
    work[ck_id].depends_on = new_deps
    work[ck_id].inputs = new_inputs
    after = list(work.values())
    _check_acyclic(workflow, after)
    edit = GraphEdit(
        op="set_edges",
        task_id=ck_id,
        depends_on=new_deps,
        inputs=new_inputs,
        source=actor,
        at=at,
        edit_id=edit_id,
    )
    return EditPlan(edits=[edit], tasks_after=after)


# ---------------------------------------------------------------------------------------------
# commit / replay
# ---------------------------------------------------------------------------------------------


def commit_plan(
    workflow: WorkflowSpec, state: RunState, plan: EditPlan, *, parent: str, at: str
) -> None:
    """Install a validated plan: workflow tasks, run-state rows, provenance and the edit ledger.

    Appends to ``injected_tasks`` / ``graph_edits`` in the same step as the mutation so a single
    save makes both durable (G5). *parent* is the spawn label (``sentinel-tick``,
    ``sentinel-gNN`` or ``ctl:<actor>``).
    """
    workflow.tasks[:] = plan.tasks_after
    for spec in plan.new_tasks:
        state.injected_tasks.append(spec)
        state.tasks[spec.id] = TaskRunState(origin=SPAWN_ORIGIN_SENTINEL)
        state.spawned_by[spec.id] = SpawnRecord(
            parent_task_id=parent,
            parent_dispatch_cycle=0,
            origin=SPAWN_ORIGIN_SENTINEL,
            injected_at=at,
        )
    _apply_runstate_effects(state, plan.edits)
    state.graph_edits.extend(plan.edits)
    if plan.new_tasks and state.sentinel is not None:
        state.sentinel.injected_count += len(plan.new_tasks)  # FR-19 counter (all sources)
        state.sentinel.injected_ids.extend(t.id for t in plan.new_tasks)


def _apply_runstate_effects(state: RunState, edits: list[GraphEdit]) -> None:
    for e in edits:
        if e.op == "remove_task":
            state.tasks.pop(e.task_id, None)
            state.injected_tasks[:] = [t for t in state.injected_tasks if t.id != e.task_id]
        elif e.op == "skip":
            state.tasks[e.task_id] = TaskRunState(
                status="skipped",
                origin=state.tasks[e.task_id].origin if e.task_id in state.tasks else "static",
            )


def replay_edits(workflow: WorkflowSpec, state: RunState) -> None:
    """Re-apply ``state.graph_edits`` to *workflow* (after the injected-task merge). Idempotent.

    Called by ``prepare_resume`` (and the engine's raw-state guard). ``skip`` rows are returned to
    ``skipped`` here because the resume reset pass turns every non-terminal task ``pending``.
    """
    by_id = {t.id: t for t in workflow.tasks}
    for e in state.graph_edits:
        if e.op == "set_edges":
            t = by_id.get(e.task_id)
            if t is not None:
                if e.depends_on is not None:
                    t.depends_on = list(e.depends_on)
                if e.inputs is not None:
                    t.inputs = list(e.inputs)
        elif e.op == "remove_task":
            if e.task_id in by_id:
                del by_id[e.task_id]
                workflow.tasks[:] = [t for t in workflow.tasks if t.id != e.task_id]
            state.tasks.pop(e.task_id, None)
            state.injected_tasks[:] = [t for t in state.injected_tasks if t.id != e.task_id]
        elif e.op == "skip":
            if e.task_id in by_id and (
                e.task_id not in state.tasks or state.tasks[e.task_id].status == PENDING
            ):
                prev = state.tasks.get(e.task_id)
                state.tasks[e.task_id] = TaskRunState(
                    status="skipped", origin=prev.origin if prev else "static"
                )


def priorities(state: RunState) -> dict[str, int]:
    """Effective FILL priority per task id (latest ``priority`` edit wins; absent => 0)."""
    out: dict[str, int] = {}
    for e in state.graph_edits:
        if e.op == "priority" and e.value is not None:
            out[e.task_id] = e.value
    return out
