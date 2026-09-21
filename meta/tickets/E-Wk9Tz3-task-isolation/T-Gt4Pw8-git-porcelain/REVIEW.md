# Review: E-Wk9Tz3 task-isolation (T-Gt4Pw8-git-porcelain)

Reviewer: `reviewer` agent · Date: 2026-09-07
Scope: uncommitted working tree, branch `ad/task-isolation` —
`src/agent_orchestrator/isolation/__init__.py`, `src/agent_orchestrator/isolation/git.py`,
`src/agent_orchestrator/xdg.py`, the additive block in `src/agent_orchestrator/errors.py`,
`tests/isolation/__init__.py`, `tests/isolation/conftest.py`, `tests/isolation/test_git.py`,
`tests/test_xdg.py`, plus `TASK.md`/`STATUS.md`. `T-Sc7Rm2`'s concurrent files
(`models.py`, `spec.py`, `runstate.py`, `budget.py`, `specs/*.schema.json` + their tests)
were not read for correctness and were not touched.

Verification method: every finding is labeled **[EXECUTED]** (independently reproduced
against a throwaway repo/`tmp_path`, transcripts kept in this session) or **[READ]**
(established by reading code/tests, not independently executed).

## Gates observed

- `uv run ruff check src/agent_orchestrator/isolation src/agent_orchestrator/xdg.py tests/isolation tests/test_xdg.py` → **All checks passed.** [EXECUTED]
- `uv run ruff format --check` (same set) → **7 files already formatted.** [EXECUTED]
- `uv run mypy src` → **exactly 4 errors, all in `_version.py`** (pre-existing baseline). [EXECUTED]
- `uv run pytest tests/isolation tests/test_xdg.py -q -p no:cacheprovider` → **88 passed.** [EXECUTED]
- `uv run pytest -q -p no:cacheprovider` (full suite) → **2211 passed, 7 skipped, 0 failed.** [EXECUTED]
- Coverage (`--cov=agent_orchestrator.isolation.git --cov=agent_orchestrator.xdg`) →
  `isolation/git.py` **96%** (410 stmts, 16 missed), `xdg.py` **100%**. Matches STATUS.md's
  claim. [EXECUTED]
- Ran `tests/isolation/test_git.py` standalone **5×** and once inside the full suite: **no
  flake reproduced** (see F-1 below for the root-cause hunt anyway). [EXECUTED]

All ticket-stated gates check out. The two findings below are real bugs the gates above did
not catch — both are gaps in what the tests assert, not gaps in what commands were run.

---

## Blocking (must fix before commit)

### C-1. `commit_tree()` is not actually deterministic — the AC-17 test only passes because it's fast, not because the code pins anything. Root cause of the reported flake.

**Location**: `src/agent_orchestrator/isolation/git.py:703-705` (`commit_tree`);
`tests/isolation/test_git.py:586-594` (`test_commit_tree_is_deterministic_with_pinned_dates`).

**Observation**: `commit_tree` builds `["commit-tree", tree_ish, "-p", parent, "-m", message]`
and calls `self._run(...)` with no `extra_env`. `_run`'s only env forcing (`_FORCED_ENV`) is
`LC_ALL`/`GIT_EDITOR`/`GIT_TERMINAL_PROMPT`/`GIT_ASKPASS` — **never**
`GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE`. `git commit-tree` embeds the current wall-clock
second into both the author and committer lines of the commit object, so its sha is a
function of real time unless the date is pinned. The test's own name
("`..._with_pinned_dates`") asserts a determinism guarantee that AC-17 states explicitly
("committing the same tree/parent/message twice yields the same sha (test pins
`GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE`)") — but neither the method nor the test actually
pins anything. The test passes today only because its two back-to-back calls almost always
land inside the same wall-clock second.

**Evidence** [EXECUTED]: reproduced directly against the real module —
```
sha1 = g.commit_tree(tree, parent, "squash message")
time.sleep(1.1)   # simulate the two calls straddling a second under load
sha2 = g.commit_tree(tree, parent, "squash message")
# sha1 = 8d81fe2...   sha2 = 3a30387...   EQUAL: False
```
This is very likely the exact mechanism behind the flake `T-Sc7Rm2`'s developer saw during
a full-suite run: under the load of 2211 tests running concurrently with other work, the
subprocess-spawn gap between the two `commit-tree` invocations can exceed one second more
often than it does standalone (5/5 standalone reruns here passed; one full-suite run here
also passed — consistent with "passed standalone and on repeated full runs" being the
*usual* case, not proof the race doesn't exist). No other candidate reproduced or matched:
line-based worktree/status ordering assumptions aren't present in any test (all assertions
key by path/dict, not position); there is no admin-dir basename-collision test at all yet
(so it cannot be the collision source); the only real-timeout test uses a 10s timeout with
no tight margin; and the autouse `_isolated_git_env` fixture redirects `HOME`/`AO_STATE_DIR`
to a fresh `tmp_path` per test (itself unique per pytest worker), which rules out
cross-test/xdist interference on a shared `EMPTY_HOOKS_DIR`.

