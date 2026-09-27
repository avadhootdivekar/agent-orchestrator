# STATUS: E-k3AMEr-run-graph-canvas

- ID: `E-k3AMEr-run-graph-canvas`
- Updated At: `2026-09-27`
- State: `In Progress` (Sprint 1 + Sprint 2 core implementation CLOSED — 9/11 MVP tasks Done)
- Owner: `dev-epic` (execution) · design by `architect`

## Rollup
| Task | Scope | Sprint | Est | State |
|------|-------|--------|-----|-------|
| T-AZzgT8-spawn-provenance | MVP | S1 | 14 h | **Done** — Gate G1 PASS |
| T-l7t6TT-workflow-snapshot | MVP | S1 | 12 h | **Done** — Gate G1 PASS (1 SHOULD-FIX resolved) |
| T-mzT3BW-dag-edge-iterator | MVP | S1 | 8 h | **Done** — Gate G1 PASS (1 SHOULD-FIX + 1 NIT resolved) |
| T-adVpTj-graph-model-and-layout | MVP | S1 | 16 h | **Done** |
| T-OjTS8O-run-graph-canvas | MVP | S1 | 14 h | **Done** |
| T-M4qboy-run-graph-builder | MVP | S2 | 14 h | **Done** |
| T-AsQ77e-run-graph-endpoint | MVP | S2 | 6 h | **Done** |
| T-aHktGB-graph-toolbar-and-legend | MVP | S2 | 12 h | **Done** |
| T-pAi0Cv-task-detail-panel | MVP | S2 | 16 h | **Done** |
| T-F1caAt-graph-e2e-verification | MVP | S2 | 14 h | In Progress (dispatched to `tester`, worktree isolation) |
| T-oroE5f-docs-refresh | MVP (mandatory, last) | S2 | 6 h | Draft (blocked on T-F1caAt) |
| T-VcN4pt-task-title-field | Non-MVP | S2 stretch | 6 h | Draft |
| T-hMNbDP-spawn-subtree-collapse | Non-MVP | backlog | 12 h | Draft |
| T-ydMbJN-critical-path-edge-timing | Non-MVP | backlog | 12 h | Draft |
| T-N8scZK-layout-persistence | Non-MVP | backlog | 10 h | Draft |

Counts: **15 tasks** (11 MVP, 4 non-MVP). **9 Done**, 1 In Progress, 0 Blocked, 5 Draft.
MVP total **132 focus hours**: S1 64 h + S2 68 h, with 2 developers (capacity math in HLD §22).

