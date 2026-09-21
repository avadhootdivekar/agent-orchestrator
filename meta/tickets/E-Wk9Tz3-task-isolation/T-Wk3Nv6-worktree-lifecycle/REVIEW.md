# REVIEW: T-Wk3Nv6-worktree-lifecycle

- Reviewer: reviewer-agent
- Date: 2026-09-07
- Scope reviewed: `src/agent_orchestrator/isolation/paths.py`, `isolation/worktrees.py`,
  `isolation/view.py` (new); `service/paths.py`, `artifacts.py` (edited); `tests/isolation/test_paths.py`,
  `test_worktrees.py`, `test_view.py`, `test_service_paths_migration.py`; ticket docs. Uncommitted work
  from `T-Ov9Bt5`, `T-Tp7Zs2`, `T-Ib5Qy9` in the same checkout was **not** read for review purposes
  beyond confirming `isolation/git.py`'s modification belongs to `T-Ov9Bt5` (per its own docstring
  reference and the developer's gate note), not this ticket.
- Verified independently (not trusted from the report): `uv run ruff check`/`format --check` on scope
  files (clean), `uv run mypy src` (4 pre-existing `_version.py` errors, no new), targeted pytest
  (`tests/isolation tests/service tests/test_artifacts.py tests/test_xdg.py`) — **632 passed, 0
  failed, 16.22s**; `test_paths.py` alone — 250 passed in 0.43s (no perf concern despite the 220-string
  corpus); coverage on the four owned/edited modules — `paths.py` 100%, `view.py` 100%, `worktrees.py`
  97% (misses: 84-85, 210, 438, 461-462), `service/paths.py` 100% (in the full targeted run).

## Verdict: **APPROVE WITH CHANGES**

Must-fix before merge: **C-1, C-2**. C-3 is a coordination action (ticket-doc fix), not a code change to
this ticket, but should happen before `T-En8Hd4`/`T-Ee3Mn8` start. C-4/C-5/C-6/C-8 are should-fix but
non-blocking; C-7 is accepted as-is.

## Summary

The implementation is careful, well-tested (298 new tests, real git fixtures, an AST-based
no-blanket-prune guard, a 220-entry property corpus pinned against real `git check-ref-format`), and
faithfully follows HLD §7.1/§7.2/§7.3/§11 M3 for the parts it implements. The S-4 per-task
`IsolatedArtifactView` design is sound: I traced `resolve_unchecked` → `IsolatedArtifactView.resolve` →
`effective_path` by hand against every case in HLD §7.3 rule 5 (sibling-task path, cross-run path,
traversal, symlink-escape, reserved prefixes, nested `RepoRef`) and all hold, matching the dedicated
tests. Two real defects survived testing (C-1: a task-id truncation collision that can silently merge
two tasks' worktrees, undermining S-4 for realistic long/agent-supplied ids; C-2: `gc_run()` deletes
branch refs regardless of whether the worktree removal that should precede it actually succeeded, an
untested code path per coverage). The most consequential finding is cross-ticket, not in this diff: two
downstream tickets (`T-En8Hd4`, `T-Ee3Mn8`) still describe the pre-S-4-amendment `extra_roots`-on-
`LocalFsArtifactStore` design this ticket explicitly and correctly did **not** build — left as written,
either could reintroduce the exact widening hazard S-4 exists to prevent. Goal alignment is otherwise
good: DAG/spec-first, pluggable (`Runner` injection, `probe` injection in `group_repos`), deterministic
(sorted grouping, fixed corpus seed), and the module boundaries (pure `paths.py` vs impure
`worktrees.py`) match CLAUDE.md's design principles.

---

## Blocking

None. Both Major findings are fixable without touching a locked interface, so I am not blocking
overall merge on them, but they must land before this code is exercised by a real long-lived run —
see must-fix list.

## Major

### C-1 — `sanitize_ref_component` truncates but never hashes; two long task ids can collide onto the same branch/worktree, and `ensure()` doesn't defend against it

