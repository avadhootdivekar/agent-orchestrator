# STATUS

- ID: `T-OjTS8O-run-graph-canvas`
- Updated At: `2026-09-27`
- State: `Done`
- Owner: `Dev B (developer)`
- Scope: `MVP` · Sprint: `S1` · Estimate: `14 h`

## This update
- By: architect · Role: architect · Date: 2026-09-27 · Comment: Task created as part of the
  E-k3AMEr design package (design + tickets only; **no code written**). Acceptance criteria,
  pseudocode, and interfaces are in `TASK.md`, and the authoritative design is
  `docs-md/run-graph-canvas-hld.md`. Hardened by the Phase-4 consultations (HLD §23.1).
- By: developer · Role: developer · Date: 2026-09-27 · Comment: Task **Done**. Fast-forwarded
  onto `ad/run-graph-canvas` (T-adVpTj's merged foundation) first, then built the canvas core:
  `ui/src/graph/RunGraph.tsx` (React Flow provider/canvas, view toggle, fetch/refetch lifecycle,
  async layout with stale-result discarding, edge styling), `ui/src/graph/TaskNode.tsx`
  (memoized node renderer + `BADGE_LABELS`), and the Table|Graph switch in
  `ui/src/components/RunDetail.tsx`. All 8 acceptance criteria pass with evidence below.
  `ui/src/graph/model.ts`/`layout.ts`/`ui/src/types.ts`/`ui/src/api.ts` untouched (consumed only);
  no Python file touched.

## Evidence

### AC-1 — View toggle
- `ui/src/test/run-graph.test.tsx`, describe block "RunGraph view toggle (AC-1)", 3 tests:
  - Initial render: `role="radiogroup"` with "Execution order" `aria-checked="true"`,
    "Spawned by" `aria-checked="false"`; the `edges` array captured from a mocked `ReactFlow`
    (its `nodes`/`edges` props are captured via `vi.mock("@xyflow/react", ...)`, per TASK.md's
    own R-3 note that jsdom edge-path DOM is unreliable) matches `edgesForView(fixture,
    "dependency")` by id, sorted.
  - `ArrowRight` (via `userEvent.keyboard` after focusing the "Execution order" radio) switches
    to "Spawned by" (`aria-checked="true"`) and `readPrefs().view === "spawn"` afterwards.
  - Clicking "Spawned by" switches the view and the captured edge model to
    `edgesForView(fixture, "spawn")`.

### AC-2 — Node label only (U-6)
- `ui/src/test/task-node.test.tsx` "AC-2": for a node with `label: "cp1"` and a stat carrying
  `duration_seconds: 42`/`cost_usd: 1.23`, `.task-node-label` text content is exactly `"cp1"`,
  and the whole rendered container's text contains neither `"$"` nor `"42s"` — confirmed no
  metric strip/cost/duration text exists in the node body (see Deviations #1: the metric strip
  itself is out of this task's scope).

### AC-3 — Text-only rendering (D-5)
- `ui/src/test/task-node.test.tsx` "AC-3/D-5": a node labeled `"<img src=x onerror=alert(1)>"`
  renders that exact string as `.task-node-label`'s `textContent`, and
  `container.querySelector("img")` is `null`.
- `grep -r dangerouslySetInnerHTML ui/src/graph/` → **empty** (exit code `1`, no matches).

### AC-4 — Accessibility (D-7)
- Design correction made during implementation: React Flow's own node wrapper (the actually
  focusable element) defaults to `role="group"` and reads its accessible name from
  `Node.ariaLabel` — **not** from anything inside the custom node component. The first draft set
  `aria-label` on `TaskNode`'s own inner div, which would have left the real focusable element
  unlabeled (a nested, separately-labeled inner group instead). Fixed by exporting
  `accessibleNodeName(node)` from `TaskNode.tsx` and setting it as `ariaLabel` on each React
  Flow node object in `RunGraph.tsx`'s `rfNodes` builder.
- `ui/src/test/task-node.test.tsx`, 8 tests reading `aria-label` off the rendered
  `.react-flow__node` wrapper (`rfNodeWrapper` helper): label + status word + injected/loop-
  iteration/emitter/router/missing/unknown-origin badge text all present; `failed` status
  confirmed present as the literal word (never color-only) alongside a non-empty status glyph;
  no trailing `", "` artifact when a node has no badges.
- `grep -nE "#[0-9a-fA-F]{3,8}\b" ui/src/graph/*.tsx` → **empty** (no hardcoded hex in the
  graph component files).

### AC-5 — Version-gated refetch (D-3)
- `ui/src/test/run-graph.test.tsx`, describe block "RunGraph refetch gating (AC-5)", 3 tests,
  each spying on `api.runGraph`:
  - 3 rerenders with the same `graphVersion="v1"` → `api.runGraph` called **exactly once**.
  - Changing `graphVersion` to `"v2"` → **exactly one more** call (2 total).
  - Rerendering with only `tasks` changed (same `graphVersion`) → `api.runGraph` still called
    exactly once (no refetch), **and** `computeLayout` (spied via
    `vi.spyOn(layoutModule, "computeLayout")`, imported as a namespace) has the same call count
    before/after. A sanity assertion (`layoutCallsAfterMount` > 0) confirms the spy actually
    intercepted the mount-time call, so the "unchanged count" assertion isn't vacuous. A second,
    independent signal — the node's `position` object reference from the captured `ReactFlow`
    props is `Object.is`-identical before and after the tasks-only rerender, since it's read
    from the same, unreplaced `layout.positions` `Map` — corroborates the spy result.

### AC-6 — Feature detection, lazy load, bundle
- `ui/src/test/run-detail-graph-tab.test.tsx`, 3 tests:
  - `graph_version: null` → no `role="tablist"` named "Tasks view"; the plain table (with
    column headers) renders exactly as before.
  - `graph_version` present → tablist renders, "Table" tab is `aria-selected="true"` by default.
  - Clicking "Graph" lazily mounts `RunGraph` (dynamic `import()`, not mocked in this file) and
    triggers the first `api.runGraph("run-1")` call only then (`not.toHaveBeenCalled()` before
    the click); `readPrefs().tab === "graph"` afterwards; column headers are gone while Graph is
    active and reappear on switching back to Table (`readPrefs().tab === "table"`).
  - `ui/src/test/run-detail.test.tsx` (T-adVpTj's pre-existing suite, unmodified) — **passes
    unchanged**: its fixtures use `graph_version: null`, so it exercises exactly the
    pre-existing table code path.
- `npm run build` (from a clean `npm ci`), measured against an independently-built "before"
  baseline (a throwaway `git worktree add --detach` at this task's start commit `301457b`,
  built once, then removed — no build output from it is part of this commit):
  - Before: main JS `119.94 KB` gzip (matches T-adVpTj's own recorded number exactly), main CSS
    `2.90 KB` gzip.
  - After: main JS `121.26 KB` gzip, main CSS `3.56 KB` gzip. **Main-chunk JS delta ≈ +1.32 KB
    gzip** — well under the ≤ 5 KB budget. (CSS delta is +0.66 KB, from the new graph theme
    tokens added to `styles.css`; AC-6's gzip cap is stated for "the main chunk" and tracked
    against the sibling task's own JS-only baseline, so this is recorded for completeness
    rather than as a second gate.)
  - A **separate** `RunGraph-*.js` chunk (`75.48 KB` gzip) plus its own `RunGraph-*.css` chunk
    (`2.03 KB` gzip, the `@xyflow/react/dist/base.css` import) confirms `@xyflow/react`/
    `@dagrejs/dagre` and this task's own graph code load only when `RunGraph` is dynamically
    imported — the initial dashboard load is unaffected. Total added (`75.48 + 2.03 = 77.51 KB`
    gzip) stays under T-adVpTj's informational `≤ 90 KB` NFR-4 estimate, and is almost identical
    to T-adVpTj's own spike measurement (`75.48 KB`/`2.03 KB`) — the real `RunGraph.tsx`/
    `TaskNode.tsx` code turned out to add negligible size on top of the library floor.

### AC-7 — Themes / no hardcoded hex
- Three new CSS custom properties added to `ui/src/styles.css`: `--graph-edge-dependency`,
  `--graph-edge-loop`, `--graph-edge-spawn`. `grep -n -- "--graph-edge-<name>:" ui/src/styles.css`
  for each shows exactly 3 matches — the light `:root` block, the `@media (prefers-color-scheme:
  dark)` block, and the `:root[data-theme="dark"]` block — confirming both light and dark
  (both dark mechanisms) explicitly define every new token.
- `--graph-edge-dependency` aliases `var(--text-secondary)` in all three blocks (no distinct hue
  needed — it's the neutral default line color, already theme-aware via the token it aliases).
  `--graph-edge-loop`/`--graph-edge-spawn` get their own violet/teal hues, distinct light vs.
  dark shades for contrast, matching the existing `--accent`/`--status-*` pattern.
  A dimming opacity value (`0.5`, used by `.task-node--dimmed`) is a plain, non-themed CSS value
  defined once, following the SAME existing convention as `--radius`/`--font-sans` (structural
  constants that are not redefined per theme) — not counted as one of the "new tokens" requiring
  dual-block definition, since it carries no color.
- `grep -nE "#[0-9a-fA-F]{3,8}\b" ui/src/graph/*.tsx` → empty (component files use CSS
  classes/tokens only, per `ui/README.md`).

### AC-8 — Build/test/typecheck
- `npm run typecheck` (`tsc -b --noEmit`) → clean.
- `npm test` → **151 passed, 0 failed, 14 test files** (11 pre-existing + 3 new:
  `task-node.test.tsx` 13 tests, `run-graph.test.tsx` 6 tests, `run-detail-graph-tab.test.tsx` 3
  tests). Pre-existing suite was 129 tests across 11 files (T-adVpTj's baseline) — all still
  green, confirming no regression.
- `npm run build` → exit `0`; `src/agent_orchestrator/ui/static/` regenerated and committed.

## Deviations from TASK.md (flagged, not silent)
1. **No bottom metric strip on `TaskNode`.** HLD §8.6's node bullet list includes a "bottom 3 px
   metric strip, width proportional to `metricFraction`", but `TASK.md`'s own Description item 3
   bullet list for `TaskNode` — the actual acceptance bar — omits it, and neither AC-2 nor the
   pseudocode's `rfNodes` builder (§ "Pseudocode / Algorithm") references `metricFraction`/
   `MetricMaxima` at all. The metric mode that would drive it (`None`/`Duration`/`Cost` select)
   is explicitly `T-aHktGB`'s "Metric select" toolbar item. Building a strip with no way to pick
   a mode would always render nothing (mode defaults to `"none"`, so a mode-less strip has
   nothing to be proportional to) — omitted entirely rather than half-built, mirroring
   T-adVpTj's own precedent of leaving `displayText` out of its scope for the same reason
   (present in the HLD's function list, absent from the task's own AC list). Flagging so
   `T-aHktGB` knows to add both the selector and the strip together.
2. **Edge dimming from a failed/`not_taken` upstream task, and selection-based edge emphasis/
   dimming to 35%, are not implemented.** Both appear in HLD §8.6's "Edges" prose immediately
   *after* the styling table, not in the table itself — `TASK.md` item 4 scopes this task to
   "Edge styles per the HLD §8.6 **table**", which is exactly the 5 kind/set rows implemented
   here (plus a defensive "unknown spawn origin" fallback, by direct analogy with the node's own
   mandated unknown-origin badge). Selection-based emphasis also depends on interaction state
   that belongs to `T-pAi0Cv`'s hover/click work (`onNodeClick` is exposed for exactly this).
3. **Arrow markers added uniformly to every edge kind**, not only the two rows the table calls
   out ("arrow at target"/"arrow at child") — a small, low-risk visual superset for directional
   clarity, using one `defaultMarkerColor` (not per-kind-colored markers, to avoid embedding a
   `var(--…)` CSS reference inside a generated SVG `<marker>` id string).
4. **The inferred-dependency edge's truncated artifact path renders as the same on-path,
   zoom-gated text label used by the other rows**, not a literal native SVG `<title>` tooltip.
   The HLD table's "tooltip title" phrasing reads as a description of a secondary annotation
   rather than a mandate for the literal HTML `title` mechanism (which would need a bespoke
   custom edge component); one mechanism for the whole "Label" column keeps this simple and
   isn't AC-tested either way.
5. **`showUnrelated`/metric/search/fit/reset controls are not built** — explicitly `T-aHktGB`
   scope per the change boundary. The underlying plumbing IS wired per the pseudocode
   (`prefs.showUnrelated` already threads through `nodesForView`/`computeLayout`), so `T-aHktGB`
   only needs to add the checkbox itself, not any new state.

## Risks / Blockers
- Blockers: none. Task complete.
- The R-3 risk (jsdom edge-path DOM unreliability) was real: `run-graph.test.tsx` asserts the
  edge *model* via a mocked `ReactFlow`'s captured props, not rendered SVG — the real-browser
  edge check is `T-F1caAt`'s job, unchanged from the original risk note.
- `useOnViewportChange`'s `onChange` callback re-renders `RunGraphCanvas` only when
  `showEdgeLabels` actually flips across the `EDGE_LABEL_MIN_ZOOM` threshold (a functional
  `setState` bail-out compares old/new booleans by value), not on every pan/zoom tick — kept
  deliberately cheap for the pan/zoom ≥ 30 fps NFR, but this wasn't measured in a real browser
  here (jsdom has no real paint loop); `T-F1caAt`'s manual/browser smoke test is the right place
  to confirm on a 160+ node graph.
- Carried forward for `T-aHktGB`/`T-pAi0Cv`: `RunGraph.tsx` exposes `onNodeClick`/
  `onNodeMouseEnter` props and tracks `selectedNodeId` internally (recentered across a view
  toggle); the toolbar/legend/banners/hover-card/detail-panel tasks should extend
  `RunGraphCanvas`'s existing state rather than re-deriving selection separately.

## Next actions
- None for this task.
- Downstream: `T-aHktGB-graph-toolbar-and-legend` adds the toolbar extras (search, metric
  select + the metric strip deferred in Deviation #1, "show unrelated" checkbox, fit/reset) and
  the legend (reusing `TaskNode.tsx`'s exported `BADGE_LABELS`) plus degraded banners (from
  `graph.warnings[]`, not yet surfaced). `T-pAi0Cv-task-detail-panel` wires `onNodeClick`/
  `onNodeMouseEnter` into a hover card and pinned side panel, and should add `displayText` to
  `model.ts` when it needs it (T-adVpTj's own carried-forward note). `T-F1caAt` is the real-
  browser smoke test that covers what jsdom can't (edge rendering, 30 fps pan/zoom on a large
  graph, the real backend-served CSP header).
