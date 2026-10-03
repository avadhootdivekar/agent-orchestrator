# Dashboard frontend

React + TypeScript + Vite. Served by `ao ui` — see the
[Dashboard section of the root README](../README.md#dashboard-ao-ui) for the user-facing
docs and [the HLD](../docs-md/dashboard-and-general-instructions-hld.md) for the design.

## Build output is committed — on purpose

`npm run build` writes to **`../src/agent_orchestrator/ui/static/`**, inside the Python
package. That directory is committed so `pip install` ships a working dashboard without
requiring node at install time.

**If you change anything under `src/`, re-run `make ui-build` and commit the result.**
`node_modules/` is git-ignored; the build output is not.

## Commands

Run these from the repo root (they wrap the npm scripts here):

```bash
make ui-install     # npm install
make ui-build       # build into the Python package
make ui-test        # vitest
make ui-typecheck   # tsc -b --noEmit
make ui             # build, then serve via `ao ui`
make ui-dev         # vite dev server on :5173, proxying /api to :8765
```

For hot reload: run `make ui` in one terminal (serves the API on `:8765`), then `make ui-dev`
in another and open `:5173`. The dev server proxies `/api` across, so the frontend reloads on
save while talking to a real backend.

## Layout

```
src/
  main.tsx            entry point
  App.tsx             shell: sidebar nav, tab bar + mounted tab panels, hash/localStorage sync, theme toggle
  api.ts              typed client for /api (throws ApiError carrying the status code)
  types.ts            response shapes, mirroring src/agent_orchestrator/ui/app.py
  launch.ts           PURE launch helpers: status fallback, poll constants, guarded storage for the last launch / dismissals
  useLaunchPanel.ts   owns a launcher's launch record; remembers an unresolved one in sessionStorage
  usePolling.ts       poll hook: ticks now + every N ms, paused while document.hidden / disabled (E-iafh2F)
  format.ts           pure display helpers — bytes, durations, cost, status tone/glyph
  styles.css          theme tokens + layout; light and dark both explicitly defined
  components/
    LaunchResultPanel.tsx  the outcome of a launch (starting / started / not confirmed / failed); never navigates by itself
    FailedLaunches.tsx     runs-list strip of recent failed-to-start launches (dismissable)
    common.tsx        StatusChip, Tile, ErrorBanner, LiveBadge, Empty
    RunsList.tsx      run table + workspace-wide stat tiles
    NowRunning.tsx    fixed 3-row, non-collapsible live-task box (full + compact variants), E-iafh2F
    RunDetail.tsx     per-run stats, task table, tripped breakers, CLI log
    PromptPanel.tsx   recorded run prompt (text-only, collapsible, truncation/changed notices)
    NewRun.tsx        workflow picker + prompt box + override fields
    FileBrowser.tsx   directory tree + code viewer
    Settings.tsx      workspace config + effective general instructions
    Usage.tsx         usage report: group table, run filter, survival toggle, outcomes
    FeedbackControls.tsx  rating form (good/ok/bad, reason tags, note) shared by run + task
    FeedbackPanels.tsx    run feedback history, implicit-signals/survival panel
  tabs/               tabbed workspace (E-iafh2F Phase 2, docs-md/live-activity-and-tabs-hld.md §2)
    model.ts          PURE: Tab type, kind/param allowlists, hash codec, persistence codec, reducer
    storage.ts        guarded localStorage read/write
    context.tsx       TabActiveContext (inactive tabs stop polling) + TabActionsContext (navigate/open)
    TabBar.tsx        tab strip: close, drag + Alt+Arrow reorder, roving tabindex
    TabLink.tsx       <a href="#/..."> links + the explicit "open in new tab" button
    TabView.tsx       tab -> view component;  TaskTab.tsx / GraphTab.tsx  the two tab-only views
  graph/              run graph canvas (E-k3AMEr) — lazy-loaded chunk, see docs-md/run-graph-canvas-hld.md
    model.ts          PURE (no React, no fetch): named constants, joinNodes/edgesForView/nodesForView,
                      metricFraction, searchNodes, relatedIds, waitSeconds, panelModel, prefs
    layout.ts         PURE async computeLayout() — the only dagre import (swap seam, ADR-0017 D5)
    hooks.ts          useSelectAndCenter — shared "jump to node" for search and panel links
    RunGraph.tsx      React Flow canvas: view toggle, fetch-on-graph_version, banners, wiring
    TaskNode.tsx      memoized node: label (text only), status glyph + stripe, badges
    GraphToolbar.tsx  show-unrelated, metric select, search, fit, reset layout
    Legend.tsx        edge styles for the current view, badges, source/degraded notes
    TaskHoverCard.tsx hover/focus preview (React Flow NodeToolbar)
    TaskDetailPanel.tsx pinned side panel (bottom sheet under 720 px)
  test/               vitest suites (jsdom, mocked fetch; React Flow jsdom shims in setup.ts)
```

## Launching a run (no auto-redirect)

`NewRun` ("Start run") and `TemplateLaunch` ("Create & run") never navigate on their own. The POST
response carries a derived `status` and a bounded `log_tail`; `LaunchResultPanel` renders it and
every way out is an explicit button (`onLaunched(runId | null)`, `null` = run list). A launch whose
engine died before creating a run directory shows "Failed to start" with the log, and also lands in
the strip at the top of the runs list for 24 h (or until dismissed). Design and limits:
[HLD Phase 3](../docs-md/live-activity-and-tabs-hld.md#phase-3--launch-status-and-pre-spawn-validation-t-lc5rq8-launch-status).

## Conventions

- **Runtime dependencies are few, and each one is justified by an ADR.** Anything else is a
  devDependency. All of it is bundled into the committed build, which is what lets the dashboard
  work offline and ship inside a wheel. The current runtime set is:
  - `react` / `react-dom`
  - `dompurify`, `marked`, and `highlight.js`, for file preview
    ([ADR-0011](../docs-md/adr/ADR-0011-untrusted-workspace-content-rendering.md))
  - `@xyflow/react` and `@dagrejs/dagre` (both MIT), for the run graph canvas and its layout
    ([ADR-0017 D5](../docs-md/adr/ADR-0017-run-graph-provenance-snapshot-and-canvas.md#d5-frontend-canvas--xyflowreact-12--dagrejsdagre-3-layered-auto-layout)).
    They are code-split into the lazy `RunGraph` chunk, so they don't load until the Graph tab
    opens.

  Adding one means a recorded rationale, a measured gzip budget, and a clean
  `npm audit --omit=dev --audit-level=high`.
- **Status is never color-alone.** `StatusChip` renders a glyph *and* the status word;
  color reinforces. This keeps the UI readable in grayscale, under `forced-colors`, and for
  colorblind users.
- **Colors come from theme tokens in `styles.css`**, not hardcoded hex in components. Both
  light and dark are explicitly stepped (from the project's validated data-viz palette) —
  dark is a chosen set of values, not an automatic inversion. The OS setting and the in-app
  toggle are both honoured, and the toggle wins in either direction.
- **Tabular figures only in aligned columns** (`.num` in tables). Large standalone values —
  stat tiles, the hero figure — use proportional figures, which read better at display size.
- **One hero figure per view.**
- **Wide content scrolls inside its own container** (`.table-wrap`); the page body never
  scrolls horizontally.
- **`format.ts` stays pure** so it is directly unit-testable; components hold the I/O.
