# STATUS

- ID: `T-OeRYSO-executor-argv-builder`
- Updated At: `2026-10-05`
- State: `Done`
- Owner: `developer` (Dev C)

## This update
Implemented by the developer agent and independently re-verified by the manager (commit ac35e73). The pure build_claude_argv(agent, prompt) is extracted from ClaudeCliExecutor.execute; execute() calls it; no other behaviour changed.
Two pre-existing quirks were preserved on purpose: the form --max-turns=5 does not block injection, and max_turns=0 injects.

## Evidence
- Manager re-run: pytest -q tests/test_claude_cli_argv_builder.py tests/test_executor.py -> 221 passed.
- Manager re-run: ruff check and ruff format --check on both files clean; mypy src -> only the 4 pre-existing _version.py errors.
- Developer-reported: selection executor/claude_cli/fake_executor 245 passed 1 skipped; tests/ minus ui and bench 4038 passed before, 4138 after (+100 new), 0 failed.
- Behaviour identity: re-inlining the function reproduces the base module AST; base vs new execute() agree over 116,640 generated agents with 0 mismatches; 11 injected defects were all caught; 28 hand-derived pinned argvs.
- Diff: src/agent_orchestrator/executors/claude_cli.py (+65/-45) and tests/test_claude_cli_argv_builder.py (new). No pre-epic test edited.

## Risks / Blockers
- No blockers.
- Risk: argv reordering; U-A1 compares against the real `Popen` argv.

## Next actions
1. T-uoYW6b imports build_claude_argv for the key (GV-1).
2. On any deliberate argv change update the pinned argv table and GV-1 together (see HANDOFF).

## Comments
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Status initialized (Draft).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 re-plan after the Phase-4
  consultation. State stays `Draft` (Sprint 1, Wave 1); this file, `TASK.md`, `HANDOFF.md` (when present)
  and the epic `STATUS.md` rollup agree.
- By: manager · Role: manager · Date: 2026-10-05 · Comment: State -> Done. Implemented by the developer agent and independently re-verified by the manager (commit ac35e73). The pure build_claude_argv(agent, prompt) is extracted from ClaudeCliExecutor.execute; execute() calls it; no other behaviour changed.
