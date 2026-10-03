# EPIC: E-iafh2F-live-status-and-tabbed-workspace

## Metadata
- Epic ID: `E-iafh2F-live-status-and-tabbed-workspace`
- Title: Live status of running tasks, then a tabbed dashboard workspace
- Owner: dev-epic
- Created: 2026-10-02
- Last Updated: 2026-10-02
- Status: Done

## Summary
- Goal: Phase 1 shows live turns/tokens/last-action/stuck hint for running tasks; Phase 2 adds a tab bar (persisted, hash-addressable) to the dashboard.
- Scope In: `src/agent_orchestrator/ui/` (read-only), `ui/src/`, committed static bundle, docs.
- Scope Out: engine/state changes, new router library, split-pane view (future work), editing DAGs.

## Requirements (all MVP unless noted)
- FR-1 (MVP): per running task, live turns, tokens so far, cost if derivable, last action, seconds-since-last-event (stuck hint) from a bounded tail of `transcript.jsonl`; settled tasks get turns from `result.json`. Verified by: unit + integration tests.
- FR-2 (MVP): reader is read-only, tolerant of missing/partial/garbled files, cached by size+mtime. Verified by: edge-case unit tests.
- FR-3 (MVP): exposed additively (new endpoint; TaskStat model/effort/agent; RunSummary.running_tasks). Verified by: HTTP tests.
- FR-4 (MVP): "Now running" section on run page top + compact variant in runs list; **hard UX**: 3 visible rows, fixed height, internal scroll, not collapsible. Verified by: vitest + screenshots with 5+ running tasks.
- FR-5 (MVP): row fields: task id, status, model, effort, turns, live tokens, cost, elapsed, last action, stuck hint; task table gains Model and Turns columns.
- FR-6 (MVP): 3 s poll retained; paused when `document.hidden`.
- FR-7 (MVP): tab bar; Tab={id,kind,params,title}; kinds runs/run/task/graph/file/usage/new/settings; closable, reorderable.
- FR-8 (MVP): middle/ctrl-click and explicit "open in new tab" on run rows, task rows/graph nodes (task detail), output file paths (file viewer).
- FR-9 (MVP): persistence in localStorage (guarded) + active tab in URL hash restoring on load / real new browser tab.
- FR-10 (MVP): inactive tabs stay mounted, polling paused.
- FR-11 (MVP): RunDetail/FileBrowser/RunGraph/TaskDetailPanel take ids/paths as props.
- FR-12 (follow-up): a launch outcome is always visible; the launcher never auto-navigates. FR-13 (follow-up): a workflow whose repo_set/agents/reposets do not resolve is rejected (4xx, valid names listed) before spawning.
- NFR-1: path safety per ADR-0011 (no traversal, workspace-scoped); bounded reads. NFR-2: pure builder + I/O split, fixed clocks in tests. NFR-3: no layout jump. NFR-4: hash/localStorage untrusted, validated against kind allowlist.
- Non-MVP: split-pane; live cost via price table (not derivable today). Stretch: per-row sparkline.

## Task List
- [x] `T-qifi3r-activity-reader` — Phase 1: Pure+IO live-activity reader (ui/activity.py)
- [x] `T-LYf6DJ-activity-endpoint` — Phase 1: Activity endpoint + additive payload fields
- [x] `T-j6dTdO-now-running-ui` — Phase 1: Now-running box, list compact variant, Model/Turns columns, usePolling
- [x] `T-ySND8C-phase1-verify` — Phase 1: Phase 1 gates: tests, e2e, screenshots with 5+ running tasks
- [x] `T-f0YWSy-tab-model-and-persistence` — Phase 2: Tab model, reducer, hash codec, localStorage persistence, validation
- [x] `T-WQjrEt-tab-shell-and-new-tab-actions` — Phase 2: Tab bar UI, reorder/close, open-in-new-tab actions
- [x] `T-mee0lL-component-props-refactor` — Phase 2: RunDetail/FileBrowser/RunGraph/TaskDetailPanel take ids/paths as props
- [x] `T-mJWSkm-phase2-verify` — Phase 2: Phase 2 gates: tests, SPA-fallback check, screenshots
- [x] `T-6UNaea-docs-and-closeout` — Phase 2: Docs (HLD, ui/README), bundle rebuild, closeout
- [x] `T-Lc5Rq8-launch-status` — Follow-up (2026-10-03): launch result panel (no auto-redirect), launch status + log tail, failed-launch strip, pre-spawn repo_set validation

## Risks and Dependencies
- Transcript counts best-effort under huge files (approximate flag). Cost is a floor.
- Phase 2 refactors shared components; keep edits minimal; phase 1 ships and commits first.

## Links
- Design doc: `docs-md/live-activity-and-tabs-hld.md`; ADR: `docs-md/adr/ADR-0018-live-activity-reader-and-tabbed-workspace.md`
- Output artifacts: `output/E-iafh2F-live-status-and-tabbed-workspace/`

## Comments
- By: dev-epic · Role: manager · Date: 2026-10-02 · Comment: Epic opened; phases delivered in order.
- By: dev-epic · Role: reviewer · Date: 2026-10-02 · Comment: Early gate (reviewer) outcome: sound direction; MUST-FIX items (verify accounting against real transcripts, define attempt/cycle token scope, capture-dir resolution rules, tail bounds) were all addressed in HLD §1.3-1.4 and code; architect pass not run separately (design authored to ADR-0017 conventions; reviewer covered design + trust model). Phase 2 SHOULDs (title never taken from hash, rel=noopener, concrete MAX_TABS, single click semantics) carry into Phase 2 HLD.
- By: dev-epic · Role: manager · Date: 2026-10-02 · Comment: Phase 1 DONE (4/4). Evidence in STATUS.md.
- By: dev-epic · Role: tester · Date: 2026-10-02 · Comment: Late gate: independent tester pass over the real `ao ui` server + system Chrome; one failure traced to a stale verification script (not product) and fixed; final re-runs PASS.
- By: dev-epic · Role: manager · Date: 2026-10-02 · Comment: Phase 2 DONE (5/5). Epic DONE. Open items: cross-window localStorage last-writer-wins; split-pane future work; live cost needs a price table (not derivable).
