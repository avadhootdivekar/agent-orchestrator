# Live task activity and tabbed workspace — HLD/LLD (E-iafh2F)

**Status:** Implemented (Phase 1 + Phase 2) · **Date:** 2026-10-02 · **ADR:** [ADR-0018](adr/ADR-0018-live-activity-reader-and-tabbed-workspace.md)
**Epic:** [`meta/tickets/E-iafh2F-live-status-and-tabbed-workspace/`](../meta/tickets/E-iafh2F-live-status-and-tabbed-workspace/EPIC.md)
**Builds on:** [ADR-0010](adr/ADR-0010-dashboard-architecture-and-general-instructions.md) ·
[ADR-0011](adr/ADR-0011-untrusted-workspace-content-rendering.md) ·
[`run-graph-canvas-hld.md`](run-graph-canvas-hld.md) (pure builder / I/O split, `display_text`) ·
[`dashboard-and-general-instructions-hld.md`](dashboard-and-general-instructions-hld.md)

Two phases, delivered in order. Phase 1 makes a *running* task visible (turns, live tokens,
last action, stuck hint). Phase 2 turns the single-view dashboard into a tabbed workspace.
Neither phase changes engine state or the persisted run format.

---

## Phase 1 — Live status of running tasks

### 1.1 Problem (established findings)

- The run page polls every 3 s (`RunDetail.tsx` `POLL_MS`).
- `TaskRunState.cumulative_*` tokens/cost are mirrored only when a task *settles*, so a running
  task shows 0 tokens.
- Turns are recorded nowhere except the per-attempt `transcript.jsonl` (claude `stream-json`)
  and, once finished, `result.json` (`num_turns`, `usage`, `total_cost_usd`).
- Capture layout (engine `_run_with_retries`): cycle 1 → `<run_dir>/<task_id>/attempt-<n>/`;
  cycle ≥ 2 → `<run_dir>/<task_id>/cycle-<c>/attempt-<n>/`.

### 1.2 Decision: read-only reader in `ui/`, no engine change

`src/agent_orchestrator/ui/activity.py`, same split as `graph.py`/`runs.py`:

| Layer | Contents |
|---|---|
| Pure | `TranscriptScan` accumulator (`fold_line`), `describe_action`, `parse_result`, `build_task_activity`, `build_run_activity` — no I/O, clock injected (`now`). |
| I/O | `TranscriptTailer` (bounded incremental reader + cache) and `locate_attempt_dirs` (path-guarded directory resolution). |
| Wiring | `RunRepository.load_activity` → `DashboardService.run_activity` → `GET /api/runs/{id}/activity`. |

**Why a separate endpoint, not folded into run detail.** Detail is polled for every open run view
and is a pure function of `state.json`; activity does file I/O proportional to running tasks. A
separate endpoint keeps detail cheap/unchanged, lets the client degrade independently (old backend
→ 404 → section hides) and lets the cache and bounds live in one place.

### 1.3 Bounded incremental tail

Per `(path)` cache entry: `offset` (start of first unconsumed line), `size`, `mtime_ns`, running
accumulator. Per call:

1. `stat` → if `(size, mtime_ns)` equals cached, return the cached scan (no read).
2. If `size < offset` (truncated/replaced) → reset.
3. If `size - offset > TAIL_CAP_BYTES` (first sight of a big file, or a burst) → seek to
   `size - TAIL_CAP_BYTES`, drop the partial first line, mark the scan `approximate=True`
   (turn/token counts are then lower bounds).
4. Read `[offset, size)`; consume only up to the last `\n` (a partial trailing line is left for the
   next poll). A single line longer than `MAX_LINE_BYTES` is skipped, not parsed.
5. Each line: `json.loads`; non-dict/garbled lines are counted (`bad_lines`) and ignored.

Cache is an in-process, lock-guarded, size-bounded LRU (`MAX_CACHE_ENTRIES`, oldest evicted),
keyed by resolved path; entries also store the inode so a replaced file resets. Same-size,
same-mtime rewrites are not detected (accepted). Per-request work is bounded: attempt dirs kept
per task (`MAX_ATTEMPT_DIRS`), tasks per run (`MAX_ACTIVITY_TASKS`, running first). Bytes are split
on `\n` *before* decoding (`errors="replace"`), so a multi-byte character cut by the writer never
raises; an over-long unterminated line is dropped and the offset advances (no stall).
Capture-dir choice uses the state's `dispatch_cycle` (not directory sniffing): cycle <= 1 flat
`attempt-<n>`, cycle >= 2 `cycle-<c>/attempt-<n>`; attempt numbers sort numerically.

