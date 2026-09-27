# STATUS: E-k3AMEr-run-graph-canvas

- ID: `E-k3AMEr-run-graph-canvas`
- Updated At: `2026-09-27`
- State: `Draft` (design complete and hardened; ready for execution; **no code written**)
- Owner: `dev-epic` (next: execution) · design by `architect`

## Rollup
| Task | Scope | Sprint | Est | State |
|------|-------|--------|-----|-------|
| T-AZzgT8-spawn-provenance | MVP | S1 | 14 h | Draft |
| T-l7t6TT-workflow-snapshot | MVP | S1 | 12 h | Draft |
| T-mzT3BW-dag-edge-iterator | MVP | S1 | 8 h | Draft |
| T-adVpTj-graph-model-and-layout | MVP | S1 | 16 h | Draft |
| T-OjTS8O-run-graph-canvas | MVP | S1 | 14 h | Draft |
| T-M4qboy-run-graph-builder | MVP | S2 | 14 h | Draft |
| T-AsQ77e-run-graph-endpoint | MVP | S2 | 6 h | Draft |
| T-aHktGB-graph-toolbar-and-legend | MVP | S2 | 12 h | Draft |
| T-pAi0Cv-task-detail-panel | MVP | S2 | 16 h | Draft |
| T-F1caAt-graph-e2e-verification | MVP | S2 | 14 h | Draft |
| T-oroE5f-docs-refresh | MVP (mandatory, last) | S2 | 6 h | Draft |
| T-VcN4pt-task-title-field | Non-MVP | S2 stretch | 6 h | Draft |
| T-hMNbDP-spawn-subtree-collapse | Non-MVP | backlog | 12 h | Draft |
| T-ydMbJN-critical-path-edge-timing | Non-MVP | backlog | 12 h | Draft |
| T-N8scZK-layout-persistence | Non-MVP | backlog | 10 h | Draft |

Counts: **15 tasks** (11 MVP, 4 non-MVP). 0 Done, 0 In Progress, 0 Blocked, 15 Draft.
MVP total **132 focus hours**: S1 64 h + S2 68 h, with 2 developers (capacity math in HLD §22).

## This update
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
1. **`dev-epic` executes this epic.** Start S1 with T-AZzgT8, T-l7t6TT, and T-mzT3BW (Dev A) and
   T-adVpTj then T-OjTS8O (Dev B) in parallel.
2. Gate G1 at the end of S1: reviewer and tester sign off on the engine-touching tasks before T-M4qboy.
3. Gate G2: dev-security review of the frontend rendering plus the T-F1caAt evidence. Gate G3: final
   review, then T-oroE5f docs refresh, then close.
