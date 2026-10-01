# ADR-0017: Run graph: spawn provenance on RunState, per-session workflow snapshot, shared edge derivation, and a React Flow + dagre canvas

- Status: **Accepted (implemented)** on 2026-09-27. Merged on `ad/run-graph-canvas` @ `dbd3657`,
  with review gates G1 (engine), G2 (security), and G3 (final) all closed PASS. D1–D7 held. See the
  implementation note at the end for what changed in the shipped code. Epic
  `E-k3AMEr-run-graph-canvas`.
- Deciders: `architect`, with Phase-4 consultations (manager, developer, reviewer, tester,
  dev-security, dev-critic). The record is in `docs-md/run-graph-canvas-hld.md` §9 and the epic
  `STATUS.md`.
- Related: ADR-0010 (dashboard architecture, no auth D7), ADR-0011 (untrusted workspace content,
  SPA CSP), ADR-0007 (parallel execution: settle on main thread), ADR-0014 (service-owned
  triggers), `E-Grpp0X` (injected-task validation, same function).

## Context

The user wants to *see* a workflow run on an open canvas with two toggleable edge sets over the
same tasks:

- **Dependency / execution order**: who waits on whom.
- **Spawn**: who created whom.

The user also wants a per-task reveal of cost, time, and retries. Grounding against main @ 191da69 found three facts:

1. Spawn provenance is **not recorded**. `TaskRunState.origin` is a category, not a pointer.
2. Static `depends_on` edges exist only in the spec file. The dashboard's spec lookup
   (`DashboardService._workflow_for_run`) is best-effort and wrong for runs started by bare `ao run`
   or `ao service` triggers.
3. `TaskRunState` is **replaced wholesale** on resume (`runstate.prepare_resume`) and on two engine
   failure paths, which is why `task_integration` already lives on `RunState`.

There is no graph library in the frontend. The frontend ships inside the Python wheel under a
strict CSP.

## D1: Spawn provenance lives on `RunState.spawned_by: dict[child_id, SpawnRecord]`

- **Options:**
  - (a) `TaskRunState.parent_task_id`
  - (b) a field on `TaskSpec`
  - (c) infer it from `injected_tasks` order
  - (d) a `RunState`-level map written in `_inject`
- **Decision:** (d). `SpawnRecord = {parent_task_id, parent_dispatch_cycle, origin: str (open set; the engine writes "injected" | "loop"), injected_at, loop_id?, iteration?}`.
  `origin` is deliberately **not** a closed `Literal` (dev-critic, HIGH). `extra="ignore"`
  protects old readers from unknown *fields* but not from unknown *values*, so a future origin
  kind must not make an older engine or dashboard reject the whole `state.json`.
  `_inject` takes a **keyword-only, required** `parent_task_id`, so mypy rejects any future call
  site that forgets it.
- **Reason:**
  - (a) is silently wiped by resume and failure resets.
  - (b) lets an agent-authored emit manifest *forge* its parent, and mixes runtime provenance into the spec model.
  - (c) is ambiguous for nested emits and for loop clones.
  - (d) follows the existing `task_integration` precedent.
- **Consequences:**
  - The API still exposes it per node as `parent_task_id`.
  - There are two `_inject` call sites (emit, loop). Router activation does not inject and is not a spawn.
  - Old runs have an empty map and are reported as `spawn_data: not_recorded`, with no inference.
  - `origin` is also carried across `TaskRunState` resets. It has no behavioral reader. `route` is
    **not** carried: it has behavioral reach, so it is tracked as a separate finding.
  - A single parent per task is assumed. Multi-parent spawning would change the map value to a list,
    with a `schema_version` bump.

## D2: Write-once `workflow.snapshot.<sha12>.json` per distinct static spec, plus an append-only `RunState.spec_sessions` log

- **Options:**
  - (a) the runtime spec lookup via launch record
  - (b) store edges in `state.json`
  - (c) store the full spec in `state.json`
  - (d) one snapshot file rewritten each session (the first draft)
  - (e) write-once files per distinct spec sha, plus a small session log in `state.json`
  - (f) a content-addressed spec store shared across runs
- **Decision:** (e). At each session start (fresh or resume), the engine:
  - appends `SpecSession{session, started_at, spec_sha256}` before the run's first save (`engine.py:~681`)
  - writes `<run_dir>/workflow.snapshot.<sha12>.json` only if absent (write-once, atomic)

  A changed sha on resume logs `run.spec_changed_on_resume` and leaves the earlier file intact.
  A write failure warns and never fails the run.
