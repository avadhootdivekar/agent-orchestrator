# HANDOFF: T-XpF1pF-cache-engine-integration

- Task: `T-XpF1pF-cache-engine-integration`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev A)
- To: T-o95l1M, T-JCOAsq, T-fXWbqg (gate G1b)

## What will be handed over
- `Orchestrator(result_cache=...)`; seams (c), (c') and (d); the private methods
  `_reverse_stale_charge`, `_result_cache_lookup` and `_result_cache_store`.
- Integration tests I-3…I-8, I-18, I-21, I-22, I-27 and U-AST-E.

## Frozen names / contracts
- The seam positions (HLD §8.7.2) and the success-side-effect rule (HLD §8.7.4).

## Verification the receiver should run
- `pytest -q tests/cache/test_engine_result_cache.py tests/cache/test_noop_proof.py`
- `git diff --stat main -- src/agent_orchestrator/engine.py` (line budget)

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: shared
  `_reverse_stale_charge`, honest budget. State `Draft` mirrors `TASK.md` and `STATUS.md`.
