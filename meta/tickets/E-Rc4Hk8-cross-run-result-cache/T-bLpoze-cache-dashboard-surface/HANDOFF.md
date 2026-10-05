# HANDOFF: T-bLpoze-cache-dashboard-surface

- Task: `T-bLpoze-cache-dashboard-surface`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev C)
- To: T-fXWbqg (G2), T-JCOAsq, T-bdQZW4, the parent (bundle rebuild after merging)

## What will be handed over
- `TaskStat.result_cache`, `RunDetail.result_cache`; the file-browser deny; the `cached` tag and
  the "Result cache" tile; the rebuilt bundle in a separate commit.

## Frozen names / contracts
- The payload shapes (HLD §13.5).

## Verification the receiver should run
- `pytest -q tests/ui/test_result_cache_ui.py`
- `cd ui && npx vitest run src/test/run-detail-result-cache.test.tsx`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: owner Dev C, separate
  bundle commit, lazy import. State `Draft` mirrors `TASK.md` and `STATUS.md`.
