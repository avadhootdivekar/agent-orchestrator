# TASK: T-ydMbJN-critical-path-edge-timing

## Metadata
- Task ID: `T-ydMbJN-critical-path-edge-timing`
- Epic ID: `E-k3AMEr-run-graph-canvas`
- Owner: `developer` (unassigned)
- Created: `2026-09-27`
- Last Updated: `2026-09-27`
- Status: `Draft` (**Non-MVP**, backlog)
- Estimate: `12 focus hours (1.5 days)`

## Requirements Mapping
- Requirement IDs: U-1 ("how much time it took"), U-7 extension

## Description
In the **dependency** view:
- Add a "Highlight critical path" toggle that emphasizes the longest duration-weighted path
  through the settled DAG. This is the chain that bounded wall-clock time.
- Optionally show, on each dependency edge, the **wait gap**: downstream `started_at` minus
  upstream `ended_at`. The label is visible at zoom ≥ `EDGE_LABEL_MIN_ZOOM`, and gaps above
  `WAIT_GAP_WARN_SECONDS` are highlighted.

## Acceptance Criteria
1. The pure `criticalPath(graph, statsById)` returns the node-id sequence that maximizes the sum of
   `duration_seconds` along dependency edges. Ties break lexicographically by id. Unsettled nodes
   (null duration) count as 0. Unit tests cover a diamond, a chain, parallel branches, and the empty case.
2. With the toggle on, path nodes and edges carry a `critical` class, and all others are dimmed. With it
   off, rendering is byte-identical to MVP (snapshot test of the class list).
3. Edge wait label = `formatDuration(max(0, down.started_at - up.ended_at))`. It is shown only when both
   timestamps exist. A negative raw gap (clock skew or parallel start) is shown as `0s` and never negative.
4. The computation runs in < 50 ms for 200 nodes/500 edges (dagre-independent pure function, timed test with 3× headroom).

## Risks
- Retries reset `started_at` (latest dispatch only), so the gap reflects the final dispatch. The label
  tooltip says so.

## Dependencies
- `T-pAi0Cv-task-detail-panel` (shares `waitSeconds`), `T-OjTS8O-run-graph-canvas`.

## Pseudocode / Algorithm
```text
FUNCTION criticalPath(nodes, depEdges, stats):
  order = Kahn topological sort (ignore edges in cycles; if a cycle exists RETURN [])
  best[n] = dur(n); prev[n] = null
  FOR n IN order: FOR m IN succ(n): IF best[n] + dur(m) > best[m] (or == and n < prev[m]): best[m] = ..., prev[m] = n
  end = argmax(best) (tie by id); walk prev back; RETURN reversed
```

## Schemas / Interface Notes
- Interface: `model.ts::criticalPath`, `model.ts::edgeWaitSeconds`. API: none.

## Handoff Boundary
- Upstream: MVP canvas and panel. Downstream: none.

## Artifacts
- Docs/comments: this folder. Large outputs: N/A.