- **Reason:**
  - (a) is unreliable for non-`ao ui` launches. The **dev-security review (HIGH)** also found it
    would read and parse an agent-steerable path from a launch record inside the workspace.
  - (b) cannot express loop-id deps, which resolve to the *latest* iteration, or inferred IO edges
    without re-implementing engine rules.
  - (c) rewrites an unchanging blob on every save.
  - (d) mutated finished runs' files and lost the spec an earlier session ran. It also derived its
    counter from a possibly corrupt file (**dev-critic, HIGH**).
  - (f) is over-engineered for one consumer.
  - (e) keeps `graph_version` computable from `state.json` alone, so the 3 s poll never opens a
    snapshot. It also makes a run reproducible "from spec + artifacts", a CLAUDE.md principle.
- **Consequences:**
  - The file carries the same sensitivity as the spec (instructions, hook argv; no secret fields by
    design). It is retained and deleted with the run dir, and never served raw.
  - Pre-epic runs (no `spec_sessions`) render with `source: "unavailable"`, meaning all nodes and
    injected-task edges plus a banner. The launch-record fallback was **removed**.

## D3: One edge-derivation implementation, `dag.iter_dependency_edges`, consumed by `build_dag` and the dashboard

- **Options:**
  - (a) re-implement edge rules in `ui/graph.py`
  - (b) call `build_dag` and read the adjacency
  - (c) extract a typed edge iterator that `build_dag` consumes
- **Decision:** (c). Edge kinds are `explicit | loop | inferred`, with `via`.
- **Reason:**
  - (a) drifts.
  - (b) loses edge kinds.
- **Consequences:** Behavior identity of `build_dag` is a hard acceptance gate. An oracle test over
  every repo spec checks it, including adjacency list order, since order feeds the topological
  order and therefore scheduling.

## D4: Dedicated topology endpoint plus a version token; stats joined client-side

- **Options:**
  - (a) extend `GET /runs/{id}` with edges
  - (b) a `/graph` endpoint that also carries per-task stats
  - (c) a `/graph` endpoint carrying topology only, plus `RunDetail.graph_version`
- **Decision:** (c). The client refetches `/graph` only when `graph_version` changes. Status and
  cost come from the existing 3 s `RunDetail` poll.
- **Reason:**
  - (a) resends topology every poll.
  - (b) duplicates the per-task source of truth.
  - (c) keeps one source per datum and makes live runs cheap.
- **Consequences:**
  - The client joins by id and tolerates a one-poll skew ("stats pending").
  - Degraded modes are `200` with `warnings[]`, never 5xx.

## D5: Frontend canvas = `@xyflow/react` 12 + `@dagrejs/dagre` 3 (layered auto-layout)

- **Options:** React Flow, Cytoscape.js, vis-network, Sigma/graphology, raw D3 (+ d3-dag), a
  hand-rolled SVG canvas; for layout, dagre, elkjs, or force.
- **Decision:** React Flow for the canvas and interaction, dagre for layout. Layout sits behind one
  **async** function (`layout.ts::computeLayout(...) → Promise<{positions, direction}>`), so dagre
  can be replaced by elkjs (async/worker) without changing callers. React Flow itself is a
  **firm dependency** (dev-critic). Only the layout algorithm is designed to be swappable.
- **Reason:**
  - React Flow gives React-native rich, accessible, focusable HTML nodes.
  - It brings pan, zoom, minimap, and NodeToolbar out of the box, and is fine at ~160–300 nodes.
  - It is MIT, `react >=17` peer (checked: 12.12.0), and needs no CSP change (inline style
    attributes are already allowed; no eval or workers).
  - dagre is deterministic, small, and synchronous.
  - Canvas and WebGL renderers sacrifice accessibility and jsdom testability for scale we don't need.
  - elkjs is roughly 400 KB+ gzip, and its worker mode would need a CSP change.
  - Force layout is non-deterministic.
- **Consequences:**
  - This relaxes the `ui/README.md` "no runtime deps beyond React" convention again, which was
    already relaxed by E-Fp7Qv2. The bundle budget is ≤ 90 KB gzip added.
  - Exit cost is bounded: the domain model, builder, and layout seam are ours, so only
    `RunGraph.tsx`/`TaskNode.tsx` touch React Flow APIs.

## D6: Detail reveal = hover preview card + click-to-pin side panel

- **Options:** hover tooltip only, side panel only, a modal, or a combination.
- **Decision:** A combination. The hover card (on pointer *or* keyboard focus, delayed) shows id,
  status, duration, cost, and attempts. The pinned side panel shows timing (including wait before
  start), usage, retries and dispatches, spawn parent and children, dependencies and dependents as
  navigable links, and outcome.
- **Reason:**
  - Hover-only fails at 160-node density, on touch, and for keyboard users, and cannot hold links.
  - Panel-only makes scanning slow.
  - A modal blocks the canvas.

## D7: Auto-layout by default; draggable nodes; no persisted positions in MVP

- **Options:** hand placement (Obsidian-style), force layout, or layered auto-layout.
- **Decision:** Layered auto-layout: dependency view left-to-right, spawn view top-to-bottom.
  Dragging is allowed for exploration. "Reset layout" is available. Persistence is non-MVP.