**Accounting (verified against 60 real attempts in `ao-runner-ai-models`, redacted fixture
`tests/fixtures/transcript_stream_real_shape.jsonl`).** stream-json emits one `assistant` event per
content block; blocks of one API message share `message.id` and repeat that message's `usage`. A
*turn* = a distinct non-sidechain `message.id` (subagent events carry `parent_tool_use_id` and add
tokens but not turns). Without an id (older emitters) consecutive assistant events are one turn.
Live turns are typically 70-90% of the final `result.num_turns` (the CLI counts extra internal
turns); the settled number replaces them. **Input tokens** (non-cache, matching the existing Tokens
column) are exact per message. **Output tokens are an estimate**: the stream's own
`usage.output_tokens` is a first-chunk value (~1% of the final in the sample), so live output =
Σ per message `max(reported, chars/4)` of model text + tool input, plus the `system/thinking_tokens`
events' `estimated_tokens_delta`. In the sample this lands at 61-87% (median 76%) of the final
figure: a **lower bound**, shown as `~N` (`tokens_estimated=true`). **Cost is not derivable** from
live events (no price table in the repo; adding one would drift). A running task's cost is the sum
of `total_cost_usd` of its already-finished attempts in the current cycle (a floor), else `null`
(UI "—"). Scope rule, one for all three metrics: running value = finished attempts of the current
cycle (`result.json`, exact) + the live attempt (transcript, estimated); at settle the engine's
exact `TaskStat` figures replace it, so the number may step up at settle.
Settled tasks without a `result.json` (crash/quota kill) fall back to the transcript scan.

**Last action.** From the newest `assistant` event: `tool_use` → `"<Tool>: <arg>"` (Bash command first
line, file path, pattern…), text-only → `"replying"`, `thinking` → `"thinking"`. A returning `user`
`tool_result` does not replace the last action (what the agent last *did* is the useful part). All text passes through `graph.display_text` (strips bidi/
control chars, caps length) — the transcript is agent-authored, untrusted.

**Stuck hint.** `idle_seconds = now - transcript mtime`; `stuck = idle_seconds >= STUCK_AFTER_SECONDS`
(named constant, 300 s; the chip reads "idle 6m 40s", and a long single `Bash` call can legitimately trip it, so it is a hint, not a verdict). A running task with no transcript yet reports `source="none"`, no hint.

**Settled tasks.** `turns` from the latest attempts' `result.json` (`num_turns`, summed across
attempts of the last cycle); `source="result"`. Cached by `(size, mtime_ns)`.

### 1.4 Path safety (ADR-0011 conventions)

`task_id` and run id come from `state.json` / the URL → untrusted. The run dir comes from
`RunRepository.run_dir` (already traversal-guarded). Each candidate path is `resolve()`d and must
be inside the resolved run dir (symlink escapes resolve outside → rejected) and must be a regular
file (`is_file`, so a FIFO planted as `transcript.jsonl` cannot block the server). The resolved
target is what gets opened (narrowing, not eliminating, the check/open race: acceptable for a
loopback, unauthenticated-by-design dashboard, ADR-0010 D7). Task ids that are empty, `.`/`..`, or
contain `/`, `\` or NUL are rejected before any join. All failures degrade to
`source="none"`, never an error response.

### 1.5 Payload (additive)

`GET /api/runs/{run_id}/activity` →

```json
{ "schema_version": 1, "run_id": "…", "generated_at": "ISO", "tasks": {
  "<task_id>": { "task_id":"…", "status":"running", "source":"transcript|result|none",
    "attempt": 2, "cycle": 1, "turns": 14, "input_tokens": 1200, "output_tokens": 340,
    "cost_usd": null, "last_action":"Bash: pytest -q", "idle_seconds": 12.0,
    "elapsed_seconds": 95.0, "stuck": false, "approximate": false,
    "tokens_estimated": true } } }
