# STATUS

- ID: `T-Rm2Lx7-mechanical-resolvers`
- Updated At: 2026-09-07
- State: In Review
- Owner: developer-agent

## This update
- Ticket created by the architect as part of the `E-Wk9Tz3-task-isolation` design package. Not
  started; no code written.
- **2026-09-07 — Phase-2 review amendment applied.** Folded in: S-6 (regenerate subprocess uses `rule.timeout_seconds`, with a test asserting it fails fast at its own bound rather than holding the per-repo lock for 1800s). Estimate unchanged at 2.5 days.
- Per-finding dispositions: HLD §24 "Review dispositions".
- **2026-09-07 — Implemented: In Review.** `src/agent_orchestrator/isolation/resolvers.py` (new, 179
  statements) + `tests/isolation/test_resolvers.py` (new, 33 tests, real git fixtures throughout — no
  mocked git). All ACs 1-10 covered, including the S-5/S-6 amendments. Gates:
  - `ruff check` / `ruff format --check` on `resolvers.py` + `test_resolvers.py` +
    `tests/isolation/conftest.py` (untouched, no diff): clean.
  - `uv run mypy src`: unchanged at 4 pre-existing `_version.py` errors (no `engine.py` transient
    error observed this run).
  - `uv run pytest tests/isolation/test_resolvers.py tests/isolation/test_integrator.py -q`: **78
    passed / 0 failed**.
  - `uv run pytest tests/isolation -q`: **537 passed / 0 failed** (no regression).
  - `uv run pytest -q` (full suite, foreground): **3518 passed / 7 skipped / 0 failed**, one clean
    run (no transient failures to re-run) in 133.64s. The count is above the ticket's "≈3459"
    estimate because `T-Wl2Bq7`/`T-Cx4Jf1` (concurrent, disjoint files) have landed more tests in the
    same window — confirmed via this run touching none of `resolvers.py`/`test_resolvers.py`.
  - Coverage on `resolvers.py`: **94%** (target ≥90%). Ten uncovered statements are all defensive
    subprocess-exception branches (`_merge_file_union`'s `TimeoutExpired`/`OSError` handler and its
    `merged is None` fallback after a successful pre-check; `RegenerateResolver`'s `OSError` handler)
    — deliberately not force-covered via mocking, per the instruction to exercise real git fixtures
    rather than fakes; each is a narrow belt-and-suspenders path already guarded by an earlier
    deterministic check (binary/non-additive detection before `_merge_file_union` is even called).

- **2026-09-07 — Review response: APPROVE WITH CHANGES, all Must-Fix + Should-Fix + nits applied.**
  Full disposition below (`REVIEW.md` findings C-1..C-8). No commit made.

  **C-1 (Blocking) — FIXED.** Added `GitRepo.checkout_stage`/`checkout_merge`/`merge_file_union`
  (`src/agent_orchestrator/isolation/git.py`, appended after `show_stage` — the true end of the
  class at the time of edit — never touching `T-Wl2Bq7`'s concurrent, disjoint `fast_forward_checkout`
  addition earlier in the same file) plus one additive `import tempfile`. Deleted `resolvers.py`'s
  local `_merge_file_union`/`_git_checkout` entirely; `UnionResolver`/`RegenerateResolver` now call
  the `GitRepo` methods exclusively. `grep -n subprocess src/agent_orchestrator/isolation/
  resolvers.py` shows exactly one real `subprocess.run(` call left — `RegenerateResolver`'s execution
  of the spec-supplied `rule.command` (explicitly authorized to stay, "it is not git"). `git.py`'s
  `call_args` structural sweep (`tests/isolation/test_git.py::TestStructuralNoNetworkSurface`) got
  three new entries appended at the end of its dict (never reordering/touching existing ones); a new
  `TestCheckoutStageAndMergeFileUnion` class (5 tests, real git + one fake-runner forbidden-verb
  sweep) appended at the file's end. `UnionResolver.apply`/`RegenerateResolver.apply` now wrap the
  `GitRepo` calls that can raise (`merge_file_union` on a genuine infra failure) so a single path's
  decline never crashes the whole plan — preserves the pre-fix graceful-decline behavior exactly.
  `git diff --stat` on both files confirms additive-only diffs (git.py: +42 net incl. the earlier
  `fast_forward_checkout`, all mine appended after it; test_git.py: +157, 0 deletions).

  **C-2 (Major) — FIXED.** `Integrator._git_for` (`isolation/integrator.py`, committed, authorized —
  nobody else editing that file) now passes `rerere=self._spec.resolvers.rerere` to `GitRepo(...)`.
  Added `TestMechanicalResolution::
  test_rerere_false_prevents_replay_end_to_end_via_the_real_integrator` (`tests/isolation/
  test_integrator.py`) — teaches a resolution via a real rebase, then runs the SAME conflict through
  a real `Integrator.integrate()` with `resolvers.rerere=False` in the spec and T-Rm2Lx7's actual
  `resolve_mechanically` (not a stub) as `resolver_hook`: asserts the task escalates
  (`status=conflict_resolver`, both paths still in `conflicted_paths`), never lands. Proves the
  escape hatch stops git's own replay end-to-end, not just `resolve_mechanically`'s crediting.

  **C-3 (Major) — FIXED.** `Integrator._rebase_onto_and_resolve` now gates the `resolver_hook` call
  on `TIER_MECHANICAL in self._spec.ladder`; when absent, every path in `outcome.paths` is treated as
  unresolved without calling the hook, and the stage's `tier_reached` correctly stays `auto` (not
  `mechanical` — mechanical was never attempted, so it must not be credited; this refinement wasn't
  in the review's literal fix snippet but follows directly from its own reasoning). Added
  `TestMechanicalResolution::test_removing_mechanical_from_the_ladder_skips_the_hook_entirely`: a
  union rule that WOULD resolve the conflict is configured, `ladder=[auto, llm, rerun]` (no
  `mechanical`), and a spy wrapping the real `resolve_mechanically` proves it is never invoked; the
  raw rebase conflict escalates unresolved, with `tier_reached == "auto"`.

  **C-4 (Should-fix) — FIXED.** Added `logger.warning` in both previously-silent decline branches:
  `UnionResolver.apply`'s `merge_file_union(...) is None` path (`EVENT_MERGE_FILE_ERROR`), and
  `RegenerateResolver.apply`'s `cp.returncode != 0` path (`EVENT_REGENERATE_ERROR`, now including the
  exit code and a `GIT_STDERR_TAIL_BYTES`-bounded stderr tail — reusing `git.py`'s existing 4 KiB
  constant rather than a new literal, per the review's own suggestion).

  **C-5 (Should-fix) — FIXED.** Replaced `_snapshot_mtimes` (a double `Path.rglob("*")` worktree walk)
  with `_touched_paths`, diffing two `GitRepo.status_porcelain(worktree, untracked=True)` calls
  (before/after the regenerate command) instead — git's own status computation, unaffected by
  filesystem timestamp granularity, and the primitive the review itself pointed at. Behavior-
  preserving: all pre-existing regenerate tests (including the two-file sibling-scoping regression
  test) pass unchanged.

  **C-6 (Nit) — FIXED.** Corrected the registry section's header comment: `apply_plan`'s dispatch is
  genuinely zero-core-change to extend, but `plan_resolution`'s own precedence branches and
  `ResolutionStep.resolver`'s closed `Literal` are NOT — the overclaiming "no `apply_plan`/
  `plan_resolution` core-code change" wording is gone; `register_resolver`'s own docstring already
  had the accurate version.

  **C-7 (Nit) — FIXED.** Added `TestResolverHookConformance::
  test_empty_conflicted_paths_is_a_trivial_noop`, closing the one previously-uncovered line that
  wasn't a subprocess-exception branch (the `if not conflicted_paths: return []` guard). The
  remaining uncovered lines really are all subprocess-exception branches now (see updated coverage
  figure below) — the STATUS.md characterization this review flagged as slightly-off is now accurate.

  **C-8 (Nit) — FIXED.** Corrected: the prior "Interface change requests" section's claim that
  `git.py` wasn't touched "per the ticket's explicit instruction not to edit `git.py`" was wrong —
  `TASK.md`'s "Do NOT touch" list never named `git.py` (only `integrator.py`/`engine.py`/`models.py`/
  `cli.py`); not touching it was a `T-Wl2Bq7`-coordination choice, now moot since C-1 authorized and
  required editing it. That whole section is replaced below.

  **Gates re-verified after all fixes:**
  - `uv run ruff check .` / `uv run ruff format --check .`: clean on every file this task touched
    (`resolvers.py`, `git.py`, `integrator.py`, `test_resolvers.py`, `test_git.py`,
    `test_integrator.py`). One pre-existing, unrelated `ruff format --check` finding in
    `tests/test_engine_workspace_lock_sync.py` (`T-Wl2Bq7`'s own concurrent, uncommitted file, not
    touched here) reported separately, not fixed here.
  - `uv run mypy src`: unchanged, 4 pre-existing `_version.py` errors only.
  - `grep -n subprocess src/agent_orchestrator/isolation/resolvers.py`: confirms exactly one real
    `subprocess.run(` call site, the regenerate-command runner (C-1's exact acceptance grep).
  - `uv run pytest tests/isolation/test_resolvers.py tests/isolation/test_integrator.py
    tests/isolation/test_git.py -q`: **202 passed / 0 failed**.
  - `uv run pytest tests/isolation -q`: **546 passed / 0 failed**, one clean run — no transient
    `test_runlock.py`/`TestFastForwardCheckout` failures observed.
  - `uv run pytest -q` (full suite, foreground): **3544 passed / 7 skipped / 0 failed**, one clean
    run (no transient failures to re-run) in 132.43s.
  - Coverage on `resolvers.py`: **94%** (162 statements now, down from 179 — `_merge_file_union`/
    `_git_checkout`/`_snapshot_mtimes` moved out or replaced). 9 uncovered lines, all genuinely
    subprocess/infra-exception branches (`merge_file_union`'s `GitError`/`None`-return handling in
    `UnionResolver.apply`; `RegenerateResolver.apply`'s `OSError` handler) — narrow, already guarded
    by earlier deterministic checks, not force-covered via mocking per the "real fixtures" convention.

## Implementation notes (corrections to the HLD §11 M6 pseudocode found while implementing)

> These three findings predate the review round and describe the ORIGINAL bugs found/fixed. The
> underlying mechanism for #1's `checkout --ours/--theirs`/`--merge` and #2's sibling-scoping has
> since moved (C-1: into `GitRepo.checkout_stage`/`checkout_merge`; C-5: from an mtime walk to a
> `status_porcelain` diff) — see the "Review response" entry above for the current implementation.
> The bugs themselves, and why each was wrong, are unchanged and still accurate below.

1. **`RegenerateResolver` must NOT `git add` the chosen stage before running the command.** The
   pseudocode's `git.checkout_stage(wt, rule.take, step.path)` step, if implemented as `show_stage` +
   `write_bytes` + `git add`, prematurely resolves the path in the INDEX regardless of whether the
   regenerate command then succeeds — so AC-6's re-query would report the path as resolved even on a
   failed/timed-out command (verified with a real hanging/failing script; this was the first real
   test failure). Fixed by using `git checkout --ours/--theirs -- <path>` (worktree-file-only, index
   untouched — confirmed empirically) to materialize the stage, and `git checkout --merge -- <path>`
   to restore the original conflict markers on any failure path (timeout, nonzero exit, `OSError`) —
   now `GitRepo.checkout_stage`/`checkout_merge` (C-1), not a local helper.
2. **`git add -A` after a successful regenerate is unsafe when the same rebase left an UNRELATED path
   also conflicted.** `git add -A`/`git add <path>` stages ANY currently-unmerged path using whatever
   bytes are on disk for it — including a sibling path this regenerate command never touched, still
   sitting with literal conflict markers as its content, silently "resolving" it with garbage. Caught
   by a two-file test (`TestRegenerateResolver::
   test_requery_reflects_truth_not_apply_plans_own_bookkeeping`) that put a second, rule-less
   conflicting path in the same rebase. Fixed by snapshotting file `st_mtime_ns` (metadata only,
   NFR-1-safe) before/after the command and staging only paths that actually changed (plus
   `step.path` itself, unconditionally) — still honors "a regeneration may legitimately touch sibling
   files" without the collateral-damage risk.
3. **`git rebase`'s ours/theirs are swapped relative to a merge** (git's own documented behavior, not
   a bug here): during `git rebase --onto <onto> <upstream> <branch>`, index stage 2 ("ours",
   `git checkout --ours`) is the ONTO target; stage 3 ("theirs") is the branch being replayed.
   `RegenerateRule.take`'s default `"theirs"` therefore selects the TASK's own new content by default
   (correct/intended), but this reversed several of my own test's initial expectations before I
   understood it — documented prominently in `RegenerateResolver`'s class docstring so the next
   reader doesn't rediscover it the same way.

None of these required deviating from the LOCKED `resolve_mechanically`/`plan_resolution` interfaces
or the `ResolverHook` contract — they are all inside `apply_plan`'s/the resolvers' own internal
mechanics.

## Interface change requests — RESOLVED by the review round (C-1, C-2)

- ~~`GitRepo` has no wrapper for `git merge-file`/`checkout --ours/--theirs/--merge`~~ — **fixed**:
  `GitRepo.checkout_stage`/`checkout_merge`/`merge_file_union` added (`isolation/git.py`, review C-1).
  `resolvers.py` now calls these exclusively; the local `_merge_file_union`/`_git_checkout` helpers
  are deleted.
- `apply_plan`'s locked interface note (`apply_plan(plan, worktree, git, env) -> list[str]`) still
  omits how a `RegenerateRule`'s command/timeout/take reaches the applier — this is unchanged from
  before the review and still stands as documentation only: `apply_plan(plan, worktree, git, env,
  cfg)` (5 params, `cfg: ResolverConfig` added) is internal to this module (`Integrator` never calls
  `apply_plan` directly, only the unchanged, structurally-verified `resolve_mechanically`), so it
  affects no other task's contract.
- ~~`Integrator._git_for` never threads `spec.resolvers.rerere` through~~ — **fixed** (review C-2):
  `_git_for` now passes `rerere=self._spec.resolvers.rerere` to `GitRepo(...)`. The escape hatch works
  end-to-end now, proven by a real-`Integrator` test (see "Review response" above).

## Hook points for `T-Lr6Ka3` (escalation, T2/T3/T4)

- What T1 leaves for you: `resolve_mechanically`'s return value (surfaced to you via
  `IntegrationResult.conflicted_paths` / `TaskIntegrationState.conflicted_paths`, set by
  `Integrator._rebase_onto_and_resolve`/`_squash_rebase_verify_land`) is the list of paths STILL
  containing git's own original conflict markers, UNSTAGED — i.e. exactly the paths `git
  diff --name-only --diff-filter=U` reports at the moment mechanical resolution gave up. Every path
  NOT in that list has already been `git add`ed with real, resolved content (never partial/garbage —
  a resolver either fully resolves+stages a path or leaves it completely untouched, verified by the
  "non-conflicted files untouched" / byte-identity tests).
- `tier_reached="mechanical"` is set by `Integrator` itself (not by this module) whenever
  `resolver_hook` is actually INVOKED (i.e. `TIER_MECHANICAL in spec.ladder` and the rebase produced
  a genuine conflict — review C-3 fix), REGARDLESS of whether anything was actually resolved that
  call — so seeing `tier_reached="mechanical"` does not by itself mean any file was fixed; check
  `conflicted_paths` (still exactly what rebase left, if `resolve_mechanically` resolved nothing) vs.
  the pre-call conflict count if you need that distinction. If `"mechanical"` is absent from the
  ladder, the hook is skipped and `tier_reached` stays `"auto"` instead — do not assume every
  escalation you receive passed through this module.
- Rerere-driven resolutions you'll never see: when git's own `rerere.autoupdate` fully resolves every
  conflicting path in a single-commit rebase, `git rebase` still exits non-zero (mid-rebase) but
  `conflicted_paths()` is already empty — `Integrator` detects this (`outcome.paths` empty) and never
  calls `resolver_hook`/escalation at all for that case; you will simply never be invoked for it. You
  only see a task after this module (or a partial rerere replay alongside a genuinely-new conflict)
  has done what it can.
- `RESOLVER_REGISTRY`/`register_resolver`/`MechanicalResolver` (all public in `resolvers.py`) are NOT
  part of your contract — you do not need them; they are this module's own internal extension point.

## Evidence
- Design: [`docs-md/task-isolation-hld.md`](../../../../docs-md/task-isolation-hld.md) (see the
  module section named in `TASK.md`) and
  [`ADR-0013`](../../../../docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md).
- Code: `src/agent_orchestrator/isolation/resolvers.py`, `tests/isolation/test_resolvers.py`.

## Risks / Blockers
- See `TASK.md` > Risks. Not blocked. The `resolvers.rerere: false` / `Integrator._git_for` gap and
  the `"mechanical"` ladder-gating gap (both previously listed here as latent limitations) are now
  **fixed** (review C-2, C-3) rather than merely flagged.
- One pre-existing, unrelated `ruff format --check` finding in `tests/test_engine_workspace_lock_sync
  .py` (`T-Wl2Bq7`'s own concurrent, uncommitted file) — not this task's file, not touched, reported
  for visibility only.

## Next actions
1. Re-review (reviewer agent / human) against this response. No commit made per instruction.
2. `T-Lr6Ka3` (escalation) can proceed using the "Hook points" above once it starts — the
   `tier_reached` semantics note there was updated for the C-3 fix.

---
- By: developer-agent · Role: developer · Date: 2026-09-07 · Comment: Implementation complete, all
  gates green, ticket moved to In Review. See "This update" for full evidence and "Implementation
  notes" for the three corrections to the HLD pseudocode discovered while implementing (all within
  this module's own internals — no locked interface changed).
- By: reviewer-agent · Role: reviewer · Date: 2026-09-07 · Comment: **APPROVE WITH CHANGES.** Full
  findings in `REVIEW.md`. This module's own ACs (1-10) verified correct via independent
  `ruff`/`mypy`/`pytest --durations=10` runs (78 passed) and coverage (94%, matches STATUS.md).
  Confirmed both self-disclosed gaps and adjudicated them: **C-1** (must-fix, Blocking) — the local
  `subprocess.run(["git", ...])` calls in `_merge_file_union`/`_git_checkout` are a real S-1
  layering breach, not just a documentation gap; fix is additive `GitRepo.checkout_stage`/
  `checkout_merge`/`merge_file_union` methods, `COORDINATE` with `T-Wl2Bq7`'s concurrent additive
  `git.py` edit (its new `fast_forward_checkout`) so both land without a textual conflict. **C-2**
  (must-fix, Major, pre-existing committed `integrator.py:340-352` bug, not this task's file) —
  confirmed `Integrator._git_for` never threads `spec.resolvers.rerere` into `GitRepo(rerere=...)`,
  so the OQ-4-documented `resolvers.rerere: false` escape hatch has zero effect in production even
  though this module's own crediting logic is correct; exact fix + missing end-to-end test in
  `REVIEW.md`. Also found, while investigating the ladder question this review's brief raised:
  **C-3** (must-fix, Major, same file/disposition as C-2) — `integrator.py` never checks
  `"mechanical" in spec.ladder` before invoking the resolver hook, so removing `"mechanical"` from
  `integration.ladder` is currently a no-op, contradicting HLD §8.4. Two should-fix items (C-4:
  silent decline on a regenerate/union nonzero-exit where sibling exception branches do log; C-5:
  mtime-based sibling-file scoping should diff `GitRepo.status_porcelain` instead of a full
  worktree `rglob` walk) and three nits (C-6 docstring overclaims registry pluggability at the
  `plan_resolution` layer; C-7 STATUS.md's coverage narrative misses that line 526 is a trivial
  empty-input guard, not a defensive branch; C-8 STATUS.md's "the ticket's explicit instruction not
  to edit `git.py`" doesn't match `TASK.md`'s actual "Do NOT touch" list). No source/test edits
  made; no commits made.
- By: developer-agent · Role: developer · Date: 2026-09-07 · Comment: All 8 findings (C-1..C-8)
  fixed — full per-finding disposition in the "Review response" entry under "This update" above.
  C-1: `GitRepo.checkout_stage`/`checkout_merge`/`merge_file_union` added additively at the end of
  the class (never touching `T-Wl2Bq7`'s concurrent `fast_forward_checkout`), `resolvers.py`'s local
  subprocess helpers deleted; `grep -n subprocess resolvers.py` shows exactly the one authorized
  regenerate-command call site. C-2/C-3: `integrator.py` fixes applied (authorized, sole editor) with
  new real-`Integrator` end-to-end tests for both. C-4/C-5 (should-fix) and C-6/C-7/C-8 (nits) all
  applied, each within the ≤15-line guidance. Gates re-verified: `ruff check .`/`format --check .`
  clean on every file touched (one unrelated pre-existing finding in `T-Wl2Bq7`'s own
  `tests/test_engine_workspace_lock_sync.py`, not touched); `mypy src` unchanged at 4 pre-existing
  `_version.py` errors; `tests/isolation/{test_resolvers,test_integrator,test_git}.py` 202 passed;
  `tests/isolation` 546 passed, one clean run; coverage on `resolvers.py` 94% (162 stmts, 9 miss, all
  genuine subprocess/infra-exception branches). Full suite `pytest -q`: 3544 passed / 7 skipped / 0
  failed, one clean run. Ticket stays In Review pending re-review.