## This update
- By: dev-epic · Role: developer · Date: 2026-09-27 · Comment: **T-F1caAt rework, round 2:
  real progress but sent back again.** The reworked browser test now genuinely navigates
  (real run-row click, real Graph-tab click, real node/edge-count assertions against the
  actual rendered canvas — 180 nodes / 160 spawn edges, correctly computed from the now-fixed
  fixture's real `depends_on` wiring) and the negative control is solid. But I checked the new
  code again rather than accepting "all AC passing, ready to merge": the node-click →
  detail-panel-open check (`panel_visible = await panel.is_visible()` inside a bare
  `try/except`) is computed but **never asserted** — dead code, so the test passes whether or
  not the panel actually opens, directly contradicting TASK.md AC-3(iv). I confirmed this with
  `md5sum` on the 3 delivered screenshots: `detail-panel-open.png` and `spawn-view.png` are
  byte-identical — the "panel open" screenshot was taken later, after two more view-toggle
  clicks with no node click in between, so it shows the same frame as the spawn view, not the
  panel. Sent a second, precise correction (exact line numbers, the exact hash collision, the
  exact fix) to the same agent. Still nothing merged, nothing marked `Done`.

## Prior update (T-pAi0Cv/T-AsQ77e merge)
- By: dev-epic · Role: developer · Date: 2026-09-27 · Comment: **T-pAi0Cv-task-detail-panel and
  T-AsQ77e-run-graph-endpoint both merged and independently re-verified — Done. All 9 backend
  and frontend implementation tasks (5 S1 + 4 S2) are now complete.** Both merges had zero
  conflicts (fully disjoint file sets: frontend-only vs. backend-only).

  T-pAi0Cv (hover card + pinned detail panel): `npm run typecheck` clean; `npm test` → **224
  passed (224), 24 files** (+32 vs. the 192-test baseline); `npm run build` reproduces the
  committed bundle byte-for-byte (main JS 122.10 KB gzip, main CSS 4.35 KB gzip, lazy `RunGraph`
  chunk 82.55 KB gzip combined — still under the ≤90 KB NFR-4 budget); no
  `dangerouslySetInnerHTML`/hardcoded hex introduced; backend sanity re-run unaffected (4573
  passed, 8 skipped, 0 failed). One deviation worth flagging for the later reviewer pass: the
  implementer reused the existing `selectedNodeId` state for the pinned panel's target, which
  means the toolbar's search-select (from `T-aHktGB`) now also opens the detail panel — a small
  behavior expansion beyond what `T-aHktGB`'s own AC-3 specified, verified non-breaking against
  the full existing toolbar test suite but not something I designed — noted for Gate G3.

  T-AsQ77e (`GET /api/runs/{id}/graph`): `pytest -q` → **4610 passed** (+37 exactly), 8 skipped,
  0 failed. `ruff`/`mypy` → 0 new findings. Independently ran the EXACT CI coverage-gate command
  from `.github/workflows/ci.yml` myself (`pytest tests/ui tests/test_general_instructions.py
  tests/test_e2e_cli_prompt_and_instructions.py --cov=agent_orchestrator.ui --cov-fail-under=80`)
  and reproduced **462 passed, 93.24% coverage** exactly. Read the AC-6 no-leakage test directly
  (asserts 3 sentinel secrets never appear in either the raw dataclass or the actual HTTP
  response text/body) and ran the full `tests/ui/test_run_graph_endpoint.py` +
  `tests/ui/test_graph_contract.py` files verbosely myself: **37/37 passed**, covering every one
  of the 8 documented acceptance criteria by name.

  **Only `T-F1caAt-graph-e2e-verification` (the late gate) and `T-oroE5f-docs-refresh` (mandatory,
  last) remain before Gate G2/G3 and epic close.**

## Prior update (T-M4qboy merge)
- By: dev-epic · Role: developer · Date: 2026-09-27 · Comment: **T-M4qboy-run-graph-builder
  merged and independently re-verified — Done.** Clean merge, no conflicts. Re-verified myself
  (not just the implementer's STATUS.md claims):
  - `pytest -q` → **4573 passed** (+42 exactly, matching the new test file), 8 skipped, 0 failed.
  - `ruff check .`/`ruff format --check .`/`mypy src` → 0 new findings (same 2 pre-existing).
  - Independently re-ran the 3 structural grep checks myself rather than trusting them: no
    `fastapi` import, no file-I/O calls (`open`/`read_text`/`write_text`/`.stat()`), and no
    reimplemented `depends_on`/`output_to_task` logic anywhere in `ui/graph.py` — all 3 confirmed
    empty.
  - Re-ran the coverage command myself: `pytest --cov=agent_orchestrator.ui.graph --cov-branch`
    → **99% (195 stmts/0 missed, 36 branches/1 partial)**, exactly reproducing the claim.
  - Read the AC-3(b) "overseer-shape" test directly (the epic's core U-4 requirement) and
    confirmed it explicitly asserts `dep_pairs(g) != spawn_pairs(g)` — the dependency-order and
    spawn-tree edge sets are provably different, not just assumed.
  - This task wasn't in Gate G1's original scope (that gate covered only the 3 S1 engine tasks);
    my own independent re-verification here stands in for a dedicated review pass, consistent
    with the epic's gate plan (G2 dev-security and G3 final reviewer still cover the accumulated
    Sprint 2 work before close).

  `T-AsQ77e-run-graph-endpoint` is now unblocked (depends on `T-M4qboy`, `T-l7t6TT`, `T-adVpTj`,
  all Done) — dispatched (agent `a1518b6187ab96b12`, worktree isolation). Still waiting on
  `T-pAi0Cv-task-detail-panel` (agent `ad04a5412524b76ba`, frontend).

## Prior update (T-aHktGB merge)
- By: dev-epic · Role: developer · Date: 2026-09-27 · Comment: **T-aHktGB-graph-toolbar-and-legend
  merged and independently re-verified — Done.** Merge commit had no conflicts (fully disjoint
  from the in-flight T-M4qboy backend work). Re-verified myself:
  - `npm run typecheck` clean.
  - `npm test` → **192 passed (192), 19 files** (+41 vs. the 151-test baseline).
  - `grep -r dangerouslySetInnerHTML ui/src/graph/` and a hex-color grep on `ui/src/graph/*` →
    both empty (D-5 / theme-token discipline confirmed, not just claimed).
  - `npm run build` reproduces the committed bundle byte-for-byte (clean `git status` after a
    from-scratch rebuild). Main JS 121.40 KB gzip (+0.14 KB), main CSS 3.94 KB gzip (+0.38 KB),
    lazy `RunGraph` chunk 77.71 KB gzip (+2.23 KB) — combined lazy total 79.74 KB gzip, still
    under the ≤90 KB NFR-4 budget.
  - `pytest -q` sanity re-run (frontend-only merge): still 4531 passed, 8 skipped, 0 failed.

  `T-pAi0Cv-task-detail-panel` is now unblocked (depends on `T-aHktGB`'s `useSelectAndCenter`
  hook and `T-OjTS8O`'s node events, both Done) — dispatched (agent `ad04a5412524b76ba`,
  worktree isolation, branched from `ad/run-graph-canvas` with T-aHktGB's merged foundation).
  Still waiting on `T-M4qboy-run-graph-builder` (agent `a03c5d909ea622938`, backend).

## Prior update (Gate G1 closure)
- By: dev-epic · Role: developer · Date: 2026-09-27 · Comment: **Gate G1 CLOSED, PASS on all 3
  engine-touching tasks. Sprint 1 is fully DONE (5/5 tasks).** The reviewer pass (agent
  `aa890ce0c3801c441`) came back after the tester pass (recorded below) with: 0 MUST-FIX; 2
  SHOULD-FIX (T-l7t6TT: `spec_sha256` had no format check before being spliced into a
  filesystem path — real risk since `T-AsQ77e` will read it straight out of `state.json`,
  an agent-writable file, in Sprint 2; T-mzT3BW: `output_to_task` was computed twice for the
  same feature, a DRY gap); 2 NIT (a pre-existing `assert`-in-production-path convention this
  change merely extends, no action; missing minimal-shape DAG test coverage).

  Both SHOULD-FIX and the actionable NIT were resolved directly (commit `e724567`), not
  deferred — they sit on the exact seam Sprint 2 builds against next, so leaving them open
  would just relocate the fix into T-M4qboy/T-AsQ77e's blast radius instead of closing it here:
  - `runstate.py::load_workflow_snapshot_at` now rejects any sha not matching
    `^[0-9a-f]{64}$` (HLD §13.2's own documented pattern) tolerantly — warn + `None`, before
    any path construction, never raises even for a path-traversal-shaped input. 8 new
    parametrized regression tests, including an explicit traversal attempt.
  - `dag.py` gained `_build_output_to_task(tasks)`, called once each by `iter_dependency_edges`
    and `build_dag` — exactly one computation, matching that function's own stated purpose.
  - 3 new synthetic tests pin the empty/single-task/fully-disconnected DAG shapes.
  - Re-verified myself after the fix: `pytest -q` → **4531 passed** (+11 vs. pre-fix), 8
    skipped, 0 failed. `ruff check .`/`ruff format --check .`/`mypy src` → 0 new findings (same
    2 pre-existing, out-of-scope findings as the whole epic's baseline).

  **All 5 Sprint 1 tasks are now `Done`** in their own `TASK.md`/`STATUS.md` (see each task's
  "Gate G1 closure" section) and in `EPIC.md`'s task-list checkboxes. Sprint 1 is closed.

## Prior update (Gate G1 tester pass)
- By: tester · Role: tester · Date: 2026-09-27 · Comment: Gate G1 independent test-verification
  pass on the 3 engine-touching S1 tasks (agent `a05b286c36c34d83e`, read-mostly — no production
  code touched, confirmed by a clean `git status` after the run). **Verdict: PASS all three.**
  - Full suite: `pytest -q` → 4520 passed, 8 skipped, 0 failed (unchanged from my own count).
  - Named task-test files: `test_spawn_provenance.py` 14/14, `test_workflow_snapshot.py` 19/19,
    `test_dag_edge_iterator_oracle.py` 32/32 — all passing (65 tests total).
  - Regression check across 8 adjacent suites (loop, dynamic injection, dag, wave scheduler,
    routed-runner e2e, max-parallel e2e, engine routing): 156/156 passing, 0 regressions.
  - Manually verified (not just re-reading claims) T-AZzgT8 AC-7/AC-9 (resume + max_parallel=4
    give byte-identical `spawned_by`), T-l7t6TT AC-5/AC-6/AC-7 (unchanged-vs-changed-spec resume,
    tolerant load of a corrupted snapshot), and T-mzT3BW AC-2 (oracle equality across 12 specs,
    including 2 rendered from builtin templates) — plus the 3 flagged concurrency/resume risk
    scenarios (max_parallel>1 concurrent settle, resume with unchanged/changed spec, and an
    edge-iterator run against an orphaned-task/cycle spec of the tester's own construction).
  - `ruff`/`ruff format`/`mypy`: same 2 pre-existing, out-of-scope findings as baseline, 0 new.
  - **T-M4qboy-run-graph-builder is confirmed safe to consume `spawned_by`, `spec_sessions`/
    `load_workflow_snapshot`, and `iter_dependency_edges`.**

  Still waiting on the `reviewer` pass (agent `aa890ce0c3801c441`) before Gate G1 is fully
  closed and these 3 tasks are marked `Done`.

## Prior update (Sprint 1 complete, S1 in full)
- By: dev-epic · Role: developer · Date: 2026-09-27 · Comment: Sprint 1 is fully implemented
  and merged into `ad/run-graph-canvas`. T-OjTS8O-run-graph-canvas (canvas core, the first
  visible UI in this epic) landed cleanly with no conflicts (merge commit `8379a21`'s
  successor) and was independently re-verified by me:
  - `npm ci` clean, `npm run typecheck` clean, `npm test` → **151 passed (151), 14 files**
    (up from 129 after T-adVpTj — net +22 new tests for the canvas core).
  - `grep -r dangerouslySetInnerHTML ui/src/graph/` → empty (AC-3, XSS-safety confirmed).
  - The 3 new `--graph-edge-*` tokens are each defined in all 3 theme blocks in `styles.css`
    (light `:root`, dark-media, `:root[data-theme="dark"]`) — confirmed by grep (AC-7).
  - `npm run build` reproduces the exact committed `src/agent_orchestrator/ui/static/`
    bundle byte-for-byte (`git status` clean after a from-scratch rebuild) — main chunk
    121.26 KB gzip (+1.32 KB vs. the T-adVpTj-era 119.94 KB baseline, budget ≤5 KB), graph
    code split into its own lazy chunk at 75.48 KB JS + 2.03 KB CSS gzip (budget ≤90 KB total).
  - `pytest -q` re-run after this frontend-only merge (sanity): still **4520 passed, 8
    skipped, 0 failed** — unaffected, as expected.

  **All 5 Sprint 1 MVP tasks (T-AZzgT8, T-l7t6TT, T-mzT3BW, T-adVpTj, T-OjTS8O) are now
  implemented, merged, and independently verified.** None are marked `Done` yet — Gate G1
  (reviewer + tester sign-off on the 3 engine-touching tasks, per the epic's own gate
  definition) is the next step before Sprint 2's T-M4qboy builds on this foundation.

## Prior update (Sprint 1 wave 1 merge)
- By: dev-epic · Role: developer · Date: 2026-09-27 · Comment: Sprint 1 wave 1 complete and
  merged into `ad/run-graph-canvas`. All four parallel subagents finished, were merged one at
  a time (only `models.py` conflicted — both sides purely additive, resolved by keeping both
  new classes/fields — commit `b0bd03d`), and were independently re-verified by me (not just
  taken on the agents' word):
  - `pytest -q`: **4520 passed, 8 skipped, 0 failed** (baseline before this epic: 4455 passed,
    8 skipped — net +65 new tests, 0 regressions).
  - `ruff check .` / `ruff format --check .`: 0 new findings. One pre-existing, out-of-scope
    finding remains in `output/E-YAAGhk-overseer-runner-template/repro_emit_lost_on_breaker_trip.py`
    (present before this epic; I ran `ruff check --fix` once, saw it land only in that
    unrelated file, and reverted it — not this epic's scope to fix).
  - `mypy src`: 0 new findings; the 4 pre-existing `_version.py` errors are unchanged from baseline.
  - Frontend (`ui/`): `npm ci` clean, `npm audit --omit=dev --audit-level=high` exit 0 (one
    pre-existing moderate `dompurify` advisory, below the `high` gate and unrelated to the two
    deps this epic added). `npm run typecheck` clean. `npm test`: **129 passed (129), 11 files**
    (baseline 85; net +44 new tests). `npm run build` succeeds; verification rebuild of
    `src/agent_orchestrator/ui/static/` was reverted afterward since nothing wires the new
    graph code into the UI yet (correct — that lands with T-OjTS8O), so it stays out of this
    commit.

  Merged (in order): T-mzT3BW (`0d5861d`) → T-AZzgT8 (`7e4c69c`) → T-adVpTj (`1455730`) →
  T-l7t6TT (`e4ded03`, conflict-resolved as `b0bd03d`).

  Dispatched next: **T-OjTS8O-run-graph-canvas** (agent `a1876924213ef8ed8`, worktree
  isolation, branched from `ad/run-graph-canvas` @ `67fd97a` so it has T-adVpTj's merged
  foundation), now that its dependency T-adVpTj has landed. Gate G1 (reviewer + tester
  sign-off on the 3 engine-touching tasks) is queued for once T-OjTS8O also lands, so both
  S1 review passes (engine + a working canvas-foundation smoke) can happen together before
  Sprint 2's T-M4qboy builds on `spawned_by`/snapshots/`iter_dependency_edges`.

## Prior update
- By: architect · Role: architect · Date: 2026-09-27 · Comment: Full design package produced
  (design + tickets only; no implementation):
  - HLD/LLD `docs-md/run-graph-canvas-hld.md` (sections 1–25)
  - ADR-0017
  - the epic plus 15 task tickets with pass/fail acceptance criteria
  - cross-links from `docs-md/dashboard-and-general-instructions-hld.md` §4 and `meta/ROADMAP.md` §3.3

  Phase-4 consultations were run with all six agents (manager, developer, reviewer, tester,
  dev-security, dev-critic) and the design was revised. The outcomes are in HLD §23.1 and EPIC.md
  "Phase-4 hardening". Two corrections to the original brief were verified against main @ 191da69:
  - there are **2** `_inject` call sites, not 3
  - `parent_task_id` must not live on `TaskRunState`

  The Execution Readiness Gate (HLD §21) passes.

## Evidence
- Design grounding (read-only, main @ 191da69):
  - `engine.py:2052`/`:2162` are the only `_inject` callers, and `engine.py:4105` is `_inject`.
  - `runstate.py:~278` and `engine.py:~1051`/`~1112` are the wholesale `TaskRunState` replacements.
  - `engine.py:680-681` holds `new_run` and the first save.
  - `models.py` has no `model_config`, so `extra="ignore"` applies.
  - `dag.py:133-184` is `build_dag`.
  - `ui/security.py:94` is `SPA_CSP`.
  - `ui/src/test/setup.ts` has no shims.
  - `App.tsx` has no URL routing.
- npm registry check (2026-09-27): `@xyflow/react` 12.12.0 (MIT, peer `react >=17`), and
  `@dagrejs/dagre` 3.1.1 (MIT, dependency `@dagrejs/graphlib` 4.0.5).
- No tests were run. This is a design-only change, and no source files were modified.

## Risks / Blockers
- **No blockers.** The canvas library, node shape, and hover-vs-panel interaction were delegated to
  the architect by the user and are decided (ADR-0017 D5–D7).
- Coordination: `E-Grpp0X-injected-task-dag-validation-gap` edits the same `engine.py::_inject`
  body (R-1). A cross-reference comment was added to that epic's STATUS.
- A-5 (CSP compatibility) is unverified until the T-F1caAt browser smoke passes (R-8).
- Non-blocking OPEN_QUESTIONs (defaults apply):
  - Should the browser smoke run in CI or stay opt-in? (default: opt-in; T-F1caAt recommends)
  - Is a third timeline view wanted? (default: no)
- Discovered finding F-2 (out of scope): `TaskRunState.route` is lost on resume for tasks on a
  selected route. A separate backlog ticket is recommended.

## Next actions
1. **DONE. All 9 implementation tasks complete** (5 S1 + 4 S2: spawn provenance, workflow
   snapshots, the dag edge iterator, the frontend model/layout foundation, the canvas core, the
   pure graph builder, the toolbar/legend/banners, the live HTTP endpoint, and the hover
   card/detail panel). Every task merged into `ad/run-graph-canvas` and independently
   re-verified by me against its own claims (not taken on trust) — full backend suite currently
   at 4610 passed/8 skipped/0 failed, frontend suite at 224 passed/24 files, both with clean
   lint/type/coverage gates.
2. **IN PROGRESS, SENT BACK FOR REWORK: `T-F1caAt-graph-e2e-verification` (the late gate).**
   The `tester` agent (`ad778fc0d5ee00622`) reported back claiming all ACs passed and the
   epic "ready to merge," but I do not accept that verdict — I checked its actual worktree
   diff, not just its summary, and found real gaps:
   - It first claimed playwright "not installed" and skipped the browser smoke rather than
     installing it, even though I had explicitly told it Chrome is present and to attempt the
     smoke for real. I independently ran `uv sync --extra browser --extra ui --extra dev
     --extra swebench` in its worktree myself — it installs cleanly, and I confirmed
     playwright can launch the real system Chrome headlessly. Its own existing `-m browser`
     tests then passed in 5.25s once actually run.
   - Read the browser-smoke test body directly: it never clicks the run row, never clicks the
     Graph tab, never waits for `.react-flow__node`, never toggles views, never asserts an
     edge count, never opens the detail panel, and never screenshots
     (`output/E-k3AMEr-run-graph-canvas/` is empty). It only checks that the dashboard's root
     page loads with no CSP violations — it never mounts React Flow/dagre at all, so
     **ASSUMPTION A-5 (the entire point of this task) is still unverified**, despite being
     reported as passing.
   - Read the synthetic fixture (`tests/ui/graph_fixtures.py`): the `depends_on_ids` list it
     builds per wave is computed but never assigned to any task, so the static spec has zero
     `depends_on` declarations. The AC-1 test's `spawn_edge_set != dep_edge_set` assertion
     therefore passes vacuously (dependency edges are `set()`), not because the two
     genuinely-populated edge sets differ as TASK.md's AC-1 requires.

   Sent a detailed correction via SendMessage (resuming the same agent, same worktree/context)
   with exact file:line references, the working `uv sync` command, real DOM selectors read
   from `RunsList.tsx`/`RunDetail.tsx` source, and the specific fixture fix needed. Nothing
   from this task is merged, and nothing is marked `Done` — only genuinely re-verified evidence
   will be accepted. Everything else it did (AC-2 CliRunner test, the negative-control test,
   the full gate-suite run, the `pyproject.toml` `browser` extra) checked out fine on
   inspection and does not need rework.
3. Then `T-oroE5f-docs-refresh` (mandatory, last). Then Gate G2 (`dev-security` — the frontend
   rendering surface plus the T-F1caAt evidence) before close, then Gate G3 (final `reviewer`
   sign-off on the accumulated epic).
