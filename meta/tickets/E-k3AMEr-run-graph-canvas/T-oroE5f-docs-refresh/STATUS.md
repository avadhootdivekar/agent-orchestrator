# STATUS

- ID: `T-oroE5f-docs-refresh`
- Updated At: `2026-09-27`
- State: `In Review` (doc reconciliation complete; AC-5 reviewer sign-off in progress)
- Owner: `architect` (execution) · `reviewer` (AC-5 sign-off)
- Scope: `MVP (mandatory, last)` · Sprint: `S2` · Estimate: `6 h`

## This update
- By: architect · Role: architect · Date: 2026-09-27 · Comment: **All 8 documents reconciled
  against the merged code.** The code baseline is `ad/run-graph-canvas` @
  `dbd36570288fd3518e4b0a7e76ac85715e172212`. This task changed no code under `src/`, `ui/src/`,
  or `tests/`, so every `path:line` below holds at both `dbd3657` and this task's own commit. The
  worktree started on `main` @ `191da69`, so I first fast-forwarded it to `dbd3657`, with no merge
  commit and no conflicts.
  - **Deviations found:** 15 (DV-1 to DV-15, HLD §0.3). Three of them were **never carried into
    any task AC** and were silently dropped between design and implementation, so they were not in
    any gate report:
    - DV-7: the dependency-cycle back-edge warning
    - DV-8: the "+N tasks" live-growth notice
    - DV-9: failed-upstream edge dimming and selection edge emphasis

    None of the three is a code *bug*. The code behaves correctly without them (a cycle cannot
    crash the builder, and layout tolerates cycles). They are recorded as backlog notes FU-6 to
    FU-8.
  - **Follow-ups listed:** 13 (FU-1 to FU-13, HLD §0.4), each with its ticket ID or backlog note.
    They include F-2, R-10, Gate G2 L-1/L-4, Gate G3 Warnings #1/#3 and the Suggestions, the 4
    non-MVP tickets, the optional CI wiring, and the E-Grpp0X coordination.
  - **AC-3 grep is clean** (see Evidence).
  - Ticket-doc inconsistencies found are listed under "Risks / Blockers". **I did not fix them**:
    they are out of this task's change boundary and go to `dev-epic`.

## Documents changed

