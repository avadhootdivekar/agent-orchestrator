# TASK: T-Gt4Pw8-git-porcelain

## Metadata
- Task ID: `T-Gt4Pw8-git-porcelain`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: developer-agent
- Created: 2026-09-06
- Last Updated: 2026-09-07
- Status: Done
- Estimate: 2 days

## Requirements Mapping
- Requirement IDs: FR-2, FR-6, FR-14, NFR-1, NFR-4 (see `../EPIC.md`) · Design: HLD §11 M1, §14
- Review findings folded in: **S-1** (blocking, security), **R-6** (major, unscoped prune),
  **R-23** (major, release-on-failure needs tolerant primitives). Both gates confirmed this task is
  **not blocked by any finding** and may start immediately, in parallel with `T-Sc7Rm2`.

## Description
Build `src/agent_orchestrator/isolation/git.py` — the **single** place in the codebase that shells out
to git for isolation work. Everything else in this epic calls it; nothing else calls `subprocess` with
`git`. Typed results, bounded timeouts, an injectable runner, and a `GitError` hierarchy that never
leaks a raw `CalledProcessError`/`TimeoutExpired`.

Because it is the single choke point, this module also owns three cross-cutting guarantees the rest of
the epic simply inherits:
1. **No repo-local hook ever fires from an engine-issued git call** (S-1).
2. **The engine's porcelain performs no network operation, ever** — by construction and by a runtime
   guard.
3. **Pruning and removal are scoped and tolerant** — ao never deregisters a worktree it did not
   create (R-6), and removal never raises on an already-absent or locked worktree, because the
   lifecycle's `release()` runs on failure paths where raising would mask the real error (R-23).

