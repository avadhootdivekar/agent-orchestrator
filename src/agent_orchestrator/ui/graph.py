"""Pure builder that turns engine ``RunState`` + workflow snapshot into the dashboard's
run graph (HLD §8.3.2, ADR-0017 D3/D4).

No I/O, no clock (NFR-2): every function here takes already-loaded models and returns
plain, ``asdict``-serializable dataclasses. The I/O around this module (loading
``state.json`` and the snapshot file) is `ui.runs.RunRepository.load_graph` (T-AsQ77e).

Edge derivation is delegated ENTIRELY to ``dag.iter_dependency_edges`` (ADR-0017 D3): this
module must never re-derive explicit dependency declarations or output/input path matching
itself, so the dashboard and ``dag.build_dag`` can never disagree on what an edge is.

Importable without the dashboard's optional web-framework extra: no such import, and no
file I/O (AC-10).
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from ..dag import iter_dependency_edges
from ..models import RunState, TaskSpec, WorkflowSnapshot, WorkflowSpec, strip_iter_suffix

# ---------------------------------------------------------------------------
# Named constants (NFR-5) -- see TASK.md AC-1 / HLD §8.3.2.
# ---------------------------------------------------------------------------

GRAPH_SCHEMA_VERSION = 1
# Hard cap on node count, applied BEFORE edge derivation (dev-security MEDIUM): bounds the
# work a huge/forged state can make this pure function do, regardless of edge count.
GRAPH_MAX_NODES = 5000
GRAPH_LABEL_MAX_CHARS = 200
# Length of the graph_version hex digest returned by `compute_graph_version`.
GRAPH_VERSION_HEX_CHARS = 16
GRAPH_SOURCE_SNAPSHOT = "snapshot"
GRAPH_SOURCE_UNAVAILABLE = "unavailable"

# `RunGraph.spawn_data` (closed enum, HLD §14.2).
SPAWN_DATA_RECORDED = "recorded"
SPAWN_DATA_NOT_RECORDED = "not_recorded"
SPAWN_DATA_NONE = "none"

# `TaskRunState.origin`'s own default (models.py) -- the fallback for a node this run never
# recorded provenance for (a phantom, or a run that predates spawn tracking entirely).
_ORIGIN_STATIC_FALLBACK = "static"

# Placeholder identity for the synthetic, loop/branch-free `WorkflowSpec` used only as a
# `.model_copy(update={"tasks": ...})` base when no snapshot is available (HLD §8.3.2's
# `EMPTY_WORKFLOW_SHELL`). Never surfaced in the API response -- `RunGraph.run_id` always
# comes from `state.run_id`.
_UNAVAILABLE_SHELL_VERSION = "0"
_UNAVAILABLE_SHELL_ID = "__unavailable__"
_UNAVAILABLE_SHELL_REPO_SET = "__unavailable__"

# Warning text templates (byte-identical wording matters: the frontend renders these
# literally, and tests pin the exact strings).
_WARNING_SPEC_CHANGED = (
    "The workflow spec changed during this run; showing the spec from session {session}."
)
_WARNING_SNAPSHOT_MISSING = (
    "Static task dependencies are unavailable: this run predates workflow snapshots, or its "
    "snapshot is missing or unreadable."
)
_WARNING_PRE_SNAPSHOT = (
    "Static task dependencies are unavailable: this run predates workflow snapshots."
)
_WARNING_TRUNCATED = "Graph truncated to the first {max_nodes} tasks."
_WARNING_PHANTOM = (
    "{count} dependency id(s) reference unknown tasks and are shown as missing nodes."
)
_WARNING_SPAWN_NOT_RECORDED = (
    "Spawn relationships were not recorded for this run (it predates spawn tracking)."
)

# Every Unicode Cc (control) and Cf (format) codepoint -- bidi embedding/override/isolate
# controls, zero-width characters, the invisible "Tags" block, etc -- the categories an
# agent-authored task id could smuggle in to spoof another id or hide content in the UI
# (ADR-0017 D4, dev-security LOW/M-1 at Gate G2). Derived from `unicodedata.category`
# itself, once at import time, rather than an explicit enumerated range: a prior explicit
# range missed 150 real Cc/Cf codepoints (Gate G2 M-1), including U+200E/U+200F (LRM/RLM --
# the same bidi-spoofing class as the LRE/RLE/LRO/RLO it did cover) and the U+E0000-E007F
# "Tags" block (a documented invisible-payload-smuggling range). Deriving from unicodedata
# guarantees every current (and future, on a Python upgrade) Cc/Cf codepoint is covered,
# not just the ones an author happened to enumerate. One-time cost (~70ms) at import, off
# the engine hot path (NFR-7) -- `ui.graph` is only imported when the dashboard starts.
_INVISIBLE_OR_BIDI_TRANSLATION = {
    codepoint: None
    for codepoint in range(0x110000)
    if unicodedata.category(chr(codepoint)) in ("Cc", "Cf")
}


# ---------------------------------------------------------------------------
# Dataclasses (frozen, `asdict`-serializable) -- field names/order match HLD §14.2 exactly.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GraphNode:
    """One task (real or phantom) as rendered on the canvas (HLD §14.2)."""

    id: str
    label: str
    label_sanitized: bool
    origin: str
    parent_task_id: str | None
    route: str | None
    loop_id: str | None
    iteration: int | None
    spawn_depth: int | None
    children_count: int
    is_emitter: bool
    is_router: bool
    is_loop_gate: bool
    exec_ordinal: int | None
    missing: bool


@dataclass(frozen=True)
class GraphDependencyEdge:
    """One "must settle before" edge (`dag.DependencyEdge`, converted for JSON output)."""

    source: str
    target: str
    kind: str
    via: str | None


@dataclass(frozen=True)
class GraphSpawnEdge:
    """One "created" edge, from recorded provenance only -- never inferred (ADR-0017 D1)."""

    source: str
    target: str
    origin: str
    loop_id: str | None
    iteration: int | None


@dataclass(frozen=True)
class GraphLoop:
    """One loop's static shape plus how far this run has materialized it."""

    id: str
    body: list[str]
    gate_task_id: str
    max_iterations: int
    iterations_materialized: int


