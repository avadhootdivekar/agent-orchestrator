# HANDOFF: T-6tRKml-cache-cli-commands

- Task: `T-6tRKml-cache-cli-commands`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev C)
- To: T-JCOAsq, T-fXWbqg (G2), T-bdQZW4

## What will be handed over
- `ao cache ls|stats|show|rm|prune|clear|verify`, each with `--json`.
- The §13.4 schema fixtures.

## Frozen names / contracts
- The exit codes and JSON schema ids in HLD §8.9 and §13.4.

## Verification the receiver should run
- `pytest -q tests/test_e2e_cli_result_cache_admin.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Handoff stub created.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 contents. State `Draft`
  mirrors `TASK.md` and `STATUS.md`.
