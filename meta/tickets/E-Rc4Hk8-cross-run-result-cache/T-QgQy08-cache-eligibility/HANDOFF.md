# HANDOFF: T-QgQy08-cache-eligibility

- Task: `T-QgQy08-cache-eligibility`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev B)
- To: T-gDNjN2; the parent merging E-Ag7Pw3

## What will be handed over
- `agent_orchestrator.cache.eligibility`: `Eligibility`, `check_eligibility`, `TASK_ANY_VALUE`,
  `TASK_VALUE_RULED`, `WORKFLOW_FIELD_COVERAGE`, `DEFAULTS_FIELD_COVERAGE`.

## Frozen names / contracts
- The reason strings of HLD §8.3.3, including `unknown_agent_field`.
- An approval or human-gate field is always RULED; a new `AgentSpec` field is classified in
  `constants.py`.

## Verification the receiver should run
- `pytest -q tests/cache/test_eligibility.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: `unknown_agent_field`.
  State `Draft` mirrors `TASK.md` and `STATUS.md`.
