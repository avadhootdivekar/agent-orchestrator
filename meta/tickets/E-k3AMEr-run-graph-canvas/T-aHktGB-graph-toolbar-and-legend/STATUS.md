# STATUS

- ID: `T-aHktGB-graph-toolbar-and-legend`
- Updated At: `2026-09-27`
- State: `Done`
- Owner: `Dev B (developer)`
- Scope: `MVP` · Sprint: `S2` · Estimate: `12 h`

## This update
- By: architect · Role: architect · Date: 2026-09-27 · Comment: Task created as part of the
  E-k3AMEr design package (design + tickets only; **no code written**). Acceptance criteria,
  pseudocode, and interfaces are in `TASK.md`, and the authoritative design is
  `docs-md/run-graph-canvas-hld.md`. Hardened by the Phase-4 consultations (HLD §23.1).
- By: developer · Role: developer · Date: 2026-09-27 · Comment: Task **Done**. Worktree started
  behind `ad/run-graph-canvas` (missing T-adVpTj/T-OjTS8O's merged foundation) — fast-forwarded
  with `git merge --ff-only ad/run-graph-canvas` first, per the dispatch note, before touching
  anything. Built `ui/src/graph/GraphToolbar.tsx` (unrelated-tasks checkbox, metric select,
  search-to-focus with keyboard cycling), `ui/src/graph/Legend.tsx` (collapsible edge/badge/
  status/source-notes card), `ui/src/graph/hooks.ts` (`useSelectAndCenter`, extracted for
  `T-pAi0Cv` reuse), and extended `ui/src/graph/RunGraph.tsx` (wiring, degraded banners,
  unsupported-schema hard stop, the metric strip via a `TaskNodeWithMetric` wrapper, and
  drag/reset position tracking) plus `ui/src/styles.css` (toolbar/legend/banner/metric-strip
  CSS, reusing existing theme tokens only — no new tokens needed). `model.ts`/`layout.ts`/
  `TaskNode.tsx`/`types.ts`/`api.ts` untouched (consumed only); no Python file touched; no
  live backend endpoint exists yet (built/tested entirely against
  `ui/src/test/fixtures/run-graph.json` and a mocked `api.runGraph`, matching T-OjTS8O's own
  approach). All 8 acceptance criteria pass with evidence below.

## Evidence

### AC-1 — Unrelated filter
- `ui/src/test/run-graph-toolbar.test.tsx`, describe "RunGraph unrelated filter (AC-1)":
  in the spawn view, the 3 fixture nodes with no spawn edge (`triage`, `dev`, `missing-dep`)
  are absent from the captured React-Flow `nodes` prop by default, the checkbox reads
  "Show 3 unrelated tasks", and checking it makes all 3 reappear. A second test confirms the
  checkbox does not render at all in the dependency view.
- `ui/src/test/graph-toolbar.test.tsx` covers the checkbox in isolation: label text for
  `hiddenCount` 3 and 1 (singular "task"), absence in the dependency view, and that toggling
  reports the new boolean via `onShowUnrelatedChange`.

### AC-2 — Metric
- `ui/src/test/run-graph-toolbar.test.tsx`, describe "RunGraph metric strip (AC-2)": with the
  fixture's `cp1` given the highest `cost_usd` and `triage` given `0`, selecting "Cost" drives
  `cp1`'s captured node `data.metricFraction` to exactly `1` and `triage`'s to exactly `0`
  (both via the real `metricFraction` pure function, maxima computed over the **visible** node
  set). Selecting "None" drives every visible node's `metricFraction` to `null`.
- The strip itself is a new `TaskNodeWithMetric` wrapper in `RunGraph.tsx` composed **around**
  the untouched `TaskNode` (a sibling `<span className="task-node-metric-strip">`, width set
  from `metricFraction`, rendered only when it isn't `null`) — see Deviations below for why a
  wrapper rather than a `TaskNode.tsx` edit.

### AC-3 — Search
- `ui/src/test/run-graph-toolbar.test.tsx`, describe "RunGraph search-to-focus (AC-3)":
  - Typing `u1` (the fixture's only id/label containing it) and pressing Enter calls the
    mocked `useReactFlow().setCenter` exactly once, with `(node.position.x + NODE_WIDTH/2,
    node.position.y + NODE_HEIGHT/2, {zoom: 1})` — asserted against the SAME position React
    Flow was actually given (not a hardcoded dagre coordinate) — and the node's captured
    `selected` flag is `true`.
  - In the spawn view, searching `triage` (hidden by the unrelated filter) and pressing Enter
    checks the "Show unrelated" checkbox, then centers on `triage`'s real (post-relayout)
    position and selects it — exercising the full defer-until-relayout path.
- `ui/src/test/graph-toolbar.test.tsx` covers the cycling/display contract directly against
  `GraphToolbar` with a 2-match fixture: Enter selects match 1 ("1 of 2"), `ArrowDown` cycles to
  match 2 ("2 of 2") and wraps back to match 1, `ArrowUp` cycles backwards, an empty query shows
  no count, and a query with zero matches shows no count and makes Enter a no-op.
- `ui/src/test/graph-hooks.test.ts` unit-tests the extracted `useSelectAndCenter` hook itself
  (5 tests) with `useReactFlow` mocked per the TASK.md Risk note: immediate center, respecting a
  zoom already above the floor, a no-op for an id absent from the layout, the full
  defer-then-center sequence once a hidden id's relayout lands, and no spurious re-center on an
  unrelated layout update.

### AC-4 — Fit and Reset
- `ui/src/test/run-graph-toolbar.test.tsx`, describe "RunGraph fit and reset (AC-4)": clicking
  "Fit" calls the mocked `fitView` exactly once. A simulated drag (invoking the captured
  `onNodesChange` with a `NodePositionChange` for `cp1`) moves its captured position; clicking
  "Reset layout" restores it to the ORIGINAL `computeLayout` position.
- Implementation: dragged offsets are tracked in a `draggedPositions` state map (populated only
  from `onNodesChange`'s `type: "position"` entries) that `rfNodes` prefers over
  `layout.positions`; "Reset" simply clears the map rather than re-invoking `computeLayout`
  (see Deviations). The map is also cleared on a view toggle, since a dagre pass in the other
  direction places the same node id somewhere unrelated to any prior drag offset.
- `ui/src/test/graph-toolbar.test.tsx` covers the Fit/Reset buttons' own click→callback wiring
  in isolation.

### AC-5 — Banners (FR-7, D-4)
- `ui/src/test/graph-banners.test.tsx`, 4 tests:
  - `warnings: ["<b>x</b>", "second"]` renders two banner lines; `screen.getByText("<b>x</b>")`
    finds the FIRST warning as a literal string, and `container.querySelector("b")` is `null`
    (confirmed via a DOM query, not snapshot text, per the task's own instruction).
  - An empty `warnings[]` renders no `.graph-banners` container at all.
  - `schema_version: 2` renders "unsupported graph schema" (case-insensitive match) and
    `screen.queryByTestId("rf-stub")` (the mocked `<ReactFlow>`) is `null` — and, further, the
    view-toggle radiogroup and the Fit button are ALSO absent, confirming the hard stop replaces
    the whole toolbar/canvas/legend, not just the canvas.
  - `schema_version: 1` (the supported value) renders the canvas normally with no such message.
- Implementation: `isSupportedSchema` gates the view-model memos (`viewNodes`/`edges`) and the
  layout effect too, not just the render branch — a mismatched payload is never joined, edged,
  or laid out, only detected and reported.

### AC-6 — Legend
- `ui/src/test/graph-legend.test.tsx`, 11 tests: the dependency view's Edges section lists
  exactly 3 rows (explicit/inferred/loop, scoped via `within()` on that `<section>` so the
  node-badge section's own "loop iteration" text can't create a false positive); the spawn view
  lists exactly 2 (injected/loop); `source="unavailable"` shows "static dependencies unavailable"
  (and `"snapshot"` does not); `spawn_data="not_recorded"` shows its note, `"none"` shows
  nothing; a `warnings[]` entry matching `/spec changed/i` is surfaced **verbatim** (not a
  separately hand-written string) while an unrelated warning is not; `truncated` gates its own
  note; node-badge rows reuse `BADGE_LABELS` text directly (`accessibleNodeName`'s own source of
  truth — see `TaskNode.tsx`); all 8 known statuses render via the existing `StatusChip` (D-7:
  glyph + word, never color alone); the collapse toggle hides/shows the body and flips
  `aria-expanded`.

### AC-7 — Keyboard (D-8)
- `ui/src/test/run-graph-toolbar.test.tsx`, describe "RunGraph toolbar keyboard reachability
  (AC-7)": starting from the checked "Spawned by" radio (the radiogroup's one Tab stop, per the
  existing ARIA APG roving-tabindex pattern), successive `Tab`s land on the checkbox, the metric
  `<select>`, the search `<input>`, "Fit", then "Reset layout" — in that exact visual/DOM order,
  each found via an RTL `getByRole` query with its accessible name (never a CSS selector).
- `ui/src/test/graph-toolbar.test.tsx`'s own "AC-7" test independently confirms every control
  resolves via `getByRole` with a name in isolation (checkbox, combobox, textbox, both buttons).

### AC-8 — Build/test/typecheck
- `npm run typecheck` (`tsc -b --noEmit`) → clean.
- `npm test` → **192 passed, 0 failed, 19 test files.** Baseline (T-OjTS8O checkpoint) was
  **151 passed, 14 files** — all 151 still green (zero regressions), plus **41** new tests
  across 5 new files: `graph-hooks.test.ts` (5), `graph-toolbar.test.tsx` (12),
  `graph-legend.test.tsx` (11), `graph-banners.test.tsx` (4), `run-graph-toolbar.test.tsx` (9).
- `npm run build` → exit `0`. Bundle deltas vs. T-OjTS8O's own recorded "after" numbers:
  - Main chunk JS: `121.26 KB` → `121.40 KB` gzip (**+0.14 KB**, well under the ≤5 KB budget).
  - Main chunk CSS: `3.56 KB` → `3.94 KB` gzip (**+0.38 KB** — the new toolbar/legend/banner/
    metric-strip CSS in `styles.css`; this file isn't code-split, so it's part of the initial
    load regardless of whether the Graph tab is ever opened, same as T-OjTS8O's own edge-token
    CSS delta).
  - Lazy `RunGraph-*.js` chunk: `75.48 KB` → `77.71 KB` gzip (**+2.23 KB**, the new
    `GraphToolbar`/`Legend`/`hooks.ts` code, all inside the lazy chunk). Its CSS companion
    (`@xyflow/react/dist/base.css`) is unchanged at `2.03 KB` gzip.
  - Combined lazy total: `77.71 + 2.03 = 79.74 KB` gzip — still under T-adVpTj's informational
    `≤ 90 KB` NFR-4 estimate.
  - `src/agent_orchestrator/ui/static/` regenerated via `make ui-build` from the repo root and
    committed (deterministic rebuild: re-running produced byte-identical asset filenames).
- `grep -r dangerouslySetInnerHTML ui/src/graph/` → empty (exit `1`).
- `grep -nE "#[0-9a-fA-F]{3,8}\b" ui/src/graph/*.tsx ui/src/graph/*.ts` → empty (exit `1`) — no
  hardcoded hex in any graph component/hook file; `styles.css` additions are `var(--...)`
  references to EXISTING tokens (`--accent`, `--graph-edge-*`, `--text-*`, `--surface-*`,
  `--hairline`, `--radius*`) only — no new color tokens were needed, so no new dual-theme
  (light/dark) block was required either.

## Deviations from TASK.md (flagged, not silent)
1. **The metric strip is a NEW wrapper component (`TaskNodeWithMetric`) in `RunGraph.tsx`, not
   an edit to `TaskNode.tsx`.** The change boundary forbids touching `TaskNode.tsx`, and its own
   render has no slot for extra content. `TaskNodeWithMetric` renders `<TaskNode {...props} />`
   plus a sibling absolutely-positioned strip `<span>`, using a strict-superset `data` shape
   (`TaskNodeWithMetricData extends TaskNodeData`) so the spread stays type-safe. `NODE_TYPES`
   in `RunGraph.tsx` now points `"task"` at this wrapper instead of `TaskNode` directly — still
   only ever *consuming* `TaskNode`'s export, never editing it.
2. **Metric maxima are computed over the currently VISIBLE node set, not the full graph.** TASK.md
   doesn't specify this either way; using the full (possibly unrelated-filtered) set would let a
   hidden task's cost/duration cap the max, making the costliest ON-SCREEN task read as less than
   100% — judged less useful/more confusing than scoping maxima to what's actually shown.
3. **"Reset layout" clears the drag-override map rather than re-invoking `computeLayout`.**
   `computeLayout` is a pure function of the unchanged nodes/edges/view, so recomputing it would
   reproduce the exact positions already cached in `layout` — clearing the override map (which
   `rfNodes` already prefers over `layout.positions`) is the cheaper way to reach the identical,
   AC-mandated result (an async dagre re-run on every Reset click would only add latency/flicker
   for no different outcome).
4. **`useSelectAndCenter` was NOT retrofitted onto `RunGraph.tsx`'s existing view-toggle recenter
   effect** (the one T-OjTS8O already shipped, tested, and left carried-forward for this task to
   extend). That effect intentionally keeps the CURRENT zoom (`zoom: reactFlow.getZoom()`) when
   recentering after a view switch, while the hook's own policy is a zoom FLOOR of 1 (HLD §8.6
   item 4's explicit "zoom of at least 1") — two different policies sharing only the "wait for
   layout, then setCenter" mechanic, and unifying them into one parameterized hook seemed like
   more abstraction than the actual duplication (a few lines) justified, at the cost of touching
   already-tested T-OjTS8O code outside this task's stated scope. `useSelectAndCenter` is used
   for the search toolbar here, and is the one `T-pAi0Cv` should call for every detail-panel
   link — exactly the reuse TASK.md asked this extraction to enable.
5. **The Legend's collapse/expand state is local `useState`, not persisted via `writePrefs`.**
   TASK.md's "every toggle is persisted via `writePrefs`" instruction is under the `GraphToolbar`
   bullet specifically; `model.ts::GraphPrefs` (off-limits to edit) has no field for it, and nothing
   in the AC list tests persistence of the legend's own collapse state.

## Risks / Blockers
- Blockers: none. Task complete.
- Carried forward for `T-pAi0Cv`: `useSelectAndCenter` in `ui/src/graph/hooks.ts` is ready to
  reuse for every detail-panel link (parent/children/dependency/dependents) — call it with the
  same `isHidden`/`setShowUnrelated`/`onSelect` wiring `RunGraph.tsx` already assembles.
  `model.ts::displayText` (flagged as missing by both prior tasks) is still not implemented;
  `T-pAi0Cv` will need it for the panel's header.
- Not covered here (per the change boundary, `T-F1caAt`'s job): real-browser drag-and-drop
  physics, real pan/zoom interaction with the metric strip at various zoom levels, and the real
  backend-served CSP header. All of this task's own new behavior was verified against mocked
  `ReactFlow`/`useReactFlow` and the shared fixture, same as T-OjTS8O's own approach.

## Next actions
- None for this task.
- Downstream: `T-pAi0Cv-task-detail-panel` builds the hover card and pinned side panel on top
  of `RunGraph.tsx`'s `onNodeClick`/`onNodeMouseEnter` props and this task's
  `useSelectAndCenter` hook. `T-AsQ77e-run-graph-endpoint` is the real backend this frontend
  work has been building against a fixture for. `T-F1caAt` is the real-browser smoke test.
