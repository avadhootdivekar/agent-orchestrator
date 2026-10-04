# HANDOFF: T-bLpoze-cache-dashboard-surface

- Task: `T-bLpoze-cache-dashboard-surface`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev B)
- To: T-fXWbqg (G2), T-bdQZW4 (docs), the parent merger (bundle rebuild)

## What will be handed over
- The `TaskStat.result_cache` and `RunDetail.result_cache` payload fields.
- The file-browser deny.
- The `cached` tag and the "Result cache" tile.
- A rebuilt bundle.

## Frozen names / contracts
- The payload shapes, from HLD §13.5.

## Verification the receiver should run
- `pytest -q tests/cache/test_ui_result_cache.py`
- `cd ui && npx vitest run src/test/run-detail-result-cache.test.tsx`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents. State `Draft`
  mirrors `TASK.md` and `STATUS.md`.
