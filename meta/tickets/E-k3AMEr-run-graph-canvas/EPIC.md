# EPIC: E-k3AMEr-run-graph-canvas

## Metadata
- Epic ID: `E-k3AMEr-run-graph-canvas`
- Title: `Run graph canvas: a free-form dashboard view of a run's dependency graph and spawn tree, with a task detail panel`
- Owner: `dev-epic` (execution) · design by `architect`
- Created: `2026-09-27`
- Last Updated: `2026-09-27`
- Status: `In Progress` (Sprint 1 implemented and merged on `ad/run-graph-canvas`; Gate G1 next — see `STATUS.md`)

## Summary
- **Goal:** Let an operator *see* a workflow run in `ao ui` on a pannable, zoomable,
  non-paginated canvas ("Obsidian Canvas" feel). The canvas has two toggleable edge sets over the
  same task nodes:
  1. **Execution order** (dependency DAG: B must finish before A), annotated with the actual start order.
  2. **Spawned by** (which task created which dynamically injected tasks).

  Nodes show only the task title. A hover preview plus a click-to-pin side panel reveal cost,
  running time, retries, and more. The canvas must stay usable at overseer-runner scale (160
  injected tasks) and must work for runs launched *any* way (`ao ui`, bare `ao run`,
  `ao resume`, `ao service` cron/event triggers).
- **Scope In:**
  - Engine: `RunState.spawned_by` spawn provenance written in `engine.py::_inject` (2 call sites),
    `origin` carried across `TaskRunState` resets, and a `RunState.spec_sessions` log plus
    write-once `workflow.snapshot.<sha12>.json` files.
  - `dag.py`: extract `iter_dependency_edges`, with `build_dag` behavior-identical.
  - Dashboard backend: pure `ui/graph.py` builder, `GET /api/runs/{id}/graph`,
    `RunDetail.graph_version`, and additive `TaskStat.{dispatch_cycle, not_taken_reason}`.
  - Dashboard frontend: Graph tab in `RunDetail`, React Flow canvas plus dagre layout, view toggle,
    hover card, detail panel, search, legend, metric strip, live refresh, and degraded-mode banners.
  - Tests: pytest (unit, integration, e2e) plus vitest, and an opt-in real-browser smoke under the real CSP.
  - Docs: HLD/LLD, ADR-0017, cross-links, roadmap update, and a post-implementation docs refresh.
- **Scope Out:**
  - Editing or building DAGs in the browser (the other half of the roadmap item stays deferred).
  - A timeline/Gantt view.
  - Live streaming (SSE).
  - Authentication (ADR-0010 D7).
  - Transcripts in the panel.
  - The `route`-loss-on-resume defect (finding F-2, separate follow-up).
  - `E-Grpp0X` injected-`depends_on` validation (coordination only).

## Design
- HLD + LLD: [`docs-md/run-graph-canvas-hld.md`](../../../docs-md/run-graph-canvas-hld.md) (sections 1–25)
- ADR: [`docs-md/adr/ADR-0017-run-graph-provenance-snapshot-and-canvas.md`](../../../docs-md/adr/ADR-0017-run-graph-provenance-snapshot-and-canvas.md)
- Cross-linked from: `docs-md/dashboard-and-general-instructions-hld.md` §4 and `meta/ROADMAP.md` §3.3

## Requirements

### User-stated (verbatim intent)
- U-1: View a run diagrammatically: structure, execution order, spawn relationships, time and cost.
- U-2: Open, free-flowing canvas, not paginated.
- U-3: Execution-order (dependency) view.
- U-4: Parent-child spawn view. The edges can genuinely differ from U-3's edges.
- U-5: Toggle between the two views.
- U-6: A block shows only the task title.
- U-7: Hover and/or click reveals details (cost, running time, retries, and more).

### MVP functional
- FR-1: Persist spawn provenance (`RunState.spawned_by`). It survives resume.
- FR-2: Persist a static workflow snapshot per run session.
- FR-3: A pure graph builder produces both edge sets and node annotations, and reuses `dag.py` edge logic.
- FR-4: `GET /api/runs/{run_id}/graph` plus `RunDetail.graph_version` (version-gated refetch).
- FR-5: Canvas with a two-view toggle, auto-layout, pan/zoom/fit, minimap, search, legend, and metric strip.
- FR-6: Hover preview plus pinned detail panel with navigable related tasks.
- FR-7: Explicit degraded modes (no snapshot, no spawn data, unknown dependency ids, truncation).
- FR-8: The existing task table is unchanged. Graph is an additional tab.

### Non-functional
- NFR-1: Additive and backward-compatible only (old `state.json` loads, no API field changes meaning).
- NFR-2: Deterministic, byte-identical graph for identical inputs.
- NFR-3: For 200 nodes and 500 edges: server build ≤ 150 ms, client layout ≤ 300 ms, pan/zoom ≥ 30 fps.
- NFR-4: Added bundle ≤ 90 KB gzip. `npm audit --omit=dev --audit-level=high` clean.
- NFR-5: No magic literals.
- NFR-6: CI gates hold (`agent_orchestrator.ui` coverage ≥ 80%, and ruff, mypy, vitest, tsc, build).
- NFR-7: The engine hot path gains no measurable cost.

