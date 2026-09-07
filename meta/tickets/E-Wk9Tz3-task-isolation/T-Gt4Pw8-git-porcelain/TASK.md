# TASK: T-Gt4Pw8-git-porcelain

## Metadata
- Task ID: `T-Gt4Pw8-git-porcelain`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: unassigned (developer)
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 2 days

## Requirements Mapping
- Requirement IDs: FR-2, FR-6, NFR-4 (see `../EPIC.md`) · Design: HLD §11 M1

## Description
Build `src/agent_orchestrator/isolation/git.py` — the **single** place in the codebase that shells out
to git for isolation work. Everything else in this epic calls it; nothing else calls `subprocess` with
`git`. Typed results, bounded timeouts, and a `GitError` that never leaks a raw
`CalledProcessError`/`TimeoutExpired`.

Files you own:
- `src/agent_orchestrator/isolation/__init__.py` (new package)
- `src/agent_orchestrator/isolation/git.py` (new)
- `src/agent_orchestrator/errors.py` (edit — add `GitError` only, if the module is the project's error
  home; otherwise define it in `git.py` and say so in your handoff)
- `tests/isolation/__init__.py`, `tests/isolation/test_git.py` (new)
- `tests/isolation/conftest.py` (new — the shared real-git fixture builder other tasks will import)

Do NOT touch: `engine.py`, `models.py`, `spec.py`, `artifacts.py`, `runstate.py`, `cli.py`,
`specs/*.schema.json`, `templates/`, or any other `isolation/` module (owned by later tasks).

Prior art to copy rather than reinvent: `src/agent_orchestrator/bench/swebench_provider.py::_run_git`
(subprocess wrapper raising a domain error, with named timeout constants) and
`tests/bench/test_swebench_provider.py::_git` (real `git init` fixtures with pinned
`-c user.email` / `-c user.name`).

## Acceptance Criteria
1. `GitRepo(path, timeout=GIT_DEFAULT_TIMEOUT_SECONDS, rerere=True)` exists with the exact method set
   in HLD §11 M1. `GIT_DEFAULT_TIMEOUT_SECONDS = 300`, `GIT_MIN_VERSION = (2, 30)`,
   `GIT_MERGE_TREE_MIN_VERSION = (2, 38)` are module constants, not literals at call sites.
