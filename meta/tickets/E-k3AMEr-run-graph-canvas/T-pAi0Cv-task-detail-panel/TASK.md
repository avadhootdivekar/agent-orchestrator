# TASK: T-pAi0Cv-task-detail-panel

## Metadata
- Task ID: `T-pAi0Cv-task-detail-panel`
- Epic ID: `E-k3AMEr-run-graph-canvas`
- Owner: `developer` (Dev B, frontend)
- Created: `2026-09-27`
- Last Updated: `2026-09-27`
- Status: `Done`
- Estimate: `16 focus hours (2 days)`

## Requirements Mapping
- Requirement IDs: FR-6, U-7, D-5, D-8 · HLD §8.7 · ADR-0017 D6

## Description
Build the detail reveal. It combines a **hover preview card** with a **click-to-pin side panel**
(ADR-0017 D6: hover-only fails at 160-node density, on touch, and for keyboard users).

1. `ui/src/graph/TaskHoverCard.tsx`: a React Flow `<NodeToolbar isVisible>` next to the node, not
   scaled by zoom.
   - It opens after `HOVER_OPEN_DELAY_MS` on pointer hover **or keyboard focus** and closes after
     `HOVER_CLOSE_DELAY_MS`. It is suppressed while panning or dragging.
   - Content: full `label` (wrapping), status chip, duration, cost, attempts, retries
     (`max(0, attempts − 1)`), and a "Click for details" hint.
2. `ui/src/graph/TaskDetailPanel.tsx`: `<aside role="complementary" aria-label="Task details">`,
   360 px on the right, and a bottom sheet under 720 px viewport width.
   - It opens on node click or Enter on a focused node. It closes on ×, Esc, or a click on empty
     canvas. Focus moves into the panel on open and returns to the node on close.
   - Sections and fields exactly per the HLD §8.7 table: Header, Timing (including **wait before
     start** via `waitSeconds` and "started #n (latest dispatch)"), Usage, Retries (attempts in the
     last dispatch, retries, dispatches = `dispatch_cycle`), Spawn (parent link, children links with
     the first 20 plus "show all", loop id and iteration), Dependencies (depends-on and dependents
     links with status glyphs), and Outcome (`not_taken_reason`, integration status, tier, and
     conflicts, output artifact path with a copy button, and outputs).
   - Show "Stats pending" when `stat === null`. Show "hidden characters removed" when
     `label_sanitized`. All values use the existing `format.ts` helpers.
3. **Navigation.** Every related-task link calls the shared `useSelectAndCenter(id)` (from T-aHktGB).
   It works across views, and in the spawn view it auto-enables "show unrelated" when needed.

## Acceptance Criteria
1. **Hover card.**
   - With fake timers, hovering node `u1` shows no card before 250 ms and shows it at ≥ 250 ms.
     The card contains `formatDuration`/`formatCost` of `u1`'s fixture stats and "retries: 1" when
     `attempts == 2`. It closes 150 ms after leave.
   - Focusing the node via keyboard also opens it.
2. **Panel.**
   - Clicking `u1` opens the panel with a header label of `u1`.
   - Timing shows the exact `formatDuration` of `duration_seconds` and a wait-before-start equal to
     `waitSeconds` for the fixture (a precomputed expected string).
   - Usage shows cost and tokens.
   - Retries shows attempts, retries, and dispatches.
   - Spawn shows "parent: cp1".
   - Dependencies lists `cp1` under "depends on" and `cp2` under "dependents".
3. **Close and focus.** Esc closes the panel and returns focus to the `u1` node. The × button
   closes. Clicking empty canvas (`onPaneClick`) closes.
4. **Navigation.** Clicking the "cp1" parent link selects `cp1` (panel header becomes `cp1`) and
   calls the `setCenter` spy with `cp1`'s center. It works in both views.
5. **Children cap.** An emitter with 45 children shows 20 links and "show all (45)". Clicking it
   shows all 45.
6. **Degraded data.**
   - `stat === null` → "Stats pending", with no crash.
   - `started_at` null → wait shows "—".
   - A node with `missing=true` shows "Unknown task (referenced by a dependency but never defined)".
7. **Text-only** (D-5). A label with markup renders literally in the card and panel, and `grep
   dangerouslySetInnerHTML` in `ui/src/graph` is empty.
8. **Responsive.** At a jsdom viewport width of 600 the panel has the bottom-sheet class. At 1200 it
   has the side class.
9. `npm run typecheck`, `npm test`, and `npm run build` pass. `static/` is regenerated and committed.

## Risks
- `NodeToolbar` positioning in jsdom is meaningless. Assert visibility and content, not position.
  Position is verified in the T-F1caAt screenshot.

## Dependencies
- `T-OjTS8O` (node events), `T-aHktGB` (`useSelectAndCenter`), and `T-adVpTj` (`relatedIds`, `waitSeconds`).

## Pseudocode / Algorithm
```text
panelModel(id, graph, statsById):
  node = graph.nodes[id]; stat = statsById.get(id) ?? null; rel = relatedIds(graph, id)
  RETURN { header: {label: node.label, sanitized: node.label_sanitized, status: stat?.status, origin: node.origin, route: node.route},
           timing: {started: stat?.started_at, ended: stat?.ended_at, duration: stat?.duration_seconds,
                    wait: waitSeconds(id, graph, statsById), ordinal: node.exec_ordinal},
           usage: {...}, retries: {attempts: stat?.attempts, retries: max(0,(stat?.attempts??0)-1), dispatches: stat?.dispatch_cycle},
           spawn: {parent: rel.parent, children: rel.children, loop: [node.loop_id, node.iteration]},
           deps: {dependsOn: rel.dependsOn, dependents: rel.dependents}, outcome: {...} }
```
Keep `panelModel` pure in `model.ts`, so it is unit-testable without rendering.

## Schemas / Interface Notes
- Interface: `TaskDetailPanel` props `{nodeId: string | null; graph: RunGraph; statsById: Map; onClose; onNavigate}`.
- Spec / data schema: none new. Triggers / events: N/A. Artifacts: rebuilt `static/`.

## Handoff Boundary
- Upstream: `T-OjTS8O`, `T-aHktGB`.
- Downstream: `T-F1caAt` screenshots the panel open. Non-MVP `T-ydMbJN` reuses `waitSeconds`.

## Artifacts
- Docs/comments: `meta/tickets/E-k3AMEr-run-graph-canvas/T-pAi0Cv-task-detail-panel/`
- Large outputs: N/A
