# STATUS

- ID: `T-Gt4Pw8-git-porcelain`
- Updated At: 2026-09-07
- State: Done
- Owner: developer-agent

## This update
- Ticket created by the architect as part of the `E-Wk9Tz3-task-isolation` design package. Not
  started; no code written.
- **2026-09-07 — Phase-1 review amendment applied.** Folded in the two pre-implementation gates:
  **S-1** (every engine-issued git call carries `SAFETY_ARGS` — `core.hooksPath` at an always-empty
  dir, `commit.gpgsign=false`, `core.editor=true`, `gc.auto=0` — plus a hook-free/prompt-free child
  env, with a planted-hook test proven non-vacuous), the **no-network** guarantee
  (`FORBIDDEN_SUBCOMMANDS` runtime guard + a structural test over the whole public method surface),
  **R-6** (blanket `git worktree prune` removed from the public API; only `prune_worktrees_scoped`,
  with a foreign-worktree survival test), and **R-23** (`worktree_remove`/`delete_branch` return
  outcomes rather than raising, so the lifecycle's `release()` can run on a failure path). Added an
  injectable `Runner` protocol and locked the exception hierarchy and type names. AC count 13 -> 24;
  estimate unchanged at 2 days.
- Both gates state explicitly that **nothing must resolve before this task starts**.
- **2026-09-07 — implemented, all 24 ACs met.** `src/agent_orchestrator/isolation/git.py` (new),
  `src/agent_orchestrator/isolation/__init__.py` (new), `src/agent_orchestrator/errors.py` (edited:
  added `GitError`/`GitTimeoutError`/`GitUnavailableError`/`GitForbiddenCommandError` to the existing
  `OrchestratorError` hierarchy), `src/agent_orchestrator/xdg.py` (new — see "Interface routing"
  below), `tests/isolation/__init__.py`, `tests/isolation/conftest.py`, `tests/isolation/test_git.py`
  (all new), `tests/test_xdg.py` (new).
- **Interface routing applied mid-task (architect Phase-2 message).** The HLD's `EMPTY_HOOKS_DIR =
  state_dir()/"empty-hooks"` names `isolation/paths.py::state_dir()`, owned by the not-yet-landed
  `T-Wk3Nv6` — M1 must not depend on M3. Resolved per explicit routing: (1) a new, shared
  `src/agent_orchestrator/xdg.py::resolve_state_dir(override_env, xdg_subdir, default_subdir)`,
  generalized from `service/paths.py::default_state_dir()` (left untouched — `T-Wk3Nv6` refactors it
  onto this helper); (2) `git.py::resolve_empty_hooks_dir()` joins `resolve_state_dir("AO_STATE_DIR",
  "ao", "ao") / "empty-hooks"`, creates it (mode 0700), asserts it is empty; (3) `GitRepo.__init__`
  gained an explicit `hooks_dir: Path | None = None` override (not in the ticket's original "exactly"
  wording, added under the routing message's explicit instruction) so tests inject a `tmp_path`-scoped
  directory directly instead of relying only on env monkeypatching. No other locked
  name/signature/exception changed. `tests/test_xdg.py` covers `resolve_state_dir`'s 3-tier precedence
  (override env / `XDG_STATE_HOME` / home fallback) including the case where `xdg_subdir` and
  `default_subdir` differ.
- Prior art followed: `bench/swebench_provider.py::_run_git` (subprocess wrapper raising a domain
  error, named timeout constants) and `tests/bench/test_swebench_provider.py::_git` (real `git init`
  fixtures, pinned `-c user.email`/`-c user.name`).
- Design decisions discovered empirically (real git 2.39.5, documented inline in `git.py`/`conftest.py`
  docstrings, not blocking): (a) `git worktree list --porcelain` never exposes a linked worktree's
  admin-dir id, and git disambiguates it with a numeric suffix on a basename collision (verified: two
  worktrees both named `repoA` under different task dirs become `.git/worktrees/repoA` and `repoA1`) —
  `parse_worktree_list` (pure) computes a best-effort `<common_dir>/worktrees/<basename>` guess;
  `GitRepo.worktree_list()` (impure) corrects it against the real `.../worktrees/*/gitdir` records
  before anything destructive (`prune_worktrees_scoped`) uses it. (b) During a `rebase`, index stage 2
  is the tree being rebased **onto** and stage 3 is the replayed commit — the inverse of a plain
  merge's ours/theirs — documented on the `delete_modify` `show_stage` test.

## Evidence
- Design: [`docs-md/task-isolation-hld.md`](../../../../docs-md/task-isolation-hld.md) §11 M1, §8.2,
  §14 and
  [`ADR-0013`](../../../../docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md).
- Commands run (from repo root) and results:
  - `uv run ruff check src/agent_orchestrator/isolation/git.py src/agent_orchestrator/isolation/__init__.py src/agent_orchestrator/xdg.py src/agent_orchestrator/errors.py tests/isolation/ tests/test_xdg.py`
    → All checks passed.
  - `uv run ruff format --check` (same file set) → all formatted.
  - `uv run mypy src` → 4 errors, all in `_version.py` (pre-existing baseline, unchanged).
  - `uv run pytest -q tests/isolation/ tests/test_xdg.py -p no:cacheprovider` → **108 passed**, run
    **5x in a loop** per the review re-check request, all 5 green.
  - `uv run pytest -q -p no:cacheprovider` (full suite) → **2250 passed, 7 skipped, 0 failed** (clean
    rerun). Note: the ticket's stated baseline ("2008 passed / 7 skipped") predates this run and this
    branch already carries `T-Sc7Rm2`'s concurrent work; skip count (7) is unchanged from the stated
    baseline. One full-suite-**with-coverage** run hit an unrelated pre-existing flake in
    `test_wave_scheduler.py` (see note below) that did not reproduce on rerun.
  - `uv run pytest -q --cov=agent_orchestrator --cov-report=term -p no:cacheprovider` →
    `isolation/git.py` **97%** (452 stmts, 15 missed — remaining misses are defensive edges: rare
    `OSError` races in `version()`/`probe()`, a couple of unreachable-in-practice early-return guards),
    `xdg.py` **100%**, **TOTAL 94%** (matches the stated 94% baseline, no regression), 2250 passed / 7
    skipped / 0 failed.

- **2026-09-07 — review findings addressed.** All MUST-FIX and ALSO-FIX items from `REVIEW.md`
  resolved except C-9 (deferred, reason below). Per-finding disposition:
  - **C-1 (Blocking, fixed).** `commit_tree()` is now deterministic BY CONSTRUCTION: gained
    `author_name`/`author_email`/`author_date`/`committer_date` kwargs (all optional); when unset,
    identity defaults to fixed module constants and both dates default to *parent*'s own recorded
    committer date (`git log -1 --format=%cI <parent>`, itself deterministic once `parent` is fixed) —
    no caller cooperation required, no wall-clock dependency. New test sleeps 1.1s (the review's own
    repro) between two `commit_tree()` calls and asserts equal shas; another test asserts the explicit
    override kwargs actually land in the resulting commit.
  - **C-2 (Blocking, fixed).** `commit()`'s staged check now uses a new `_index_matches_head(cwd)`
    helper (`git diff --cached --quiet`, index-vs-HEAD) instead of `status_porcelain(untracked=False)`
    (which returned tracked-but-unstaged deltas too). New regression test modifies a tracked file
    without staging it and asserts `commit()` returns `None` rather than raising.
  - **C-3 (Major, fixed).** New `TestMergeTreeProbeVersionGate` unit test scripts a fake `git --version`
    below 2.38 and asserts `merge_tree_probe` returns `None` without ever calling `merge-tree`.
  - **C-4 (Major, fixed).** Two new integration tests plant a hostile `post-checkout` hook and point
    `core.hooksPath` at it — once via repo-local `git config`, once via `GIT_CONFIG_GLOBAL` pointed at a
    tmp file (the review's exact scenario) — and assert `SAFETY_ARGS`' `-c` still wins in both cases.
  - **C-5 (fixed).** Extracted `GitRepo._raise(args, cp)`; all 7 (not 6 — one extra site used an
    already-decoded local var) duplicated `raise GitError(...)` sites now call it.
  - **C-6 (fixed, accepted as a documented limitation, not a bug).** Added explicit code comments at
    both `update_ref_cas` and `worktree_remove`'s stderr-matching sites naming this as this module's one
    deliberate exception to "never parse human-readable messages", and cross-referencing the tests that
    already pin the installed git's real message text (`test_update_ref_cas_fails_when_ref_moved`,
    `test_remove_in_use_when_dirty_without_force` — pre-existing, now cross-referenced from the code).
  - **C-7 (fixed).** New regression test creates two worktrees sharing a basename (`repoA`) under
    different parent dirs and asserts `worktree_list()` resolves each `admin_dir` to the correct,
    distinct, git-disambiguated directory (checked against each admin dir's own `gitdir` record) —
    guards the destructive `rmtree` in `prune_worktrees_scoped`.
  - **C-8 (fixed).** `_run`'s forbidden-verb guard is now structural: new pure module-level helpers
    `_find_subcommand`/`_forbidden_alias_key` skip `-c key=value`/`-C <path>`/`--git-dir[=| ]<path>`
    global options to find the real subcommand token, and reject any `-c alias.*=...` key outright
    (value not parsed — can be arbitrary/shell-like). New tests: `git -c alias.p=push p` (rejected),
    `worktree add --track ...` (NOT rejected — false-positive guard), a forbidden verb after global
    options (`-c foo.bar=baz push origin`, rejected even though `args[0]` is `"-c"`), plus direct unit
    tests for both new pure helpers.
  - **C-9 (deferred).** Switching `worktree list --porcelain` parsing to `-z` would mean rewriting
    `parse_worktree_list`'s block-splitting algorithm AND every captured-text fixture in
    `TestParseWorktreeList` (currently newline-joined) — a bigger, riskier change than the review's own
    "≤20 lines" budget once tests are included, this late in review. Worktree paths in this system are
    always engine-constructed (`isolation/paths.py::sanitize_ref_component`-derived, `T-Wk3Nv6`) and
    never contain a literal newline, so the practical risk is low. Left as a fast-follow note for
    whichever task next touches `parse_worktree_list`.
  - **C-10 (fixed).** New test creates and removes a worktree at a path containing a space and a
    non-ASCII character (`"task ünïcödé 1"`), confirming argv-list (never-shell) construction handles it
    with no special-casing needed.
  - **C-11 (fixed).** Added a comment on `GIT_MIN_VERSION` cross-referencing where enforcement lands
    (`WorktreeManager._activate_integration`, `isolation/worktrees.py`, `T-Wk3Nv6`).
- **Flaky-test note (unrelated to this module):** one full-suite-with-coverage run hit an unrelated,
  pre-existing flake — `tests/test_wave_scheduler.py::TestParallelDispatchProof::
  test_two_independent_tasks_overlap_at_max_parallel_two` — which passes standalone and did not
  reproduce on two subsequent full reruns; it is a timing-based overlap proof sensitive to system load
  under coverage instrumentation, not touched by this task and outside its scope.
  — By: developer-agent · Role: developer · Date: 2026-09-07

## Interface confirmation for downstream tasks (`T-Wk3Nv6`, `T-Ib5Qy9`)
Read the merged `src/agent_orchestrator/isolation/git.py`, not this ticket, for exact bodies. Final
public signatures (all landed as specified, with the one amendment below):

- `GitRepo(path: str, *, timeout: int = GIT_DEFAULT_TIMEOUT_SECONDS, rerere: bool = True, runner:
  Runner | None = None, env: dict[str, str] | None = None, hooks_dir: Path | None = None)` — **one
  param added** vs. the ticket's original list: `hooks_dir` (keyword-only, defaults to `None` ->
  env-based resolution). Every other constructor param, and every method listed in HLD §11 M1, landed
  unchanged.
- `Runner` protocol, `GitError`/`GitTimeoutError`/`GitUnavailableError`/`GitForbiddenCommandError` (in
  `errors.py`), `RepoProbe`, `WorktreeEntry`, `WorktreeRemoveOutcome` (`Literal["removed",
  "already_absent", "locked", "in_use"]`), `PruneReport`, `StatusEntry`, `RebaseOutcome`, `MergeProbe`,
  `parse_worktree_list(text, common_dir)`, `status_porcelain`, `add_paths`, `prune_worktrees_scoped`,
  `SAFETY_ARGS`, `FORBIDDEN_SUBCOMMANDS` — all landed exactly as named.
- `status_porcelain(cwd, *, untracked=True) -> list[StatusEntry{path, index, worktree}]` — landed as
  specified; a rename/copy's original path is parsed (via `-z` output) and discarded, only the new
  path is kept, per the locked 3-field shape.
- `add_paths(cwd, paths) -> None` — landed as specified; stages exactly the given paths (`add --
  <paths>`), a no-op (no subprocess call at all) when `paths` is empty.
- `worktree_remove(path, *, force=False) -> WorktreeRemoveOutcome` — landed as specified; never raises
  for `already_absent`/`locked`/`in_use`; raises `GitError` only for a genuinely unexpected git
  failure (tested via the fake runner, since real git rarely produces that condition).
- `delete_branch(name, *, force=False) -> bool` — `False` when absent (before or after a raced-away
  delete attempt), never raises for that case; raises `GitError` for an unexpected failure (same
  philosophy as `worktree_remove`, tested via the fake runner).
- `commit_tree(tree_ish, parent, message, *, author_name=None, author_email=None, author_date=None,
  committer_date=None) -> str` — **four optional kwargs added** post-review (C-1 fix) for deterministic-
  by-construction identity/date pinning; the original 3 positional params are unchanged.
- `prune_worktrees_scoped(path_prefix) -> PruneReport` — landed exactly per HLD §11 M1's algorithm;
  `mode="scoped"` only removes `ours`' own admin dirs and lists foreign prunable entries in
  `skipped_foreign`; `mode="global"` runs the real `git worktree prune`.
- Shared test fixtures (`tests/isolation/conftest.py`, locked per AC-23): `make_repo(tmp_path, files:
  dict[str, str] | None = None) -> Path`, `make_conflict_repo(tmp_path, kind: str) -> tuple[Path, str,
  str, str]` (kinds: `clean`, `union`, `true_conflict`, `add_add`, `delete_modify`, `binary` — all six
  verified against real git 2.39.5), `make_hooked_repo(tmp_path) -> Path`. Also present, reusable but
  not locked: `RecordingFakeRunner`, `ok(...)` helper.
- New shared helper (not in the original ticket, added per the routing message):
  `src/agent_orchestrator/xdg.py::resolve_state_dir(override_env: str, xdg_subdir: str,
  default_subdir: str) -> Path`.

## Risks / Blockers
- Not blocked. See `TASK.md` > Risks for the pre-identified git-version/locale risks — all mitigated
  as designed (`GIT_MIN_VERSION`/`GIT_MERGE_TREE_MIN_VERSION` guards, `-z` porcelain parsing, `LC_ALL=C`
  everywhere).
- `service/paths.py` is untouched, as instructed — `T-Wk3Nv6` still owns refactoring it onto
  `xdg.resolve_state_dir` and owns `isolation/paths.py::state_dir()` itself.

## Next actions
1. `T-Wk3Nv6`/`T-Ib5Qy9` read the merged `isolation/git.py` (not this ticket) and build against the
   "Interface confirmation" section above.
2. `T-Wk3Nv6` refactors `service/paths.py::default_state_dir()` onto `xdg.resolve_state_dir` when
   convenient (not blocking; both currently implement the same precedence independently).
3. Awaiting review before commit (per instruction, no `git commit` performed by this task).

---
- By: reviewer-agent · Role: reviewer · Date: 2026-09-07 · Comment: Full review filed at
  `REVIEW.md`. Verdict **APPROVE WITH CHANGES**. All stated gates independently re-run and
  confirmed green (ruff/format/mypy 4 pre-existing/pytest 88 passed/full suite 2211 passed 7
  skipped/coverage 96%+100%). Two Blocking findings, both verified by direct reproduction, not
  just reading: (C-1) `commit_tree()` never pins `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE`, so it
  is not actually deterministic despite AC-17 and the test's own name — this is the identified
  root cause of the flaky `test_commit_tree_is_deterministic_with_pinned_dates` failure
  `T-Sc7Rm2`'s developer saw on a full-suite run (confirmed: inserting a 1.1s gap between the
  two calls changes the resulting sha); (C-2) `commit()`'s "nothing staged -> None" check
  (AC-18) actually tests "any tracked delta from HEAD", so a tracked-but-unstaged edit with
  nothing in the index raises `GitError` instead of returning `None` (masked today only
  because the one documented calling convention always runs `add -A` first). Two Major
  test-coverage gaps on explicit ACs/blocking security guarantees (C-3: AC-22's "git < 2.38 ->
  None" branch is only incidentally covered, never deliberately asserted; C-4: S-1's "`-c`
  wins over a pre-existing `core.hooksPath`" is unverified by any persisted test, though
  manually confirmed correct). Full findings, evidence, and fixes in `REVIEW.md`; no source or
  test files were edited by this review.

- By: coordinator · Role: manager · Date: 2026-09-07 · Comment: State -> **Done**. Review findings
  in this ticket's `REVIEW.md` were dispositioned by the implementing agent, the gates were re-run
  independently by the coordinator (ruff check/format clean, `mypy src` at exactly the 4 pre-existing
  `_version.py` errors, full suite green with no regression against the pre-epic baseline of 2008
  passed / 7 skipped / 94% coverage), and the work is committed on `ad/task-isolation` under this
  ticket's own commit. Anything still open was re-filed against a named later ticket rather than left
  in this one; see the epic `STATUS.md` rollup for that ticket's entry.