@dataclass(frozen=True)
class GraphRouter:
    """One router's static shape plus which routes this run selected."""

    id: str
    router_task_id: str
    selected: list[str]


@dataclass(frozen=True)
class RunGraph:
    """The full graph payload for one run (HLD §14.2)."""

    schema_version: int
    run_id: str
    graph_version: str
    source: str
    spawn_data: str
    truncated: bool
    warnings: list[str]
    nodes: list[GraphNode]
    dependency_edges: list[GraphDependencyEdge]
    spawn_edges: list[GraphSpawnEdge]
    loops: list[GraphLoop]
    routers: list[GraphRouter]


# ---------------------------------------------------------------------------
# display_text -- the server-side label sanitizer (ADR-0017 D4).
# ---------------------------------------------------------------------------


def display_text(raw: str) -> tuple[str, bool]:
    """Sanitize *raw* for display: strip bidi/zero-width/control characters and cap length
    at ``GRAPH_LABEL_MAX_CHARS`` (dev-security LOW: anti-spoofing, not just CSS truncation).

    Returns ``(cleaned, changed)`` where ``changed`` is True iff the output differs from
    the input, either because characters were stripped or because the string was
    truncated. The raw task ``id`` is NEVER passed through this function for the ``id``
    field itself -- it is the join key and must stay byte-exact; only ``label`` (or the
    mirrored ``model.ts::displayText``, for ids reached via links) is sanitized.
    """
    cleaned = raw.translate(_INVISIBLE_OR_BIDI_TRANSLATION)
    if len(cleaned) > GRAPH_LABEL_MAX_CHARS:
        cleaned = cleaned[: GRAPH_LABEL_MAX_CHARS - 1] + "…"
    return cleaned, cleaned != raw


def label_for(spec: TaskSpec, task_id: str) -> str:
    """Return the display label for *task_id*, given its static spec.

    THE seam for a future, non-MVP ``TaskSpec.title`` field (HLD §8.3.2) -- returns the
    raw id today. ``T-VcN4pt`` changes only this function's body; every other caller keeps
    working unchanged because ``label_for`` (not the id) is what feeds ``display_text``.
    """
    del spec  # unused until TaskSpec gains a title field (T-VcN4pt's seam)
    return task_id


# ---------------------------------------------------------------------------
# compute_graph_version -- THE ONLY version derivation (reviewer MUST-FIX, HLD §8.3.2).
# ---------------------------------------------------------------------------


def compute_graph_version(state: RunState) -> str:
    """Return a short, deterministic fingerprint of everything the graph's nodes and
    edges depend on in *state*.

    Reads ``state`` alone -- no clock, no snapshot file -- so the existing 3s
    ``RunDetail`` poll (``ui.runs.RunRepository.detail``) never has to open a snapshot
    just to know whether the topology changed. Covers the latest session's spec sha, the
    injected-task id list (order matters -- a reordered emit batch IS a different graph),
    the full task id set, the spawned-task id set, and loop iteration counts. Deliberately
    excludes per-task status/attempts/cost: those come from ``RunDetail.tasks`` and change
    on every poll without changing the graph's topology.
    """
    latest_sha = state.spec_sessions[-1].spec_sha256 if state.spec_sessions else None
    payload = [
        GRAPH_SCHEMA_VERSION,
        latest_sha,
        [t.id for t in state.injected_tasks],
        sorted(state.tasks),
        sorted(state.spawned_by),
        sorted(state.loop_iterations.items()),
    ]
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:GRAPH_VERSION_HEX_CHARS]


