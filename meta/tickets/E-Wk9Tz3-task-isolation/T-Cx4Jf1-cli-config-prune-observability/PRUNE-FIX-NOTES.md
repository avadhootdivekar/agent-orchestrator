# `ao prune` ref-leak fix — field defect (T-Cx4Jf1 AC-5 / AC-6 / AC-14)

- By: developer-agent · Role: developer · Date: 2026-09-07
- Scope: `src/agent_orchestrator/cli.py`, `tests/test_e2e_cli_prune_worktrees.py`. No other
  file touched (`engine.py`, `models.py`, `ui/`, `tests/isolation/*`, `tests/ui/`,
  `tests/test_e2e_isolation.py`, `tests/test_isolation_events.py`, `STATUS.md`/`TASK.md`
  and the epic rollup are all owned by other agents right now). No commit made.

## 1. Defect (reproduced live, not hypothesised)

A two-task isolated `ao run` in a throwaway repo; both tasks succeeded. `ao prune -w <ws>
--older-than 0` deleted the run directory, reported success, and left behind **every** ref
the run created:

```
refs/heads/ao/<run>/alpha
refs/heads/ao/<run>/beta
refs/heads/ao/<run>/integration
refs/ao/runs/<run>/alpha/squash-1
refs/ao/runs/<run>/beta/squash-1
```

`ao prune --worktrees-only` then reported `0 orphaned run(s) reaped` and also left them.
This is precisely the leak class AC-6 exists to eliminate (the consumer repo's 14 orphan
branches).

## 2. Root cause (confirmed)

`_discover_run_worktree_repos` discovered a run's repositories **solely** by probing the
physical worktree directories still on disk under the run's worktree prefix. A run whose
tasks all succeed has every worktree directory removed at task end by design
(`WorktreeManager.release` → `worktree.removed`), so probing finds nothing,
`_gc_run_worktrees` returns 0, and no ref is ever deleted. The `prune --help` text framed
this as an edge case ("if EVERY worktree directory for a run's repo is gone…"); it is in
fact the **normal end state of every fully successful run**.

## 3. Fix

Repo discovery is now the **union** (deduplicated by normalised git common dir) of two
sources:

