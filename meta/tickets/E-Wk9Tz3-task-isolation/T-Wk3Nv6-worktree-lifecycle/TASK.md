# TASK: T-Wk3Nv6-worktree-lifecycle

## Metadata
- Task ID: `T-Wk3Nv6-worktree-lifecycle`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: unassigned (developer)
- Created: 2026-09-06
- Last Updated: 2026-09-07
- Status: Draft
- Estimate: 3 days

## Requirements Mapping
- Requirement IDs: FR-2, FR-3, FR-4, FR-14, NFR-4 · Design: HLD §7.1, §7.2, §11 M3
- Review findings folded in: **S-4** (blocking-adjacent major: per-task path-guard scoping),
  **R-6** (scoped prune — consume `T-Gt4Pw8`'s `prune_worktrees_scoped`, never a blanket prune),
  **R-11** (DRY: a shared XDG helper instead of a third hand-copy), **R-8** (`declared_outputs` rides on
  `TaskIsolation`), **S-9** (0700 directories). Re-estimated 2.5 -> 3 days.

## Description
Deterministic naming, the `effective_path` remap rule, repo grouping, and an idempotent worktree
lifecycle (create / reuse / release / reconcile / GC).

Files you own:
- `src/agent_orchestrator/isolation/paths.py` (new — all pure)
- `src/agent_orchestrator/isolation/worktrees.py` (new)
- `tests/isolation/test_paths.py`, `tests/isolation/test_worktrees.py` (new)
- `src/agent_orchestrator/xdg.py` (new — the shared `resolve_state_dir` helper, R-11)
- `src/agent_orchestrator/service/paths.py` (edit — migrate `default_state_dir`/`default_registry_path`
  onto the shared helper; **no behaviour change**, same env vars, same defaults, proven by the existing
  `tests/service/` suite passing unedited)
- `src/agent_orchestrator/isolation/view.py` (new — `IsolatedArtifactView`, S-4)
- `tests/isolation/test_view.py` (new)

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

### Amendments from the 2026-09-07 review gates

16. **R-6 — never call a blanket prune.** Every prune site in this module calls
    `T-Gt4Pw8`'s `prune_worktrees_scoped(worktree_root_prefix_for(run_id))`. A test asserts this module
    contains no call to a global `git worktree prune` (grep/AST assertion over the module source), and
    the foreign-worktree survival test is repeated at **this** level: a user-created worktree, made
    unreachable, survives `ensure()`, `reconcile()` and `gc_run()` untouched.
17. **R-11 — one XDG helper, not a third copy.** Add `xdg.resolve_state_dir(override_env, xdg_subdir,
    default_subdir)` and use it from `isolation/paths.py::state_dir()` **and** from
    `service/paths.py::default_state_dir()`/`default_registry_path()`. `project_config.py` is
    deliberately **not** migrated (its pattern is config-file anchoring, not XDG state resolution —
    folding it in would be a false DRY). Gate: the whole existing `tests/service/` suite passes
    **unedited**, proving the migration is behaviour-preserving.
18. **S-4 — `IsolatedArtifactView` is per-task, and that is the security property.** Ship it in
    `isolation/view.py`, constructed from **one** `TaskIsolation` and nothing else — never from the
    `WorktreeManager`'s registry of every worktree in the run. Follow HLD §7.3's normative wrapper
    shape; do **not** add an `extra_roots` parameter to `LocalFsArtifactStore` (one instance is shared
    with `RunStateStore`, so widening it would silently widen run-state resolution).
    Tests, all four distinct: (a) an absolute path under **task B's** worktree, submitted as an input,
    an output, and as `cwd` for **task A**, raises `ArtifactPathError` from A's view (three cases);
    (b) traversal (`../../etc/passwd`) still raises; (c) a symlink inside A's worktree pointing outside
    still raises; (d) `RunStateStore`'s store is never an `IsolatedArtifactView`.
19. **R-8 + cycle — `ensure(task_id, cycle, declared_outputs) -> TaskIsolation`.** `TaskIsolation`
    carries `cycle: int` and `declared_outputs: list[str]` so the Integrator can decide "Empty" and do
    the untracked-output copy-back **without ever seeing a `TaskSpec` or `RunState`** (this is what
    keeps R-20/NFR-3 true). Test that `declared_outputs` round-trips and that `TaskIsolation` exposes
    no reference to mutable run state.
20. **S-9 — 0700.** Every directory this module creates under `$AO_STATE_DIR` (worktree parents
    included) is mode 0700 on POSIX. One test asserting the mode.

## Risks
- Path length: deep `run_id`/`task_id` can approach `PATH_MAX`. Mitigation: truncate + hash any
  component over 80 chars in `sanitize_ref_component`, tested.
- `$AO_STATE_DIR` pointing inside a repo would put worktrees inside a working tree. Mitigation:
  `WorktreeManager` validates and refuses (the caller degrades) — test it.
- Deleting a worktree while an agent grandchild holds a file open. Mitigation: `--force` plus a caught,
  logged failure; never fatal.
- `service/paths.py` belongs to no other ticket in this epic, so migrating it here is safe — but it is
  shared with the `ao service` subsystem. The migration must be **behaviour-identical** (same env var
  names, same XDG fallbacks, same final subdirectories); the unedited `tests/service/` suite is the
  gate. If the two resolutions turn out not to be genuinely the same shape, do **not** force them
  together — report it and leave `service/paths.py` alone.
- S-4 is easy to implement "almost right": handing the view the manager's registry instead of one
  `TaskIsolation` looks equivalent and quietly breaks isolation between siblings. AC-18's task-A/task-B
  test is the only thing that catches it.

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

---
- By: architect · Role: architect · Date: 2026-09-07 · Comment: Phase-2 amendment. Folded in S-4 (the
  per-task `IsolatedArtifactView` now lives here, with the task-A-cannot-reach-task-B test that the
  producer-restriction test does not cover), R-6 (all prune sites go through
  `prune_worktrees_scoped`, plus a module-level assertion that no blanket prune exists), R-11 (shared
  `xdg.resolve_state_dir`, migrating `service/paths.py` with the unedited service suite as the gate),
  R-8 (`declared_outputs` on `TaskIsolation`, which is what lets the Integrator stay `RunState`-free
  per R-20) and S-9 (0700). `ensure()` gains `cycle` and `declared_outputs`. Re-estimated 2.5 -> 3 days
  for the extra module (`view.py`), the shared helper plus its migration, and five new tests.