| # | Document | Change |
|---|---|---|
| 1 | `docs-md/run-graph-canvas-hld.md` | Status → **Implemented**. New **§0 "Implementation outcome and deviations"**: §0.1 what shipped, plus measured outcomes; §0.2 A-5 VERIFIED and browser-smoke OPEN_QUESTION RESOLVED (opt-in); §0.3 a 15-row deviation table; §0.4 a 13-row follow-up table. Inline "as shipped" corrections in §3 A-5, §5.2 (README line), §7.3 (`hooks.ts`), §7.5 (DV-2/DV-12), §8.1 (shipped anchors), §8.2 (shipped reader behavior and anchors; ADR-0011 inventory now done), §8.3.2 (translation table, spawn-data constants, `display_text`, the cycle-warning edge case), §8.5 (`displayText` not built; extra `model.ts` exports; the "+N" notice), §8.6 (edge dimming), §8.7 (search-select opens the panel), §14.1 (`_workflow_snapshot_filename`), §15 (a new `run.workflow_snapshot_unavailable` event), §16 (the `browser` extra), §17 (`write_synthetic_run` signature), §19 D-5 (grep is a gate check, not a test), §23 R-8 closed, OPEN_QUESTION resolved, F-2 still open, and §25 outcome. |
| 2 | `docs-md/adr/ADR-0017-run-graph-provenance-snapshot-and-canvas.md` | Status → **Accepted (implemented)**. New "Implementation note" covering D1–D7 as shipped: the G1/G2 hardening, the D4 client-mirror gap, D5 measurements and the exit-cost correction (4 React Flow importers), the D6 search-opens-panel expansion, and the G3 Warning #1 consequence debt. |
| 3 | `docs-md/dashboard-and-general-instructions-hld.md` | §2.5: the runtime-deps line corrected (it was already stale since E-Fp7Qv2), plus a run-graph paragraph on the Table/Graph tab, lazy chunk, and gating on `graph_version`. §2.6: the `/runs/{id}/graph` row, the `graph_version` note on `/runs/{id}`, and a "Run graph additions" block (status codes, `graph_version`, `TaskStat` fields). §4: the read-only half marked **delivered**, with a link to the epic, and the editor half still deferred with its G3 W#1 prerequisite. |
| 4 | `meta/ROADMAP.md` §3.3 | The read-only half is marked **delivered**. The editor half stays **deferred**, with G3 Warnings #1/#3 named as prerequisite debt. |
| 5 | `docs-md/guide-dynamic-task-injection.md` | New section "Seeing who spawned what: spawn provenance (`spawned_by`)" covering the record shape, emit and nested emit parents, **loop-clone parent = gate of the previous iteration** (A-3), router ≠ spawn, resume survival, legacy runs, how to view it (Graph tab → "Spawned by", the panel, `curl /graph`), and the trust note. Also fixes the engine line anchors in "Settle ordering", which this epic's engine edits had shifted. |
| 6 | `README.md` (Dashboard section) | A "Run graph" bullet with usage. The "Deferred" line now says the graph is read-only. |
| 7 | `ui/README.md` | Layout: a `graph/` subtree with each file's role. The dependency-policy line is replaced: it names every runtime dep (`react`/`react-dom`; `dompurify`/`marked`/`highlight.js` → ADR-0011; `@xyflow/react`/`@dagrejs/dagre`, MIT → **ADR-0017 D5** link) plus the rule for adding one. |
| 8 | `docs-md/adr/ADR-0011-untrusted-workspace-content-rendering.md` | New addendum: a **run-directory sensitivity inventory**. It covers `workflow.snapshot.<sha12>.json`, the new `state.json` fields, and agent-authored ids/labels (text-only rendering, the server sanitizer, and the L-1 gap), and relates them to D1–D3. No sensitivity inventory existed anywhere before; the HLD's reference to one was forward-looking, so it was created here, in the ADR the task names first. |

## Evidence

### AC-1: claim → citation (all at `dbd3657`; this task touched no code)

