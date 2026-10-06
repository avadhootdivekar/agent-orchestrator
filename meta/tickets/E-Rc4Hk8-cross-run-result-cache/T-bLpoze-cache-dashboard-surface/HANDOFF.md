# HANDOFF: T-bLpoze-cache-dashboard-surface

- Task: `T-bLpoze-cache-dashboard-surface`
- State: `Done (handoff available)`
- Commits: `faf8f57` (source + tests), `4e61e68` (bundle, separate)
- From: `developer` (Dev C)
- To: T-fXWbqg (G2), T-JCOAsq, T-bdQZW4, the parent (bundle rebuild after merging)

## What was delivered
- `TaskStat.result_cache`, `RunDetail.result_cache`; the file-browser deny; the `cached` tag and
  the "Result cache" tile; the rebuilt bundle in a separate commit.

## Frozen names / contracts
- The payload shapes (HLD §13.5): `tasks[].result_cache` = `report.task_view(rec)` for CURRENT records else `null`;
  top-level `result_cache` = `report.run_block(state)` else `null`. Both keys are always present.
- `FileBrowser.resolve` raises `PathNotAllowedError` for any resolved path equal to or under
  `<root>/.orchestrator/cache` (constant `CACHE_DIR_PARTS`); list, read and html preview all go through it.
- Frontend: `ResultCacheTaskView`, `ResultCacheRunBlock` (`ui/src/types.ts`); `ResultCacheTag`, `ResultCacheTile`
  (`RunDetail.tsx`). Strings render as text only; the tooltip is a `title` attribute.
- The existing key-pin test `test_run_graph_endpoint.py::TestAdditiveOnly` now lists `result_cache`.

## Verification the receiver should run
- `pytest -q tests/ui/test_result_cache_ui.py`
- `cd ui && npx vitest run src/test/run-detail-result-cache.test.tsx`
- Bundle: after merging sibling dashboard epics, drop `4e61e68` and run `cd ui && npm ci && npm run build`.

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: owner Dev C, separate
  bundle commit, lazy import. State `Draft` mirrors `TASK.md` and `STATUS.md`.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done; handoff available (commits `faf8f57`, `4e61e68`). `TASK.md`, `STATUS.md`, epic rollup agree.
