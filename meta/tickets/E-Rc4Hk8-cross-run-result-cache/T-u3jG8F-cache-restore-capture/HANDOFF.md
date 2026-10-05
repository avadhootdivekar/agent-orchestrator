# HANDOFF: T-u3jG8F-cache-restore-capture

- Task: `T-u3jG8F-cache-restore-capture`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev B)
- To: T-gDNjN2 (and T-JCOAsq for ADV integration)

## What will be handed over
- `agent_orchestrator.cache.restore`: `HashingWriter`, `RestoreResult`, `capture_outputs`,
  `restore_outputs`.

## Frozen names / contracts
- The signatures in HLD §8.5; the `RestoreMiss` values in the §8.5 failure matrix.

## Verification the receiver should run
- `pytest -q tests/cache/test_restore.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: owner Dev B; test
  hygiene. State `Draft` mirrors `TASK.md` and `STATUS.md`.
