# TASK: T-bLpoze-cache-dashboard-surface

## Metadata
- Task ID: `T-bLpoze-cache-dashboard-surface`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev C)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 3, implemented)
- Status: `Done`
- Estimate: `10 focus hours (1.25 days)` · Sprint 3 · surfaces

## Requirements Mapping
- Requirement IDs: FR-13, NFR-1 (lazy import), NFR-10 (M-10)
- HLD: §8.10
- ADR-0019: D9, D15, D22, D35

## Description
A minimal, display-only dashboard surface.

**Backend.**
- `ui/runs.py`: `TaskStat.result_cache: dict[str, object] | None = None` and
  `RunDetail.result_cache: dict[str, object] | None = None`, filled with `report.task_view` (the
  exact D35 fields) and `report.run_block`. Import `cache.report` **lazily, only when
  `state.result_cache` is non-empty**.
- `ui/files.py`: the browser refuses any resolved path equal to or under
  `<root>/.orchestrator/cache` (`PathNotAllowedError`; M-10).
- `ui/app.py` and `ui/service.py` are **not** touched.

**Frontend.**
- `ui/src/types.ts`: `ResultCacheTaskView`, `ResultCacheRunBlock` and the optional fields.
- `ui/src/components/RunDetail.tsx`: a `cached` tag when `task.result_cache?.hit` (tooltip shows
  `source_run_id` as **plain text**); a "Result cache" tile when `detail.result_cache` is present
  (`${hits} hit(s)` / `~${formatCost(saved)} saved (est.)`, or `${would_hits} would-hit(s)
  (shadow)` when `would_hits > 0`). Never reuse the prompt-cache components (`CacheDetails`,
  `cache_hit_rate`).

**Bundle.** Rebuild with `npm ci && npm run build` and commit the rebuilt
`src/agent_orchestrator/ui/static/**` as a **separate commit**, so the parent can drop it and
re-run the build after merging the sibling epics. Never hand-merge the bundle.

## File scope (exclusive)
- `src/agent_orchestrator/ui/runs.py`, `src/agent_orchestrator/ui/files.py`
- `ui/src/types.ts`, `ui/src/components/RunDetail.tsx`
- `tests/ui/test_result_cache_ui.py` (new; counts toward the existing dashboard coverage gate)
- `tests/ui/test_run_graph_endpoint.py` (**amended, two expected-key sets only**; see Comments)
- `ui/src/test/run-detail-result-cache.test.tsx` (new)
- `src/agent_orchestrator/ui/static/**` (rebuilt bundle, separate commit)

## Inputs / Outputs
- **Inputs:** T-eyn5UG (`task_view`, `run_block`, `current_records`).
- **Outputs:** dashboard fields and widgets.

## Acceptance Criteria
1. **D-1a (payload).** `tasks[].result_cache` equals `task_view(rec)` for current records (with
   exactly the D35 fields) and `null` otherwise; the top-level `result_cache` equals
   `run_block(state)` or `null`; a run without records serializes as before apart from the two
   `null` keys.
2. **D-1b (file browser).** `.orchestrator/cache`, `.orchestrator/cache/blobs/xx/<sha>` and a
   `..` path resolving into the cache are refused; `.orchestrator/runs/...` reads behave as before.
3. **D-1c (vitest).** The `cached` tag renders only when `hit` is true; a `source_run_id` of
   `<img onerror=...>` renders as text (no injected element); the tile renders hits and savings
   and the shadow variant; nothing renders when the fields are `null`.
4. **U-LZ2 (dashboard part).** In a subprocess, building the run detail for a state with
   `result_cache == {}` leaves `agent_orchestrator.cache.report` out of `sys.modules`.
5. **Bundle.** `npm ci && npm run build` succeeds; the rebuilt bundle is its own commit; the
   existing vitest suite and the existing UI pytest suite pass unedited.
6. **Hygiene.** The dashboard CI step (`--cov=agent_orchestrator.ui --cov-fail-under=80`) still
   passes; ruff (≤ 100 columns) and mypy are clean; `pytest -q` has no new failures.

## Test requirements
- `tests/ui/test_result_cache_ui.py`: D-1a, D-1b, U-LZ2.
- `ui/src/test/run-detail-result-cache.test.tsx`: D-1c.

## Risks
- **E-Da5Tn9 (dashboard auth) touches `ui/files.py` or the bundle in parallel.** Mitigation: an
  additive deny-list hunk; the bundle is a separate commit, rebuilt once after all merges.

## Dependencies
- T-eyn5UG.

## Pseudocode / Algorithm
```text
runs.py:  if state.result_cache: (lazy import) rc_tasks, rc_run = result_cache_status_fields(state)
          TaskStat(..., result_cache=rc_tasks.get(tid)); RunDetail(..., result_cache=rc_run)
files.py: cache_root = realpath(join(root, ".orchestrator", "cache"))
          if resolved == cache_root or resolved.startswith(cache_root + os.sep): raise PathNotAllowedError
```

## Schemas / Interface Notes
- **Payload shapes:** HLD §13.5 (`resultCacheTask`, `resultCacheRun`).

## Handoff Boundary
- **Upstream:** T-eyn5UG.
- **Downstream:** T-fXWbqg (G2 checks text-only rendering and the separate bundle commit),
  T-JCOAsq Part 3, T-bdQZW4.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-bLpoze-cache-dashboard-surface/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Minimal dashboard: tag and tile.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2: file-browser deny, shadow
  tile, text-only provenance.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 (early-gate C, D; staffing):
  lazy import (U-LZ2); backend tests in `tests/ui/` (dashboard coverage gate); the rebuilt bundle
  is a separate commit; exact D35 fields; owner moves to Dev C (HLD §22.1).
- By: developer · Role: developer · Date: 2026-10-05 · Comment: Implemented (commits `faf8f57` source+tests, `4e61e68` bundle). Deviation: the existing additive-only key-pin test `tests/ui/test_run_graph_endpoint.py::TestAdditiveOnly::test_only_the_three_documented_keys_are_new` pins the exact run-detail / task key sets, so the two `result_cache` keys that D-1a mandates (null for a run without records) required adding `result_cache` to its two expected sets (no assertion weakened). Status Done; matches `STATUS.md`, `HANDOFF.md` and the epic rollup.