### Derived
- D-1: Usable at ≥ 160 nodes.
- D-2: Works for every launch path.
- D-3: Live update.
- D-4: Legacy runs degrade honestly.
- D-5: Agent-authored strings are rendered as text only.
- D-6: The SPA CSP is unchanged.
- D-7: Status is never color alone.
- D-8: Keyboard and screen-reader access.

Traceability: HLD §19 (acceptance criteria matrix).

## Task List

### MVP (sprint 1 → sprint 2)
- [x] `T-AZzgT8-spawn-provenance`: `RunState.spawned_by` plus `_inject` keyword-only `parent_task_id` (emit and loop sites), and `origin` carry-forward at 3 reset sites (FR-1, NFR-1). 14 h. S1. **Done — Gate G1 PASS.**
- [x] `T-l7t6TT-workflow-snapshot`: `RunState.spec_sessions` plus write-once `workflow.snapshot.<sha12>.json` (FR-2, D-2). 12 h. S1. **Done — Gate G1 PASS (1 SHOULD-FIX resolved: sha format validated before path use).**
- [x] `T-mzT3BW-dag-edge-iterator`: `dag.iter_dependency_edges` with a behavior-identical `build_dag` and an oracle test (FR-3 foundation, R-2). 8 h. S1. **Done — Gate G1 PASS (1 SHOULD-FIX resolved: deduped `output_to_task`; 1 NIT resolved: minimal-shape tests).**
- [x] `T-adVpTj-graph-model-and-layout`: npm deps, TS types, pure `model.ts`/`layout.ts` (async layout seam), jsdom shims, contract fixture, bundle measurement, and a 2 h spike (FR-5 foundation, NFR-3, NFR-4). 16 h. S1. **Done.**
- [x] `T-OjTS8O-run-graph-canvas`: canvas core, i.e. React Flow, `TaskNode`, edge styles, view toggle, Graph tab (lazy), and version-gated live refetch (FR-5, FR-8, U-5, U-6, D-3, D-5, D-7). 14 h. S1. **Done.**
- [x] `T-M4qboy-run-graph-builder`: pure `ui/graph.py` builder, `display_text`, early cap, and `compute_graph_version` (FR-3, FR-7, NFR-2). 14 h. S2. **Done.**
- [x] `T-AsQ77e-run-graph-endpoint`: `GET /api/runs/{id}/graph`, `RunDetail.graph_version`, `TaskStat` additions, and a contract test against the frontend fixture (FR-4, FR-7). 6 h. S2. **Done.**
- [x] `T-aHktGB-graph-toolbar-and-legend`: search-to-focus, metric strip select, unrelated filter, fit/reset, legend, and degraded banners (FR-5, FR-7, D-8). 12 h. S2. **Done.**
- [x] `T-pAi0Cv-task-detail-panel`: hover card plus pinned panel with navigation (FR-6, U-7, D-8). 16 h. S2. **Done.**
- [ ] `T-F1caAt-graph-e2e-verification`: e2e via real server plus `ao run`, a Playwright (system Chrome) CSP smoke with a negative control, and perf gates (D-1, D-2, D-6, NFR-3). 14 h. S2.
- [ ] `T-oroE5f-docs-refresh`: post-implementation reconciliation of `docs-md/` and READMEs (mandatory, last). 6 h. S2.

### Non-MVP (backlog / sprint-2 stretch)
- [ ] `T-VcN4pt-task-title-field`: optional `TaskSpec.title` for friendlier node labels. 6 h (S2 stretch).
- [ ] `T-hMNbDP-spawn-subtree-collapse`: collapse and expand a spawner's subtree. 12 h.
- [ ] `T-ydMbJN-critical-path-edge-timing`: critical-path highlight plus per-edge wait-time labels. 12 h.
- [ ] `T-N8scZK-layout-persistence`: persisted dragged positions plus incremental stable layout for live runs. 10 h.

MVP total: **132 focus hours**. Plan: **2 developers × 2 sprints** (S1 64 h, S2 68 h + 6 h
stretch). Capacity math is in HLD §22. The backend chain (62 h serial) and the frontend chain
(46 h serial) each exceed one developer's 34–41 h per-sprint commitment band, so one sprint is
infeasible regardless of team size.

