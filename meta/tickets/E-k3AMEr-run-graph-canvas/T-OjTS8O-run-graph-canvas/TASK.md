# TASK: T-OjTS8O-run-graph-canvas

## Metadata
- Task ID: `T-OjTS8O-run-graph-canvas`
- Epic ID: `E-k3AMEr-run-graph-canvas`
- Owner: `developer` (Dev B, frontend)
- Created: `2026-09-27`
- Last Updated: `2026-09-27`
- Status: `Draft`
- Estimate: `14 focus hours (< 2 days)`

## Requirements Mapping
- Requirement IDs: FR-5 (core), FR-8, U-2, U-5, U-6, D-3, D-5, D-7 · HLD §8.6 (canvas core parts) · ADR-0017 D5, D7

## Description
This is the **canvas core**. It was split from the toolbar, legend, and banners (T-aHktGB) after
the manager review.

1. `ui/src/graph/RunGraph.tsx`: `ReactFlowProvider` + `<ReactFlow>` with:
   - `nodeTypes={{task: TaskNode}}`
   - `fitView` on the first layout, `minZoom=0.05`, `maxZoom=2`
   - `nodesDraggable`, `nodesConnectable={false}`
   - `onlyRenderVisibleElements` when there are more than `LARGE_GRAPH_NODES` nodes
   - `<MiniMap pannable zoomable>`, `<Controls showInteractive={false}>`, and `<Background variant="dots">`
   - `@xyflow/react/dist/base.css` plus theme-token overrides in `styles.css` (light **and** dark)
2. **View toggle.** `role="radiogroup"` with the options **"Execution order"** (`dependency`) and
   **"Spawned by"** (`spawn`), with arrow-key support. It swaps `edgesForView` and re-runs
   `computeLayout` (async, stale results discarded). The selected node stays selected and centered.
3. `ui/src/graph/TaskNode.tsx` (memoized):
   - a 184×48 rounded rectangle with explicit `width`/`height`
   - a status stripe plus the status glyph (existing `StatusChip` vocabulary)
   - the `label` as a **text node only**
   - badges `#n` ◆ ↻N ⤴k ⑂ ⚑, each with a text alternative from `BADGE_LABELS`
   - dimmed and dashed styling for `not_taken`/`skipped`
   - invisible handles positioned from `LayoutResult.direction`
   - unknown `origin` rendered as a neutral badge
4. **Edge styles** per the HLD §8.6 table, via CSS classes keyed on `set`/`kind`/`origin`. Edge
   labels render only at zoom ≥ `EDGE_LABEL_MIN_ZOOM`.
5. **Tab integration.** `RunDetail.tsx`'s Tasks section gets a **Table | Graph** switch (Table is
   the default, and the choice is persisted via `writePrefs`). `RunGraph` is imported with
   `React.lazy` + `Suspense`. The Graph tab is shown only if `detail.graph_version` is a non-empty string.
6. **Live refetch.** `RunGraph` receives `runId`, `tasks`, and `graphVersion` props. It fetches
   `api.runGraph` on mount and whenever `graphVersion` differs from the last fetched
   `graph.graph_version`. Stats always come from the `tasks` prop (joined via `joinNodes`). On a
   fetch error it keeps the last good graph and shows the existing `ErrorBanner`.
7. After changing `ui/src`, run `make ui-build` and commit `src/agent_orchestrator/ui/static/`
   (per `ui/README.md`).

## Acceptance Criteria
1. **Toggle** (component test with the fixture graph):
   - Initially "Execution order" is `aria-checked="true"`, and the edge model passed to React Flow
     equals `edgesForView(graph, "dependency")`. Assert via a test hook or a mocked `ReactFlow`
     prop capture; edge DOM in jsdom is unreliable (R-3).
   - Pressing ArrowRight or clicking "Spawned by" switches to `spawn`, and
     `readPrefs().view === "spawn"` afterwards.
