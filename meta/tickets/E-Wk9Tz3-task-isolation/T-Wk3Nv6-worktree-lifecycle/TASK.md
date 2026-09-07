# TASK: T-Wk3Nv6-worktree-lifecycle

## Metadata
- Task ID: `T-Wk3Nv6-worktree-lifecycle`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: unassigned (developer)
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 2.5 days

## Requirements Mapping
- Requirement IDs: FR-2, FR-3, FR-4, FR-14, NFR-4 · Design: HLD §7.1, §7.2, §11 M3

## Description
Deterministic naming, the `effective_path` remap rule, repo grouping, and an idempotent worktree
lifecycle (create / reuse / release / reconcile / GC).

Files you own:
- `src/agent_orchestrator/isolation/paths.py` (new — all pure)
- `src/agent_orchestrator/isolation/worktrees.py` (new)
- `tests/isolation/test_paths.py`, `tests/isolation/test_worktrees.py` (new)

Do NOT touch: `git.py` (read it, do not edit), `integrator.py`, `engine.py`, `models.py`, `cli.py`.
Read the **merged** `isolation/git.py` from `T-Gt4Pw8` for the real API and reuse its
`tests/isolation/conftest.py` fixture builder.

## Acceptance Criteria
1. `paths.py` is import-safe without git and contains only pure functions:
   `sanitize_ref_component`, `task_branch`, `integration_branch`, `squash_ref`, `workspace_key`,
   `state_dir`, `worktree_root`, `effective_path`, `RESERVED_SHARED_PREFIXES`.
2. `sanitize_ref_component` — property test (hypothesis or a generated corpus of >= 200 strings
   including unicode, `..`, `.lock`, `//`, empty, 300 chars, leading/trailing `-`/`.`): the output is
   always accepted by `git check-ref-format --allow-onelevel` (asserted by actually running it).
3. `RESERVED_BRANCH_COMPONENTS = {"integration"}`: `task_branch` asserts the sanitized task id
   is not a reserved component (the fatal case is caught earlier by `T-Sc7Rm2`'s V9; this is
   defence in depth against an injected id). Test both the assert and a legal id.
4. **No D/F ref conflict**: `integration_branch(run)` is `ao/<run>/integration` and `task_branch` is
   `ao/<run>/<task>`; a test creates both in a real repo and asserts both exist. A test asserts
   `ao/<run>` is never itself created as a branch.
5. `state_dir()` resolves `AO_STATE_DIR` > `$XDG_STATE_HOME/ao` > `~/.local/state/ao`, reading
   `os.environ` at call time (no caching), mirroring `service/paths.py`. `worktree_root` additionally
   honours `AO_WORKTREE_ROOT`. Tests monkeypatch env; **no test touches a real `~`**.
6. `workspace_key(root)` is stable across calls, filesystem-safe, and different for two roots whose
   basenames collide (`/a/proj` vs `/b/proj`).
7. `effective_path` implements HLD §7.2 exactly, with tests for: a path inside an isolated repo →
   remapped; a path outside every repo → unchanged; a path under `.orchestrator/` or `.ao/` →
   unchanged **even when inside an isolated repo**; **nested repos → the innermost (longest toplevel)
   wins**; the repo toplevel itself → the worktree root; `task_iso is None` → identity.
8. `group_repos(repo_paths)` (HLD §7.1): two `RepoRef`s inside one git repo (the real consumer's
   `core=./fin_plan`, `docs=./fin_plan/docs-md` shape) produce **one** `IsolatedRepo` with two
   members and correct `rel` values (`""` and `"docs-md"`); a non-git path is skipped and reported; a
   submodule is its own group and sets `submodule=True`; the returned order is sorted by repo key and
   therefore deterministic (assert stable across 10 runs with shuffled input).
9. `WorktreeManager.ensure(task_id, attempt)` creates the worktree with
   `git worktree add -b <branch> <path> <integration_head>` and records `base`, `branch`,
   `worktree_root` per repo. Re-calling `ensure` for the same task **reuses** the worktree (asserted
   by inode/mtime and by `worktree.reused`), and if a rebase was in progress it is aborted first.
10. Crash recovery: a test that (a) leaves a stale, unregistered directory at the worktree path →
   `ensure` removes it and recreates; (b) leaves a registered worktree whose directory was deleted →
   `worktree_prune` clears it and `ensure` recreates; (c) leaves a repo mid-rebase → `ensure` aborts
   it and the worktree is usable.
11. `release(task_id, outcome, policy)` honours `never` / `on_failure` / `always`; a
    `worktree_remove` failure is caught, logged and **never** propagates (test with a
    `GitError`-raising stub).
12. `reconcile(known_task_ids)` reaps worktrees and `refs/heads/ao/<run>/*` refs whose task id is not
    in the known set, and is a no-op on a clean state (idempotent — running it twice changes nothing).
13. `gc_run(run_id)` removes every worktree, prunes, and deletes `refs/heads/ao/<run>/*` and
    `refs/ao/runs/<run>/*`. A test asserts zero `ao/`-namespaced refs remain and
    `git worktree list` shows only the main checkout.
14. A test asserts a created worktree **does not** materialize a `.gitignore`d directory (create a
    large ignored dir in the source repo; assert it is absent from the worktree) — the NFR-6 claim.
15. `uv run pytest -q tests/isolation/` green; full suite green with a recorded before/after count;
    `ruff` clean; `uv run mypy src` zero new errors.

## Risks
- Path length: deep `run_id`/`task_id` can approach `PATH_MAX`. Mitigation: truncate + hash any
  component over 80 chars in `sanitize_ref_component`, tested.
- `$AO_STATE_DIR` pointing inside a repo would put worktrees inside a working tree. Mitigation:
  `WorktreeManager` validates and refuses (the caller degrades) — test it.
- Deleting a worktree while an agent grandchild holds a file open. Mitigation: `--force` plus a caught,
  logged failure; never fatal.

## Dependencies
- Upstream: `T-Gt4Pw8-git-porcelain` (API), `T-Sc7Rm2-isolation-schema-models` (`TaskIntegrationState`,
  `RunIntegrationState`).
- Downstream: `T-Ib5Qy9`, `T-En8Hd4`, `T-Cx4Jf1`.

## Pseudocode / Algorithm
```text
HLD §7.1 group_repos, §7.2 effective_path, §11 M3 WorktreeManager.ensure/release/reconcile/gc_run.
```

## Schemas / Interface Notes
- Interface / API (locked): `group_repos`, `IsolatedRepo`, `RepoMember`, `TaskIsolation`,
  `RepoIsolation`, `WorktreeManager(workspace_root, run_id, repos, integration)` with
  `ensure/release/reconcile/gc_run`, and every `paths.py` function named in AC-1.
- Spec / data schema: none new (consumes `T-Sc7Rm2`'s models).
- Triggers / events: emits `worktree.created|reused|removed|remove_failed|orphan_reaped` via the
  standard `extra={"event": ...}` convention; the full event contract is `T-Cx4Jf1`'s to audit.
- Artifacts: worktrees under `$AO_STATE_DIR/worktrees/...` (never under the workspace root).

## Handoff Boundary
- Upstream: read the merged `isolation/git.py`.
- Downstream: `T-Ib5Qy9` consumes `TaskIsolation`; `T-En8Hd4` calls `ensure`/`release`/`reconcile`;
  `T-Cx4Jf1` calls `gc_run` from `ao prune`. Publish final signatures in `STATUS.md`.

## Artifacts
- Docs/comments: `meta/tickets/E-Wk9Tz3-task-isolation/T-Wk3Nv6-worktree-lifecycle/`
- Large outputs: none
