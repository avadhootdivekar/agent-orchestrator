# HANDOFF: T-uoYW6b-cache-key-builder

- Task: `T-uoYW6b-cache-key-builder`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev A)
- To: T-gDNjN2 (and T-JCOAsq for the adversarial key tests)

## What will be handed over
- `agent_orchestrator.cache.fingerprint`: `CliVersionReader`, `claude_cli_fingerprint`,
  `guarded_resolve`, `guarded_abs`, `CLAUDE_FINGERPRINT_ENV_VARS`, `CLAUDE_CONTEXT_PATHS`.
- `agent_orchestrator.cache.keys`: `build_cache_key`, `summary_from_doc`.

## Frozen names / contracts
- Key schema v1 and GV-1 Rev 2. A change needs an HLD §8.2.7 update and a decision on
  `KEY_SCHEMA_VERSION`.

## Verification the receiver should run
- `pytest -q tests/cache/test_keys_golden.py tests/cache/test_keys.py tests/cache/test_fingerprint.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: AgentSpec field sets come
  from `constants`. State `Draft` mirrors `TASK.md` and `STATUS.md`.
