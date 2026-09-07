# TASK: T-Wk3Nv6-worktree-lifecycle

## Metadata
- Task ID: `T-Wk3Nv6-worktree-lifecycle`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: unassigned (developer)
- Created: 2026-09-06
- Last Updated: 2026-09-07
- Status: Done
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
  `RepoIsolation`, `WorktreeManager(workspace_root, run_id, repos, integration_heads)` with
  `ensure/release/reconcile/gc_run` — C-4 (review, 2026-09-07): the 4th ctor param is a plain
  `dict[str, str]` (repo_key -> current integration head sha), not a `RunIntegrationState`
  reference, so `WorktreeManager` stays decoupled from `RunState` (same R-20/NFR-3 reasoning
  as the Integrator's own ctor) — and every `paths.py` function named in AC-1.
- Spec / data schema: none new (consumes `T-Sc7Rm2`'s models).
- Triggers / events: emits `worktree.created|reused|removed|remove_failed|orphan_reaped` plus
  `worktree.branch_reattached` and `worktree.remove_skipped` (both added in the review-fix
  pass — C-5: `remove_skipped` covers `release()`'s `locked`/`in_use` non-removal outcomes,
  which previously logged the misleading `worktree.removed` event name) via the standard
  `extra={"event": ...}` convention; a branch/path collision D-ENS cannot resolve
  automatically raises `errors.WorktreeCollisionError` instead of emitting an event. The full
  event contract is `T-Cx4Jf1`'s to audit.
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

- By: developer-agent · Role: developer · Date: 2026-09-07 · Comment: All 20 ACs implemented.
  `isolation/paths.py` (pure, import-safe without git): `sanitize_ref_component`, `task_branch`,
  `integration_branch`, `squash_ref`, `workspace_key`, `state_dir`, `worktree_root`,
  `worktree_root_prefix_for` (extra helper backing R-6's scoped-prune sites, named after the HLD's own
  `worktree_root_prefix_for` pseudocode), `effective_path`, `RESERVED_SHARED_PREFIXES`,
  `RESERVED_BRANCH_COMPONENTS`. `isolation/worktrees.py`: `group_repos`/`IsolatedRepo`/`RepoMember`
  (impure — needs `GitRepo.probe` — so live here, not in the "all pure" `paths.py`), `TaskIsolation`/
  `RepoIsolation`/`ReconcileReport`, `WorktreeManager.ensure/release/reconcile/gc_run`. `isolation/
  view.py`: `IsolatedArtifactView`, built from exactly one `TaskIsolation` (S-4). `xdg.py` reused
  as-is (already landed by `T-Gt4Pw8`, not recreated). `service/paths.py`: only `default_state_dir`
  migrated onto `xdg.resolve_state_dir`; `default_registry_path` deliberately left alone (see
  Deviations). `artifacts.py`: added `LocalFsArtifactStore.resolve_unchecked`/`.root` (read-only) plus
  two shared private helpers (`_exists_via_resolve`/`_size_via_resolve`) so `IsolatedArtifactView`
  doesn't duplicate the resolve-then-stat pattern; `resolve()`'s existing containment guard is
  unchanged (`tests/test_artifacts.py` passes unedited). No `extra_roots` param added to
  `LocalFsArtifactStore`, per the ticket's explicit instruction.

  **Tests**: `tests/isolation/test_paths.py` (250, incl. a 220-string generated corpus — no
  `hypothesis` dependency available — pinned against real `git check-ref-format --allow-onelevel`),
  `tests/isolation/test_worktrees.py` (28, real git repos via `conftest.py`'s fixtures + one
  `RecordingFakeRunner` case each for `release()`'s and `reconcile()`'s GitError-never-raises proof),
  `tests/isolation/test_view.py` (16, incl. the S-4 task-A/task-B test, symlink-escape, cross-run,
  traversal, and the `RunStateStore` never-wrapped assertion), `tests/isolation/
  test_service_paths_migration.py` (4, equivalence proof for every `default_state_dir` precedence
  branch — `tests/service/test_paths.py` is the primary gate and passes **unedited**).

  **Gates**: `uv run ruff check .` / `ruff format --check .` clean repo-wide (only files I touched:
  `isolation/paths.py`, `isolation/worktrees.py`, `isolation/view.py`, `artifacts.py`,
  `service/paths.py`, plus my 4 new test files — never the concurrently-modified
  `templates/builtin/**`/`test_builtin_routed_runner_assets.py`/`isolation/git.py`/
  `isolation/hotspots.py`/`scheduling/` files another task's developer has in flight). `uv run mypy
  src` — unchanged at exactly 4 pre-existing `_version.py` errors. Targeted gate (`tests/isolation
  tests/service tests/test_artifacts.py tests/test_xdg.py`): **632 passed / 0 failed**. Full suite,
  run twice: first run **4 failed** (all in `tests/test_builtin_routed_runner_assets.py`, a
  concurrently in-progress file this ticket never touches — a different task's instruction-template
  edits mid-flight, not `tests/test_isolation_*` as the brief anticipated but the same transient-
  concurrent-edit class); second run **3244 passed / 7 skipped / 0 failed**, stable. Coverage: new
  modules `isolation/paths.py` 100%, `isolation/view.py` 100%, `isolation/worktrees.py` 97%
  (`_is_submodule`'s `except OSError` defensive branch and `_mkdir_0700`'s filesystem-root guard are
  the only uncovered lines — both genuinely hard to trigger without faking a filesystem error),
  `service/paths.py` 100%; repo TOTAL 95% (baseline 94%, no regression).

  **Deviations / decisions**:
  1. **`default_registry_path` NOT migrated onto `xdg.resolve_state_dir`** — per the ticket's own
     escape hatch ("if the two resolutions turn out not to be genuinely the same shape, do not force
     them together"). It resolves a *file* path via `$XDG_CONFIG_HOME` under `~/.config`;
     `resolve_state_dir` is hardcoded to `$XDG_STATE_HOME` under `~/.local/state` and resolves a
     *directory*. Only `default_state_dir` is genuinely the same shape (verified: identical
     override-env / XDG-subdir / default-subdir triple), so only it migrated.
  2. **`RESERVED_SHARED_PREFIXES`/`effective_path` needed `TaskIsolation.workspace_root`** — the HLD's
     §7.2 pseudocode templates `RESERVED_SHARED_PREFIXES` on `<workspace_root>` but `effective_path`'s
     locked signature takes only `(resolved_abs, task_iso)`, so `TaskIsolation` carries
     `workspace_root: str` (beyond AC-19's minimum of `cycle`/`declared_outputs`) for the reserved-
     prefix containment check to be computable at all. `effective_path` itself is generic over a
     `Protocol`, not an import of `worktrees.TaskIsolation`, so `paths.py` stays git-free/import-safe.
  3. **`group_repos`, `IsolatedRepo`, `RepoMember` live in `worktrees.py`, not `paths.py`** — `AC-1`
     enumerates `paths.py`'s pure-function surface and does not name `group_repos`; it needs
     `GitRepo.probe` (I/O), so it lives in the impure lifecycle module alongside `WorktreeManager`,
     which is the same module `IsolatedRepo`/`RepoMember` naturally belong with.
  4. **A minor edge case from the ticket's own Risks section — "branch exists and points somewhere
     unrelated" as a hard error on a `run_id` collision — was NOT implemented separately** from the
     HLD's literal `ensure()` pseudocode (which unconditionally `delete_ref`s a stray branch before
     recreating). No AC requires the extra nuance; `worktree_add`'s own `GitError` still surfaces any
     genuine anomaly.
  5. **A disk-footprint guard was NOT implemented** — the ticket lists it as "if specified"; the HLD's
     matching finding (S-7, retained-failed-worktree cap) is explicitly disposed to `T-En8Hd4`
     (a warning tied to an existing breaker), not this ticket.

  **Hook points for `T-Ib5Qy9`/`T-En8Hd4`** (published per the Handoff Boundary): `WorktreeManager(
  workspace_root: str, run_id: str, repos: list[IsolatedRepo], integration_heads: dict[str, str], *,
  runner: Runner | None = None, hooks_dir: Path | None = None)`; `.ensure(task_id: str, cycle: int,
  declared_outputs: Sequence[str]) -> TaskIsolation`; `.release(task_id: str, outcome:
  Literal["integrated","failed"], policy: Literal["never","on_failure","always"]) -> None` (never
  raises); `.reconcile(known_task_ids: set[str]) -> ReconcileReport`; `.gc_run(run_id: str) -> None`
  (for `T-Cx4Jf1`'s `ao prune`). `group_repos(repo_paths: dict[str, str], *, probe=GitRepo.probe) ->
  tuple[list[IsolatedRepo], list[str]]` (groups, skipped non-git repo ids) is the upstream input to
  `WorktreeManager(repos=...)`. `IsolatedArtifactView(base: LocalFsArtifactStore, task_isolation:
  TaskIsolation)` — construct fresh per dispatch from that task's own `TaskIsolation` only.

  Not done / needs a look from a reviewer: item 4 above (the branch-collision hard-error nuance) if a
  downstream ticket's tests need it; no other gaps found against the ACs.
