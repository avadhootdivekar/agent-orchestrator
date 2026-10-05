# TASK: T-OeRYSO-executor-argv-builder

## Metadata
- Task ID: `T-OeRYSO-executor-argv-builder`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev C)
- Created: `2026-10-05`
- Last Updated: `2026-10-05`
- Status: `Done`
- Estimate: `6 focus hours (0.75 day)` · Sprint 1, Wave 1

## Requirements Mapping
- Requirement IDs: FR-3 (argv is a key component), NFR-8
- HLD: §8.2.5 (argv), §8.2.7, §24.2 (`claude_cli.py` row)
- ADR-0019: D7

## Description
Extract a **pure, behaviour-identical** `build_claude_argv(agent: AgentSpec, prompt: str) ->
list[str]` from `ClaudeCliExecutor.execute` in `src/agent_orchestrator/executors/claude_cli.py`.
`execute()` then calls it.

The result-cache key hashes this exact argv. That way, any future change to argv construction
changes cache keys automatically, with no manual version bump. Examples:

- the `EFFORT_MAX_TURNS` values;
- flag injection;
- tool policy;
- stream flags.

The extraction moves the existing code verbatim, in this order (see `execute()` at base
`bb6d8a0`):

1. template substitution;
2. `extra_args`;
3. the `--model` injection guard;
4. `--max-turns` (explicit `max_turns`, else `EFFORT_MAX_TURNS[effort]`). Keep the lazy
   `from ..models import EFFORT_MAX_TURNS` import as it is.
5. `_apply_tool_policy`;
6. `_ensure_exclude_dynamic_sections`;
7. `_ensure_stream_capture_flags`.

`execute()` keeps `prompt = build_prompt(ctx)` and then calls
`argv = build_claude_argv(ctx.agent, prompt)`. Nothing else in `execute()` changes.
`claude_cli.py` must import nothing from `agent_orchestrator.cache`.

## File scope (exclusive)
- `src/agent_orchestrator/executors/claude_cli.py`: extraction only. T-bdQZW4 later adds one
  pointer comment.
- `tests/test_claude_cli_argv_builder.py` (new)

## Inputs / Outputs
- **Inputs:** `claude_cli.py` at base.
- **Outputs:** the public, pure function `build_claude_argv`, which T-uoYW6b imports.

## Acceptance Criteria
1. **U-A1 (behaviour identity).** For a matrix of agents, `build_claude_argv(agent, p)` equals the
   argv that `ClaudeCliExecutor.execute` passes to `subprocess.Popen`.
   - Capture the `Popen` argv by monkeypatching `agent_orchestrator.executors.claude_cli.subprocess.Popen`
     with a fake that records it and simulates an immediate exit.
   - The agent matrix:
     - model set or unset;
     - `--model` already in `command_template`;
     - `-m` in `extra_args`;
     - effort `low`, `medium`, `high`, `xhigh` and unset;
     - explicit `max_turns`;
     - `--max-turns` already in `extra_args`;
     - non-empty `disallowed_tools`;
     - non-empty `forced_disallowed_tools`;
     - an own `--allowedTools` policy;
     - `exclude_dynamic_system_prompt_sections=True`, with and without a custom
       `--system-prompt`;
     - an own `--output-format json`.
2. **U-A2 (purity).** With `os.makedirs`, `open` and `subprocess.Popen` monkeypatched to raise,
   `build_claude_argv` still returns. It does no I/O.
3. **U-A3 (non-mutation).** The input `agent.command_template` and `agent.extra_args` lists are
   unchanged after a call.
4. **U-K8a (tripwire).** `build_claude_argv(agent, build_prompt(ctx))` is identical for two
   `TaskContext`s that differ only in `run_id` and `task_id`. If a future template starts using
   either id, this test fails and the key design must be revisited.
5. **No regressions.**
   - Every existing executor test passes unchanged, including `tests/test_executor.py`. No
     pre-epic test file is edited.
   - The diff to `claude_cli.py` is limited to the new function, plus `execute()` calling it.
   - `ruff` and `mypy` are clean.

## Test requirements
- `tests/test_claude_cli_argv_builder.py`: AC-1..AC-4.

## Risks
- **A subtle reordering changes dispatch argv for real runs.** Mitigation: U-A1 compares against
  the real `execute()` path across the whole matrix.
- **A sibling epic edits argv construction concurrently.** Mitigation: the merge rule in HLD
  §24.2. The change goes inside `build_claude_argv`; then re-run U-A* and GV-1.

## Dependencies
- None. It can start on day 1, in parallel with T-FJH6LI.

## Pseudocode / Algorithm
```text
def build_claude_argv(agent, prompt) -> list[str]:      # pure; body moved verbatim from execute()
    argv = [a.replace("{prompt}", prompt) if "{prompt}" in a else a for a in agent.command_template] + agent.extra_args
    if agent.model and "--model" not in argv and "-m" not in argv: argv = argv + ["--model", agent.model]
    if "--max-turns" not in argv: (explicit max_turns, else EFFORT_MAX_TURNS[effort] via the lazy import)
    argv = _apply_tool_policy(argv, tuple(agent.disallowed_tools), tuple(agent.forced_disallowed_tools))
    argv = _ensure_exclude_dynamic_sections(argv, agent.exclude_dynamic_system_prompt_sections)
    return _ensure_stream_capture_flags(argv)
```

## Schemas / Interface Notes
- **Interface:** `build_claude_argv(agent: AgentSpec, prompt: str) -> list[str]`, public and pure.
- **Spec / data / events / artifacts:** N/A.

## Handoff Boundary
- **Upstream:** none.
- **Downstream:** T-uoYW6b, which puts the argv in the key and reproduces GV-1.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-OeRYSO-executor-argv-builder/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-05 · Comment: New in Rev 2. Split out of the
  key builder after dev-critic #4 and reviewer R7: hashing the real argv retires the "remember to
  bump `KEY_SCHEMA_VERSION`" convention for argv-visible changes.