2. **Node label only** (U-6). For the fixture node `cp1`, the node's visible text content, minus
   badge text, equals `label` exactly. No cost or duration appears in the node body.
3. **Text-only rendering** (D-5). A node whose label is `"<img src=x onerror=alert(1)>"` renders the
   literal string, and `container.querySelector("img")` is null. `grep -r dangerouslySetInnerHTML
   ui/src/graph` is empty (test).
4. **Accessibility** (D-7). Every node's accessible name includes the label, the status word, and
   the badge texts (for example `"u1, succeeded, injected task"`). Status is never conveyed by color
   alone, which the presence of the glyph and status word asserts.
5. **Version-gated refetch** (D-3). Rendering with the same `graphVersion` across 3 prop updates
   calls `api.runGraph` exactly once. Changing `graphVersion` triggers exactly one more call.
   Changing only `tasks` stats triggers **no** refetch and **no** `computeLayout` call (spy).
6. **Feature detection and lazy load.**
   - With `graph_version: null` or absent, there is no Graph tab.
   - The Table tab's existing `run-detail.test.tsx` passes **unchanged** (FR-8).
   - `npm run build` emits a separate chunk containing `@xyflow/react`, and the main chunk delta is
     ≤ 5 KB gzip (record the numbers in STATUS).
7. **Themes.** Both light and dark define every new CSS token (a grep test for each token under both
   theme blocks). There are no hardcoded hex colors in `ui/src/graph/*.tsx`.
8. `npm run typecheck`, `npm test`, and `npm run build` pass. The committed `static/` is regenerated.

## Risks
- First React Flow integration for the team. The 2 h spike happens in T-adVpTj.
- jsdom edge rendering is unreliable. Assert edges at the model/prop level, and in the browser in T-F1caAt.

## Dependencies
- `T-adVpTj` (model, layout, types, shims, fixture). It works against the fixture, and the live API
  (T-AsQ77e) is needed only for manual checks.

## Pseudocode / Algorithm
```text
RunGraph({runId, tasks, graphVersion}):
  [graph, setGraph] = state(null); lastVersion = ref(null)
  effect([graphVersion]): IF graphVersion && graphVersion !== lastVersion.current:
       api.runGraph(runId).then(g => { lastVersion.current = g.graph_version; setGraph(g) }).catch(showError)
  viewNodes = memo(joinNodes(graph, tasks), [graph, tasks])
  {visible} = memo(nodesForView(viewNodes, edgesForView(graph, prefs.view), prefs.view, prefs.showUnrelated))
  layout = useAsyncMemo(() => computeLayout(visibleTopology, edges, prefs.view), [graph?.graph_version, prefs.view, prefs.showUnrelated])
  rfNodes = visible.map(n => ({id: n.id, type: "task", position: layout.positions.get(n.id), data: {node: n, direction: layout.direction}, width: NODE_WIDTH, height: NODE_HEIGHT}))
  RETURN <ViewToggle/> <ReactFlow nodes={rfNodes} edges={toRfEdges(edges)} .../>
```

## Schemas / Interface Notes
- Interface: `RunGraph` props `{runId: string; tasks: TaskStat[]; graphVersion: string}`.
  `TaskNode` data `{node: ViewNode; direction: "LR"|"TB"}`.
- Spec / data schema: consumes HLD §14.2 via `model.ts` view-models only.
- Triggers / events: N/A. Artifacts: rebuilt `src/agent_orchestrator/ui/static/`.

## Handoff Boundary
- Upstream: `T-adVpTj`.
- Downstream: `T-aHktGB` adds toolbar extras, legend, and banners into `RunGraph`. `T-pAi0Cv`
  adds hover and panel on node events (`onNodeClick`, `onNodeMouseEnter`, focus).

## Artifacts
- Docs/comments: `meta/tickets/E-k3AMEr-run-graph-canvas/T-OjTS8O-run-graph-canvas/`
- Large outputs: N/A
