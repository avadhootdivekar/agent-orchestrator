# HANDOFF: T-8tr1H4-cache-hashing

- Task: `T-8tr1H4-cache-hashing`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev C)
- To: T-uoYW6b, T-gDNjN2

## What will be handed over
- `agent_orchestrator.cache.hashing`: `HashBudget`, `hash_regular_file`, `hash_directory` and
  `digest_path`.
- `agent_orchestrator.cache.repo_state`: `has_git_marker`, `RepoHeadReader` and `WorktreeProbe`.

## Frozen names / contracts
- The signatures in HLD §8.2.3 and §8.2.4.
- The reason strings: `input_*`, `repo_head_unavailable` and `repo_worktree_probe_failed`.

## Verification the receiver should run
- `pytest -q tests/cache/test_hashing.py tests/cache/test_repo_state.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Handoff stub created. State
  `Draft` mirrors `TASK.md` and `STATUS.md`.