| # | Claim added or changed in the docs | Citation |
|---|---|---|
| 1 | `SpawnRecord` has `parent_task_id`, `parent_dispatch_cycle`, `origin: str` (open), `injected_at`, `loop_id`, `iteration` | `src/agent_orchestrator/models.py:965-982` |
| 2 | Engine writes only typed `SPAWN_ORIGIN_INJECTED`/`SPAWN_ORIGIN_LOOP` | `src/agent_orchestrator/models.py:961-962`; `src/agent_orchestrator/engine.py:2088,2205` (via `origin=` at both call sites) |
| 3 | `RunState.spawned_by` / `RunState.spec_sessions` defaulted (old state loads) | `src/agent_orchestrator/models.py:1108,1113` |
| 4 | `SpecSession` / `WorkflowSnapshot` (schema_version 1) models | `src/agent_orchestrator/models.py:985,1002,1013` |
| 5 | `_inject` keyword-only required `parent_task_id`; loop pairing assert; one timestamp per batch; record in same per-spec step | `src/agent_orchestrator/engine.py:4153,4192-4196,4207-4214` |
| 6 | Exactly 2 `_inject` call sites (emit, loop), parent = `tid` | `src/agent_orchestrator/engine.py:2084-2090,2201-2209` |
| 7 | Loop-clone parent = gate task of the previous iteration (iter-3 clones → `gate__iter2`) | `src/agent_orchestrator/engine.py:2180-2209`; `tests/test_spawn_provenance.py:210,247-249` |
| 8 | Router activation is not an injection site | `src/agent_orchestrator/engine.py:3155` (`_on_router_success`, no `_inject` call — `grep -n "self._inject(" engine.py` returns only 2084/2201) |
| 9 | `origin` carried across 3 resets; `route` NOT carried (F-2 open) | `src/agent_orchestrator/runstate.py:491-506`; `src/agent_orchestrator/engine.py:1073-1076,1136-1143` |
| 10 | Mixed-version unknown `origin` value loads | `tests/test_spawn_provenance.py:630-637` |
| 11 | `record_spec_session` before first save; `OSError` → warn `run.snapshot_failed` | `src/agent_orchestrator/engine.py:683,694-701,703` |
| 12 | Snapshot write-once, atomic tmp+replace; `run.spec_changed_on_resume` on sha change | `src/agent_orchestrator/runstate.py:330-384,386-395` |
| 13 | Snapshot constants (prefix/suffix/12 sha chars/schema 1/20 MB cap) | `src/agent_orchestrator/runstate.py:37-43` |
| 14 | Sha validated `^[0-9a-f]{64}$` BEFORE path construction (G1); tolerant warn+None | `src/agent_orchestrator/runstate.py:54,85-107` |
| 15 | `_workflow_snapshot_filename` raises `ValueError` on malformed sha (G2 L-3) | `src/agent_orchestrator/runstate.py:66-82`; `tests/test_workflow_snapshot.py:547` |
| 16 | Size cap via `stat()` before parse; every tolerant branch logs `run.workflow_snapshot_unavailable` | `src/agent_orchestrator/runstate.py:109-155` |
| 17 | `dag.iter_dependency_edges` / `DependencyEdge` / `EDGE_KIND_*`; `output_to_task` computed once | `src/agent_orchestrator/dag.py:18-35,158-171,174-225,228` |
| 18 | Builder constants: `GRAPH_SCHEMA_VERSION=1`, `GRAPH_MAX_NODES=5000`, `GRAPH_LABEL_MAX_CHARS=200`, `GRAPH_VERSION_HEX_CHARS=16`, sources, `SPAWN_DATA_*` | `src/agent_orchestrator/ui/graph.py:32-45` |
| 19 | Sanitizer is a `unicodedata` Cc/Cf translation table (G2 M-1), not a regex | `src/agent_orchestrator/ui/graph.py:79-94,187-200`; `tests/test_ui_graph.py:495,521` |
| 20 | `label_for` returns the id (title seam) | `src/agent_orchestrator/ui/graph.py:203-211` |
| 21 | `compute_graph_version` covers latest sha, injected ids, task ids, spawned ids, loop iterations; 16 hex; no cap (G2 L-4) | `src/agent_orchestrator/ui/graph.py:219-241` |
| 22 | Early node cap before edge derivation | `src/agent_orchestrator/ui/graph.py:319-326,340` |
| 23 | No dependency-cycle back-edge warning in the builder (DV-7) | `src/agent_orchestrator/ui/graph.py:293-459` (no cycle/DFS logic over `dep_edges`; the only cycle guard is spawn-depth BFS at `:249-280`); layout tolerates cycles `ui/src/test/graph-layout.test.ts:71` |
| 24 | Forged spawn cycle terminates via visited set | `src/agent_orchestrator/ui/graph.py:249-280`; `tests/test_ui_graph.py:582,593` |
| 25 | `load_graph` does the I/O via shared reader; logs `ui.graph.degraded`; no launch-record fallback | `src/agent_orchestrator/ui/runs.py:336-362` |
| 26 | 404 for unknown/traversal/unreadable run | `src/agent_orchestrator/ui/runs.py:197-205,222-234`; `src/agent_orchestrator/ui/service.py:423-438`; `src/agent_orchestrator/ui/app.py:224-229` |
| 27 | `RunDetail.graph_version` via the same `compute_graph_version` | `src/agent_orchestrator/ui/runs.py:145,333` |
| 28 | `TaskStat.dispatch_cycle` / `not_taken_reason` additive | `src/agent_orchestrator/ui/runs.py:86-87,309-310`; `ui/src/types.ts:122-123` |
| 29 | `SPA_CSP` unchanged by this epic | `src/agent_orchestrator/ui/security.py:94` (`git diff 191da69 dbd3657 -- src/agent_orchestrator/ui/security.py` empty) |
| 30 | Browser smoke opt-in: `browser` extra `playwright>=1.45`, `browser` marker | `pyproject.toml:29-31,80` |
| 31 | Smoke drives system Chrome (`channel="chrome"`), skips if playwright/Chrome absent, 180 nodes | `tests/ui/test_e2e_graph.py:292-298,314,406,446` |
| 32 | Negative control imports the live `SPA_CSP` | `tests/ui/test_e2e_graph.py:27,536,559` |
| 33 | A-5 verified (zero violations + negative control) | `meta/tickets/E-k3AMEr-run-graph-canvas/STATUS.md:135-150` (T-F1caAt merge re-verification, 2/2 browser tests passed) |
| 34 | Runtime deps `@xyflow/react ^12.12.0`, `@dagrejs/dagre ^3.1.0` (+ existing dompurify/marked/highlight.js/react) | `ui/package.json:15-23` |
| 35 | Lazy chunk ≈ 79.4 KB JS + 2.0 KB CSS gzip (-9); 82.55 KB as reported by Vite | measured this task: `gzip -9c src/agent_orchestrator/ui/static/assets/RunGraph-DLlnaNAN.js \| wc -c` → 79361, `RunGraph-CeJeskZg.css` → 2037; Vite figure `meta/tickets/E-k3AMEr-run-graph-canvas/STATUS.md:245-247` |
| 36 | Graph tab lazy (`React.lazy` + `Suspense`), shown only when `graph_version` truthy, Table default, tab persisted | `ui/src/components/RunDetail.tsx:1,16,105-110,230,253-260`; `ui/src/graph/model.ts:399-404` |
| 37 | Prefs key `ao.runGraph.prefs.v1`; view/metric/showUnrelated/tab persisted | `ui/src/graph/model.ts:40,392-404,424-454` |
| 38 | Search-select also opens the panel (shared `selectedNodeId`) — DV-1 | `ui/src/graph/RunGraph.tsx:262,271-273,550-554,686-687`; `ui/src/graph/hooks.ts:28,40-83` |
| 39 | Node click opens panel | `ui/src/graph/RunGraph.tsx:424-428` |
| 40 | `TaskHoverCard`/`TaskDetailPanel` take raw `RunGraph` (DV-2) vs. `types.ts` boundary claim | `ui/src/graph/TaskHoverCard.tsx:19-24`; `ui/src/graph/TaskDetailPanel.tsx:25-28`; `ui/src/types.ts:264-275` |
| 41 | `model.ts::displayText` not implemented; `labelFor` falls back to raw id (DV-3) | `ui/src/graph/TaskDetailPanel.tsx:53-60` (`grep -rn displayText ui/src/graph/` empty) |
| 42 | Residual `"pending"` literal in `statusFor` (FU-10) | `ui/src/graph/TaskDetailPanel.tsx:62-63`; shared constant `ui/src/graph/model.ts:214` |
| 43 | No "+N tasks" notice (DV-8) | `ui/src/graph/RunGraph.tsx` (`grep -n "added\|notice"` empty; fetch/relayout at `:276,285-287`) |
| 44 | No failed-upstream dimming / selection emphasis; arrow on every edge (DV-9) | `ui/src/graph/RunGraph.tsx:213-248` (`edgeVisualKind`/`toRfEdges`: class by set/kind only, `markerEnd` unconditional at `:245`) |
| 45 | Metric strip via `TaskNodeWithMetric` wrapper (DV-10) | `ui/src/graph/RunGraph.tsx:119,132,149` |
| 46 | Inferred-edge path as on-path label truncated to 24 chars (DV-11) | `ui/src/graph/RunGraph.tsx:104,203-206,222-231` |
| 47 | React Flow imported by 4 files; dagre only by `layout.ts`; `model.ts` React-free (DV-12) | `ui/src/graph/RunGraph.tsx:46`, `ui/src/graph/TaskNode.tsx:15`, `ui/src/graph/TaskHoverCard.tsx:13`, `ui/src/graph/hooks.ts:10`, `ui/src/graph/layout.ts:15` |
| 48 | Spawn loop edge label `iter N`; panel shows `loop: <id> · iteration N` (DV-13) | `ui/src/graph/RunGraph.tsx:230`; `ui/src/graph/TaskDetailPanel.tsx:324-326` |
| 49 | "Reset layout" clears drag overrides (DV-14) | `ui/src/graph/RunGraph.tsx:403-408`; `ui/src/graph/GraphToolbar.tsx:131` |
| 50 | `write_synthetic_run(root, waves, fanout, *, clock)` (DV-15) | `tests/ui/graph_fixtures.py:22` |
| 51 | Canvas config: `minZoom 0.05`, `maxZoom 2`, `onlyRenderVisibleElements > 300`, MiniMap/Controls/Background, base.css | `ui/src/graph/RunGraph.tsx:47,101-102,661-672`; `ui/src/graph/model.ts:33` |
| 52 | Unsupported-schema banner | `ui/src/graph/RunGraph.tsx:116,605` |
| 53 | Panel: 720 px breakpoint, "show all (N)", copy button, "started #n (latest dispatch)", "Stats pending", "hidden characters removed" | `ui/src/graph/TaskDetailPanel.tsx:36-37,134,143-161,217,247,252` |
| 54 | Hover card uses `NodeToolbar`; retries via shared `retriesFromAttempts` | `ui/src/graph/TaskHoverCard.tsx:13,16,43,46` |
| 55 | "Show N unrelated tasks" checkbox only in spawn view | `ui/src/graph/GraphToolbar.tsx:80-90` |
| 56 | View labels "Execution order" / "Spawned by", radiogroup | `ui/src/graph/RunGraph.tsx:151-154,160,181` |
| 57 | Version-gated refetch (D-3) | `ui/src/graph/RunGraph.tsx:276,285-287` |
| 58 | jsdom React Flow shims in `setup.ts` | `ui/src/test/setup.ts:4-17` |
| 59 | Markup-in-id / markup-in-warning renders literally (tests) | `ui/src/test/task-detail-panel.test.tsx:311-333`; `ui/src/test/graph-banners.test.tsx:33-43` |
| 60 | No `dangerouslySetInnerHTML`/`innerHTML` in `ui/src/graph/` (only a comment match) | `ui/src/graph/TaskNode.tsx:171` is a comment (`grep -rn "dangerouslySetInnerHTML\|innerHTML" ui/src/graph/`) |
| 61 | `.json` files render in the file browser as highlighted code, not HTML | `ui/src/components/viewer/CodeView.tsx:13,36` |
| 62 | Contract test pins fixture ↔ dataclass | `tests/ui/test_graph_contract.py:47,56,60` |
| 63 | Perf: build 0.89 ms, layout 136 ms (200/500); CI budgets 450/900 ms | `meta/tickets/E-k3AMEr-run-graph-canvas/T-F1caAt-graph-e2e-verification/STATUS.md:136-137`; `tests/test_ui_graph.py:738-749`; `ui/src/test/graph-layout.test.ts:112` |
| 64 | Test totals: 4621 passed / 8 skipped; UI coverage 93.91%; vitest 224 | `meta/tickets/E-k3AMEr-run-graph-canvas/STATUS.md:74-77,129-131,156-158` |
| 65 | Guide "Settle ordering" anchors: injection block L2046-2104, save L2106, `evaluate_breakers` L2127, `task.injected` L2168-2176 | `src/agent_orchestrator/engine.py:2046,2104,2106,2127,2168-2176` |
| 66 | Legend "spec changed" is regex-matched from `warnings[]` (FU-10) | `ui/src/graph/Legend.tsx:104-110` |

