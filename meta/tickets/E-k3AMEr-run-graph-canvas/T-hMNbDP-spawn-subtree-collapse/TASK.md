# TASK: T-hMNbDP-spawn-subtree-collapse

## Metadata
- Task ID: `T-hMNbDP-spawn-subtree-collapse`
- Epic ID: `E-k3AMEr-run-graph-canvas`
- Owner: `developer` (unassigned)
- Created: `2026-09-27`
- Last Updated: `2026-09-27`
- Status: `Draft` (**Non-MVP**, backlog)
- Estimate: `12 focus hours (1.5 days)`

## Requirements Mapping
- Requirement IDs: D-1 (legibility at scale), FR-5 extension

## Description
In the **spawn** view, let the operator collapse and expand an emitter's subtree. A collapsed
emitter renders its descendants as a single "+N tasks" chip on the emitter node, with aggregated
status counts, total cost, and total duration. Collapse state is per view and held in component state
(persistence belongs to `T-N8scZK`). In the **dependency** view, collapse is not offered, because
hiding nodes would break the path semantics.

## Acceptance Criteria
1. Given the spawn view with emitter `cp1` having 40 descendants, when the collapse control on
   `cp1` is activated (click or Enter), then those 40 nodes and their spawn edges are removed from the
   canvas, and `cp1` shows `+40` plus a status breakdown and a summed cost that equal the sums over the
   hidden `TaskStat`s (unit-tested pure function `collapseSubtrees(graph, collapsedIds)`).
2. Expanding restores the exact previous node set. Layout is recomputed and the viewport stays centered on `cp1`.
3. Search for a hidden task auto-expands its ancestors and then centers on it.
4. Nested collapse works (collapsing an ancestor of an already-collapsed emitter counts all descendants once).
5. Toggling to the dependency view shows all nodes, regardless of spawn-view collapse state.

## Risks
- Low/medium. Aggregation double-counting is guarded by AC-4 tests.

## Dependencies
- `T-OjTS8O-run-graph-canvas`, `T-adVpTj-graph-model-and-layout`.

## Pseudocode / Algorithm
```text
FUNCTION collapseSubtrees(nodes, spawnEdges, collapsed:Set) -> {nodes, edges, aggregates: Map<id, Agg>}
  children = adjacency from spawnEdges
  hidden = Set(); FOR c IN collapsed: DFS(children, c) adding descendants (not c) to hidden
  agg[c] = sum over descendants(c) \ (descendants of any collapsed descendant already counted under c) -- count each once
  RETURN nodes.filter(!hidden), edges.filter(both endpoints visible), agg
```

## Schemas / Interface Notes
- Interface: `model.ts::collapseSubtrees` (pure). `TaskNode` gets optional `aggregate` data.
- Spec / data schema: none. API: none.

## Handoff Boundary
- Upstream: canvas component. Downstream: `T-N8scZK` may persist collapse state.

## Artifacts
- Docs/comments: this folder. Large outputs: N/A.
