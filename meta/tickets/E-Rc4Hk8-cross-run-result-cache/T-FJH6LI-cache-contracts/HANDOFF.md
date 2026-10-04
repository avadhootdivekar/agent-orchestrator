# HANDOFF: T-FJH6LI-cache-contracts

- Task: `T-FJH6LI-cache-contracts`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev A)
- To:
  - T-28J9oR: commit 1 (`constants.py`).
  - Every other cache task, T-8tr1H4 included: commit 3 (the whole task).

## What will be handed over
- `agent_orchestrator.cache.constants`: every §8.1.5 constant (incl. `DEFAULT_TASK_CACHE_POLICY`,
  `AGENT_KEY_FIELDS`, `AGENT_NON_KEY_FIELDS`), `REASON_*` and `EVENT_*`.
- `agent_orchestrator.cache.safeio`: the flags, the nine functions and the four errors of §8.2.2.
- `agent_orchestrator.cache.types`:
  - `canonical_json`;
  - the entry models and `parse_entry_bytes`;
  - the dataclasses of §8.2.1, §8.6.1 and §13.4;
  - `ResultCacheHook`, `CacheStore` and `CacheAdmin`;
  - the errors, including `CacheUnsafePathError`.
- `tests/cache/fakes.py`, `tests/cache/store_contract.py` (CacheStore half),
  `tests/fixtures/result_cache/corpus/`.

## Frozen names / contracts
- Every name above, plus the signatures in HLD §8.2.1, §8.2.2, §8.4.3, §8.6.1 and §14.1.
- A change requires a note in each consumer's `STATUS.md` and an HLD §14.1 update.

## Verification the receiver should run
- `pytest -q tests/cache/test_types.py tests/cache/test_safeio.py tests/cache/test_ast_guard.py`
- `python -c "import agent_orchestrator.cache.types, agent_orchestrator.cache.safeio"`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: type-only model reference,
  fixed commit numbering, new constants and `CacheUnsafePathError`. State `Draft` mirrors
  `TASK.md` and `STATUS.md`.