### AC-2: HLD "Implementation outcome" section
- `docs-md/run-graph-canvas-hld.md` §0.3 lists 15 deviations (DV-1 to DV-15), and §0.4 lists 13
  follow-ups (FU-1 to FU-13). The follow-ups include F-2 route-on-resume (FU-1), R-10 (FU-2), the
  4 non-MVP tickets by ID (FU-11), Gate G2 L-1/L-4 (FU-4/FU-5), Gate G3 W#1/W#3/Suggestions
  (FU-3/FU-9/FU-10), and the CI-vs-opt-in resolution (§0.2 + FU-12).

### AC-3: grep
```
$ grep -rn "launch_record\|write_workflow_snapshot" docs-md/
(no output, exit 1)
```
Every remaining prose "launch record" mention in the HLD and ADR-0017 is in a rejected-alternative
or historical context: HLD §7.4, §8.3.2, §9 D2, §23.1; ADR-0017 D2 options/reason/consequences
and the implementation note.

### AC-4: ticket consistency
- I checked every task's `TASK.md` Status against its `STATUS.md` State, and against the
  `EPIC.md` checkboxes and the epic `STATUS.md` rollup. The **task-level Done/Draft words and the
  counts agree** (10 Done, 5 Draft, 15 total). The stale text I found is listed under
  Risks / Blockers for `dev-epic` to reconcile. I did not change it (out of scope).

