# HANDOFF: T-ZTxN1x-bench-cache-force-off

- Task: `T-ZTxN1x-bench-cache-force-off`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev B)
- To: T-JCOAsq (final suite), T-bdQZW4 (benchmarking docs)

## What will be handed over
- `AoWorkflowSubject` always runs `ao run ... --no-cache` with `AO_CACHE=0`.

## Frozen names / contracts
- `_AO_NO_CACHE_FLAG`.

## Verification the receiver should run
- `pytest -q tests/bench/test_bench_cache_forced_off.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2. State `Draft` mirrors
  `TASK.md` and `STATUS.md`.