- **Location**: `src/agent_orchestrator/isolation/paths.py:88` (`out = out[:_MAX_REF_COMPONENT_LEN]`);
  compounded by `src/agent_orchestrator/isolation/worktrees.py:286-300` (`ensure()`'s reuse branch).
- **Evidence**: The ticket's own `TASK.md` Risks section says the mitigation is "truncate **+ hash**
  any component over 80 chars in `sanitize_ref_component`, tested." HLD §11 M3's own Edge Cases prose
  says the same: "Worktree path length near `PATH_MAX` → **hash-shorten** `task_id` beyond 80 chars."
  The shipped code only truncates — no hash suffix anywhere in `sanitize_ref_component`. Two task ids
  that are identical in their first 80 sanitized characters (very plausible for LLM-generated,
  descriptive ids — the function's own docstring notes ids may be "agent-supplied via an injected
  `emit_tasks` manifest," and `specs/workflow.schema.json`'s task `id` pattern
  (`^[a-z0-9][a-z0-9-_]*$`) has **no `maxLength`**) sanitize to the identical branch name and the
  identical worktree path (`worktree_root` is built entirely from sanitized components).
  `ensure()`'s reuse check (`if path.exists() and entry is not None:`) then treats task B's `ensure()`
  call as a legitimate **reuse** of task A's already-created worktree — it never checks
  `entry.branch == branch` before reusing (only checks `rebase_in_progress`) — silently merging two
  supposedly-isolated tasks' work. This is exactly the class of leak S-4 exists to prevent, just
  reached through id collision instead of a widened view. Not disclosed in the developer's 5
  deviations. No test in `test_paths.py`'s 220-entry corpus exercises two *different* long strings that
  collide after truncation (only single-string truncation-length is asserted).
- **Why it matters**: undermines S-4/AC-18's isolation guarantee for a realistic input class the
  module's own docs call out as untrusted, not merely a theoretical edge case.
- **Fix**: when truncating over 80 chars, append a short deterministic hash of the *original* value
  (e.g., last 6-8 hex chars of `sha256(value)`) so two inputs sharing a long common prefix still
  diverge. Add a collision test: two >80-char strings identical up to char 80 but differing after must
  sanitize to different outputs. This is a pure internal behavior change to `sanitize_ref_component`'s
  output for the (currently completely untested) >80-char case — no function signature changes, so
  **no COORDINATE needed** for `T-Ib5Qy9`/`T-En8Hd4` (they consume branch/worktree strings opaquely).

### C-2 — `gc_run()` deletes every run ref unconditionally, even when the preceding worktree removal did not actually succeed

- **Location**: `src/agent_orchestrator/isolation/worktrees.py:446-479`, specifically line 460
  (`git.worktree_remove(norm, force=True)` — return value discarded) vs. lines 472-475 (unconditional
  ref deletion).
- **Evidence**: `worktree_remove` returns a non-raising `WorktreeRemoveOutcome` of `"locked"` or
  `"in_use"` for a real, expected condition (a lock file, or a grandchild process still holding a file
  open — exactly the scenario this ticket's own Risks section calls out: "Deleting a worktree while an
  agent grandchild holds a file open"). `gc_run()` doesn't inspect that return value at all, so a
  `"locked"`/`"in_use"` (non-removed) worktree still has its `refs/heads/ao/<run>/*` and
  `refs/ao/runs/<run>/*` refs deleted immediately after, unlike `reconcile()` (lines 381-442), which
  explicitly tracks `remaining_branches` and skips ref-deletion for a worktree whose removal failed
  (lines 424-425, 437-438). Coverage confirms this path is untested: `worktrees.py:461-462` (`gc_run`'s
  own `except GitError` branch) is at 0% in the coverage run I took, and `TestGcRun`'s 2 tests are both
  happy-path only.
- **Why it matters**: leaves a broken/dangling worktree (directory present, still git-registered,
  branch gone) after `ao prune` (`T-Cx4Jf1`'s consumer) — a resume-safety/data-integrity regression on
  exactly the "GC" path CLAUDE.md flags as needing safe-by-default behavior, and inconsistent with
  `reconcile()`'s already-correct handling of the identical scenario one function away.
- **Fix**: mirror `reconcile()`'s pattern in `gc_run()` — capture `worktree_remove`'s outcome (or catch
  `GitError`) per entry, and only delete the corresponding branch ref when the worktree was actually
  removed or was already absent. Add a `RecordingFakeRunner` test scripting a failed/locked removal
  (same technique already used in `TestRelease`/`TestReconcile`'s GitError tests) and assert the branch
  ref survives.

### C-3 — COORDINATE: `T-En8Hd4-engine-isolation-wiring` and `T-Ee3Mn8-e2e-and-review` TASK.md still describe the pre-S-4-amendment `extra_roots`-on-`LocalFsArtifactStore` design

- **Location**:
  `meta/tickets/E-Wk9Tz3-task-isolation/T-En8Hd4-engine-isolation-wiring/TASK.md` — "Files you own":
  `"src/agent_orchestrator/artifacts.py (edit — extra_roots + IsolatedArtifactView only)"`; AC-6:
  `"IsolatedArtifactView wraps the base store, applies effective_path, and adds only the task's own
  worktree roots as extra_roots... assert its _extra_roots is empty"`.
  `meta/tickets/E-Wk9Tz3-task-isolation/T-Ee3Mn8-e2e-and-review/TASK.md:66,91` — same `extra_roots`-in-
  `artifacts.py` framing.
- **Evidence**: This ticket's own S-4 amendment (folded in per `TASK.md`'s Phase-2 review comment) and
  HLD §7.3's normative code block explicitly **reject** widening `LocalFsArtifactStore` via a
  constructor parameter — the security property is that the widening lives in a **separate wrapper
  class**, `isolation/view.py::IsolatedArtifactView(base, task_isolation)`, with an internal `_roots`
  tuple that `LocalFsArtifactStore` itself knows nothing about. I confirmed by grep that no
  `extra_roots` parameter exists anywhere in `artifacts.py` or `LocalFsArtifactStore`, and that
  `IsolatedArtifactView` already ships (this ticket) at `isolation/view.py`, not inside `artifacts.py`.
  `T-En8Hd4`'s TASK.md was written before this amendment and was not updated; `T-Ee3Mn8`'s inherited
  the same stale framing. Neither the epic `STATUS.md` nor `EPIC.md` currently flags this as a pending
  doc-sync action.
- **Why it matters**: a developer picking up `T-En8Hd4` as literally written could (a) try to add an
  `extra_roots` constructor parameter to `LocalFsArtifactStore` — precisely the widening HLD §7.3
  warns "would silently widen run-state resolution too" (since `RunStateStore` shares that same
  instance) — or (b) write AC-6's test literally, asserting a `_extra_roots` attribute that does not
  exist anywhere in the real object graph, which cannot pass as written.
- **Fix (not this ticket's code — a ticket-doc action)**: architect/manager updates both TASK.md files
  before either ticket starts: replace `extra_roots`-in-`artifacts.py` language with "consume the
  already-shipped `isolation.view.IsolatedArtifactView`," and rewrite AC-6's `RunStateStore`
  non-widening test to match this ticket's own `tests/isolation/test_view.py::
  TestRunStateStoreNeverWrapped` pattern (`assert rs_store._store is base` /
  `not isinstance(rs_store._store, IsolatedArtifactView)`). This is not a T-Wk3Nv6 code change; T-Wk3Nv6
  built the correct (amended) design.

## Minor

### C-4 — `TASK.md`'s own "Interface Notes" section is stale relative to `STATUS.md`'s published Hook Points for `WorktreeManager`'s constructor

- **Location**: `TASK.md` "Schemas / Interface Notes": `WorktreeManager(workspace_root, run_id, repos,
  integration)` (4th param `integration`, matching HLD §11 M3's `RunIntegrationState`-typed
  pseudocode) vs. the shipped constructor at `worktrees.py:231-240`, whose 4th positional param is
  `integration_heads: dict[str, str]` — correctly documented in `STATUS.md`'s "Hook points" section.
- **Why it matters**: minor, since downstream consumers are directed to `STATUS.md` (correct) by the
  Handoff Boundary process, not `TASK.md`'s forward-looking interface notes — but this is exactly the
  drift CLAUDE.md's ticket-sync rule ("never update one without the others") exists to prevent, and a
  reader who only opens `TASK.md` would get the wrong shape. The change itself (plain dict instead of a
  `RunIntegrationState` reference) is well-justified — it keeps `WorktreeManager` decoupled from
  `RunState`, consistent with `T-Ib5Qy9`'s own R-20 fix for `Integrator`.
- **Fix**: one-line edit to `TASK.md`'s Interface Notes to match `STATUS.md`.

### C-5 — `release()` logs the event name `"worktree.removed"` even for non-removal outcomes

- **Location**: `worktrees.py:368-377` — `logger.info("worktree.removed", extra={..., "outcome":
  result})` fires with the literal event name `"worktree.removed"` even when `result` is
  `"locked"`, `"in_use"`, or `"already_absent"`.
- **Why it matters**: observability — anyone grepping/alerting on the `worktree.removed` event name
  (not the `outcome` field) would mis-count non-removals as removals. Minor since `outcome` does carry
  the truth.
- **Fix**: branch the event name on `result`, or rename to a neutral `"worktree.remove_attempted"`.

### C-6 — `ensure()`'s reuse path never verifies the existing worktree entry is on the expected branch (Deviation 4, disposition below)

- **Location**: `worktrees.py:286-300`.
- Folded into C-1 above as the mechanism that turns a truncation collision into a silent cross-task
  merge; see C-1's fix, which also addresses this by removing the collision that would otherwise reach
  this gap. Independently, this is also HLD §11 M3's own stated invariant ("a genuine collision would
  reuse branches, so `ensure` treats 'branch exists and points somewhere unrelated' as a hard error
  rather than silently reusing") — see Deviations disposition #4.

### C-8 — `reconcile()`'s branch-retention-on-failed-removal path is untested

- **Location**: `worktrees.py:437-438` (`if branch_name in remaining_branches: continue`).
- **Evidence**: 0% exercised — the existing GitError regression test
  (`test_worktree_remove_git_error_during_reconcile_is_caught_and_logged`) scripts `list_refs` to
  return **empty**, so the ref-deletion loop's body — including the exact line that is supposed to
  protect a branch whose worktree removal just failed — never runs in any test.
- **Fix**: extend that test (or add a sibling) where `list_refs` returns the orphan's own branch ref
  after the scripted removal failure, and assert it survives (`deleted_refs == []`).

## Nits

### C-7 — Accepted low-value coverage gaps

`_is_submodule`'s `except OSError` (worktrees.py:84-85) and `_mkdir_0700`'s filesystem-root guard
(worktrees.py:210) are uncovered; both are genuinely hard to trigger without faking a filesystem
error, as the developer's report already states. No action needed.

### C-9 — `group_repos`: no test mixes a git and a non-git `RepoRef` in the *same* call

`TestGroupRepos` tests "two refs, one repo," "one non-git ref (alone)," "one submodule," and
"deterministic order across N git repos" — but AC-8's own wording ("a non-git path is skipped and
reported") is phrased alongside the multi-ref grouping test, and no single test combines a real git
ref with a non-git ref in one `group_repos({...})` call to prove the fallback doesn't disturb the
grouping of the others. Low risk (the loop body is independent per `repo_id`), but cheap to add.

### C-10 — `resolve_unchecked` has no structural guard against future misuse

`artifacts.py:69-80`'s docstring says "every other caller in this codebase must keep using `resolve()`"
— enforced only by comment. I confirmed by grep it is currently called only from `resolve()` itself and
from `isolation/view.py`. Given this file already has a precedent for exactly this kind of guard
(`test_worktrees.py::TestNoGlobalPrune.test_module_source_never_calls_a_blanket_worktree_prune`'s
AST check), consider a similar cheap AST/grep test asserting `resolve_unchecked` is called nowhere
outside `artifacts.py`/`isolation/view.py`, as insurance against a future accidental bypass of the
traversal guard.

---

## Disposition of deviations 1-5 (developer's report)

1. **`default_registry_path` not migrated onto `xdg.resolve_state_dir`** — **ACCEPT.** Verified: the
   two resolutions are genuinely different shapes (`$XDG_CONFIG_HOME`/file vs. `$XDG_STATE_HOME`/dir);
   this is explicitly sanctioned by the ticket's own escape hatch; `tests/service/test_paths.py` passes
   unedited (confirmed in my own 632-test run). The implicit sub-question ("should `xdg.py` gain a
   `resolve_config_path` sibling?") is correctly answered **no** — a second helper for a single caller
   would be speculative generality, not DRY.
2. **`TaskIsolation.workspace_root` added beyond AC-19's minimum** — **ACCEPT.** Necessary for
   `effective_path`'s reserved-prefix check to be computable from the locked `(resolved_abs, task_iso)`
   signature. Verified `effective_path` stays generic over a structural `Protocol`
   (`_TaskIsolationLike`), not an import of `worktrees.TaskIsolation`, so `paths.py` keeps AC-1's
   import-safety. No downstream interface impact — `T-Ib5Qy9` never constructs `TaskIsolation` itself.
3. **`group_repos`/`IsolatedRepo`/`RepoMember` live in `worktrees.py`, not `paths.py`** — **ACCEPT.**
   `group_repos` requires `GitRepo.probe` (I/O); keeping it out of `paths.py` is what makes AC-1's
   "import-safe without git" claim true, and `TestImportSafety` proves it (imports `paths` with
   `PATH=""`).
4. **Branch-collision hard error not implemented separately from the HLD's literal pseudocode** —
   **PARTIAL ACCEPT, with a follow-up recommendation.** The developer faithfully implemented HLD §11
   M3's own code block (unconditional `delete_ref` before recreate). However, the same HLD section's
   "Edge cases" prose explicitly contradicts that code block, framing the hard-error behavior as
   protection against "a genuine [run_id] collision." Given C-1 shows a collision path *is* reachable
   (task-id truncation, not just run_id timestamp collision), I recommend this not be treated as a pure
   documentation nit: the architect should resolve the HLD's internal contradiction, and C-1's fix
   (hashing on truncation) closes the most realistic version of this gap regardless of how that
   resolves.
5. **Disk-footprint guard not implemented** — **ACCEPT.** Explicitly out of scope per the ticket text
   ("if specified") and formally owned by `T-En8Hd4` (S-7); confirmed via HLD §24's disposition table,
   which names `T-En8Hd4`'s own test as the fix location.

## Testing notes

- **What to mock**: `Runner` (already done, via `RecordingFakeRunner` — good pattern, reused
  consistently); for C-2's fix, script a `"locked"`/`"in_use"` `worktree_remove` outcome (not just a
  raising one) to prove `gc_run()` no longer deletes the corresponding ref.
- **What to integration-test**: keep using real `git init` temp repos for lifecycle/crash-recovery
  (already done, and done well — real `git submodule add`, real mid-rebase state, real `.gitignore`).
- **Coverage gaps**: `worktrees.py:461-462` (gc_run's `GitError` path, 0% — see C-2),
  `worktrees.py:437-438` (reconcile's branch-retention path, effectively 0% by tracing — see C-8); both
  are exactly the "never raises / never orphans a ref" safety mechanisms this ticket's ACs care most
  about, so they're worth closing even though the aggregate 97% module figure looks fine.

## Security walkthrough result (S-4, per the review brief's specific challenge)

Traced by hand (not merely trusting the tests): sibling task, other run, `..` traversal, absolute path
outside every root, symlink inside A's own worktree pointing outside, and reserved shared prefixes
(`.orchestrator/`, `.ao/`) all correctly raise `ArtifactPathError` or stay unremapped, matching HLD
§7.3 rule 5 exactly — `IsolatedArtifactView._roots` is built from exactly one `TaskIsolation.repos`
list (never the manager's registry), and `resolve_unchecked`'s symlink resolution happens before any
containment test in both the "inside workspace" and "already-absolute worktree path" branches. A
`RepoRef` nested path (e.g., the `core`/`docs-md` shape) is correctly handled through
`effective_path`'s longest-toplevel-wins rule, exercised by `test_paths.py::TestEffectivePath`. The one
open question is C-1 (task-id collision reaching S-4 through a different door than the artifact-path
guard) and C-10 (no structural guard against a future `resolve_unchecked` misuse) — see above.
