# HANDOFF: T-OeRYSO-executor-argv-builder

- Task: `T-OeRYSO-executor-argv-builder`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev C)
- To: T-uoYW6b

## What will be handed over
- `agent_orchestrator.executors.claude_cli.build_claude_argv(agent, prompt) -> list[str]`: pure,
  and behaviour-identical to the argv `ClaudeCliExecutor.execute` runs.

## Frozen names / contracts
- The function name and signature. Its output is part of the cache key (GV-1).

## Verification the receiver should run
- `pytest -q tests/test_claude_cli_argv_builder.py tests/test_executor.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Handoff stub created. State
  `Draft` mirrors `TASK.md` and `STATUS.md`.
