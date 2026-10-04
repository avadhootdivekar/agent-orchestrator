# HANDOFF: T-8tr1H4-cache-hashing

- Task: `T-8tr1H4-cache-hashing`
- State: `Draft` (handoff not yet available)
- From: `developer` (Dev C)
- To: T-uoYW6b, T-gDNjN2

## What will be handed over
- `agent_orchestrator.cache.hashing`: `HashBudget`, `hash_regular_file`, `hash_directory`,
  `digest_path`.
- `agent_orchestrator.cache.repo_state`: `find_git_toplevel`, `nested_repo_marker`,
  `RepoHeadReader`, `WorktreeProbe`.

## Frozen names / contracts
- The signatures in HLD §8.2.3 and §8.2.4; the reason strings `input_*`,
  `repo_head_unavailable` and `repo_worktree_probe_failed`.

## Verification the receiver should run
- `pytest -q tests/cache/test_hashing.py tests/cache/test_repo_state.py`

## Comments
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Handoff stub created (Rev 2).
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3: workspace-bounded repo
  detection and `nested_repo_marker`. State `Draft` mirrors `TASK.md` and `STATUS.md`.