### AC-5: reviewer sign-off
- Pending. Dispatched to `reviewer` against this task's commit (see "Next actions").

## Risks / Blockers
- **Ticket-doc inconsistencies found and NOT fixed** (outside this task's boundary; for
  `dev-epic`):
  1. `EPIC.md:9` Status still reads "Sprint 1 implemented and merged …; Gate G1 next". All three
     gates have since closed.
  2. `EPIC.md:144-145` still lists "browser smoke in CI or opt-in?" as an open question. It was
     resolved as opt-in (epic `STATUS.md:451-455`).
  3. Epic `STATUS.md:5` State says "only T-oroE5f docs-refresh + Gates G2/G3 remain", but G2/G3 are
     closed. `STATUS.md:21` says T-oroE5f is "blocked on Gate G3", which is also closed.
  4. `T-M4qboy-run-graph-builder/TASK.md:9` Status says "Gate G1 reviewer/tester sign-off
     pending". T-M4qboy was never in G1's scope, and its `STATUS.md:5` says `Done`.
  5. `T-F1caAt-graph-e2e-verification/STATUS.md` reports numbers from an earlier round that the
     epic STATUS supersedes:
     - fixture "162 nodes" (lines 139, 148), where the final fixture is 180
       (`tests/ui/test_e2e_graph.py:446`)
     - "npm test: 129 tests pass" (line 206), where it is 224
     - "4612 passed, 10 skipped" (line 183), where it is 4614 passed / 8 skipped at merge
     - smoke runtime "~10 s", where the epic records ≈ 7.5 s
- **Small code-hygiene residue (not a bug; not fixed, per the change boundary):**
  `ui/src/graph/TaskDetailPanel.tsx:63` still uses a literal `"pending"` instead of
  `PANEL_PENDING_STATUS`. Gate G3 Warning #2 was recorded as fully resolved. Logged as FU-10.
- **No code bugs found.** DV-7/8/9 are unimplemented design details, not defects: the shipped
  behavior is safe and correct without them.
- Pre-existing doc drift outside this epic, noted but not changed: `ui/README.md`'s layout omits
  `components/TemplateLaunch.tsx` and `components/viewer/`, which predate this epic.

## Next actions
1. `reviewer` signs off on the reconciled docs (AC-5). Record the outcome here, then set
   `Done` in both this file and `TASK.md`.
2. `dev-epic` reconciles the ticket inconsistencies above, rolls this task up into `EPIC.md` and
   the epic `STATUS.md`, and closes the epic.
