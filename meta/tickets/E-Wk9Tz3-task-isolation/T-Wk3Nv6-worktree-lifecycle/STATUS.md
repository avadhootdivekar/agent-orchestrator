# STATUS

- ID: `T-Wk3Nv6-worktree-lifecycle`
- Updated At: 2026-09-07
- State: Done
- Owner: developer-agent

## This update
- Ticket created by the architect as part of the `E-Wk9Tz3-task-isolation` design package. Not
  started; no code written.
- **2026-09-07 — Phase-2 review amendment applied.** Folded in: S-4 (per-task `IsolatedArtifactView` + the task-A-cannot-reach-task-B test), R-6 (all prune sites use `prune_worktrees_scoped`), R-11 (shared `xdg.resolve_state_dir`, migrating `service/paths.py`), R-8 (`declared_outputs` on `TaskIsolation`), S-9 (0700 dirs). New files: `xdg.py`, `isolation/view.py`. **Re-estimated 2.5 -> 3 days.**
- Per-finding dispositions: HLD §24 "Review dispositions".
- **2026-09-07 — implementation complete: In Review.** All 20 ACs implemented across
  `isolation/paths.py` (new, pure), `isolation/worktrees.py` (new), `isolation/view.py` (new),
  `service/paths.py` (edited — `default_state_dir` only migrated onto the already-landed
  `xdg.resolve_state_dir`), `artifacts.py` (edited — `resolve_unchecked`/`root` added to
  `LocalFsArtifactStore`, no `extra_roots` param, `resolve()`'s guard unchanged). 298 new tests across
  4 new test files (`tests/isolation/test_paths.py`, `test_worktrees.py`, `test_view.py`,
  `test_service_paths_migration.py`). Targeted suite (`tests/isolation tests/service
  tests/test_artifacts.py tests/test_xdg.py`) 632 passed / 0 failed. Full suite run twice per the
  brief's guidance for transient concurrent-edit failures: first run 4 failed, all in
  `tests/test_builtin_routed_runner_assets.py` (a different, concurrently in-flight task's file, not
  touched by this ticket); second run **3244 passed / 7 skipped / 0 failed**, stable. `ruff check .` /
  `ruff format --check .` clean repo-wide; `uv run mypy src` unchanged at 4 pre-existing `_version.py`
  errors. Coverage: `isolation/paths.py` 100%, `isolation/view.py` 100%, `isolation/worktrees.py` 97%,
  `service/paths.py` 100%; repo TOTAL 95% (baseline 94%). Full detail, hook-point signatures for
  `T-Ib5Qy9`/`T-En8Hd4`/`T-Cx4Jf1`, and deviations are in `TASK.md`'s developer-agent comment.
  Awaiting review; no commit made per instruction.
  — By: developer-agent · Role: developer · Date: 2026-09-07

## Evidence
- Design: [`docs-md/task-isolation-hld.md`](../../../../docs-md/task-isolation-hld.md) (see the
  module section named in `TASK.md`) and
  [`ADR-0013`](../../../../docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md).
- New source: `src/agent_orchestrator/isolation/paths.py`, `src/agent_orchestrator/isolation/
  worktrees.py`, `src/agent_orchestrator/isolation/view.py`.
- Edited source: `src/agent_orchestrator/service/paths.py`, `src/agent_orchestrator/artifacts.py`.
- New tests: `tests/isolation/test_paths.py`, `tests/isolation/test_worktrees.py`,
  `tests/isolation/test_view.py`, `tests/isolation/test_service_paths_migration.py`.

## Risks / Blockers
- See `TASK.md` > Risks. Blocked only by the dependencies listed in `TASK.md` > Dependencies.
- Not blocked. Both upstream dependencies (`T-Gt4Pw8`, `T-Sc7Rm2`) were available in the working tree
  and consumed as documented (imported, never re-derived or edited).

## Next actions
1. Reviewer: check the 5 deviations logged in `TASK.md`'s developer-agent comment, especially #1
   (`default_registry_path` intentionally left unmigrated) and #2 (`TaskIsolation.workspace_root`
   added beyond AC-19's minimum field list).
2. `T-Ib5Qy9-integrator-core` and `T-En8Hd4-engine-isolation-wiring` can now consume the published
   `WorktreeManager`/`TaskIsolation`/`IsolatedArtifactView`/`group_repos` signatures (see `TASK.md`).
3. `T-Cx4Jf1` wires `WorktreeManager.gc_run` into `ao prune`.

---

Comment: Code review complete — full findings in
[`REVIEW.md`](./REVIEW.md). Verdict: **APPROVE WITH CHANGES** (must-fix: C-1, C-2 before this is
exercised on a real run; C-3 is a ticket-doc coordination action, not a code change here). Verified
independently: `ruff check`/`format --check` clean on scope files; `mypy src` at the same 4
pre-existing `_version.py` errors (no new); targeted suite `tests/isolation tests/service
tests/test_artifacts.py tests/test_xdg.py` — 632 passed / 0 failed in 16.22s (`test_paths.py` alone:
250 passed in 0.43s, no perf concern); coverage on the four owned/edited modules matches the reported
figures. Two real (untested) defects found: **C-1** `sanitize_ref_component` truncates but never
hashes beyond 80 chars, so two long/agent-supplied task ids sharing an 80-char prefix collide onto the
identical branch+worktree path, and `ensure()`'s reuse branch doesn't verify `entry.branch` before
treating that as a legitimate reuse — a realistic route to the exact cross-task leak S-4 exists to
prevent (not disclosed as a deviation). **C-2** `gc_run()` discards `worktree_remove`'s return value
and unconditionally deletes the run's branch/ref namespace afterward, unlike `reconcile()`'s already-
correct `remaining_branches` guard for the identical scenario — confirmed untested via 0% coverage on
`worktrees.py:461-462`. **C-3 (COORDINATE)**: `T-En8Hd4-engine-isolation-wiring/TASK.md` and
`T-Ee3Mn8-e2e-and-review/TASK.md` both still describe the pre-S-4-amendment `extra_roots`-on-
`LocalFsArtifactStore` design this ticket correctly rejected in favor of the `isolation/view.py`
wrapper — recommend the architect/manager update both TASK.md files before those tickets start, so a
developer doesn't reintroduce the widening hazard S-4 exists to prevent. Deviations 1/2/3/5 accepted
as reasoned; deviation 4 partial-accept with a note that the HLD's own §11 M3 prose and pseudocode
contradict each other on this exact point. Full detail, evidence, and concrete fixes for every finding
(including Minor/Nit items C-4 through C-10) are in `REVIEW.md`.
— By: reviewer-agent · Role: reviewer · Date: 2026-09-07

---

## Fix pass (2026-09-07) — per-finding disposition

**C-1 — FIXED.** `isolation/paths.py::sanitize_ref_component` now hash-shortens (never plain-
truncates) any component over 80 chars: `_TRUNCATED_PREFIX_LEN` (71) chars of the sanitized prefix +
`-` + an 8-char sha256 hex digest of the ORIGINAL value. Two 100-char ids sharing an 80-char prefix now
sanitize to distinct strings — new tests `test_two_100_char_ids_sharing_an_80_char_prefix_diverge`,
`test_ids_sharing_an_80_char_prefix_produce_distinct_branches_and_worktree_roots`. `ensure()`'s reuse
path was rebuilt (see D-ENS below) to verify the registered entry's branch/HEAD before ever treating
it as a reuse.

**C-2 — FIXED.** `gc_run()` now mirrors `reconcile()`'s `remaining_branches` pattern exactly: a
branch's ref is only deleted when its worktree removal actually succeeded (`"removed"`/
`"already_absent"`); on `"locked"`/`"in_use"`, or a raised `GitError`, the branch is kept and a
`worktree.remove_failed` warning is logged. Squash refs (`refs/ao/runs/<run>/*`) are unconditionally
deleted still, matching `reconcile()`'s own scope (it never touches them either) and AC-13's literal
requirement for `gc_run`. New tests: `test_locked_worktree_keeps_its_branch_ref`,
`test_git_error_during_removal_also_keeps_its_branch_ref`. **Bonus fix found while implementing this**:
`reconcile()`'s OWN `remaining_branches` guard was comparing a full `refs/heads/...` string
(`WorktreeEntry.branch`) against a stripped short name (`ref_name[len("refs/heads/"):]`) — a format
mismatch that silently no-op'd the guard entirely (confirmed empirically: `git worktree list
--porcelain`'s `branch` line is always the full ref). Fixed by comparing full-ref-to-full-ref
everywhere (both sides now use the unstripped `ref_name`); `test_worktree_remove_git_error_during_
reconcile_is_caught_and_logged` extended to script a non-empty `list_refs` result and assert
`report.deleted_refs == []`, which fails against the pre-fix code and passes against the fix (C-8
closed the same way).

**D-ENS (coordinator follow-up, after C-1/C-2, before C-3 could be marked fully resolved) —
IMPLEMENTED.** The architect's decision (`docs-md/task-isolation-hld.md` §11 M3 "D-ENS", §24; commit
`633570f`) replaces `ensure()`'s old unconditional-`delete_ref`-then-`-b`-create path entirely:
- Registered worktree at the expected path, on the expected branch, readable HEAD → **reuse**
  (rebase-in-progress is checked and aborted FIRST, since a mid-rebase worktree reports `detached` —
  no `branch` line at all, verified empirically — which would otherwise misread as a collision).
- Registered worktree at the expected path, on a *different* branch → **hard error**
  (`errors.WorktreeCollisionError`, naming both branches, the path, and the `ao prune
  --worktrees-only` remedy).
- No worktree at the path, branch absent → **create** (`-b`), unchanged.
- No worktree at the path, branch present, held by no other worktree → **re-attach**
  (`GitRepo.worktree_attach(path, branch)` — a new method, no `-b`, preserves commits), logs
  `worktree.branch_reattached`.
- Branch present and held by an entirely different worktree → **hard error** (same exception type,
  naming the other worktree's path).
- `ensure()` never calls `delete_ref` anywhere now — deletion stays exclusively in `release()`/
  `reconcile()`/`gc_run()`.

New public API surface (all additive, no existing signature changed): `errors.WorktreeCollisionError`
(`expected_branch`, `worktree_path`, `remedy`, `found_branch`), `GitRepo.worktree_attach(path, branch)`
in `isolation/git.py` (the one exception to "do not touch git.py" — required because no existing
`GitRepo` method can issue `git worktree add <path> <branch>` without `-b`; purely additive, placed
beside `worktree_add`, and `tests/isolation/test_git.py`'s structural public-method sweep updated with
one new `call_args` entry so it stays the enforcement it is designed to be). `WorktreeManager`/
`TaskIsolation`/`IsolatedArtifactView`/`paths.*` public signatures are all UNCHANGED, as required —
`T-Ib5Qy9` needs no changes on its side. New tests in `TestEnsureDEns`: case (a) reuse, case (b)
re-attach with the leftover commit proven still reachable after re-attach (two tests: commit-sha
equality, and the `worktree.branch_reattached` log line), case (c) branch held by another worktree
(hard error; asserts the error names branch+worktree+remedy AND that no ref moved, via `rev-parse`
before/after), plus a case-2 sibling test for a *different* worktree occupying our own expected path.

**C-4 (nit) — FIXED.** `TASK.md`'s Interface Notes corrected to `integration_heads: dict[str, str]`
(matching this file's own Hook points, and now also matching D-ENS's ctor); events list corrected to
name `worktree.branch_reattached`/`worktree.remove_skipped` and the new exception type.

**C-5 (nit) — FIXED.** `release()` now logs `worktree.removed` only for `"removed"`/`"already_absent"`
outcomes; `"locked"`/`"in_use"` log under a distinct `worktree.remove_skipped` event name. New test
`test_locked_outcome_logs_remove_skipped_not_removed`.

**C-8 (nit) — FIXED** (see C-2's "Bonus fix" above — same root cause, same fix, same test extension).

**C-9 (nit) — FIXED.** New test `test_git_and_non_git_refs_in_one_call_do_not_disturb_each_other`:
one real repo + one non-git path in the same `group_repos` call; asserts the real repo's grouping is
unaffected by the fallback.

**C-10 (nit) — FIXED.** New structural test in `test_view.py`
(`TestResolveUncheckedStructuralGuard`) walks every `.py` file under `src/agent_orchestrator` and
asserts `resolve_unchecked` is referenced only in `artifacts.py` and `isolation/view.py` — insurance
against a future accidental bypass of the traversal guard, mirroring the existing no-blanket-prune AST
guard's pattern.

**C-6 (nit) — closed by C-1's fix** (as the review itself anticipated: folded into C-1).

**C-3 — COORDINATE, now resolved by the architect** (commit `633570f`, outside this ticket's own
files): `T-En8Hd4`/`T-Ee3Mn8` TASK.md updated to consume `isolation/view.py::IsolatedArtifactView`
directly; no action needed here.

**C-7 — no action** (accepted low-value coverage gaps, unchanged).

**Gates (re-run after all of the above):** `ruff check .` / `ruff format --check .` clean repo-wide.
`uv run mypy src` — unchanged, exactly 4 pre-existing `_version.py` errors. Targeted suite
(`tests/isolation tests/service tests/test_artifacts.py tests/test_xdg.py`, excluding
`test_integrator.py`/`test_locks.py` per the coordinator's note — `T-Ib5Qy9` is actively writing them
against these same hook points): **645 passed / 0 failed**. Without the exclusion: 1 transient failure
in `test_integrator.py` (an `AttributeError` on `LogRecord.event`, unrelated to this ticket's code —
`T-Ib5Qy9`'s own file, mid-write). Full suite run twice: first run **1 failed** (same
`test_integrator.py` test, same transient cause), second run **3312 passed / 7 skipped / 0 failed**,
stable. Coverage: `isolation/paths.py` 100%, `isolation/view.py` 100%, `isolation/worktrees.py` 99%
(only the two pre-accepted C-7 gaps remain), `isolation/git.py` 96-97% (unchanged shape, new
`worktree_attach` method fully covered), TOTAL 95-96% depending on which suite slice — no regression
against the 94% baseline.
— By: developer-agent · Role: developer · Date: 2026-09-07

- By: coordinator · Role: manager · Date: 2026-09-07 · Comment: State -> **Done**. Review findings
  in this ticket's `REVIEW.md` were dispositioned by the implementing agent, the gates were re-run
  independently by the coordinator (ruff check/format clean, `mypy src` at exactly the 4 pre-existing
  `_version.py` errors, full suite green with no regression against the pre-epic baseline of 2008
  passed / 7 skipped / 94% coverage), and the work is committed on `ad/task-isolation` under this
  ticket's own commit. Anything still open was re-filed against a named later ticket rather than left
  in this one; see the epic `STATUS.md` rollup for that ticket's entry.