Files you own:
- `src/agent_orchestrator/isolation/__init__.py` (new package)
- `src/agent_orchestrator/isolation/git.py` (new)
- `src/agent_orchestrator/errors.py` (edit — add `GitError` + subclasses to the existing
  `OrchestratorError` hierarchy; confirmed by review to be this repo's single error home)
- `tests/isolation/__init__.py`, `tests/isolation/test_git.py` (new)
- `tests/isolation/conftest.py` (new — the shared real-git fixture builder other tasks import)

Do NOT touch: `engine.py`, `models.py`, `spec.py`, `artifacts.py`, `runstate.py`, `cli.py`,
`specs/*.schema.json`, `templates/`, or any other `isolation/` module (owned by later tasks).

Prior art to copy rather than reinvent: `src/agent_orchestrator/bench/swebench_provider.py::_run_git`
(subprocess wrapper raising a domain error, with named timeout constants) and
`tests/bench/test_swebench_provider.py::_git` (real `git init` fixtures with pinned
`-c user.email` / `-c user.name`).

## Acceptance Criteria

### Core surface
1. `GitRepo(path, *, timeout=GIT_DEFAULT_TIMEOUT_SECONDS, rerere=True, runner=None, env=None)` exists
   with **exactly** the method set in HLD §11 M1. `GIT_DEFAULT_TIMEOUT_SECONDS = 300`,
   `GIT_MIN_VERSION = (2, 30)`, `GIT_MERGE_TREE_MIN_VERSION = (2, 38)`, `SAFETY_ARGS`, `RERERE_ARGS`,
   `FORBIDDEN_SUBCOMMANDS`, `EMPTY_HOOKS_DIR` are module constants, never literals at call sites.
2. **Injectable runner.** `Runner` is a `Protocol` with
   `__call__(argv: list[str], *, cwd: str, env: dict[str,str] | None, timeout: float) -> CompletedProcess`;
   `GitRepo` takes one and defaults to a `subprocess.run`-based implementation. At least one test
   drives `GitRepo` end to end through a **recording fake runner** with no real git, asserting the
   exact argv (this is how downstream tickets will unit-test their git usage without fixtures).
3. **Exception hierarchy** (locked): `GitError(OrchestratorError)` with `argv`, `exit_code: int|None`,
   `stderr_tail` (truncated to 4 KiB); subclasses `GitTimeoutError`, `GitUnavailableError`,
   `GitForbiddenCommandError`. A timeout raises `GitTimeoutError`, never `subprocess.TimeoutExpired`.
   `check=False` returns the `CompletedProcess` without raising.

### S-1 — engine-issued git calls are hook-free, prompt-free, signature-free (BLOCKING)
4. Every invocation built by `_run` carries `SAFETY_ARGS` **before** the subcommand:
   `-c core.hooksPath=<EMPTY_HOOKS_DIR>`, `-c commit.gpgsign=false`, `-c core.editor=true`,
   `-c gc.auto=0`; and the child env sets `LC_ALL=C`, `GIT_EDITOR=true`, `GIT_TERMINAL_PROMPT=0`,
   `GIT_ASKPASS=""`. `EMPTY_HOOKS_DIR` is created once under `$AO_STATE_DIR`, mode `0700`, and is
   asserted to be empty at creation (a **directory**, not `/dev/null` — portability).
5. **Planted-hook test (the S-1 gate).** A fixture repo gets executable `post-checkout`,
   `pre-commit`, `commit-msg`, `post-commit` and `post-rewrite` hooks that each write a sentinel file
   (and one that exits non-zero). Drive `worktree_add`, `add_all`, `commit`, `rebase_onto` and
   `rebase_continue` through `GitRepo`; assert **no sentinel file exists** and no call failed because
   of the failing hook. Then run the same git commands **without** `GitRepo` (raw `subprocess`) and
   assert the sentinels *do* appear — proving the fixture is real and the test is not vacuous.
6. **No config mutation.** After a full sequence (worktree add → commit → rebase → update-ref), assert
   `git config --local --get-regexp '.*'` in the fixture repo contains **none** of
   `rerere.enabled`, `core.hooksPath`, `commit.gpgsign`, `core.editor`, `gc.auto`, and that the user's
   global config is never written (test runs with `HOME` pointed at `tmp_path`).
7. **A repo configured to sign commits cannot hang the engine.** Fixture sets
   `commit.gpgsign=true` with no usable key; `GitRepo.commit(...)` still succeeds.

### S-1 — no network operation, ever
8. `FORBIDDEN_SUBCOMMANDS = {"push","fetch","pull","clone","remote","submodule","request-pull",
   "send-email","svn","p4","daemon","credential"}`. `_run` raises `GitForbiddenCommandError` when
   `args[0]` is in that set, **before** building argv. Tested per verb.
9. A test asserts the public method surface contains no network verb: introspect `GitRepo`'s public
   methods and assert none of them ever passes a forbidden subcommand (drive every public method once
   against the fake runner and assert no recorded argv contains a forbidden verb). This is the
   structural half of S-1 that survives future edits.

### Probes
10. Probes never raise: `GitRepo.version()` returns `None` when git is absent (monkeypatch `PATH`) and
    `GitUnavailableError` is raised only by callers that require a minimum. `GitRepo.probe(path)`
    returns `None` for a non-repo directory and, for a repo, `RepoProbe{toplevel, common_dir, bare,
    is_worktree}`. For a **worktree**, `probe` returns that worktree's own toplevel and the **shared**
    `common_dir` (assert it equals the main repo's) — this is what `group_repos` relies on.

### Worktrees — scoped prune (R-6) and tolerant removal (R-23)
11. `worktree_list() -> list[WorktreeEntry]` with `{path, head, branch|None, bare, detached, locked,
    prunable, admin_dir}`. Parsing is a **separate pure function** `parse_worktree_list(text,
    common_dir)` with its own unit tests over captured `--porcelain` output including a detached-HEAD
    entry, a `prunable` entry and a `locked` entry.
12. **A blanket `git worktree prune` is NOT part of the public API.** Only
    `prune_worktrees_scoped(path_prefix) -> PruneReport` exists, implementing HLD §11 M1's algorithm:
    enumerate first; run the global prune **only** when no unlocked, prunable, non-ao entry exists;
    otherwise remove only ao's own stale admin dirs (`$GIT_COMMON_DIR/worktrees/<id>`) and report the
    foreign entries it deliberately skipped.
13. **Foreign-worktree survival test (the R-6 gate).** Create a repo with (a) an ao-prefixed worktree
    whose directory has been deleted and (b) a **user-created** worktree at an unrelated path whose
    directory has also been made unreachable. Call `prune_worktrees_scoped(ao_prefix)`; assert the ao
    registration is gone, the **foreign registration still exists** (`worktree_list` still shows it),
    and `PruneReport.mode == "scoped"` with the foreign path in `skipped_foreign`. A second test with
    no foreign prunable entry asserts `mode == "global"`.
14. `worktree_remove(path, *, force=False) -> WorktreeRemoveOutcome` returns
    `"removed" | "already_absent" | "locked" | "in_use"` and **never raises** for the first three —
    `release()` on a failure path must not mask the underlying error (R-23). One test per outcome,
    including removing a worktree whose directory was deleted out from under git.
15. `branch_exists(name)`, `delete_branch(name, *, force=False) -> bool` (False when absent, never
    raises) — the branch half of `release()`/GC.

### Refs, commits, status, rebase
16. `update_ref_cas(ref, new, expected_old)` returns `True` on success and **`False`** (never raises)
    when `expected_old` does not match. A test proves the CAS loss: read the ref, move it by another
    call, then attempt the CAS with the stale value.
17. `commit_tree(tree_ish, parent, message)` returns a sha; committing the same tree/parent/message
    twice yields the **same** sha (test pins `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE`).
18. `commit(cwd, message)` returns `None` when nothing is staged and does **not** create an empty
    commit. `add_all` respects `.gitignore` (an ignored file is not staged). `add_paths(cwd, paths)`
    stages exactly the given paths and nothing else — this is the primitive S-3's screened auto-commit
    will use if it needs to stage selectively.
19. `status_porcelain(cwd, *, untracked=True) -> list[StatusEntry{path, index, worktree}]` — the
    primitive `T-Ib5Qy9`'s S-3 denylist screen consumes. It must distinguish **untracked** from
    modified-tracked entries (the screen only applies to untracked ones). Test with all three states
    plus a renamed and a deleted file.
20. `rebase_onto` returns `RebaseOutcome(clean=True)` for a disjoint change and
    `RebaseOutcome(clean=False, paths=[...])` for a real conflict; a rebase that fails **without** a
    conflict raises `GitError`. `rebase_in_progress` is `True` in the conflicted state and `False`
    after `rebase_abort`; `rebase_continue` never blocks on an editor (asserted with no TTY).
21. `conflicted_paths` returns exactly the `--diff-filter=U` set; `show_stage(cwd, n, path)` returns
    bytes for stages that exist and `None` for one that does not (delete/modify conflict).
22. `merge_tree_probe(a, b)` returns `None` when `git < 2.38` and otherwise a `MergeProbe(clean,
    paths)` matching the real rebase outcome for both a clean and a conflicting fixture.

### Shared fixtures (locked interface for downstream tasks)
23. `tests/isolation/conftest.py` exposes `make_repo(tmp_path, files) -> Path` and
    `make_conflict_repo(tmp_path, kind) -> (repo, base_sha, ours_branch, theirs_branch)` for kinds
    `clean`, `union`, `true_conflict`, `add_add`, `delete_modify`, `binary`, plus
    `make_hooked_repo(tmp_path)` for the S-1 gate. All git invocations pin `-c user.name`,
    `-c user.email`, `core.autocrlf=false`, and fixed `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE`.
    **Later tasks import these — the names and return shapes are a locked interface.**
24. `uv run pytest -q tests/isolation/` green; `ruff check` / `ruff format --check` clean on owned
    files; `uv run mypy src` introduces zero new errors versus the recorded baseline. No network, no
    real `~` (tests set `HOME` to `tmp_path`), no writes outside `tmp_path` / `$AO_STATE_DIR` override.

## Risks
- Git version drift across dev machines and CI. Mitigation: every version-dependent method is behind
  an explicit version check; `merge_tree_probe` degrades to `None`; tests needing `>= 2.38`
  `pytest.skip` with a clear reason rather than failing.
- `core.hooksPath` is honoured from git 2.9 onwards — comfortably below `GIT_MIN_VERSION = (2,30)`, so
  S-1 holds across the whole supported range. Verify this explicitly rather than assuming.
- Locale/i18n in git output. Mitigation: parse porcelain formats only, and set `LC_ALL=C`; never parse
  human-readable messages.
- Over-building. Do not add a method this HLD does not list — every method needs a caller. In
  particular do **not** add a convenience network verb "for later"; FR-14/S-1 depend on its absence.

## Dependencies
- Upstream: none (start immediately, in parallel with `T-Sc7Rm2`). Both review gates state explicitly
  that **nothing must resolve before this task starts**.
- Downstream: `T-Wk3Nv6`, `T-Ib5Qy9`, `T-Rm2Lx7` all build directly on this API.

## Pseudocode / Algorithm
```text
HLD §11 M1 carries the full method list, the SAFETY_ARGS/FORBIDDEN_SUBCOMMANDS constants, and the
prune_worktrees_scoped algorithm. The two non-obvious primitives:

update_ref_cas(ref, new, expected_old):
    cp = _run(["update-ref", ref, new, expected_old], check=False)
    IF cp.returncode == 0: RETURN True
    IF "unable to lock" in stderr OR "is at" in stderr: RETURN False    # CAS loss, not an error
    RAISE GitError(...)                                                 # anything else IS an error

worktree_remove(path, force):
    IF path not registered in worktree_list(): RETURN "already_absent"
    IF entry.locked: RETURN "locked"
    cp = _run(["worktree","remove", *(["--force"] if force else []), path], check=False)
    IF cp.returncode == 0: RETURN "removed"
    IF "is dirty" or "contains modified" in stderr and not force: RETURN "in_use"
    RAISE GitError(...)
```

## Schemas / Interface Notes
- Interface / API (**locked** — downstream tickets are written against these): `GitRepo` (with the
  method set above), `Runner` protocol, `GitError` / `GitTimeoutError` / `GitUnavailableError` /
  `GitForbiddenCommandError`, `RepoProbe`, `WorktreeEntry`, `WorktreeRemoveOutcome`, `PruneReport`,
  `StatusEntry`, `RebaseOutcome`, `MergeProbe`, `parse_worktree_list`, and the constants in AC-1.
- Spec / data schema: none.
- Triggers / events: none — this module raises and returns; domain events are `T-Cx4Jf1`'s.
- Artifacts: `EMPTY_HOOKS_DIR` under `$AO_STATE_DIR` (created once, always empty, mode 0700).

## Handoff Boundary
- Upstream: none.
- Downstream: publish the exact final signatures in `STATUS.md` > "Interface confirmation for
  downstream tasks". `T-Wk3Nv6` and `T-Ib5Qy9` must read the **merged** module, not this ticket.
  Explicitly confirm which of `status_porcelain` / `add_paths` / `worktree_remove` outcomes landed as
  specified, since `T-Ib5Qy9`'s S-3 screen and `T-Wk3Nv6`'s `release()` are written against them.

## Artifacts
- Docs/comments: `meta/tickets/E-Wk9Tz3-task-isolation/T-Gt4Pw8-git-porcelain/`
- Large outputs: none

---
- By: architect · Role: architect · Date: 2026-09-07 · Comment: Phase-1 amendment after the design +
  security gates. Folded in S-1 (hook/prompt/signature suppression via `SAFETY_ARGS` at the single
  choke point, with a planted-hook test that is proven non-vacuous), the no-network guarantee (method
  surface + `FORBIDDEN_SUBCOMMANDS` runtime guard), R-6 (blanket `worktree prune` removed from the
  public API; only `prune_worktrees_scoped`, with a foreign-worktree survival test), and R-23
  (`worktree_remove`/`delete_branch` return outcomes instead of raising, so `release()` can run on a
  failure path). Added the injectable `Runner` protocol and locked the exception hierarchy and type
  names so `T-Wk3Nv6`/`T-Ib5Qy9` build without churn. Estimate unchanged at 2 days: the additions are
  constants, one guard, one algorithm and tests — no new subsystem.

- By: developer-agent · Role: developer · Date: 2026-09-07 · Comment: Implemented. All 24 ACs met.
  One authorized interface amendment applied mid-task per the architect's Phase-2 routing message:
  `EMPTY_HOOKS_DIR` is resolved via a new shared `src/agent_orchestrator/xdg.py::resolve_state_dir`
  (generalized from `service/paths.py::default_state_dir`, which is left untouched for `T-Wk3Nv6` to
  refactor onto it later) rather than depending on `isolation/paths.py::state_dir()` (owned by the
  not-yet-landed `T-Wk3Nv6`); `GitRepo.__init__` gained an explicit `hooks_dir: Path | None = None`
  override so tests inject a `tmp_path`-scoped directory directly. No other locked name/signature
  changed. See `STATUS.md` "Interface confirmation for downstream tasks" for the final signatures.
  Gates: `ruff check`/`ruff format --check` clean on all owned files; `mypy src` still exactly 4
  pre-existing errors (`_version.py`); full suite 2211 passed / 7 skipped / 0 failed (baseline conflict
  noted: the ticket's stated baseline of 2008 passed predates this run — see STATUS.md); new-module
  coverage 96% (`isolation/git.py`), TOTAL 94%. Did not touch `models.py`/`spec.py`/`runstate.py`/
  `specs/*.schema.json`/`budget.py` (T-Sc7Rm2's files) or any other `isolation/` module.
