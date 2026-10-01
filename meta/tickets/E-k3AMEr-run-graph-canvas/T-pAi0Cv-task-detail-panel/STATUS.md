# STATUS

- ID: `T-pAi0Cv-task-detail-panel`
- Updated At: `2026-09-27`
- State: `Done`
- Owner: `Dev B (developer)`
- Scope: `MVP` · Sprint: `S2` · Estimate: `16 h`

## This update
- By: architect · Role: architect · Date: 2026-09-27 · Comment: Task created as part of the
  E-k3AMEr design package (design + tickets only; **no code written**). Acceptance criteria,
  pseudocode, and interfaces are in `TASK.md`, and the authoritative design is
  `docs-md/run-graph-canvas-hld.md`. Hardened by the Phase-4 consultations (HLD §23.1).
- By: developer · Role: developer · Date: 2026-09-27 · Comment: Task **Done**. Worktree started
  behind `ad/run-graph-canvas` (missing T-adVpTj/T-OjTS8O/T-aHktGB's merged foundation) --
  fast-forwarded with `git merge --ff-only ad/run-graph-canvas` first, per the dispatch note,
  before touching anything. Built `ui/src/graph/TaskHoverCard.tsx` (a React Flow `NodeToolbar`),
  `ui/src/graph/TaskDetailPanel.tsx` (the pinned `<aside>`), added the pure `panelModel` function
  (plus its supporting `Panel*` types) to `ui/src/graph/model.ts`, and extended
  `ui/src/graph/RunGraph.tsx` with the hover open/close delay timers, keyboard focus/blur
  delegation, click/Enter-to-pin (reusing the existing `selectedNodeId`), Esc/×/pane-click close
  with focus-return, and a new `.graph-canvas-row` layout so the panel sits beside (or, per the
  responsive breakpoint, below) the canvas. `ui/src/styles.css` got new rules for both new
  components, reusing existing tokens only -- no new custom properties, so no new light/dark
  block was needed either (same as T-aHktGB's own precedent). `layout.ts`/`TaskNode.tsx`/
  `GraphToolbar.tsx`/`Legend.tsx`/`hooks.ts`/`types.ts`/`api.ts`/`format.ts`/`RunDetail.tsx`
  untouched (consumed only); no Python file touched; no live backend endpoint exists yet
  (built/tested entirely against `ui/src/test/fixtures/run-graph.json`, matching every prior
  frontend task in this epic). All 9 acceptance criteria pass with evidence below.

## Evidence

### AC-1 -- Hover card
- `ui/src/test/run-graph-detail-reveal.test.tsx`, describe "RunGraph hover card (AC-1)" (real,
  unmocked `<ReactFlow>` + `vi.useFakeTimers`): hovering the real `u1` node DOM element shows no
  `.task-hover-card` before `HOVER_OPEN_DELAY_MS - 1` ms, shows it at exactly `HOVER_OPEN_DELAY_MS`,
  with the card containing `formatDuration(240)`, `formatCost(1.5)`, and the literal text
  `"retries: 1"` for an `attempts: 2` stat; a `mouseleave` closes it exactly `HOVER_CLOSE_DELAY_MS`
  later (still open 1ms before, gone at the exact delay). A second test proves keyboard `.focus()`
  on the same node also opens the card and `.blur()` closes it.
- `ui/src/test/task-hover-card.test.tsx` (isolated, real `<ReactFlowProvider>`/`<ReactFlow>` per
  node so the real `<NodeToolbar>` finds its node in the store -- TASK.md's own Risk note): 6
  tests covering the exact content list (label, status chip, duration, cost, attempts, retries,
  "Click for details" hint), `isVisible=false` / `nodeId=null` both render nothing, the
  "hidden characters removed" marker for a sanitized label, and full degradation to em dashes
  with no crash when there is no stat yet.

### AC-2 -- Panel content (HLD §8.7)
- `ui/src/test/task-detail-panel.test.tsx`, describe "TaskDetailPanel (AC-2)": opens `u1`'s panel
  against the real shared fixture and asserts, section by section: header label `"u1"`; Timing's
  duration equals `formatDuration(240)` and wait equals `formatDuration(waitSeconds("u1", fixture,
  statsById))` (the model's own function, not a hand-picked string); Usage shows
  `formatCost(1.5)`; Retries shows attempts `2` and retries `1` (`= attempts - 1`); Spawn shows
  the literal text `"parent: cp1"`; Dependencies lists `cp1` under "Depends on" and the sanitized
  `"leaf"` node under "Dependents" -- see **Deviations** #1 for why "leaf" replaces TASK.md's own
  literal "cp2" example.
- `ui/src/test/graph-panel-model.test.ts` unit-tests the underlying pure `panelModel` the same way
  (6 tests): the full field shape against the fixture's real `u1` relationships, `hasStat=false`
  defaults, a stat with `started_at: null`, a missing node's `header.missing`, the 45-child
  emitter returning all 45 unsliced, and a lookup-miss id never throwing.

### AC-3 -- Close and focus
- `ui/src/test/run-graph-detail-reveal.test.tsx`, describe "RunGraph detail panel close +
  focus-return (AC-3)": clicking the real `u1` node opens the panel with focus moved onto it
  (`toHaveFocus()`); `Escape` closes it AND returns focus to the real `u1` node DOM element
  (`waitFor(() => expect(u1).toHaveFocus())`); a click on the real `.react-flow__pane` element
  also closes it.
- `ui/src/test/task-detail-panel.test.tsx`, describe "TaskDetailPanel (AC-3)": the × button and
  an `Escape` keypress both call the `onClose` prop exactly once (the panel's own responsibility;
  focus-RETURN is RunGraph.tsx's job, covered above since it needs real canvas DOM).

### AC-4 -- Navigation
- `ui/src/test/run-graph-panel-navigation.test.tsx` (mocked `<ReactFlow>` + `useReactFlow`, same
  pattern `run-graph-toolbar.test.tsx` (T-aHktGB) uses for its own `setCenter` assertion, per the
  TASK.md Risk note): opening `u1`'s panel and clicking its "cp1" parent link calls the
  `setCenter` spy with cp1's actual captured position and a zoom floor of `1`, flips `cp1`'s
  `selected` flag to `true`, and updates the panel's own header to `"cp1"`. A second test repeats
  this in the spawn view.
- `ui/src/test/task-detail-panel.test.tsx`, describe "TaskDetailPanel (AC-4)": in isolation,
  clicking the "cp1" link calls the `onNavigate` prop with `"cp1"` -- proves the panel's own click
  wiring independent of `useSelectAndCenter`'s internals (already exhaustively unit-tested by
  T-aHktGB's `graph-hooks.test.ts`).

### AC-5 -- Children cap
- `ui/src/test/task-detail-panel.test.tsx`, describe "TaskDetailPanel (AC-5)": a synthetic
  45-child emitter graph renders exactly 20 `<li>` link rows plus a `"show all (45)"` button;
  clicking it renders all 45 and removes the button.
- `ui/src/test/graph-panel-model.test.ts` confirms `panelModel` itself returns the full,
  un-truncated 45-id array -- the cap is a `TaskDetailPanel`-only display concern
  (`CHILDREN_PREVIEW_COUNT`), not a model concern.

### AC-6 -- Degraded data
- `ui/src/test/task-detail-panel.test.tsx`, describe "TaskDetailPanel (AC-6)": `stat === null`
  renders `"Stats pending"` (present in every stat-derived section) with no crash; a stat with
  `started_at: null` (but otherwise present) renders every OTHER Timing field normally while
  "Wait before start" shows `"—"`; the real fixture's `missing-dep` node renders the literal text
  `"Unknown task (referenced by a dependency but never defined)"`.
- `ui/src/test/run-graph-detail-reveal.test.tsx`, describe "RunGraph detail panel for a missing
  node (AC-6)": clicking the real, rendered `missing-dep` phantom node end-to-end opens the panel
  with that same text, no crash.

### AC-7 -- Text-only (D-5)
- `ui/src/test/task-hover-card.test.tsx` and `ui/src/test/task-detail-panel.test.tsx` each include
  a `<b>x</b>` / `<img onerror=...>`-style malicious label test: the literal string renders as
  `textContent`, `container.querySelector("b"/"img")` is `null`, and the SAME check is repeated
  for a related-task LINK's own label (not just the header), since a link can point at any other
  agent-authored id.
- `grep -r dangerouslySetInnerHTML ui/src/graph/` -- empty (exit `1`). (An earlier draft's own doc
  comments literally contained that string as documentation text, which the grep can't
  distinguish from real usage -- reworded to describe the same guarantee without the literal
  token, so the check is genuinely clean, not accidentally passing.)

### AC-8 -- Responsive
- `ui/src/test/task-detail-panel.test.tsx`, describe "TaskDetailPanel (AC-8)": setting
  `window.innerWidth = 600` before render yields `task-detail-panel--sheet` (not `--side`);
  `1200` yields `task-detail-panel--side` (not `--sheet`). The breakpoint constant
  (`RESPONSIVE_BREAKPOINT_PX = 720`) is read once at mount and kept live via a `resize` listener.

### AC-9 -- Build/test/typecheck
- `npm run typecheck` (`tsc -b --noEmit`) -> clean.
- `npm test` -> **224 passed, 0 failed, 24 test files.** Baseline (T-aHktGB checkpoint, confirmed
  by re-running before any change) was **192 passed, 19 files** -- all 192 still green (zero
  regressions), plus **32** new tests across 5 new files: `graph-panel-model.test.ts` (6),
  `task-hover-card.test.tsx` (6), `task-detail-panel.test.tsx` (13),
  `run-graph-detail-reveal.test.tsx` (5), `run-graph-panel-navigation.test.tsx` (2).
- `npm run build` -> exit `0`. Bundle deltas vs. T-aHktGB's own recorded "after" numbers:
  - Main chunk JS: `121.40 KB` -> `122.10 KB` gzip (**+0.70 KB**, under the ≤5 KB budget).
  - Main chunk CSS: `3.94 KB` -> `4.35 KB` gzip (**+0.41 KB** -- the new hover-card/detail-panel
    CSS in `styles.css`, not code-split, same as every prior task's own CSS delta here).
  - Lazy `RunGraph-*.js` chunk: `77.71 KB` -> `80.52 KB` gzip (**+2.81 KB**, the new
    `TaskHoverCard`/`TaskDetailPanel`/wiring code, all inside the lazy chunk). Its CSS companion
    (`@xyflow/react/dist/base.css`) is unchanged at `2.03 KB` gzip.
  - Combined lazy total: `80.52 + 2.03 = 82.55 KB` gzip -- still under T-adVpTj's informational
    `≤ 90 KB` NFR-4 estimate.
  - `src/agent_orchestrator/ui/static/` regenerated via `make ui-build` from the repo root and
    committed (deterministic rebuild: re-running produced byte-identical asset filenames).
- `grep -r dangerouslySetInnerHTML ui/src/graph/` -> empty (exit `1`).
- `grep -nE "#[0-9a-fA-F]{3,8}\b" ui/src/graph/*.tsx ui/src/graph/*.ts` -> empty (exit `1`) -- no
  hardcoded hex in `TaskHoverCard.tsx`/`TaskDetailPanel.tsx`/`RunGraph.tsx`/`model.ts`;
  `styles.css` additions are `var(--...)` references to EXISTING tokens (`--surface-1`,
  `--hairline`, `--radius`/`--radius-sm`, `--text-primary`/`--secondary`/`--muted`, `--font-mono`,
  `--status-critical`) plus `color-mix(in srgb, var(--text-primary) …%, transparent)` for the
  hover card's elevation shadow (the same derived-from-token pattern the minimap mask already
  uses) -- no new color tokens, so no new light/dark block was required either.

## Deviations from TASK.md (flagged, not silent)
1. **AC-2's literal "cp2" dependents example does not exist in the shared fixture.** The
   dispatching task explicitly instructed using the fixture's real relationships rather than
   inventing data: `u1`'s only dependency-edge dependent in `ui/src/test/fixtures/run-graph.json`
   is the sanitized `"leaf"` node (id `leaf` + U+200B, label `"leaf"`), not a `"cp2"`. All AC-2
   tests assert against this real relationship (and additionally against `relatedIds`' own
   output, so the test can't silently drift from the model even if the fixture changes later).
2. **`model.ts::displayText` (HLD §8.5, flagged as missing by both T-adVpTj and T-aHktGB) was
   still not added.** The dispatching task's own change boundary narrowed `model.ts` edits to
   "the pure `panelModel` function ONLY -- do not modify any existing export," which reads as
   excluding any OTHER new export too. Since every related-task link's target id already exists
   in the loaded `graph.nodes` (parent/children/depends-on/dependents ids all come from edges
   whose endpoints are real nodes), a display label is resolvable with a plain
   `graph.nodes.find(...)` lookup -- implemented as a small local `labelFor()` helper inside
   `TaskDetailPanel.tsx` instead, falling back to the raw id (still literal text only, D-5) for
   the one case that can't happen in practice (an id absent from `graph.nodes` entirely).
3. **`selectedNodeId` (already existing state, T-OjTS8O) is reused as the pinned panel's target
   id rather than adding a separate `pinnedNodeId`.** `hooks.ts`'s own `useSelectAndCenter` doc
   comment already says its `onSelect` callback "drives `TaskNode`'s `selected` styling / a
   future detail panel" -- i.e. this was the intended design from when that hook was extracted.
   One consequence: selecting a node via the toolbar's search-to-focus (`GraphToolbar`, T-aHktGB)
   now also opens the panel, since it calls the very same `onSelect`. This is a natural,
   consistent side effect (every way of "selecting" a node now surfaces its detail) rather than
   a regression -- confirmed against the full existing `run-graph-toolbar.test.tsx` suite, which
   stays 100% green (its own keyboard-reachability test never selects a node, so the panel never
   mounts there and the toolbar's own tab order is unaffected).
4. **Keyboard focus opening the hover card is wired via a single delegated `onFocus`/`onBlur` on
   the canvas wrapper `<div>`**, reading the real React Flow node wrapper's own `data-id`
   attribute, rather than a per-node handler -- `TaskNode.tsx` is off-limits, and React's
   synthetic focus/blur events (backed by native `focusin`/`focusout`, which DO bubble) make this
   a clean, single-listener solution.
5. **Esc AND the × button both return focus to the node** (not only Esc, which is all TASK.md's
   own AC-3 bullet literally names for that behavior) -- closing the panel by either explicit
   "I'm done with this" action reasonably returns keyboard focus to where it came from; a
   pane-click does not, since that's a mouse action elsewhere on the canvas.
6. **The output-artifact-path copy button (HLD §8.7 Outcome row) is best-effort and not under a
   dedicated AC** -- guarded against a missing/denied `navigator.clipboard` (jsdom, insecure
   context, permissions) with a silent no-op; not separately unit-tested since no AC-1..AC-9 item
   covers it and it's decorative per the HLD table, not part of this task's acceptance bar.
7. **"Suppressed while panning" (Description item 1) is implemented for the pointer-hover path
   only** (`isPanningRef`, set by `onMoveStart`/`onMoveEnd`), not separately tested -- it isn't
   one of TASK.md's own enumerated AC-1 bullets, and keyboard-focus-triggered hover has no
   analogous "panning" concern to suppress.

## Risks / Blockers
- Blockers: none. Task complete.
- `NodeToolbar` positioning in jsdom is meaningless (per TASK.md's own Risk note) -- all tests
  assert visibility/content only; real on-canvas positioning is `T-F1caAt`'s job.
- Not covered here (per the change boundary, `T-F1caAt`'s job): real-browser drag-and-drop
  physics, real pan/zoom interaction with the hover card/panel at various zoom levels, and the
  real backend-served CSP header. All of this task's own new behavior was verified against the
  shared fixture, both with a mocked `<ReactFlow>` (navigation/`setCenter`) and a real one
  (hover timing, keyboard focus, focus-return, pane-click) -- same dual approach `run-graph.test.tsx`
  vs. `task-node.test.tsx` already established in this epic.
- There is still no live backend endpoint (`T-AsQ77e`); this task's data-shape assumptions all
  come from `docs-md/run-graph-canvas-hld.md` §14.2 and the shared contract fixture, same as
  every other frontend task in this epic.

## Next actions
- None for this task.
- Downstream: `T-F1caAt-graph-e2e-verification` screenshots the hover card and pinned panel open,
  against the real backend once `T-AsQ77e` lands. Non-MVP `T-ydMbJN-critical-path-edge-timing`
  reuses `waitSeconds` (unchanged by this task).
