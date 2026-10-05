# HANDOFF: T-ZTxN1x-bench-cache-force-off

- Task: `T-ZTxN1x-bench-cache-force-off`
- State: `Done (handoff available)`
- From: `developer` (Dev B)
- To: T-JCOAsq (final suite), T-bdQZW4 (benchmarking docs)

## What was delivered
- `AoWorkflowSubject` always runs `ao run ... --no-cache` with `AO_CACHE=0` in the child
  environment, overriding anything the operator exported.
- `tests/bench/test_bench_cache_forced_off.py` (7 tests: E-8a, E-8b x4 outer values, constant
  check, E-8c).

## Frozen names / contracts
- `_AO_NO_CACHE_FLAG = "--no-cache"` and `_AO_CACHE_OFF_VALUE = "0"` in `bench/subjects.py`;
  `ENV_CACHE` comes from `agent_orchestrator.cache.constants`.

## Verification the receiver should run
- `pytest -q tests/bench/test_bench_cache_forced_off.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2. State `Draft` mirrors
  `TASK.md` and `STATUS.md`.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done; handoff available.
