# TASK: T-aHktGB-graph-toolbar-and-legend

## Metadata
- Task ID: `T-aHktGB-graph-toolbar-and-legend`
- Epic ID: `E-k3AMEr-run-graph-canvas`
- Owner: `developer` (Dev B, frontend)
- Created: `2026-09-27`
- Last Updated: `2026-09-27`
- Status: `Done`
- Estimate: `12 focus hours (1.5 days)`

## Requirements Mapping
- Requirement IDs: FR-5 (toolbar parts), FR-7, D-1, D-4, D-7, D-8 · HLD §8.6 (toolbar, legend, banners)

## Description
This task was split out of the canvas task (manager review). It adds the controls that make a
160-node canvas legible, plus the degraded-mode communication:

1. `ui/src/graph/GraphToolbar.tsx`, placed after the view toggle from T-OjTS8O:
   - a **"Show unrelated tasks"** checkbox (spawn view only), labeled with `hiddenCount`
   - a **metric select** (None / Duration / Cost, default Duration), which drives each node's metric strip via `metricFraction`
   - a **search input**. Enter centers the first match (`setCenter` with zoom = max(current, 1))
     and selects it, and ↑/↓ cycle through matches. "n of m" is shown. A match hidden by the
     unrelated filter auto-enables the filter.
   - **Fit** and **Reset layout** buttons (reset discards dragged positions and re-runs layout)

   Every toggle is persisted via `writePrefs`.
2. `ui/src/graph/Legend.tsx`:
   - a collapsible card showing the edge styles **for the current view**
   - node badges with their `BADGE_LABELS` text (the same map used for aria in T-OjTS8O)
   - status glyphs
   - source notes: `source`, `spawn_data`, "spec changed during run" (derived from `warnings`/API
     fields), and `truncated`
3. **Degraded banners.** One line per `warnings[]` entry, rendered above the canvas **as text**. An
   unsupported `schema_version` (≠ 1) shows "unsupported graph schema" and no canvas.

## Acceptance Criteria
1. **Unrelated filter.** In the spawn view with the fixture (3 static tasks that have no spawn
   relation), those 3 nodes are hidden by default, and the checkbox label says "Show 3 unrelated
   tasks". Checking it shows them. The checkbox is not rendered in the dependency view.
2. **Metric.** With "Cost", the node for the most expensive task has strip width 100% and a
   zero-cost task has 0%. With "None", no strip element renders. The test uses fixture stats.
3. **Search.**
   - Typing `u` and pressing Enter selects and centers the first match in stable order (a
     `setCenter` spy receives that node's center).
   - ArrowDown moves to the 2nd match, and "2 of N" is displayed.
   - An empty query shows no count.
   - A match hidden by the unrelated filter turns the filter on and then centers.
4. **Fit and Reset.** Fit calls `fitView`. After a (simulated) drag changes a node's position,
   Reset restores the `computeLayout` position.
5. **Banners** (FR-7, D-4). `warnings: ["<b>x</b>", "second"]` renders two banner lines, with the
   first showing the literal `<b>x</b>` and no `b` element. `schema_version: 2` renders "unsupported
   graph schema" and no React Flow container.
6. **Legend.** In the dependency view it lists explicit, inferred, and loop edge styles. In the
   spawn view it lists injected and loop spawn styles. With `source="unavailable"` it shows the
   note "static dependencies unavailable".
7. **Keyboard** (D-8). Every toolbar control is reachable by Tab in visual order and operable by
   keyboard, and each has an accessible name (RTL `getByRole` queries succeed for each).
8. `npm run typecheck`, `npm test`, and `npm run build` pass. `static/` is regenerated and committed.

## Risks
- `setCenter` is not observable in jsdom. Mock `useReactFlow` and assert the spy arguments.

## Dependencies
- `T-OjTS8O` (canvas core), `T-adVpTj` (model functions).

## Pseudocode / Algorithm
```text
onSearchEnter(): matches = searchNodes(allViewNodes, query); IF none: RETURN
  target = matches[idx]; IF hidden(target) AND view=="spawn": setShowUnrelated(true); await relayout
  pos = layout.positions.get(target); setCenter(pos.x + NODE_WIDTH/2, pos.y + NODE_HEIGHT/2, {zoom: max(getZoom(), 1)})
  select(target)
```

## Schemas / Interface Notes
- Interface: `GraphToolbar` props `{view, showUnrelated, hiddenCount, metric, onChange..., onSearch, onFit, onReset}`.
  `Legend` props `{view, source, spawnData, truncated, warnings}`.
- Spec / data schema: none new. Triggers / events: N/A. Artifacts: rebuilt `static/`.

## Handoff Boundary
- Upstream: `T-OjTS8O`.
- Downstream: `T-pAi0Cv` reuses `selectAndCenter`, which this task extracts as a shared hook
  `useSelectAndCenter` in `ui/src/graph/hooks.ts`.

## Artifacts
- Docs/comments: `meta/tickets/E-k3AMEr-run-graph-canvas/T-aHktGB-graph-toolbar-and-legend/`
- Large outputs: N/A
