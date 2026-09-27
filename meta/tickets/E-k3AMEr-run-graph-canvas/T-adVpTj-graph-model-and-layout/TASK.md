# TASK: T-adVpTj-graph-model-and-layout

## Metadata
- Task ID: `T-adVpTj-graph-model-and-layout`
- Epic ID: `E-k3AMEr-run-graph-canvas`
- Owner: `developer` (Dev B, frontend)
- Created: `2026-09-27`
- Last Updated: `2026-09-27`
- Status: `Draft`
- Estimate: `16 focus hours (2 days)`. Includes a 2 h React Flow + dagre spike.

## Requirements Mapping
- Requirement IDs: FR-5 (foundation), FR-7 (types for degraded fields), NFR-3 (layout perf), NFR-4 (bundle), NFR-5, D-6

## Description
This task lays the frontend foundation with **no visible UI yet**:
1. Add the runtime deps `@xyflow/react@^12.12` and `@dagrejs/dagre@^3.1` (exact versions pinned in `package-lock.json`).
2. Mirror the §14 contract in `ui/src/types.ts`.
3. Add `ui/src/api.ts::runGraph(runId)`.
4. Implement the **pure** modules `ui/src/graph/model.ts` and `ui/src/graph/layout.ts`, with exhaustive vitest coverage.
5. Add the React Flow jsdom test shims to `ui/src/test/setup.ts`.
6. Check in the contract fixture `ui/src/test/fixtures/run-graph.json`. It is the §14.2 example extended to cover every enum value, and `T-AsQ77e` contract-tests the backend against it.
7. Measure and record the bundle delta.

Start on day 1 against the design-time contract (HLD §14). There is no need to wait for the backend.

Start with a 2 h time-boxed spike: a throwaway `<ReactFlow>` with 200 dagre-laid-out nodes in `npm run dev`. Record in STATUS whether `@xyflow/react/dist/base.css` plus the SPA CSP renders cleanly in Chrome.

## Acceptance Criteria
1. **Clean install:** `npm ci` succeeds with no `--legacy-peer-deps` and no peer warnings for react 19.
   `npm audit --omit=dev --audit-level=high` exits 0. The resolved versions are recorded in STATUS.
2. **Bundle:** `npm run build` succeeds. The gzip size of the built JS/CSS before and after is recorded in
   STATUS, and the delta is ≤ 90 KB gzip (NFR-4). Graph code is **lazy-loaded** via `React.lazy` so
   the initial dashboard load is unchanged. The main chunk delta is ≤ 5 KB gzip, and the graph chunk
   is measured separately.
3. **Types:** `types.ts` gains `RunGraph`, `GraphNode`, `DependencyEdge`, `SpawnEdge`,
   `GraphLoop`, `GraphRouter`, and the `GraphSource`/`SpawnData`/`EdgeKind` unions. Every field
   matches HLD §14.2 names 1:1. `TaskStat` gains `dispatch_cycle: number` and
   `not_taken_reason: string | null`. `RunDetail` gains `graph_version: string | null`.
   `SpawnEdge.origin` and `GraphNode.origin` are typed `string` (open set). `npm run typecheck` is clean.
