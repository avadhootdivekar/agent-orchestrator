# STATUS

- ID: `T-T2-5Ij6-tier2-providers`
- Updated At: 2026-10-23
- State: **Done** (on branch `ad/bench-tier2`, unmerged; epic rollup pending the manager)
- Owner: Claude (developer) for avadhoot

## This update
- By: Claude · Role: developer · Date: 2026-10-23 · Comment: all deliverables landed on `ad/bench-tier2`
  (base `9eabca7`). `repo_mutation` + `pr_mined` providers, `pytest_targeted` grader, `import-prs`,
  dev-medium `grading_dir` migration, `repo-mutant-mini` suite (4 recipes), docs. Notable design
  call: both providers re-snapshot the workspace as one parentless commit, because otherwise `git diff`
  / `git stash` expose or undo an injected bug and `git log --all` / `git fetch origin` hand the agent a
  PR's merge commit (see HLD §4.2a). `swebench_provider` still has that history exposure (not changed).

## Evidence
- `uv run pytest -q tests/bench` (in the worktree): 542 passed, 14 skipped -- the skips are the opt-in tiers
  (10 `network`, 3 `swebench`, 1 `real_llm`). `AO_E2E_NETWORK=1 uv run pytest -m network tests/bench`: 10 passed
  (8 repo-mutant-mini clone + install + runner e2e, 2 real-PR provider/grader). New tests this ticket:
  43 (mutation provider/grader) + 14 (unit, pytest_targeted) + 14 (pr_mined) + 12 (import-prs) + 22 (repo-mutant-mini, 8 network) + 16 new
  dev-medium tests (34 in that file now).
- `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy src/agent_orchestrator/bench`: clean.
- `uv run ao-bench validate` passes for `dev-medium` and `repo-mutant-mini`; `ao-bench import-prs` exercised against
  the live GitHub API (pallets/click, via a curl-backed `gh` shim) and the produced instances ran through the
  provider/grader (noop unsolved, gold patch solved).
- repo-mutant-mini (real clones, runner e2e): noop -> unsolved (score 0.3), copy-reference (pristine files) ->
  solved (1.0) for all 4 tasks; targeted pytest 0.8-4.8 s each. Recorded in each recipe's `verified`.

## Risks / Blockers
- A do-nothing run scores 0.3 on inject tasks (spec'd `0.7*f2p + 0.3*p2p`; its p2p sample passes). Prefer `solved`/f2p until calibrated.
- `pr_mined` without verified node ids is graded by whole test files -> score ~0.95 for a no-op; `solved` is exact.
- Unpinned `pytest`/`hypothesis` in recipe `install` commands can drift over time (versions not pinned).
- `.bench-solution/` (reference fix) still ships inside dev-core/dev-medium fixtures and is readable by the agent
  (pre-existing, not part of this ticket).

## Next actions
1. Manager: merge `ad/bench-tier2`, sync epic `EPIC.md`/`STATUS.md` rollup.
2. Follow-up ticket: `ao-bench verify-prs` (run targets at base + gold to fill f2p/p2p), and strip `.bench-solution/` from the agent workspace.