**Why it matters**: this is a determinism bug in a *locked-interface* primitive that
`T-Ib5Qy9-integrator-core` builds its squash step and CAS-retry idempotency on directly
(§8.2: "makes the 'one task = one commit' invariant structural"; R-7's `already_landed`
short-circuit assumes recomputing the same squash is safe/stable). An intermittently
non-reproducible commit sha for identical logical inputs is exactly the kind of
non-determinism CLAUDE.md's design principles rule out on the run path, and it will
manifest as exactly this kind of rare, hard-to-bisect CI flake for every future consumer of
this method, not just this test.

**Fix**: give `commit_tree` a way to pin the date — either accept an optional
`author_date: str | None = None` / `committer_date: str | None = None` (or a generic
`extra_env`) parameter and thread `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE` through `_run`'s
existing `extra_env` hook, or default to a value derived deterministically from the inputs
(e.g., the parent's own committer date) so the method needs no caller cooperation to be
pure. Then fix the test to actually pass fixed dates (mirroring `conftest.py`'s
`_commit_env()` pattern), matching what its name already claims.

### C-2. `commit()`'s "nothing staged → return `None`" check tests the wrong condition — it checks "any tracked delta from HEAD", not "anything staged"

**Location**: `src/agent_orchestrator/isolation/git.py:722-724` (`commit`);
`tests/isolation/test_git.py:596-599` (only the fully-clean-tree case is tested).

**Observation**:
```python
def commit(self, cwd: str, message: str, allow_empty: bool = False) -> str | None:
    if not allow_empty and not self.status_porcelain(cwd, untracked=False):
        return None  # nothing staged; never create an empty commit implicitly
    ...
```
`status_porcelain(cwd, untracked=False)` returns **every** tracked-file delta, staged or
not (an entry with `index=" "`/`worktree="M"` — modified but never `git add`-ed — is
included). So "a tracked file was modified but nothing was ever staged" is treated as "there
is something to commit", and the method proceeds to run `git commit -m <message>` with an
empty index — which git itself refuses with a non-zero exit ("no changes added to
commit") — so `_run`'s default `check=True` raises `GitError` instead of the documented
`None`.

**Evidence** [EXECUTED]: `GitRepo(repo).commit(repo, "msg")` after writing to a tracked file
without staging it:
```
EXCEPTION: GitError git command failed (exit=1): git ... commit -m attempt commit with unstaged-only change
```
AC-18/the docstring both promise `None` for "nothing staged"; this raises instead.

**Why it matters**: masked today only because the one documented calling convention
(`git add -A` immediately before `commit()`, per HLD §8.2 and `T-Ib5Qy9` AC-3) always stages
everything first, so the specific "unstaged-only" state can't arise in that exact sequence.
But this is a public, locked, reusable primitive on a "single choke point" module — its
contract should hold regardless of caller discipline, and nothing stops a future caller
(another isolation module, or `add_paths` followed by an unrelated unstaged edit elsewhere
in the tree) from hitting it. The existing test suite never exercises this state, so the gap
escaped both the implementation and the tests.

**Fix**: check the index specifically — e.g. `git diff --cached --quiet` (returncode 0 ⇒
nothing staged) or filter `status_porcelain`'s own entries to `index not in (" ", "?")` —
instead of "any tracked delta from HEAD". Add a test that modifies a tracked file without
staging it and asserts `commit()` returns `None`.

---

## Major (should fix before commit)

### C-3. `merge_tree_probe`'s "git < 2.38 → `None`" branch (AC-22) is only incidentally covered, never deliberately asserted

**Location**: `src/agent_orchestrator/isolation/git.py:569-572`; `tests/isolation/test_git.py:210-289`
(`TestStructuralNoNetworkSurface`) is the only place a fake runner drives `merge_tree_probe`,
and that test only inspects recorded argv, never the return value.

**Observation**: `TestMergeTreeProbe` is `skipif`-gated on the *real, installed* git being
`>= (2, 38)`, so it only ever exercises the "supported" branch. The "unsupported → `None`"
branch happens to execute during the structural sweep (the scripted stdout `"true\nfalse\n.git\n"`
doesn't match `_VERSION_RE`, so `GitRepo.version()` returns `None`, which takes the same
early-return path as "too old") — but that test asserts nothing about the return value, only
that no forbidden verb appears in any recorded argv. [READ]

**Why it matters**: AC-22 explicitly requires this behavior be tested ("returns `None` when
`git < 2.38`"). As written, a change that broke the version-gate comparison (e.g. an
off-by-one on the tuple compare, or accidentally comparing against `GIT_MIN_VERSION`
instead of `GIT_MERGE_TREE_MIN_VERSION`) would not be caught by any test with an intentional
assertion — only by accident, and only in the unrelated structural-sweep test.

**Fix**: add a small unit test using `RecordingFakeRunner` that scripts a realistic
`git --version` response below 2.38 (e.g. `b"git version 2.37.0\n"`) and asserts
`merge_tree_probe(...)` returns `None` without inspecting argv.

### C-4. S-1's "`-c` wins over a pre-existing `core.hooksPath`" scenario is unverified by any persisted test

**Location**: `src/agent_orchestrator/isolation/git.py:453-461` (`_run`'s argv construction);
`tests/isolation/test_git.py:810-895` (`TestHooksAndSafetyS1`).

**Observation**: The existing S-1 gate tests prove hooks never fire against a repo that has
**no** pre-existing hook configuration, and that no config is *written*. Neither test plants
a hostile `core.hooksPath` (repo-local **or** global) pointing at a real hook before driving
`GitRepo` through it — the exact scenario this review's brief calls out ("a repo with
`core.hooksPath` already set globally — `SAFETY_ARGS -c` must win").

**Evidence** [EXECUTED]: manually verified the code is actually correct — planting
`git config core.hooksPath <hostile-dir>` with a real executable `post-commit` hook, then
driving a commit through `GitRepo`, produces no sentinel (`-c` wins, exactly as intended by
git's own config-precedence rules).

**Why it matters**: the code is right today, but this is precisely the guarantee the
security review (`REVIEW-security-design-2026-09-07.md` S-1) made **blocking**, and it is
the single most safety-critical property of this whole module. Nothing currently pins it
against a regression (e.g., someone later reorders `SAFETY_ARGS` after `*args`, or a future
edit only sets `hooksPath` conditionally). A test gap on a blocking security guarantee
deserves the same weight as a code gap.

**Fix**: add a test to `TestHooksAndSafetyS1` that sets `core.hooksPath` (repo-local, via raw
`git config`) to a directory containing a real hook before constructing `GitRepo`, and
asserts the sentinel still never fires.

---

## Warnings (should fix)

### C-5. Repeated `GitError` construction (DRY)

**Location**: `git.py:590` (`merge_tree_probe`), `:620` (`worktree_remove`), `:669`
(`update_ref_cas`), `:690` (`delete_branch`), `:772` (`rebase_onto`), `:783`
(`rebase_continue`) — six call sites all write
`raise GitError(["git", *args], cp.returncode, _tail(_decode(cp.stderr)))` verbatim.

**Why it matters**: this is exactly the "extract to a function if logic appears twice" rule
in CLAUDE.md — the decode+tail+argv-prefix wrapping is non-trivial repeated logic, not a
one-liner, and a future change to how errors are built (e.g. adding more context) needs to
touch six call sites identically.

**Fix**: extract a private helper, e.g. `self._raise(args: list[str], cp:
CompletedProcess[bytes]) -> NoReturn`, and use it at all six sites.

### C-6. CAS-loss / dirty-worktree detection parses git's English stderr text, contradicting the module's own stated principle

**Location**: `git.py:661-669` (`update_ref_cas`: `"unable to lock"`, `"cannot lock ref"`,
`"is at"`), `:605-620` (`worktree_remove`: `"contains modified"`, `"is dirty"`).

**Observation**: TASK.md's own Risks section states "parse porcelain formats only ... never
parse human-readable messages", and the module docstring repeats it — but these two methods
must distinguish an *expected* outcome (CAS loss / dirty tree) from a *real* error using
exactly that kind of text, because `git update-ref`/`git worktree remove` give no
machine-stable signal (e.g., distinct exit code) for "expected" vs. "unexpected" failure.
`LC_ALL=C` fixes translation but not wording drift across git releases. [READ — no cleaner
git-native primitive was found for either check, so this is likely the best available
option, not obviously fixable]

**Why it matters**: if a future git version rewords one of these messages, the CAS-retry
path (`update_ref_cas`) and the tolerant-removal path (`worktree_remove`) would start
raising `GitError` for what should be a routine retry/`in_use` outcome — a regression that
would only surface as a new git version rolls out, not at review time.

**Fix**: keep the current approach (there's no better git-native signal), but say so
explicitly in a code comment (this is an accepted, git-version-pinned exception to the
"never parse human messages" rule, not an oversight), and consider a test that pins the
exact string against the installed git's actual output (already effectively done for the
"unrelated failure" tests, but not framed as a version-drift guard).

### C-7. No regression test for the admin-dir basename-collision correction

**Location**: `git.py:348-377` (`_correct_admin_dirs`); STATUS.md documents the empirically
observed behavior ("two worktrees both named `repoA` under different task dirs become
`.git/worktrees/repoA` and `repoA1`"), but `test_git.py` has no test constructing this exact
scenario.

**Evidence** [EXECUTED]: manually reproduced two worktrees named `repoA` under different
parent directories; `worktree_list()` correctly resolves `admin_dir` to `.../worktrees/repoA`
and `.../worktrees/repoA1` respectively — the code is correct.

**Why it matters**: `_correct_admin_dirs`'s whole reason to exist is this exact case, and it
feeds directly into `prune_worktrees_scoped`'s destructive `shutil.rmtree(e.admin_dir, ...)`
— an admin-dir mis-resolution here would delete the wrong repo's worktree registration. A
documented-but-untested empirical git behavior is exactly the kind of thing a git version
bump silently breaks.

**Fix**: add the scenario as a real test (two `worktree_add` calls with colliding
basenames under different parents; assert `worktree_list()` resolves both `admin_dir`s
correctly).

---

## Suggestions (nice to have)

### C-8. `FORBIDDEN_SUBCOMMANDS` guards only `args[0]`

`_run`'s guard (`git.py:447-451`) checks only the first element of `args`. Every current
public method hardcodes a literal `args[0]`, so there is no live path today where a caller's
data reaches that position — but nothing stops a hypothetical future internal caller (or a
downstream task calling the technically-accessible `_run` directly, which Python doesn't
prevent) from smuggling a forbidden verb past the guard via `["-c", "alias.x=push origin",
"x"]`. Purely defense-in-depth: consider a comment on `_run` noting the guard's soundness
today rests on "every caller hardcodes `args[0]`", so a reviewer of a *future* change knows
what invariant to preserve.

### C-9. `parse_worktree_list`/`worktree_list()` parse porcelain output line-based (no `-z`)

Unlike `status_porcelain` (which deliberately uses `-z` to be filename-content-agnostic),
`git worktree list --porcelain` is parsed via `text.splitlines()`. The installed git
(2.39.5) supports `--porcelain -z` for this subcommand, but `GIT_MIN_VERSION = (2, 30)` may
predate that support, so not using it here could be a deliberate compatibility choice rather
than an oversight — worth a one-line comment saying so (mirroring the reasoning already
given for `status_porcelain`), since a worktree path containing a literal newline (rare, but
POSIX-legal) would currently misparse.

### C-10. No test for a worktree path containing spaces/unicode

The design is safe by construction (argv lists, never a shell — `noqa: S603` is correctly
justified), so this is very unlikely to be a real bug, but the review brief calls it out
explicitly and downstream tasks (`T-Wk3Nv6`) will construct real worktree paths from
task/run ids that could contain such characters. A single test would remove any doubt.

### C-11. `GIT_MIN_VERSION` is defined but never consulted in this module

By design (AC-10: `GitRepo.version()` is a never-raising probe; enforcement is the caller's
job) — this is correct, not a bug — but a one-line comment cross-referencing where
enforcement will land (`WorktreeManager`/`T-Wk3Nv6`) would help a future reader not mistake
it for dead code.

---

## Flaky-test hunt (requested by the coordinator)

**Test**: `tests/isolation/test_git.py::TestRefsAndCommits::test_commit_tree_is_deterministic_with_pinned_dates`
**Root cause**: identified with high confidence — see **C-1** above. `commit_tree()` does
not pin `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE` (despite the test's name claiming it does),
so the resulting sha depends on real wall-clock time at one-second granularity. The test's
two back-to-back calls only produce equal shas because they normally complete inside the
same wall-clock second; under the load of a full 2211-test suite run, that margin can be
missed. Reproduced directly: inserting a 1.1s delay between the two `commit_tree()` calls
in an otherwise identical repro changes the resulting sha (`8d81fe2...` vs `3a30387...`).
**Reproducibility here**: not reproduced in 5 standalone reruns of `test_git.py`, nor in one
full-suite run (2211 passed) — consistent with this being real but rare (needs the two
subprocess calls to straddle a second boundary, which standalone/idle-machine runs rarely
hit). Other candidates on the coordinator's list were checked and ruled out: no ordering
assumptions on `worktree list`/`status --porcelain` output (all assertions key by
path/dict); no basename-collision test exists yet to collide (see C-7); the autouse
`_isolated_git_env` fixture redirects `HOME`/`AO_STATE_DIR` to a fresh `tmp_path` per test,
ruling out shared-`EMPTY_HOOKS_DIR` cross-test interference; the only real-timeout test uses
a generous 10s bound. **Fix**: same as C-1 — pin `GIT_AUTHOR_DATE`/`GIT_COMMITTER_DATE`
through `commit_tree`, then fix the test to actually pass them.

---

## Goal alignment

- **Project goals** (CLAUDE.md): pluggable (`Runner` protocol, injectable `hooks_dir`) ✅;
  safe-by-default (SAFETY_ARGS, FORBIDDEN_SUBCOMMANDS, 0700 dirs) ✅; observable (structured
  `logger.info` on `prune_worktrees_scoped`) ✅; **determinism/idempotency** ❌ — C-1 is a
  direct violation on the run path, exactly the class of bug the "deterministic, idempotent,
  resumable" pillar exists to prevent.
- **Epic/task goals**: all 24 stated ACs have working code behind them except AC-17
  (determinism, C-1) and AC-18 (nothing-staged contract, C-2) as detailed above; AC-22's
  test coverage is incomplete (C-3) though the code itself is correct as far as verified.
  The interface amendment (`hooks_dir` param, `xdg.py` extraction) is exactly what the
  architect's Phase-2 routing message authorized, cleanly scoped, and does not touch any
  other task's files. R-6/R-23/S-1 gates are all genuinely met (verified, not just asserted).
- **Code-level intent**: docstrings/comments accurately describe the implementation except
  where C-1/C-2 diverge from their own documented contracts.

## SOLID/KISS/DRY/pluggability/spec-DAG/testability — dimensions with nothing further to flag

- **SOLID/KISS**: `GitRepo` is a large class (~30 methods) but matches the ticket's explicit
  "single choke point" design; the pure/impure split (`parse_worktree_list` vs.
  `worktree_list()`) is exactly right. No over-abstraction, no speculative generality —
  every method maps to a named downstream caller. Checked, nothing further to flag.
- **Magic literals**: constants are named per AC-1; the only literal-adjacent concern is the
  stderr substring matching, covered as C-6. Checked.
- **Pluggable architecture**: `Runner` Protocol + default `_SubprocessRunner`, `hooks_dir`
  injection — clean DI seams, both exercised by tests. Checked.
- **Spec & DAG correctness**: N/A to this module (no spec/DAG surface here — confirmed by
  reading `Do NOT touch` list; this ticket owns none of those files).
- **Concurrency/rollout**: `version()`/`probe()` are stateless staticmethods, safe for
  concurrent worker threads; `resolve_empty_hooks_dir`'s mkdir/chmod/emptiness-check is
  idempotent under concurrent construction. Checked.

## Testing notes

- **Mock**: `RecordingFakeRunner` (already provided) is the right seam for every unit test
  above (C-1's fix, C-3, C-8) — no real git needed.
- **Integration-test** (real git, already the pattern here): C-2's fix, C-4, C-7.
- **Coverage gaps**: the 16 missed lines in `git.py` are defensive `OSError` races in
  `version()`/`probe()` — acceptable to leave uncovered as documented.

---

## Verdict: APPROVE WITH CHANGES

Must fix before commit: **C-1, C-2** (Blocking). Should fix before commit: **C-3, C-4**
(Major) — both are test-coverage gaps on explicit ACs / a blocking security guarantee, cheap
to close now. C-5/C-6/C-7 (Warnings) and C-8/C-9/C-10/C-11 (Suggestions) can follow in a
fast-follow if time-boxed, but C-5 (DRY) and C-7 (basename-collision regression test, since
it feeds a destructive `rmtree`) are worth doing in the same pass. All stated gates
(ruff/format/mypy/pytest/coverage) are independently confirmed green; the gaps found here
are real bugs the gates did not — and structurally could not — catch.
