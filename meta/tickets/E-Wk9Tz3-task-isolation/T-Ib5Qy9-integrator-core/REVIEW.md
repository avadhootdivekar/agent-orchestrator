# REVIEW: T-Ib5Qy9-integrator-core

- Reviewer: reviewer-agent
- Date: 2026-09-07
- Scope reviewed (uncommitted): `src/agent_orchestrator/isolation/integrator.py`,
  `isolation/locks.py` (new); `tests/isolation/test_integrator.py`, `tests/isolation/test_locks.py`
  (new); `TASK.md`/`STATUS.md` for this task and the epic rollup. `errors.py`'s
  `IntegrationLockTimeoutError` was read but not re-reviewed (already committed, out of scope per the
  brief). Did not read or evaluate `T-Rm2Lx7`/`T-En8Hd4`/`T-Lr6Ka3`/`T-Ac6Vd9`/`T-Wl2Bq7` source (none
  exists yet); their `TASK.md` files were read only to cross-check assumed names/semantics against
  what this ticket publishes.
- Verified independently (not trusted from STATUS.md): `uv run ruff check`/`ruff format --check` on
  the four scope files — clean. `uv run mypy src` — exactly 4 pre-existing `_version.py` errors, no
  new ones. `uv run pytest tests/isolation/test_integrator.py tests/isolation/test_locks.py -q -p
  no:cacheprovider --durations=10`, run 3×: **53 passed** every time (9.06s / 8.76s / 10.86s), no
  flakes. `uv run pytest tests/isolation -q` (full package): **467 passed**, matching STATUS.md.
  Coverage on the two owned modules: `integrator.py` 95% (misses: 477-484, 522-526, 550-554, 825,
  **904, 908-918**), `locks.py` 100%. Also ran a standalone git experiment (below) to confirm a
  mechanics question the brief asked me to verify empirically, not just by reading.

## Verdict: **APPROVE WITH CHANGES**

Must-fix before merge: **C-1** (blocking — resume-path lost-update against a concurrently-landed
repo), **C-2** (should-fix before merge — the T1 mechanical-resolution-succeeds path is completely
untested; the coverage gap above is not incidental, it is exactly this path). **C-3** (private-method
reach-across) is `COORDINATE` — accept for now with a follow-up ticket, do not block on it, matching
the `T-Ov9Bt5` precedent. C-4/C-5/C-6 are should-fix but non-blocking. Everything else is a nit.

**Disposition of the three developer decisions beyond the HLD's schematic pseudocode:**
- `agent_id: str` as an explicit keyword-only arg — **justified, no violation.** Verified
  `TaskIsolation` (`worktrees.py:173-190`, frozen/merged) has no `agent_id` field, and `models.py`
  confirms it; there is nowhere else to get it from without touching an off-limits file. Minimal,
  well-scoped. `COORDINATE` only so `T-En8Hd4` sees this is required at every call site.