# ---------------------------------------------------------------------------
# build_run_graph -- the builder itself (HLD §8.3.2).
# ---------------------------------------------------------------------------


def _compute_spawn_depths(edges: Iterable[tuple[str, str]]) -> dict[str, int]:
    """Iterative BFS depth over spawn edges (parent, child) pairs; depth 0 for a node with
    no incoming spawn edge.

    A visited-set guards against a forged cycle in ``spawned_by`` (the engine never writes
    one; a hand-edited or corrupted ``state.json`` could): a node with no incoming edge
    that is itself unreachable stays absent, so ``.get(id)`` on the result returns ``None``
    for every member of a pure cycle -- no exception (TASK.md AC-6).
    """
    children: dict[str, list[str]] = {}
    has_incoming: set[str] = set()
    participants: set[str] = set()
    for parent, child in edges:
        children.setdefault(parent, []).append(child)
        has_incoming.add(child)
        participants.add(parent)
        participants.add(child)

    roots = sorted(p for p in participants if p not in has_incoming)
    depth: dict[str, int] = {}
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(r, 0) for r in roots]
    while queue:
        node, d = queue.pop(0)
        if node in visited:
            continue
        visited.add(node)
        depth[node] = d
        for child in sorted(children.get(node, [])):
            if child not in visited:
                queue.append((child, d + 1))
    return depth