2. `_run` builds `["git", "--no-pager", *rerere_args, *args]` where `rerere_args` is
   `["-c", "rerere.enabled=true", "-c", "rerere.autoupdate=true"]` when `rerere=True`. **A test asserts
   the epic never writes `rerere.enabled` into any repo's `git config`** (run `git config --local
   --get rerere.enabled` after a full sequence and assert it is unset).
3. `GitError` carries `argv`, `exit_code` (`None` on timeout) and a `stderr_tail` truncated to 4 KiB.
   A timeout raises `GitError`, never `subprocess.TimeoutExpired`. `check=False` returns the
   `CompletedProcess` without raising.
4. Probes never raise: `GitRepo.version()` returns `None` when git is absent (test by monkeypatching
   `PATH`); `GitRepo.probe(path)` returns `None` for a non-repo directory and, for a repo, returns
   `toplevel`, `common_dir` and `bare`. For a **worktree**, `probe` returns that worktree's toplevel
   and the *shared* `common_dir` (assert the common dir equals the main repo's).
5. `worktree_add` / `worktree_remove` / `worktree_prune` / `worktree_list` work against a real temp
   repo. `worktree_list` parsing is a **separate pure function** (`parse_worktree_list(text)`) with its
   own unit tests over captured `--porcelain` output, including a detached-HEAD entry, a `prunable`
   entry and a `locked` entry.
6. `update_ref_cas(ref, new, expected_old)` returns `True` on success and **`False`** (never raises)
   when `expected_old` does not match. A test proves the CAS loss: read the ref, move it by another
   call, then attempt the CAS with the stale value.
7. `commit_tree(tree_ish, parent, message)` returns a sha; committing the same tree/parent/message
   twice yields the **same** sha (deterministic given pinned author/committer dates — the test pins
   `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE`).
8. `commit(cwd, message)` returns `None` when nothing is staged and does **not** create an empty
   commit. `add_all` respects `.gitignore` (test: an ignored file is not staged).
9. Rebase surface: `rebase_onto` returns `RebaseOutcome(clean=True)` for a disjoint change and
   `RebaseOutcome(clean=False, paths=[...])` for a real conflict; `rebase_in_progress` is `True` in
   that state and `False` after `rebase_abort`; `rebase_continue` runs with `GIT_EDITOR=true` so it
   never blocks on an editor (test asserts it completes with no TTY).
10. `conflicted_paths` returns exactly the `--diff-filter=U` set; `show_stage(cwd, n, path)` returns
    bytes for stages 1/2/3 that exist and `None` for one that does not (delete/modify conflict).
11. `merge_tree_probe(a, b)` returns `None` when `git < 2.38` and otherwise a `MergeProbe(clean, paths)`
    matching the real rebase outcome for both a clean and a conflicting fixture.
12. `tests/isolation/conftest.py` exposes a reusable, deterministic fixture builder
    `make_repo(tmp_path, files) -> Path` and `make_conflict_repo(tmp_path, kind)` returning
    `(repo, base_sha, ours_branch, theirs_branch)` for kinds `clean`, `union`, `true_conflict`,
    `add_add`, `delete_modify`, `binary`. All git invocations pin `-c user.name`, `-c user.email`,
    `core.autocrlf=false`, and fixed `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE`. **Later tasks import this
    builder — its names are a locked interface.**
13. `uv run pytest -q tests/isolation/` green; `ruff check` / `ruff format --check` clean on owned
    files; `uv run mypy src` introduces zero new errors versus the recorded baseline. No network, no
    real `~`, no writes outside `tmp_path`.

## Risks
- Git version drift across dev machines and CI. Mitigation: every version-dependent method is behind
  an explicit version check; `merge_tree_probe` degrades to `None`; tests that need `>= 2.38`
  `pytest.skip` with a clear reason rather than failing.
- Locale/`i18n` in git output. Mitigation: parse porcelain formats only, and set `LC_ALL=C` in `_run`'s
  environment; never parse human-readable messages.
- Over-building. Do not add a method this epic's HLD does not list — every method needs a caller.

## Dependencies
- Upstream: none (start immediately, in parallel with `T-Sc7Rm2`).
- Downstream: `T-Wk3Nv6`, `T-Ib5Qy9`, `T-Rm2Lx7` all build directly on this API.

## Pseudocode / Algorithm
```text
See docs-md/task-isolation-hld.md §11 "M1 — isolation/git.py" for the full method list and
argv shapes. The two non-obvious ones:

update_ref_cas(ref, new, expected_old):
    cp = _run(["update-ref", ref, new, expected_old], check=False)
    IF cp.returncode == 0: RETURN True
    IF "unable to lock" in stderr OR "is at" in stderr: RETURN False    # CAS loss, not an error
    RAISE GitError(...)                                                  # anything else IS an error

rebase_onto(cwd, onto, upstream, branch):
    cp = _run(["rebase", "--onto", onto, upstream, branch], cwd=cwd, check=False)
    IF cp.returncode == 0: RETURN RebaseOutcome(clean=True, paths=[])
    IF rebase_in_progress(cwd): RETURN RebaseOutcome(clean=False, paths=conflicted_paths(cwd))
    RAISE GitError(...)                                                  # failed WITHOUT a conflict
```

## Schemas / Interface Notes
- Interface / API: `GitRepo`, `GitError`, `RepoProbe`, `WorktreeEntry`, `RebaseOutcome`, `MergeProbe`,
  `parse_worktree_list`. These names are **locked** — downstream tickets are written against them.
- Spec / data schema: none.
- Triggers / events: none (logging is `T-Cx4Jf1`'s; this module raises and returns, it does not log
  domain events).
- Artifacts: none.

## Handoff Boundary
- Upstream: none.
- Downstream: publish the exact final signatures in `STATUS.md` > "Interface confirmation for
  downstream tasks". `T-Wk3Nv6` and `T-Ib5Qy9` must read the **merged** module, not this ticket.

## Artifacts
- Docs/comments: `meta/tickets/E-Wk9Tz3-task-isolation/T-Gt4Pw8-git-porcelain/`
- Large outputs: none