```

Run-detail `TaskStat` gains additive `agent`, `model`, `effort` (already on `TaskRunState`).
`RunSummary` gains `running_tasks: [{id, model, effort, started_at}]` (state-derived, zero I/O,
capped at `MAX_RUNNING_BRIEFS`=20; `task_counts.running` is the true total) for the compact list
variant. `schema_version` (activity) bumps only on a breaking field change; additive fields do not.

### 1.6 UI

- `NowRunning` component (full variant, top of run page; compact variant per live run in the
  runs list). **Fixed 3-row viewport**: container `height = 3 × row height`, `overflow-y:auto`;
  never grows, **no collapse control** (explicit user requirement: consistent behaviour). Empty
  state is a same-height placeholder so the page doesn't jump.
- Row: task id, status chip, model, effort, turns, live tokens, cost, elapsed, last action, stuck chip.
- Task table gains **Model** and **Turns** columns.
- `usePolling(fn, ms, enabled)` hook: 3 s interval, **paused while `document.hidden`** (and, in
  phase 2, while the tab is inactive); fires immediately on becoming visible.

### 1.7 Test plan

pytest unit (fold/describe/parse, fixed `now`, garbled/partial/oversize lines, approximate flag,
cache hit/invalidate, cost floor), path-safety (traversal ids, symlink escape, FIFO), service +
HTTP integration (`TestClient`), e2e via real `ao ui` server on a fixture workspace; vitest for
`NowRunning` (3-row cap, internal scroll, no toggle, empty state) and `usePolling` (hidden pause).

---

## Phase 2 — Tabbed workspace (implemented)

### 2.1 Decision

No router library. `ui/src/tabs/` holds a pure model + reducer (`model.ts`), guarded storage
(`storage.ts`), React contexts (`context.tsx`), the tab strip (`TabBar.tsx`), links
(`TabLink.tsx`) and the per-kind views (`TabView.tsx`, `TaskTab.tsx`, `GraphTab.tsx`);
`App.tsx` renders the strip above the main pane. `Tab = { id, kind, params, title }`,
`kind ∈ runs | run | task | graph | file | usage | new | settings` (closed allowlist).

| Concern | Decision |
|---|---|
| State | `useReducer(tabsReducer)` over `{tabs, activeId}`; actions `open` / `navigate` / `activate` / `close` / `move`; never tab-less (closing the last tab recreates `Runs`) |
| Params | per-kind key allowlist: `run{id}` · `task{run,id}` · `graph{run}` · `file{path?,root?}` · others none. Run ids `^[A-Za-z0-9][A-Za-z0-9._:@+-]*$` ≤200; task ids/paths: no control/bidi chars, ≤200/≤1024; root `^[A-Za-z0-9._@+-]{1,64}$` |
| Persistence | `localStorage["ao-tabs"] = {v:1, activeId, tabs:[{id,kind,params}]}`, every access try/catch guarded (private window, blocked, quota, corrupt JSON → default `Runs` tab). Titles are not stored |
| URL | Active tab encoded in the **hash** `#/<kind>?k=v…` (URLSearchParams-encoded), written with `history.replaceState` (no history spam, no `hashchange` loop). A `hashchange` (pasted/edited link) opens or focuses the tab. On load the hash tab is opened+focused on top of the restored set. A hash never reaches the server; the SPA fallback already serves `/` and any deep path with `index.html` (asserted by `tests/ui/test_activity_e2e.py::test_hash_deep_links_are_served_by_the_spa_fallback`), so **no backend change** |
| Click semantics (one rule set) | links are real `<a href="#/…">`. **Plain click** navigates *within the current tab* (reuse an identical tab, else replace the active tab's content; the old "open a run replaces the pane" behaviour, now per tab). **Ctrl/Cmd-click or middle-click** opens a new *in-app* tab in the background. The explicit **"open in new tab" button** (⧉, on run rows, task rows, graph-node panel, output file paths, the file viewer, the Graph toggle) opens and activates a new in-app tab. **Shift-click / context menu / copy-link** stay native: the hash URL opens a real new browser tab that restores from the hash alone |
| Mounting | every opened tab stays mounted (`hidden` attribute when inactive; component state survives); `TabActiveContext` → `usePolling` stops all requests for inactive tabs (verified: 0 `/api/runs` requests in 7.5 s while inactive). Max `MAX_TABS`=12; opening past it evicts the oldest *inactive* tab |
| Trust | hash + localStorage are untrusted: `kind` allowlist, key allowlist (inherited/`__proto__` keys never read), bounded strings, a supplied-but-invalid value (even optional) rejects the whole tab (a hostile `path` must not degrade into "browse root"), **title is always recomputed** (never taken from input), tab ids re-validated/regenerated, duplicate ids/singletons dropped, count capped. Params reach only the typed API client (server re-validates, ADR-0011). Links with invalid targets render as plain text |
| Components | `RunDetail({runId,onBack})` unchanged; `FileBrowser({initialPath?,initialRoot?})`; `TaskDetailPanel` gains optional `runId`; `RunGraph` passes it. `TaskTab` reuses `TaskDetailPanel`; `GraphTab` reuses the lazy `RunGraph` chunk |
| Reorder | native HTML5 drag-and-drop **and** keyboard (Alt+←/→ on a focused tab); roving tabindex arrows, Delete/middle-click/× close |
| Known limits | two browser windows share one `localStorage` key: last writer wins (no cross-window sync). Split-pane / side-by-side tabs are future work. `TaskDetailPanel` now focuses with `preventScroll` so opening a task tab does not scroll its header away |

### 2.2 Test plan (all landed)

vitest: `tabs-model.test.ts` (64: allowlists, hostile hashes/ids/paths, codec round-trip,
persistence repair, reducer incl. eviction/close/move, storage guards), `tabs-app.test.tsx` (13,
through `<App/>`: persist, hash restore on load / `hashchange` / hostile hash, sidebar, plain /
ctrl / middle / explicit new-tab on run rows, task rows, file paths, close, reorder by Alt+Arrow
and drag-drop, corrupt + throwing localStorage, inactive tabs mounted but not polling),
`tabs-components.test.tsx` (TabLink fallbacks, TabBar roving focus, Graph/Task tab errors).
Visual + real-browser: `scripts/helper/epics/E-iafh2F/shoot.py 2` drives system Chrome against a
real `ao ui` (reload restores tabs, a fresh browser context opening the hash URL restores that
tab, hostile hash ignored, zero polling while inactive); screenshots in
`output/E-iafh2F-live-status-and-tabbed-workspace/`.