- `task_integration: TaskIntegrationState` as an explicit 3rd positional arg — **justified.** The
  HLD's own M7 (`escalate(ti, spec, cause)`) needs per-task ladder state that isn't derivable from
  `task_iso`/`run_integration` alone, and the HLD's `integrate(task_iso, run_integration, attempt)`
  never explains how `escalate` gets `ti` — the pseudocode is schematic, not a closed signature (the
  ticket's own "Schemas / Interface Notes" locks the ctor exactly but only says `integrate(...) ->
  IntegrationResult` for the method). `COORDINATE` — this is the first full publication of the shape;
  make sure `T-En8Hd4`/`T-Lr6Ka3` build against `STATUS.md`'s hook-points block, not the HLD pseudocode.
- `RunIntegrationSnapshot` without `heads` — **justified, and arguably the correct call.** The LLD's
  own step 3 (`head = git.rev_parse(integration_ref(repo)) OR repo.base`) already reads the head
  *live under the lock*, never from the snapshot — confirmed in the shipped code
  (`integrator.py:717`, `:465`). Carrying `heads` on the snapshot would invite a caller to trust a
  value that's stale by construction. No downstream ticket's `TASK.md` currently assumes
  `run_integration.heads` exists (grepped all five). Not a violation.

None of the three reopens R-20/NFR-3: `task_integration` is only ever read via `.model_copy(update=
...)` (never assigned to), confirmed by reading every use site — see C-4 for the one residual gap
in that guarantee.

---

## Summary

This is careful, high-quality work: the squash/rebase/verify/CAS mechanics match HLD §8.2/§8.3
exactly, the lock module (`locks.py`, 100% covered) is genuinely solid — real second-process tests,
event-synchronized thread tests, a proven no-hang-under-opposite-ordering test, and confirmed
kernel-releases-on-kill behavior — and the single-`integrate()`-call CAS-retry/partial-landing/
already-landed-short-circuit logic (R-7) is correct and unusually well tested (three different racer-
runner injections that land a real concurrent commit at the exact CAS instant). Determinism,
event-naming, no-magic-literals, and the NFR-1 "paths only, never file contents" discipline are all
honored, including a real AST-based test that nothing in the module calls `open()`. Goal alignment is
otherwise strong: DAG/spec-first (nothing here inlines a payload), pluggable (hooks injected, `Runner`
injected), and the R-20/NFR-3 worker-thread boundary is respected by construction.

The one real defect (C-1) is serious and specific: `resume_integration()` — the path entered after a
T2 LLM-resolver round trip — releases every repo's lock when it returns from the first `integrate()`
call, then on resume treats every repo that is *not* mid-rebase as still validly staged, re-reading
only `expected_old` fresh and reusing the old (potentially stale) candidate sha for the CAS. Since
`update-ref`'s CAS is a pure pointer-equality check with no ancestry validation (confirmed
empirically below), this can silently drop a sibling task's already-landed commit from a shared
repo's history — the exact class of bug the whole CAS design exists to prevent, reopened at the one
seam (the T2 requeue boundary) that necessarily releases the lock for an extended window. The rest of
the findings are contained, well-scoped, and mostly test-quality (two vacuous `assert X or True`
lines, one real coverage gap on the T1-succeeds path, one cross-module private-member reach that
matches a pattern this same epic already flagged once in `T-Ov9Bt5`).

---

## Blocking

### C-1 — `resume_integration()` can silently overwrite a concurrently-landed sibling commit in a shared repo (lost-update / data loss)

- **Location**: `src/agent_orchestrator/isolation/integrator.py:460-491` (staging loop) and
  `:505-539` (CAS loop) inside `resume_integration()`.
- **Observation**: `integrate()` acquires every repo's lock, stages (squash+rebase) every repo, and
  on a conflict in repo B **returns from inside the `with ExitStack()` block** — which releases
  *every* repo's lock, including repo A's if A already staged cleanly before B conflicted (or, for a
  single-repo task, the one repo's own lock, once its worktree is left mid-rebase). This is clearly
  intentional (holding a lock across an entire LLM resolver dispatch — the T2 window, potentially
  minutes — would be a severe availability regression), and `resume_integration()` correctly
  re-acquires the lock(s) later. But for any repo where `rebase_in_progress(wt)` is `False` on
  resume, the code does:
  ```python
  head_sha = git.rev_parse(integration_ref)         # FRESH read
  expected_old = head_sha if head_sha is not None else ""
  ...
  new_sha = git.rev_parse(branch_ref) or recorded_squash or ""   # STALE: whatever this
                                                                   # branch was rebased onto
                                                                   # during the FIRST integrate() call
  staged[repo.key] = (expected_old, new_sha)
  ```
  It never checks whether `head_sha` still equals the head this repo's `new_sha` was actually rebased
  onto. If some other task lands into this same repo during the T2 window, `expected_old` reflects
  that new state (read fresh), so `update_ref_cas(ref, new_sha, expected_old)` **succeeds** (CAS only
  compares `ref`'s current value to `expected_old` — it does not require `new_sha` to descend from
  `expected_old`), landing a commit whose parent is the *old* head. The intervening commit becomes
  unreachable from the integration branch — silently discarded, not detected, not logged as
  `integration.partial` (that only fires on an actual CAS *failure*, which this isn't).
- **Empirically verified** (git alone, independent of this codebase) that `update-ref`'s CAS performs
  no ancestry check:
  ```
  git update-ref refs/heads/integration $T2 $BASE     # task2 lands, moving BASE -> T2
  # task1's "resume" reads expected_old = T2 fresh, then CAS's its stale candidate T1 (based on BASE)
  git update-ref refs/heads/integration $T1 $T2        # SUCCEEDS (rc=0)
  git merge-base --is-ancestor $T2 refs/heads/integration   # exit 1 -- T2 is GONE from history
  ```
  (script + output captured in this review session; trivially reproducible).
- **Why it matters**: This directly contradicts the epic's own safety framing — HLD's "why CAS and
  not merge --ff-only" section (§8.1) claims a foreign write "fails atomically" and "converts the head
  moved under me from a corruption risk into a clean, retryable error." That's true *within* a single
  `integrate()` call (verified correct above), but false across the T2 resume boundary, which is the
  one place a lock is deliberately released mid-flight. It is also a direct violation of this
  project's "safe by default" / resumable design principle (CLAUDE.md) and of R-7's own stated intent
  ("retry is scoped to THIS repo only... never re-process a repo already landed" — the bug is the
  mirror image: a repo that *isn't* being retried is trusted without revalidation). The scenario is
  not exotic: it requires only `max_parallel > 1` (an explicitly supported, epic-headline mode) and
  two tasks that touch the same repo, one of which needed a T2 LLM-resolver round trip.
  `test_resume_multi_repo_lands_the_non_conflicted_repo_too` (`test_integrator.py:1296`) is the
  closest existing test and does not cover this — it never lands a third-party commit into the
  already-staged repo between the two calls.
- **Fix (contained to this file, no interface change needed)**: for every repo in
  `resume_integration()`'s staging loop that is *not* `rebase_in_progress`, compare the freshly-read
  `head_sha` against what the branch was actually rebased onto (derivable via
  `git rev-parse {branch_ref}^`, or by tracking the target head used at squash time). If they differ,
  don't trust the stale tip: reset the worktree to `recorded_squash` (already read, from the durable
  `paths.squash_ref(...)`) and re-run `rebase --onto head_sha repo.base repo.branch` — i.e., the same
  re-staging the CAS-retry path in `_squash_rebase_verify_land` already does, just entered from a
  different caller. Consider extracting that shared "re-rebase onto a fresh head" step (currently
  duplicated logic between the CAS-retry loop at `:804-838` and the fix this needs) into one helper
  used by both. A dedicated test: land a sibling commit into the non-conflicted repo between the
  `integrate()` call and `resume_integration()` in
  `test_resume_multi_repo_lands_the_non_conflicted_repo_too`'s shape, and assert the sibling's commit
  is still an ancestor of the final head.

## Major

### C-2 — The T1 "resolver/rerere actually resolves the conflict" path is completely untested

- **Location**: `src/agent_orchestrator/isolation/integrator.py:897-918` (`_stage_one_repo`'s
  resolved-then-continue tail). Coverage: lines 904, 908-918 are **uncovered** (verified via
  `--cov-report=term-missing`, not just claimed).
- **Observation**: Every conflict scenario in `test_integrator.py` (`TestConflictHandling`,
  `TestCasRetry`, `TestResumeIntegration`) injects `_never_resolves` (the default stub that echoes the
  conflicted paths back unresolved) or otherwise never exercises a `resolver_hook` that returns `[]`.
  There is also no test where `outcome.paths` comes back empty from `rebase_onto` because rerere
  itself fully auto-resolved and auto-staged the hunks (the branch explicitly commented as an S-5
  concern at `:901-904`). Grepping the whole file confirms no stub ever returns an empty list for a
  genuinely conflicting path.
- **Why it matters**: This is the *entire point* of having a `resolver_hook` — "conflict resolved,
  rebase continues, tier=mechanical, lands normally" is a primary, expected-common-case path, not an
  edge case, and it's the one the review brief specifically asked about ("rerere replay detection and
  counting"). The code reads correctly on inspection (mirrors the already-tested clean-rebase tail),
  but that is an inspection opinion, not a verified one — and STATUS.md's "all 5 non-clean
  `make_conflict_repo` kinds parametrized" claim reads as broader coverage than what's actually there.
- **Fix**: add at least one test where a resolver stub actually resolves (returns `[]`, having
  `add`ed a real resolution in the worktree) and asserts `EVENT_RESOLVED`/`tier=mechanical` fires,
  `rebase --continue` completes, and CAS lands the correct sha; and one where `outcome.paths == []`
  straight out of `rebase_onto` (a rerere-only resolution) to close the S-5 gap specifically.

### C-3 — `Integrator` reaches `GitRepo._run`/`._raise` directly for the structural verify check

- **Location**: `src/agent_orchestrator/isolation/integrator.py:972-1013`
  (`_grep_conflict_markers`, `_diff_check_clean`), both `# noqa: SLF001`.
- **Observation**: This is the same smell flagged as `T-Ov9Bt5`'s Major C-1 (an underscore-prefixed
  git-invoking path that evades `test_git.py`'s `test_public_method_surface_never_reaches_a_forbidden_
  verb` structural sweep) — here in a stricter form: it's not a private method on `GitRepo` itself but
  a *different module* reaching across the class boundary into `GitRepo`'s private members
  (`git._run(...)`, `git._raise(...)`), something ruff's own SLF001 rule exists to catch and which is
  explicitly suppressed at both call sites. `grep`/`diff --check` are not forbidden verbs, so the S-1
  no-network guarantee itself isn't at risk (unlike the safety concern the sweep test polices), but
  the *encapsulation* boundary this module's own docstring (`:20-29`) argues it's preserving is a
  structural claim, not a mechanically-checked one for a cross-module reach.
- **Why it matters**: matches CLAUDE.md's "follow existing patterns / clean interfaces" principle,
  and the precedent this same epic already set (`T-Ov9Bt5` review) treated the narrower, same-class
  version of this pattern as a must-fix. Not blocking here because it's well-justified, well-tested
  (both git exit-code conventions verified empirically per the code comments, not assumed), and
  `git.py` is genuinely off-limits to this ticket.
- **Fix — `COORDINATE`**: add `GitRepo.grep_conflict_markers(cwd, ref, paths)` and
  `GitRepo.diff_check(cwd, base, head)` as small, additive public wrappers (a follow-up ticket, per
  the developer's own suggestion), and extend `test_public_method_surface_never_reaches_a_forbidden_
  verb`'s call-arg table for them. Route to whichever ticket ends up owning a `git.py` touch-up
  (`T-Cx4Jf1` or a new small task) — not this one.

### C-4 — `TaskIntegrationState` is a plain mutable `pydantic.BaseModel`; the R-20/NFR-3 guarantee for it rests on discipline, not the type system

- **Location**: `src/agent_orchestrator/models.py:721` (`TaskIntegrationState(BaseModel)`, no
  `frozen=True`); consumed at `integrator.py:376, 419` and read via `.model_copy(update=...)` at
  `:477, 498, 735, 755, 817`.
- **Observation**: Every use site in this module is a read via `.model_copy()`, never a direct
  attribute assignment — I checked all five call sites and confirmed none mutates the passed-in
  object. That's good discipline, but it is not enforced: `TaskIntegrationState` has no `frozen=True`
  in `models.py`, so nothing stops a future edit to this file (or a different caller) from writing
  `task_integration.tier_reached = X` directly. If `T-En8Hd4` ever passes the *live* object living
  inside `RunState.task_integration[tid]` (rather than a copy) — which STATUS.md's own comment
  ("a value/snapshot, not a live RunState field ref") asks of the caller but nothing here enforces —
  a future accidental mutation here would be exactly the worker-thread-mutates-RunState-data bug
  R-20 exists to prevent, and the R-20 AST test (`test_module_never_imports_run_state_or_calls_save`)
  would not catch it, since `TaskIntegrationState` isn't `RunState` and no `.save(` is involved.
- **Fix (cheap, contained to this file)**: defensively copy at the top of both `integrate()` and
  `resume_integration()` — `task_integration = task_integration.model_copy()` — so the invariant holds
  regardless of what the caller passes. `COORDINATE` only so `T-En8Hd4` knows this defensive copy
  exists (or will) and doesn't need its own.

## Minor / Nit

- **`test_integrator.py:1120`** (`test_auto_commit_false_never_adds_and_integrates_agents_own_commit`):
  `assert result.heads[task_iso.repos[0].key] == own_sha or True` — the `or True` makes this a
  tautology; the comparison never actually gates the test (and would fail if evaluated for real,
  since the landed head is the *squash* sha, not `own_sha`, even with `auto_commit: false`). The
  test's other assertion (`git add -A` never called) still validates the AC's primary claim, so this
  is not masking a real regression today, but fix or remove the dead line.
- **`test_integrator.py:527`** (`test_cas_loss_then_bounded_retry_succeeds`): the analogous
  `_raw_git(...) or True` is harmless (`_raw_git` itself asserts a zero return code internally, so a
  failure there raises regardless of the outer `or True`) but is confusing to read next to the C-1
  finding above — worth cleaning up in the same pass for clarity.
- **`test_locks.py:205`** (`test_two_threads_opposite_repo_order_no_deadlock`): the final
  `assert True in results.values() or all(v is False for v in results.values())` is a tautology for a
  two-entry bool dict (always true) and asserts nothing beyond what the preceding `is_alive()` checks
  already cover. Harmless, but drop it or replace with something that actually constrains the outcome.
- **`integrator.py:717` / `:960`**: `_run_verify_structural` re-reads `git.rev_parse(branch_ref)`
  rather than using the `new_sha` already captured in `staged[repo.key]` during staging; similarly the
  CAS loop's `expected_old` is recomputed by a fresh `rev_parse` per repo rather than being threaded
  through from the staging read. Not a bug under the current single-call control flow (lock is held
  continuously start-to-finish so nothing else can move these values in between), but this
  "re-derive rather than trust a captured value" pattern is exactly what made C-1 hard to spot —
  worth tightening for defense-in-depth even though today's window is safe.
- **`integrator.py:346-351`** (`_git_for`): `GitRepo(repo.toplevel, ...)` uses the *primary repo's
  toplevel* (not the task's isolated worktree) as the instance's default `cwd`, relying on every
  worktree-content call passing an explicit `cwd=repo.worktree_root`/`wt` override and every ref-only
  call (safe to run from any worktree sharing the common dir) omitting it. This is consistently
  applied today, but it's an implicit invariant with no comment calling it out and no structural test
  guarding it — a future addition that forgets `cwd=wt` on a content-sensitive call would silently
  operate against the user's real checkout instead of the isolated worktree. Suggest a one-line
  comment on `_git_for`/the class docstring stating the rule explicitly.

## Design/Goal Alignment — dimension-by-dimension

- **Project goals (CLAUDE.md)**: declarative specs (yes — `IntegrationSpec` drives everything, no
  inline payloads), DAG-first (n/a to this file directly), pluggable (`ResolverHook`/`EscalationHook`/
  `Runner` all injected protocols/callables), deterministic (`commit_tree` dates deterministic per
  `git.py`, clock injected for events), resumable (mostly — see C-1), observable (rich, consistently-
  named events with task/repo/tier/sha fields), safe-by-default (S-3 screen, no shell, argv-only
  `verify_command`). C-1 is the one place resumability's safety claim doesn't hold.
- **Epic/task goals**: all 18 ACs + the three 2026-09-07 amendments (R-20/R-7/R-8/S-3) are implemented
  and, other than C-1/C-2, match their tests. R-20's ctor is exactly as locked. R-8's Empty
  reconciliation (`_is_empty` + `_untracked_declared_outputs`) is correct and tested both ways
  (gitignored declared output present -> not Empty; genuinely nothing changed -> Empty).
- **SOLID/KISS**: `Integrator` has one job (land or report why not) and delegates cleanly to injected
  hooks; `_stage_one_repo`/`_merge_escalation` are well-factored, reused correctly by both the initial
  pass and the CAS-retry loop (though not by `resume_integration`, which is the root of C-1).
- **DRY**: the CAS-retry re-staging logic (`_squash_rebase_verify_land:804-838`) and what
  `resume_integration()` needs to do for C-1's fix are the same operation, currently only implemented
  once — extracting a shared helper as part of the C-1 fix is the one concrete DRY opportunity here.
- **No magic literals**: clean — every status/event/reason string, retry bound, and byte cap is a
  named module constant; every timeout/denylist/template default lives in `models.py`.
- **Spec/DAG correctness**: n/a (this module doesn't parse specs or resolve dependencies).
- **Determinism/resume safety**: mostly strong (see above); C-1 is the resume-safety exception.
- **Errors/logging**: `GitError` is caught once at the top of both public methods and translated to a
  single `IntegrationResult(reason="git_error")` plus one `EVENT_FAILED` log — no duplicate-layer
  spam. Every event carries `task_id`; repo-scoped events also carry `repo`.
- **Testability**: excellent — hooks are stubbed, `Runner` is injectable and used throughout to inject
  races deterministically (no sleep-based flakiness anywhere I found), real git fixtures throughout.
- **Concurrency/rollout**: lock ordering is deterministic (sorted repo key) and deadlock-tested;
  idempotency is tested and correct for repeated `integrate()` calls; C-1 is the concurrency gap.

## Testing notes

- **What to mock**: `resolver_hook`/`escalation_hook` (already done, correctly, via `Protocol`-typed
  stubs); the `Runner` for verify-command timeout/missing-binary/exit-code cases (already done).
- **What to integration-test**: everything here already is (real `git init` fixtures) — this module
  has no unit-test-with-fakes layer and doesn't need one; the git subprocess boundary IS the contract.
- **Coverage gaps**: C-2 (T1-succeeds path, lines 904/908-918) is the concrete one; C-1's fix will
  need its own dedicated test (concurrent land during the T2 window) since none of the existing
  resume tests inject a third-party ref move between the two calls.

## Pre-submit checklist

- [x] Review scope confirmed: user-specified files (`integrator.py`, `locks.py`, their tests, ticket
      docs), all uncommitted.
- [x] Checked all three alignment levels: project goals (CLAUDE.md), epic/task goals (`TASK.md`
      incl. 2026-09-07 amendments, `STATUS.md`, HLD §6/§8/§9/§11/§24, ADR-0013 D3/D4/D5/D8 — all
      read), code-level intent (docstrings/comments checked against actual behavior; no drift found
      beyond C-1's resume-path gap).
- [x] Walked every review dimension — see "Design/Goal Alignment" section above; nothing skipped
      silently.
- [x] Every finding above cites a location, observation, why-it-matters, and a concrete fix.
- [x] Findings bucketed by actual severity: C-1 blocking (proven data-loss path), C-2/C-3/C-4 major
      (real gaps, contained fixes, none corrupts a run today), rest nits.
- [x] Testing notes included (above).
- [x] No source/test edits made; no commits made. This file and a `STATUS.md` comment are the only
      writes.