def _parse_started_at(value: str | None) -> datetime | None:
    """Tolerant ISO-8601 parse; ``None`` for absent or malformed input (never raises)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def build_run_graph(state: RunState, snapshot: WorkflowSnapshot | None) -> RunGraph:
    """Build the dashboard's run graph from *state* and its latest-session *snapshot*.

    Pure: no I/O, no clock, no mutation of either argument. Two calls on identical inputs
    return identical output (TASK.md AC-7).
    """
    warnings: list[str] = []

    static: WorkflowSpec | None
    if snapshot is not None:
        static = snapshot.workflow
        source = GRAPH_SOURCE_SNAPSHOT
        if len({s.spec_sha256 for s in state.spec_sessions}) > 1:
            warnings.append(_WARNING_SPEC_CHANGED.format(session=state.spec_sessions[-1].session))
    else:
        static = None
        source = GRAPH_SOURCE_UNAVAILABLE
        warnings.append(_WARNING_SNAPSHOT_MISSING if state.spec_sessions else _WARNING_PRE_SNAPSHOT)

    static_tasks: list[TaskSpec] = list(static.tasks) if static is not None else []
    static_ids = {t.id for t in static_tasks}
    merged: list[TaskSpec] = static_tasks + [
        t for t in state.injected_tasks if t.id not in static_ids
    ]
    orphans: list[str] = sorted(set(state.tasks) - {t.id for t in merged})

    # ---- Early size cap (dev-security MEDIUM): bound the work BEFORE edge derivation.
    truncated = False
    if len(merged) + len(orphans) > GRAPH_MAX_NODES:
        keep = GRAPH_MAX_NODES
        merged = merged[:keep]
        orphans = orphans[: max(0, keep - len(merged))]
        truncated = True
        warnings.append(_WARNING_TRUNCATED.format(max_nodes=GRAPH_MAX_NODES))

    wf: WorkflowSpec = (
        static.model_copy(update={"tasks": merged})
        if static is not None
        else WorkflowSpec(
            version=_UNAVAILABLE_SHELL_VERSION,
            id=_UNAVAILABLE_SHELL_ID,
            repo_set=_UNAVAILABLE_SHELL_REPO_SET,
            tasks=merged,
        )
    )
    spec_by_id: dict[str, TaskSpec] = {t.id: t for t in merged}
    node_ids: list[str] = [t.id for t in merged] + orphans
    dep_edges = list(iter_dependency_edges(wf))
    known = set(node_ids)
    phantom: list[str] = sorted({e.source for e in dep_edges} - known)
    if truncated:
        # Never invent a phantom node for a task cut by the cap -- only for a genuinely
        # unknown dependency id (TASK.md AC-5).
        dep_edges = [e for e in dep_edges if e.target in known]
        phantom = []
    elif phantom:
        warnings.append(_WARNING_PHANTOM.format(count=len(phantom)))
    node_ids = node_ids + phantom
    known_or_phantom = set(node_ids)
    phantom_set = set(phantom)

    # ---- spawn edges: recorded provenance only, never inferred (ADR-0017 D1).
    spawn_items = sorted(state.spawned_by.items(), key=lambda kv: (kv[1].injected_at, kv[0]))
    spawn_edges_raw: list[tuple[str, str, str, str | None, int | None]] = [
        (rec.parent_task_id, child, rec.origin, rec.loop_id, rec.iteration)
        for child, rec in spawn_items
        if child in known_or_phantom and rec.parent_task_id in known_or_phantom
    ]
    spawn_data = (
        SPAWN_DATA_RECORDED
        if state.spawned_by
        else (SPAWN_DATA_NOT_RECORDED if state.injected_tasks else SPAWN_DATA_NONE)
    )
    if spawn_data == SPAWN_DATA_NOT_RECORDED:
        warnings.append(_WARNING_SPAWN_NOT_RECORDED)

    children_count: dict[str, int] = {}
    for parent, *_rest in spawn_edges_raw:
        children_count[parent] = children_count.get(parent, 0) + 1
    depth = _compute_spawn_depths((p, c) for p, c, *_r in spawn_edges_raw)

    # ---- execution ordinal: rank by parsed started_at, ties broken by node id.
    started: list[tuple[datetime, str]] = []
    for tid in node_ids:
        ts = state.tasks.get(tid)
        if ts is None:
            continue
        parsed = _parse_started_at(ts.started_at)
        if parsed is not None:
            started.append((parsed, tid))
    started.sort(key=lambda pair: (pair[0], pair[1]))
    ordinal = {tid: i + 1 for i, (_when, tid) in enumerate(started)}

    # ---- flags: routers/loop-gates come from the (possibly synthetic) workflow shell;
    # `body_loop_of` is precomputed once so a per-node lookup is O(1) (TASK.md Risks).
    router_task_ids = {r.router_task_id for r in wf.branches}
    gate_bases = {lp.gate_task_id for lp in wf.loops}
    body_loop_of: dict[str, str] = {base: lp.id for lp in wf.loops for base in lp.body}

    nodes: list[GraphNode] = []
    for tid in node_ids:
        spec = spec_by_id.get(tid)
        ts = state.tasks.get(tid)
        rec = state.spawned_by.get(tid)
        raw_label = tid if spec is None else label_for(spec, tid)
        label, label_sanitized = display_text(raw_label)
        body_loop_id = body_loop_of.get(strip_iter_suffix(tid))
        nodes.append(
            GraphNode(
                id=tid,
                label=label,
                label_sanitized=label_sanitized,
                origin=(
                    rec.origin
                    if rec is not None
                    else (ts.origin if ts is not None else _ORIGIN_STATIC_FALLBACK)
                ),
                parent_task_id=rec.parent_task_id if rec is not None else None,
                route=ts.route if ts is not None else None,
                loop_id=rec.loop_id if rec is not None else body_loop_id,
                iteration=rec.iteration if rec is not None else (1 if body_loop_id else None),
                spawn_depth=depth.get(tid),
                children_count=children_count.get(tid, 0),
                is_emitter=bool(spec is not None and spec.emit_tasks),
                is_router=tid in router_task_ids,
                is_loop_gate=strip_iter_suffix(tid) in gate_bases,
                exec_ordinal=ordinal.get(tid),
                missing=tid in phantom_set,
            )
        )

    return RunGraph(
        schema_version=GRAPH_SCHEMA_VERSION,
        run_id=state.run_id,
        graph_version=compute_graph_version(state),
        source=source,
        spawn_data=spawn_data,
        truncated=truncated,
        warnings=warnings,
        nodes=nodes,
        dependency_edges=[
            GraphDependencyEdge(source=e.source, target=e.target, kind=e.kind, via=e.via)
            for e in dep_edges
        ],
        spawn_edges=[
            GraphSpawnEdge(source=p, target=c, origin=o, loop_id=lid, iteration=it)
            for p, c, o, lid, it in spawn_edges_raw
        ],
        loops=[
            GraphLoop(
                id=lp.id,
                body=list(lp.body),
                gate_task_id=lp.gate_task_id,
                max_iterations=lp.max_iterations,
                iterations_materialized=state.loop_iterations.get(lp.id, 1),
            )
            for lp in wf.loops
        ],
        routers=[
            GraphRouter(
                id=r.id,
                router_task_id=r.router_task_id,
                selected=list(state.route_decisions.get(r.id, [])),
            )
            for r in wf.branches
        ],
    )
