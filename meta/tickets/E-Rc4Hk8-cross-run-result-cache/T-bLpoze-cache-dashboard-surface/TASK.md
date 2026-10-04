# TASK: T-bLpoze-cache-dashboard-surface

## Metadata
- Task ID: `T-bLpoze-cache-dashboard-surface`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev B)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 2)
- Status: `Draft`
- Estimate: `10 focus hours (1.25 days)` · Sprint 2

## Requirements Mapping
- Requirement IDs: FR-13, NFR-10 (M-10)
- HLD: §8.10
- ADR-0019: D15, D22

## Description
Add a minimal, display-only dashboard surface. **Backend:**

- In `ui/runs.py`, add `TaskStat.result_cache: dict | None = None` and
  `RunDetail.result_cache: dict | None = None`. Fill them with `report.task_view` for current
  records and `report.run_block`.
- In `ui/files.py`, the file browser refuses any resolved path equal to or under
  `<root>/.orchestrator/cache`, raising `PathNotAllowedError` (M-10).
- `ui/app.py` and `ui/service.py` are **not** touched.

**Frontend:**

- In `ui/src/types.ts`, add the `ResultCacheTaskView` and `ResultCacheRunBlock` interfaces and
  the optional fields that use them.
- In `ui/src/components/RunDetail.tsx`:
  - show a `cached` tag next to the origin tag when `task.result_cache?.hit`. Its tooltip shows
    `source_run_id` as **plain text**.
  - show a "Result cache" tile when `detail.result_cache` is present: `${hits} hit(s)` and
    `~${formatCost(saved)} saved (est.)`. When `would_hits > 0`, show
    `${would_hits} would-hit(s) (shadow)` instead.
- Never reuse the prompt-cache components or names (`CacheDetails`, `cache_hit_rate`).
- Rebuild the committed bundle (`npm ci && npm run build`). **Never hand-merge it.**

## File scope (exclusive)
- `src/agent_orchestrator/ui/runs.py`, `src/agent_orchestrator/ui/files.py`
- `ui/src/types.ts`, `ui/src/components/RunDetail.tsx`
- `ui/src/test/run-detail-result-cache.test.tsx` (new)
- `tests/cache/test_ui_result_cache.py` (new)
- `src/agent_orchestrator/ui/static/**` (rebuilt bundle)

## Inputs / Outputs
- **Inputs:** T-eyn5UG (`task_view`, `run_block`, `current_records`).
- **Outputs:** dashboard fields and widgets.

## Acceptance Criteria
1. **D-1a (payload).**
   - The run-detail JSON has `tasks[].result_cache` equal to `task_view(rec)` for current
     records, and `null` otherwise.
   - The top-level `result_cache` equals `run_block(state)`, or `null`.
   - A run without records serializes exactly as before, apart from the two `null` keys.
2. **D-1b (file browser).** Requests for `.orchestrator/cache`, `.orchestrator/cache/blobs/xx/<sha>`
   and a `..` path that resolves into the cache are refused. Sibling `.orchestrator/runs/...`
   reads behave as before.
3. **D-1c (vitest).**
   - The `cached` tag renders only when `hit` is true.
   - The tooltip renders a `source_run_id` containing `<img onerror=...>` as text. Assert there
     is no injected element.
   - The tile renders hits and savings, and the shadow variant.
   - Nothing renders when the fields are `null`.
4. **Bundle.**
   - `npm ci && npm run build` succeeds.
   - The committed bundle is rebuilt, not hand-edited.
   - The existing vitest suite passes, and the existing UI pytest suite passes unedited.
5. **Hygiene.** `ruff` and `mypy` are clean. `pytest -q` has no new failures.

## Test requirements
- `tests/cache/test_ui_result_cache.py`: D-1a and D-1b.
- `ui/src/test/run-detail-result-cache.test.tsx`: D-1c.

## Risks
- **E-Da5Tn9 (dashboard auth) touches `ui/files.py` or the bundle in parallel.** Mitigation: an
  additive deny-list hunk. Rebuild the bundle once, after all epics merge (HLD §24.2).

## Dependencies
- T-eyn5UG.

## Pseudocode / Algorithm
```text
runs.py: rc_tasks, rc_run = result_cache_status_fields(state)
         TaskStat(..., result_cache=rc_tasks.get(tid)); RunDetail(..., result_cache=rc_run)
files.py: cache_root = realpath(join(root, ".orchestrator", "cache"))
          if resolved == cache_root or resolved.startswith(cache_root + os.sep): raise PathNotAllowedError
```

## Schemas / Interface Notes
- **Payload shapes:** HLD §13.5 (`resultCacheTask`, `resultCacheRun`).

## Handoff Boundary
- **Upstream:** T-eyn5UG.
- **Downstream:** T-fXWbqg (G2 checks text-only rendering), T-bdQZW4.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-bLpoze-cache-dashboard-surface/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Minimal dashboard: tag and tile.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 changes, re-estimated from
  8 h to 10 h:
  - adds the `ui/files.py` deny for the cache directory (critic #8d, security NIT d);
  - adds the shadow-mode tile variant;
  - `source_run_id` is rendered as text-only.
