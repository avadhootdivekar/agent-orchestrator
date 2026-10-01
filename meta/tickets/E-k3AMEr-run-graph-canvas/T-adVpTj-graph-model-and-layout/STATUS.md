# STATUS

- ID: `T-adVpTj-graph-model-and-layout`
- Updated At: `2026-09-27`
- State: `Done`
- Owner: `developer` (Dev B, frontend)
- Scope: `MVP` · Sprint: `S1` · Estimate: `16 h`

## This update
- By: architect · Role: architect · Date: 2026-09-27 · Comment: Task created as part of the
  E-k3AMEr design package (design + tickets only; no code written). Acceptance criteria,
  pseudocode, and interfaces are in `TASK.md`, and the authoritative design is
  `docs-md/run-graph-canvas-hld.md`. Hardened by the Phase-4 consultations (HLD §23.1).
- By: developer · Role: developer · Date: 2026-09-27 · Comment: Task **Done**. Foundation laid
  with no visible UI, exactly as scoped: two new runtime deps, HLD §14 contract mirrored into
  `types.ts`, `api.ts::runGraph`, pure `model.ts`/`layout.ts` with exhaustive vitest coverage,
  jsdom shims, and the shared contract fixture. All 8 acceptance criteria pass with evidence
  below. `App.tsx`/`RunDetail.tsx`/every other existing component untouched; no Python file
  touched; no rebuilt `static/` committed.

## Evidence

### AC-1 — Clean install
- `npm ci` (from a fresh `node_modules`) → exit `0`, no `--legacy-peer-deps`, no peer warnings
  for React 19.
- `npm audit --omit=dev --audit-level=high` → exit `0`. One pre-existing **moderate**
  `dompurify` advisory (GHSA-55q2-fjhq-7xh7) remains — unrelated to this task's two new deps,
  below the `--audit-level=high` gate, out of scope to fix here.
- Resolved versions (`ui/package-lock.json`): `@xyflow/react@12.12.0`, `@dagrejs/dagre@3.1.1`
  (transitively `@dagrejs/graphlib@4.0.5`, `@xyflow/system@0.0.83`). `package.json` declares
  `^12.12.0`/`^3.1.0` per the task's ask; the lockfile pins the exact resolved versions above.

### AC-2 — Bundle
- `npm run build` → exit `0` (`tsc -b && vite build`).
- **Real committed-diff delta** (measured before/after this task's actual change set, via a
  throwaway `git worktree` at the pre-task commit `66c4625` for the "before" build, both
  reverted/removed — no build output is part of this commit):
  - Before: JS `119.92 KB` gzip, CSS `2.90 KB` gzip.
  - After: JS `119.94 KB` gzip, CSS `2.90 KB` gzip.
  - **Main chunk delta ≈ +0.02 KB gzip** (well under the 5 KB cap) — the only reachable new
    code is one small arrow function, `api.ts::runGraph`; nothing yet imports
    `graph/model.ts`, `graph/layout.ts`, or `@xyflow/react`/`@dagrejs/dagre` from the app's
    module graph, so there is **no separate graph chunk in the real build yet** — correct and
    expected, since this task adds no UI (`T-OjTS8O` wires the lazy import).
- **Informational only** (not committed): to sanity-check the eventual lazy-loaded chunk
  ahead of `T-OjTS8O`, a throwaway `React.lazy`-loaded component (200-node `<ReactFlow>`
  using the real `computeLayout`) was built once, measured, then fully reverted
  (`git checkout -- ui/src/main.tsx`; spike file deleted; rebuilt `static/` reverted). Result:
  graph chunk `73.45 KB` JS gzip + `2.03 KB` CSS gzip = **`75.48 KB` gzip total**, comfortably
  under the **≤ 90 KB gzip** NFR-4 budget with margin for `T-OjTS8O`'s own component code.

