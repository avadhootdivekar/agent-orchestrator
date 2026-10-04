# HANDOFF: T-gDNjN2-cache-coordinator

- Task: `T-gDNjN2-cache-coordinator`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev A)
- To: T-XpF1pF, T-o95l1M, T-JCOAsq

## What will be handed over
- `agent_orchestrator.cache.coordinator.ResultCache` (implements `ResultCacheHook`):
  `from_settings`, `root`, `warnings`, `lookup`, `store_success`.
- `agent_orchestrator.cache.records`: the four builders.

## Frozen names / contracts
- The engine-facing calls never raise (except in strict mode).
- `LookupOutcome.record is None` means the engine writes no record; `pending` is set only for a
  storable outcome; a hit never takes the guard-3 snapshot.

## Verification the receiver should run
- `pytest -q tests/cache/test_coordinator.py tests/cache/test_records.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: lazy guard 3, unsafe-path
  handling, `warnings`. State `Draft` mirrors `TASK.md` and `STATUS.md`.
