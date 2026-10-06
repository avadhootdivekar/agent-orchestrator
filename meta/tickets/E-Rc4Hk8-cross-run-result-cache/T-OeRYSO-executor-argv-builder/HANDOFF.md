# HANDOFF: T-OeRYSO-executor-argv-builder

- Task: `T-OeRYSO-executor-argv-builder`
- State: `Done (handoff available)`
- From: `developer` (Dev C)
- To: T-uoYW6b

## What will be handed over
- `agent_orchestrator.executors.claude_cli.build_claude_argv(agent, prompt) -> list[str]`: pure,
  and behaviour-identical to the argv `ClaudeCliExecutor.execute` runs.

## Frozen names / contracts
- The function name and signature. Its output is part of the cache key (GV-1).

## Verification the receiver should run
- `pytest -q tests/test_claude_cli_argv_builder.py tests/test_executor.py`

## As built
- Public function: agent_orchestrator.executors.claude_cli.build_claude_argv(agent: AgentSpec, prompt: str) -> list[str] (pure, module level, returns a fresh list, never mutates the agent).
- Order contract: template substitution + extra_args; --model injection; --max-turns injection; _apply_tool_policy; _ensure_exclude_dynamic_sections; _ensure_stream_capture_flags.
- Tests: tests/test_claude_cli_argv_builder.py (U-A1 identity vs real Popen argv, 30-row matrix; U-A2 no I/O; U-A3 non-mutation; U-K8a run/task id tripwire; AST guard: claude_cli.py imports nothing from cache; pinned-argv table that must change together with GV-1).
- Verify: pytest -q tests/test_claude_cli_argv_builder.py tests/test_executor.py (221 passed).

## Comments
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Handoff stub created. State
  `Draft` mirrors `TASK.md` and `STATUS.md`.
- By: manager · Role: manager · Date: 2026-10-05 · Comment: State -> Done.