Review gates (HLD §24):
- **G1** (end of S1): reviewer and tester sign off on engine-touching T-AZzgT8, T-l7t6TT, and
  T-mzT3BW before T-M4qboy builds on them. **CLOSED 2026-09-27, PASS on all 3** (0 MUST-FIX;
  2 SHOULD-FIX + 1 NIT found and resolved same-day — see each task's `STATUS.md` "Gate G1
  closure" section).
- **G2**: dev-security review before close.
- **G3**: final reviewer sign-off, then the docs refresh.

## Assumptions (full log: HLD §3)
- ASSUMPTION A-1: "task title" = task id in MVP (there is no title field on `TaskSpec`). Non-MVP `T-VcN4pt` adds one.
- ASSUMPTION A-2: "execution order" = dependency DAG plus actual start ordinal. Gantt is deferred.
- ASSUMPTION A-3: the parent of a loop clone = the gate task of the previous iteration.
- ASSUMPTION A-5 (**unverified until T-F1caAt passes**): React Flow and dagre need no CSP change. The T-adVpTj spike gives an early signal. The hard gate is the T-F1caAt smoke with a negative control.

## Blocked / ambiguous questions
- **None blocking.** Canvas library, node shape, and hover-vs-panel interaction were delegated to
  the architect by the user and are decided (ADR-0017 D5–D7).
- OPEN_QUESTION (non-blocking, default applies): should the headless-Chrome smoke run in CI or stay
  opt-in? Default: opt-in, and T-F1caAt records a recommendation.
- OPEN_QUESTION (non-blocking, default applies): is a third timeline/Gantt view wanted? Default: no.
  It is recorded as the next roadmap step.

## Design corrections to the original brief (verified against main @ 191da69)
- **Two** `_inject` call sites, not three. Emit is at `engine.py:~2052` and the loop gate at `engine.py:~2162`.
  Router activation (`_on_router_success`, `engine.py:~3107`) only tags `route` on pre-existing
  tasks and never injects. Router → route membership is therefore a node attribute, not a spawn edge.
- `parent_task_id` is **not** stored on `TaskRunState`, because that object is wholesale-replaced by
  `prepare_resume` (`runstate.py:~278`) and by the engine failure resets (`engine.py:~1051`, `~1112`).
  It lives on `RunState.spawned_by` and is still exposed as `parent_task_id` in the API (ADR-0017 D1).

## Risks and Dependencies
- **Coordination with `E-Grpp0X-injected-task-dag-validation-gap`:** both edit the body of
  `engine.py::_inject`. This epic adds `spawned_by` writes in the same per-spec step as the
  `injected_tasks` append, so E-Grpp0X's validate-before-append composes cleanly. Whichever lands
  second rebases. Neither folds in the other's scope. A regression test pins "a rejected injected
  id has no spawn record".
- `build_dag` refactor regression risk (edge order feeds scheduling). Mitigated by an oracle
  equality test over every repo spec (T-M4qboy AC).
- React Flow edges in jsdom are unreliable. Edge semantics are tested at the pure-model level, and
  rendered edges in the browser smoke test.
- Bundle growth and supply chain: two MIT deps, measured against NFR-4, with `npm audit` in CI.
- Provenance and snapshot data live in the agent-writable workspace. They are observability
  data, not a security boundary. The dashboard bounds parsing and renders text only (HLD R-9).
- Discovered finding F-2 (out of scope): `route` is lost on resume for tasks on a *selected* route.
  A separate follow-up ticket is recommended.

## Phase-4 hardening (2026-09-27)
All six consultations were run read-only on the first draft: manager, developer, reviewer, tester,
dev-security, and dev-critic. The full record is in HLD §23.1. Material design changes:

1. **Launch-record fallback removed.** Security HIGH: it read an agent-steerable path. Critic: not
   worth a third source.
2. **Snapshot is write-once per spec sha plus a `spec_sessions` log.** Critic HIGH: the rewrite
   lost history.
3. **`SpawnRecord.origin` is an open `str`**, and `parent_dispatch_cycle` was added. Critic HIGH:
   forward compatibility.
4. **A single `compute_graph_version(state)`** is shared by detail and graph. Reviewer MUST-FIX.
5. **Snapshot insertion point corrected** to before the first save at `engine.py:~681`. Developer
   MUST-FIX.
6. **Two tasks split** (T-mzT3BW, T-aHktGB), with buffers and review gates. Manager MUST-FIX.
7. **The browser smoke uses Playwright driving the system Chrome.** The app has no URL routing, and
   `--dump-dom` cannot see CSP events. It captures `securitypolicyviolation`, console, and page
   errors, with a negative control, and
   jsdom shims are explicit. Tester MUST-FIX.
8. **Server-side label sanitization** (bidi/zero-width, length cap), and parse bounded before work.
   Security MEDIUM/LOW.

Declined with rationale: recording per-dispatch intervals for a future timeline (HLD R-10).

## Links
- Design doc: `docs-md/run-graph-canvas-hld.md`
- ADR: `docs-md/adr/ADR-0017-run-graph-provenance-snapshot-and-canvas.md`
- Sprint plan: `docs-md/run-graph-canvas-hld.md` §22
- Related epics: `E-Ui7Kq2-dashboard-and-general-instructions` (dashboard base),
  `E-Fp7Qv2-dashboard-file-preview` (CSP/untrusted content), `E-YAAGhk-overseer-runner-template`
  (largest spawn trees), `E-Grpp0X-injected-task-dag-validation-gap` (same function)
- Output artifacts: `output/E-k3AMEr-run-graph-canvas/` (screenshots, perf numbers; created by T-F1caAt)
