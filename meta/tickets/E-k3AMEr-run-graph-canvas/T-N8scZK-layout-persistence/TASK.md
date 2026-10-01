# TASK: T-N8scZK-layout-persistence

## Metadata
- Task ID: `T-N8scZK-layout-persistence`
- Epic ID: `E-k3AMEr-run-graph-canvas`
- Owner: `developer` (unassigned)
- Created: `2026-09-27`
- Last Updated: `2026-09-27`
- Status: `Draft` (**Non-MVP**, backlog)
- Estimate: `10 focus hours (1.5 days)`

## Requirements Mapping
- Requirement IDs: U-2 (free-form canvas feel), HLD R-5 (layout jumps on live injection), ADR-0017 D7

## Description
Two related conveniences:
1. **Persist manually dragged node positions** per `(run_id, view)` in `localStorage`. This is a
   per-viewer convenience only. The key is namespaced (`ao.runGraph.positions.v1:<run_id>:<view>`),
   every read and write is wrapped in try/catch, and values are validated as finite numbers and clamped
   to ±1e6. Entries are capped at `POSITIONS_MAX_RUNS = 50` runs with LRU eviction.
2. **Incremental stable layout for live runs.** When new nodes arrive, existing nodes keep their
   positions and only new nodes are placed (dagre is run with existing nodes fixed via post-hoc
   translation, or new nodes are placed in a new rank next to their parent). A "Re-layout all" button
   performs a full layout.

## Acceptance Criteria
1. Drag a node, reload the page, and the node is at the dragged position. "Reset layout" clears the
   stored positions for that run and view only.
2. Corrupt or garbage `localStorage` content (non-JSON, NaN, strings) is ignored, the auto layout is used,
   and no exception is thrown (unit tests).
3. Live injection of N new nodes leaves every pre-existing node's position unchanged (unit test on the
   pure `mergeLayout(prev, next, newIds)`).
4. Storage stays ≤ `POSITIONS_MAX_RUNS` runs (LRU test).

## Risks
- Stale positions after a spec change on resume. Positions are keyed by node id, and unknown ids are
  ignored, so there is no crash.

## Dependencies
- `T-OjTS8O-run-graph-canvas`. `T-hMNbDP` (optional: persist collapse state too).

## Pseudocode / Algorithm
```text
FUNCTION mergeLayout(prevPos: Map, fresh: Map, newIds: Set) -> Map
  out = Map(prevPos filtered to ids still present)
  FOR id IN newIds: out.set(id, fresh.get(id) translated so its parent's fresh->prev offset is applied)
  RETURN out
```

## Schemas / Interface Notes
- localStorage value: `{ v: 1, positions: { [nodeId]: [x, y] }, touched_at: epochMs }`. API: none.

## Handoff Boundary
- Upstream: MVP canvas. Downstream: none.

## Artifacts
- Docs/comments: this folder. Large outputs: N/A.