- **Reason:**
  - The user's "Obsidian Canvas" reference is about the free-flowing, non-paginated surface.
  - Nobody hand-places 160 nodes.
  - Deterministic layout means the same run looks the same every visit.

## Consequences (overall)

- Additive engine changes only: two new `RunState` fields (`spawned_by`, `spec_sessions`) and
  write-once run-dir snapshot files. There is no workflow spec schema change, and `ao validate` is untouched.
- `/graph` is a *read model* of a run, not a spec round-trip format. The deferred in-browser DAG
  **editor** can reuse the canvas components (which take generic view-models) and the layout seam,
  but needs its own spec-shaped API. This epic adds no mutation surface.
- Provenance and snapshot data sit in the agent-writable workspace. They are observability data,
  not a security boundary, and the dashboard treats them as untrusted input (bounded parse,
  text-only render).
- Follow-ups:
  - `route` loss on resume (finding F-2)
  - an optional `TaskSpec.title`
  - subtree collapse
  - critical path
  - layout persistence
  - a possible timeline view. It would need per-dispatch interval history, an additive field
    **declined** here (see the HLD R-10).

## Implementation note (2026-09-27, `T-oroE5f` reconciliation)

All seven decisions shipped as decided. The full deviation list and follow-ups are in
[`run-graph-canvas-hld.md`](../run-graph-canvas-hld.md) §0, and the claim-to-citation evidence is in
`meta/tickets/E-k3AMEr-run-graph-canvas/T-oroE5f-docs-refresh/STATUS.md`. The points that touch
this ADR's decisions:

- **D1 held.** `SpawnRecord.origin` is an open `str`. The engine writes only the typed constants
  `SPAWN_ORIGIN_INJECTED` / `SPAWN_ORIGIN_LOOP`. `_inject` takes a keyword-only, required
  `parent_task_id`, with exactly 2 call sites. `route` is still not carried across resets
  (finding F-2 remains an open follow-up).
- **D2 held, and was hardened.**
  - The session is recorded before the run's first save.
  - The snapshot file is write-once.
  - The `spec_sha256` read back from the agent-writable `state.json` is validated against
    `^[0-9a-f]{64}$` before it is spliced into a filename. This was the Gate G1 SHOULD-FIX.
  - The filename helper re-checks the sha as a second layer (Gate G2 L-3).
  - Every tolerant read failure logs `run.workflow_snapshot_unavailable`.
  - The launch-record fallback stays removed. Nothing reads launch records for the graph.
- **D3 held.** `iter_dependency_edges` is the one edge implementation. `build_dag` consumes it, and
  an oracle test proves identical adjacency, including order. `output_to_task` is computed once
  (the Gate G1 DRY fix).
- **D4 held**, with one gap in the anti-spoofing layer.
  - The server-side `display_text` strips **every** Unicode Cc/Cf codepoint through a
    `unicodedata`-derived table. It replaced an enumerated regex that missed 150 codepoints
    (Gate G2 M-1, fixed).
  - The **client-side mirror (`model.ts::displayText`) was not built** (Gate G2 L-1, deferred).
    `TaskDetailPanel`'s label fallback shows a raw id, still as a React text node, only for an id
    that is not a graph node.
  - `compute_graph_version` has no explicit size-bound justification yet (Gate G2 L-4, deferred).
- **D5 held.**
  - Runtime deps: `@xyflow/react` `^12.12.0` and `@dagrejs/dagre` `^3.1.0`, both MIT.
  - The lazy `RunGraph` chunk is about 82.5 KB gzip, under the ≤ 90 KB budget.
  - `SPA_CSP` is unchanged. ASSUMPTION A-5 was **verified** by a real-Chrome smoke test with a
    negative control.
  - The smoke test stays **opt-in**, behind `@pytest.mark.browser` and the new optional `browser`
    extra (`playwright>=1.45`, system Chrome).
  - Exit cost: React Flow is imported by 4 files, all inside `ui/src/graph/`: `RunGraph.tsx`,
    `TaskNode.tsx`, `TaskHoverCard.tsx`, and `hooks.ts`. dagre is imported only by `layout.ts`.
  - The consequence above that "only `RunGraph.tsx`/`TaskNode.tsx` touch React Flow APIs" was
    already inaccurate at design time, because the hover card uses `NodeToolbar`.
- **D6 held, with one intentional expansion.** Selecting a toolbar search result also opens the
  pinned panel, because both share one `selectedNodeId`. Gate G3 judged this coherent.
- **D7 held.** Nodes are draggable, and "Reset layout" clears drag offsets. Nothing is persisted
  (non-MVP `T-N8scZK`).
- **Consequence debt (Gate G3 Warning #1, deferred):** the "components take generic view-models"
  claim in the overall consequences is only partly true as shipped. `TaskHoverCard` and
  `TaskDetailPanel` take the raw `RunGraph`. Fix this before the deferred DAG-editor reuses them.