4. **Pure functions** (each with unit tests covering every branch listed):
   - `joinNodes(graph, tasks)`: attaches `stat`, or `null` when the task is absent.
   - `edgesForView(graph, view)`: returns exactly `dependency_edges` or `spawn_edges`, with ids `dep:`/`spawn:`.
   - `nodesForView(nodes, edges, view, showUnrelated)`: the dependency view returns all nodes. The
     spawn view hides nodes with no spawn edge unless `showUnrelated`, and returns `hiddenCount`.
   - `computeLayout(nodes, edges, view): Promise<LayoutResult>`, where
     `LayoutResult = { positions: Map<string,{x,y}>, direction: "LR" | "TB" }`. It is **async by
     contract** so the layout engine can later be swapped for elkjs without changing callers
     (dev-critic finding). Dependency view uses LR and spawn view uses TB. It ignores edges whose
     endpoints are missing. It is **deterministic**: the same input gives an identical output,
     asserted by calling it twice. No two nodes overlap, asserted for a 200-node fixture (bounding
     boxes of `NODE_WIDTH`×`NODE_HEIGHT` don't intersect). It tolerates a cycle without throwing.
   - `metricFraction(stat, mode, maxima)`: returns `null` for `none`, a null stat, a null value, or a max ≤ 0. Otherwise it returns a value clamped to [0, 1].
   - `searchNodes(nodes, query)`: case-insensitive substring over id and label, stable order, capped at `SEARCH_MAX_RESULTS = 50`. An empty query returns `[]`.
   - `relatedIds(graph, id)`: returns `{parent, children, dependsOn, dependents}` from **both** edge sets, sorted.
   - `waitSeconds(id, graph, statsById)`: `started_at − max(ended_at of dependency sources)`. Returns
     `null` if the task or any source lacks a timestamp, and `0` for no dependencies. The result is
     clamped to ≥ 0.
   - `readPrefs()`/`writePrefs()`: `localStorage` key `ao.runGraph.prefs.v1`. Every access is in
     try/catch. Unknown or garbage values fall back to the defaults
     `{tab:"table", view:"dependency", metric:"duration", showUnrelated:false}`. Tested with a
     throwing `localStorage` and with invalid JSON.
5. **Perf:** a timed vitest runs `computeLayout` on a generated 200-node/500-edge graph (pure JS, so dagre timing is real under node/jsdom). The measured ms is recorded in STATUS. The test asserts
   ≤ 900 ms (3× the NFR-3 300 ms target, which avoids CI flakes). The STATUS value must be ≤ 300 ms on the dev machine.
6. **jsdom shims** in `setup.ts`: `ResizeObserver` stub, `DOMMatrixReadOnly` stub (with `m22`), and
   `HTMLElement.prototype.offsetWidth/offsetHeight` getters returning the node size, following
   React Flow's documented testing setup. The existing 79+ vitest tests stay green.
7. **Constants** (NFR-5) are exported from `model.ts`: `NODE_WIDTH=184`, `NODE_HEIGHT=48`,
   `RANK_SEP=64`, `NODE_SEP=24`, `LARGE_GRAPH_NODES=300`, `EDGE_LABEL_MIN_ZOOM=0.6`,
   `HOVER_OPEN_DELAY_MS=250`, `HOVER_CLOSE_DELAY_MS=150`, `SEARCH_MAX_RESULTS=50`, and
   `PREFS_STORAGE_KEY`. No other numeric literals appear in these modules except 0 and 1.
8. `model.ts`/`layout.ts` import nothing from React or React Flow (purity: a grep-based test or ESLint rule, or a reviewer check recorded in STATUS).

## Risks
- A peer-dep conflict with React 19. Mitigation: pin the last compatible 12.x and record it.
- `offsetWidth` shims leaking into existing tests. Mitigation: run the full suite (AC-6).

## Dependencies
- Design-time contract: HLD §14 (frozen at design time). No code dependency on the backend.

## Pseudocode / Algorithm
See HLD §8.5 (verbatim function list and algorithms). Layout:
```text
async computeLayout(nodes, edges, view):
  g = new dagre.graphlib.Graph(); g.setGraph({rankdir: dir(view), ranksep: RANK_SEP, nodesep: NODE_SEP})
  g.setDefaultEdgeLabel(() => ({}))
  nodes.forEach(n => g.setNode(n.id, {width: NODE_WIDTH, height: NODE_HEIGHT}))   // input order preserved
  edges.forEach(e => g.hasNode(e.source) && g.hasNode(e.target) && g.setEdge(e.source, e.target))
  dagre.layout(g)
  return { direction: dir(view), positions: new Map(nodes.map(n => [n.id, topLeft(g.node(n.id))])) }
```

## Schemas / Interface Notes
- Interface: exports above. The `ViewNode = GraphNode & { stat: TaskStat | null }` and
  `ViewEdge = (DependencyEdge|SpawnEdge) & { id: string; set: "dependency"|"spawn" }` view-models are
  what components receive. Components never receive the raw `RunGraph` (dev-critic: keeps the canvas
  reusable by a future editor).
- Spec / data schema: HLD §14.2 (mirror only).
- Triggers / events: N/A
- Artifacts: `ui/src/test/fixtures/run-graph.json` (contract fixture, shared with `T-AsQ77e`).

## Handoff Boundary
- Upstream: HLD §14 contract.
- Downstream: `T-OjTS8O-run-graph-canvas`, `T-aHktGB-graph-toolbar-and-legend`,
  `T-pAi0Cv-task-detail-panel` consume the exports. `T-AsQ77e` validates the backend against the fixture.

## Artifacts
- Docs/comments: `meta/tickets/E-k3AMEr-run-graph-canvas/T-adVpTj-graph-model-and-layout/`
- Large outputs: N/A (bundle numbers go in STATUS)