### AC-3 — Types
- `npm run typecheck` (`tsc -b --noEmit`) → clean.
- `ui/src/types.ts` gains `RunGraph`, `GraphNode`, `DependencyEdge`, `SpawnEdge`, `GraphLoop`,
  `GraphRouter`, `GraphSource`, `SpawnData`, `EdgeKind` (the three closed unions named after
  HLD §14.2's own enum names: `source`/`spawn_data`/`kind`), plus `GraphView`, `MetricMode`,
  `ViewNode`, `ViewEdge`, `LayoutResult` (also part of the §8.5 TYPES list, needed for the
  AC-4 function signatures). Every `RunGraph`/`GraphNode`/`DependencyEdge`/`SpawnEdge`/
  `GraphLoop`/`GraphRouter` field name matches §14.2 1:1 (verified field-by-field against the
  §14.2 example and the §8.3.2 dataclass pseudocode). `TaskStat` gains
  `dispatch_cycle: number` and `not_taken_reason: string | null`. `RunDetail` gains
  `graph_version: string | null`. `GraphNode.origin`/`SpawnEdge.origin` are `string` (open
  set), documented inline per ADR-0017 D1.
- All existing `TaskStat`/`RunDetail` fields kept as-is (additive only, per the change
  boundary).

### AC-4 — Pure functions (every named branch covered)
All in `ui/src/graph/model.ts` except `computeLayout` (`ui/src/graph/layout.ts`). File:line
anchors and branch coverage:
- `joinNodes` (`model.ts:51`) — attaches `stat` when present, `null` when absent
  (`graph-model.test.ts` `joinNodes` describe block, 3 tests incl. the full shared fixture).
- `edgesForView` (`model.ts:57`) — `dependency`/`spawn`, `dep:`/`spawn:` id prefixes, `set`
  tag (2 tests, both against the shared fixture).
- `nodesForView` (`model.ts:74`) — dependency view shows all; spawn view + `showUnrelated`
  shows all; spawn view without it hides disconnected nodes and reports `hiddenCount` (3
  tests).
- `computeLayout` (`layout.ts:50`) — LR for dependency / TB for spawn; ignores edges with a
  missing endpoint without throwing; deterministic (called twice, `Array.from(positions)`
  compared equal); tolerates a 2-node cycle without throwing; no two of 200 generated nodes
  overlap (full `O(n²)` bounding-box check); positions every input node (8 tests total in
  `graph-layout.test.ts`).
- `metricFraction` (`model.ts:106`) — `null` for `mode:"none"`, `null` stat, `null` value,
  `max <= 0` (both `0` and negative), clamps in-range and clamps above-max to `1` (6 tests).
- `searchNodes` (`model.ts:125`) — empty query → `[]`; case-insensitive over id and label;
  stable input order; capped at `SEARCH_MAX_RESULTS` (60-node input asserted to truncate to
  exactly 50, in input order) (5 tests).
- `relatedIds` (`model.ts:156`) — parent/children from `spawn_edges`, dependsOn/dependents
  from `dependency_edges`, sorted, empty/`null` for an unrelated id (3 tests).
- `waitSeconds` (`model.ts:185`) — 0 dependencies → `0`; correct `started − max(ended)`;
  `null` for no `started_at`, no stat at all, a source with no stat, a source with no
  `ended_at`; clamped to `≥ 0` for a retry-shifted negative gap (7 tests).
- `readPrefs`/`writePrefs` (`model.ts:246`/`270`) — defaults when unset; valid round-trip;
  throwing `localStorage.getItem`; invalid JSON; non-object JSON; per-field fallback on
  garbage values; `writePrefs` never throws when `setItem` throws (7 tests).

### AC-5 — Perf
- `graph-layout.test.ts` asserts `computeLayout` on a deterministically generated 200-node/
  500-edge graph completes in `≤ 900 ms` (3× the NFR-3 300 ms target, to absorb CI variance).
- **Measured on the dev machine: `136 ms`** (via `performance.now()` around the single
  `computeLayout` call) — well under both the 900 ms test threshold and the required
  `≤ 300 ms` dev-machine bar (NFR-3).

### AC-6 — jsdom shims
- `ResizeObserver` stub, `DOMMatrixReadOnly` stub (`m22 = 1`), and
  `HTMLElement.prototype.offsetWidth`/`offsetHeight` getters (reading inline `style.width`/
  `style.height`, falling back to `1`, never `0`) added to `ui/src/test/setup.ts`, mirroring
  React Flow's documented jsdom testing setup.
- Full suite: **129 passed, 0 failed, 11 test files** (`npm test`). Pre-existing suite was
  **85** tests across 9 files (verified via `--reporter=verbose`, counted before this task's 2
  new test files) — above the 79+ floor, all still green. This task adds **44** new tests (36
  in `graph-model.test.ts`, 8 in `graph-layout.test.ts`). One pre-existing file,
  `run-detail.test.tsx`, needed its `TaskStat`/`RunDetail` object-literal fixtures extended
  with the new AC-3 required fields (`dispatch_cycle`, `not_taken_reason`, `graph_version`) —
  unavoidable given those fields are additive-but-required on the type; this is the smallest
  possible fix to keep AC-6's "existing tests stay green" true after AC-3's type change.

### AC-7 — Constants / no magic literals
- `model.ts` exports exactly: `NODE_WIDTH=184`, `NODE_HEIGHT=48`, `RANK_SEP=64`,
  `NODE_SEP=24`, `LARGE_GRAPH_NODES=300`, `EDGE_LABEL_MIN_ZOOM=0.6`,
  `HOVER_OPEN_DELAY_MS=250`, `HOVER_CLOSE_DELAY_MS=150`, `SEARCH_MAX_RESULTS=50`,
  `PREFS_STORAGE_KEY`.
- Verified by grep (`grep -nE '[^a-zA-Z_.]([0-9]+)' src/graph/{model,layout}.ts`, excluding
  comments) that no other numeric literal appears **except**: `0`/`1` (per the AC), and two
  documented, deliberate additions:
  1. `MS_PER_SECOND = 1000` (`model.ts`), a local named constant for `waitSeconds`'s
     ms→seconds conversion (`Date.parse` returns epoch ms) — not in the AC-7 list verbatim,
     added because leaving `1000` as a bare literal would itself be the magic-literal NFR-5
     violates the rule intends to prevent.
  2. `NODE_WIDTH / 2` / `NODE_HEIGHT / 2` (`layout.ts`), converting dagre's center-point
     coordinates to React Flow's top-left convention — this is the HLD §8.5 pseudocode's own
     `NODE_WIDTH/2` verbatim, not a new literal; read as a permitted arithmetic derivation of
     an already-named constant, not a "magic" number.
  Both are called out here per the senior-dev contract's "flag judgment calls" rule rather
  than silently interpreting the AC.

### AC-8 — Purity
- `grep -nE "['\"](react|react-dom|@xyflow/react)['\"]" src/graph/model.ts src/graph/layout.ts`
  → **zero matches** (exit 1). `layout.ts` imports only `@dagrejs/dagre` and local
  `../types`/`./model`; `model.ts` imports only type-only from `../types`. Recorded here as
  the AC-8-sanctioned "grep-based … check recorded in STATUS" rather than as a vitest test:
  an in-repo purity test would have needed either `@types/node` (for `node:fs`/`node:url`) or
  a new ambient `.d.ts` for Vite's `?raw` imports, both outside this task's explicit
  `package.json`/`tsconfig.json` edit boundary — the plain shell grep achieves the same
  guarantee with zero footprint.

## Deviations from TASK.md (flagged, not silent)
1. **`displayText` not implemented.** HLD §8.5's function list includes
   `displayText(raw) -> string` (mirror of `ui/graph.py::display_text` for ids reached via
   panel links), but `TASK.md` AC-4's own enumerated function list — the actual acceptance
   bar for this task — does **not** include it, and no other AC-4 function needs it. It is
   consumed by the detail panel (`T-pAi0Cv`), not by anything in this task's scope. Left out
   to keep the change to exactly what AC-4 requires; flagging so `T-pAi0Cv` knows to add it to
   `model.ts` rather than assume it already exists.
2. **`RelatedIds`/`MetricMaxima` interfaces** added in `model.ts` (not in `types.ts`) as named
   return/parameter shapes for `relatedIds`/`metricFraction`. Not part of the HLD §14 API
   contract (they're pure-frontend helper shapes, not wire types), so `types.ts` was kept to
   contract-mirroring only, per its own file docstring ("Shapes returned by the dashboard
   API").
3. **`GraphTab`/`GraphPrefs`/`DEFAULT_GRAPH_PREFS`** likewise defined in `model.ts`, not
   `types.ts` — `localStorage` preference shape is a frontend-local concept with no backend
   counterpart.

## Spike outcome (2 h time-boxed, TASK.md instruction)
- Throwaway `<ReactFlow>` with 200 dagre-laid-out nodes, mounted in place of `App` via a
  temporary edit to `main.tsx` (reverted via `git checkout` immediately after), served with
  `npm run dev` on a scratch port. `index.html` temporarily carried a `<meta
  http-equiv="Content-Security-Policy">` tag copying `SPA_CSP` verbatim from
  `src/agent_orchestrator/ui/security.py` (also reverted), since `vite dev` doesn't send the
  real backend header — an approximation, not the real gate.
- Driven headlessly with Playwright (`chromium.launch({ executablePath: "/usr/bin/google-chrome" })`,
  no bundled browser was installed) against `http://localhost:5183/`.
- **Result: clean.** All 200 `.react-flow__node` elements rendered (screenshot confirmed a
  correctly laid-out LR graph with edges, minimap, and pan/zoom controls). The only console
  message was a single `404` for `/favicon.ico` — confirmed via `curl` to be a pre-existing,
  unrelated gap (`index.html` declares no favicon), not a CSP or library issue. **Zero** CSP
  violation reports, zero page errors.
- This is informational only, per the task instruction — the hard gate is `T-F1caAt`'s
  real-browser smoke test against the actual backend-served CSP header (not a `<meta>`
  approximation) with a console scan and a negative control.

## Risks / Blockers
- Blockers: none. Task complete.
- The one real risk named in `TASK.md` (peer-dep conflict with React 19) did not materialize:
  `@xyflow/react@12.12.0`'s peer range is `react >=17`, matched cleanly, `npm ci` clean.
- The `offsetWidth`/`offsetHeight` shim risk (leaking into existing tests) did not materialize
  either — full 129-test suite green, including every pre-existing file.
- Carried forward for `T-OjTS8O`: the informational 75.48 KB gzip graph-chunk estimate is
  from a minimal spike component, not the real `RunGraph.tsx`/`TaskNode.tsx`/toolbar/legend/
  hover-card/detail-panel code `T-OjTS8O` and its siblings will add — that real number could
  land higher and should be re-measured against the ≤ 90 KB total budget once that UI exists.

## Next actions
- None for this task.
- Downstream: `T-OjTS8O-run-graph-canvas`, `T-aHktGB-graph-toolbar-and-legend`,
  `T-pAi0Cv-task-detail-panel` consume `model.ts`/`layout.ts`'s exports and the `ViewNode`/
  `ViewEdge` view-models. `T-AsQ77e-run-graph-endpoint` contract-tests the real backend
  against `ui/src/test/fixtures/run-graph.json`. `T-pAi0Cv` should add `displayText` to
  `model.ts` when it needs it (deviation #1 above).