1. **The run's own persisted record** — `RunState.integration.repos` (`repo_key -> git
   common dir`) read from `<ws>/.orchestrator/runs/<run_id>/state.json`. Authoritative and
   complete; survives every worktree directory being removed. Read defensively
   (`_recorded_run_repos`): a missing/corrupt/legacy state file degrades to source 2 and
   never aborts the prune.
2. **Physical probing** — unchanged, still covers runs with no state file.

Supporting changes, all in `cli.py`:

- `_runs_dir()` / `_RUN_STATE_FILENAME` — one spelling of the run-directory layout
  (`RunStateStore` needs an `ArtifactStore` prune cannot build, and must tolerate a state
  file `RunState` would refuse to validate).
- `_isolated_repo_for_common_dir()` — extracted from `_discover_run_worktree_repos`
  (now used by both sources; DRY). Keeps **every** existing guard, including the C-4
  warning when the main checkout a common dir points at is gone/unreadable.
- `_discover_run_worktree_repos` / `_gc_run_worktrees` / `_preview_run_worktrees` take an
  optional `recorded_repos` mapping so preview and real GC always resolve the same repo
  set.
- `prune()` reads the run's record **before** `shutil.rmtree` (the record lives inside the
  directory being deleted) and passes it to the GC.
- `_prune_worktrees_only()`: an orphan's own state file is gone with its run directory, so
  it falls back to `_workspace_recorded_repos()` — the union of the repos recorded by the
  other run states still present in the workspace (computed once per invocation, lazily,
  only when there is an orphan).
- `prune --help`: the "permanently unreapable" paragraph documented the defect as
  by-design; replaced with the real behaviour plus the one genuine residual limit.

**Blast radius unchanged.** Everything still routes through `WorktreeManager.gc_run` →
`prune_worktrees_scoped` (never raw git, never a blanket `git worktree prune`), still
scoped to that run's own `ao/<run>/…` ref namespace and its own worktree prefix. A repo
reached via the workspace-wide fallback that the orphan never touched is a guaranteed
no-op, not a wider reach.

### Deliberate design decision worth review

`--worktrees-only`'s candidate run ids still come from **directories under this
workspace's worktree root**, not from enumerating `refs/heads/ao/*` in the repos. Ref
enumeration would be a more complete reconciliation, but refs are shared per-repo across
workspaces while run directories are not — it could delete a *live* run's refs when a
second workspace shares the same repo. Rejected as a blast-radius widening.

**Residual limit (documented in `--help`):** in a workspace with no run state left at all
*and* no worktree directory left to probe, an already-orphaned run's refs cannot be
located. `ao prune` no longer creates that state; it can only be reached by a pre-fix
prune, `--no-worktrees`, or a manual `rm -rf` of the run directory.

## 4. Tests (`tests/test_e2e_cli_prune_worktrees.py`, CliRunner e2e)

New fixture `_make_completed_run()` rebuilds the **field** state, not a synthetic one:
task branches + integration branch + squash refs + a persisted `state.json`, and
`manager.release(task, "integrated", "never")` for every task so **no** worktree directory
survives (asserted in the fixture itself).

- `TestPruneReapsFullySuccessfulRun`
  - refs of a run with no surviving worktree are deleted by `ao prune`
  - `--dry-run` previews all five refs and deletes nothing
  - `--worktrees-only` reaps the same run (the exact field sequence); a still-present run's
    refs are untouched
  - `--worktrees-only --dry-run` previews and deletes nothing
  - **R-6**: a user-created worktree *and* two user-created branches survive both variants
- `TestPruneRepoRecordDegradesSafely`
  - corrupt/legacy `state.json` falls back to physical probing, run still pruned
  - **C-4**: a recorded repo whose checkout has vanished warns (`gone or unreadable`) and
    is left honestly unreaped rather than silently claimed as GC'd

**Reproduction proof:** with the `cli.py` change stashed, the 6 new tests fail
(6 failed / 9 passed); with it applied, 15 passed. The 9 pre-existing tests pass both ways.

## 5. Evidence — live re-run after the fix

Fresh two-task isolated run (`smoke3`, both tasks succeeded, both worktrees removed by the
engine, 5 refs leaked, plus a hand-made `my-feature` branch and `my-wt` worktree):

- `ao prune -w repo --older-than 0 --dry-run` → listed all 5 refs, deleted nothing (5 refs
  still present afterwards).
- `ao prune -w repo --older-than 0` → `worktrees GC'd for <run>: 1 repo(s)`; all 5 refs
  gone; `refs/heads/main`, `my-feature`, `my-wt` and the user worktree intact; the stale
  worktree prefix directory removed.
- Two further runs, one run directory deleted by hand → `ao prune --worktrees-only` →
  `1 orphaned run(s) reaped`; the orphan's 5 refs gone, the surviving run's 5 refs and both
  user branches untouched.

## 6. Gates

| Gate | Result |
|---|---|
| `./.venv/bin/ruff check` (both files) | clean |
| `./.venv/bin/ruff format --check` (both files) | clean (2 files already formatted) |
| `./.venv/bin/mypy src` | 4 errors, all pre-existing in `_version.py` — unchanged |
| `pytest tests/test_e2e_cli_prune_worktrees.py` | **15 passed** (9 before, 6 new) |
| Targeted (`…prune_worktrees` + `…cli_isolation` + `isolation/test_worktrees`) | **54 passed** |
| Full suite `pytest -q` | **3685 passed / 7 skipped / 0 failed** (baseline 3678/7/0; +6 mine, +1 from a concurrent agent) |
