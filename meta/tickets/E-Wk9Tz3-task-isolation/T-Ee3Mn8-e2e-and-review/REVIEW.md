# REVIEW: T-Ee3Mn8-e2e-and-review

- Reviewer: reviewer-agent
- Date: 2026-09-07
- Scope: the uncommitted T-Ee3Mn8 deliverable on `ad/task-isolation` — `tests/test_e2e_isolation.py`
  (new, 32 tests), `tests/test_nfr2_regression_gate.py` (new, 4), `tests/isolation/test_conflict_fixtures.py`
  (new, 15), `tests/isolation/test_ladder_e2e.py` (new, 2), `tests/isolation/test_security_guards.py`
  (new, 7), `tests/isolation/conftest.py` (modified), plus `tests/isolation/test_resolvers.py`
  (modified — attributable to this ticket, see W-8, and **not** listed in the review brief).
  T-Lr6Ka3's files were committed as `8a3235f` mid-review and are **out of scope**.
- Method: not a reading pass. Every load-bearing claim was checked by mutating the product code,
  running the test, and restoring — see "Evidence" for the full list.

## Verdict: **REWORK**

The three former xfails are correctly resolved — I independently confirmed all three were test bugs,
not product defects, by disabling each product gate and watching the corresponding test fail
(C-0 below). The NFR-2 golden gate, the two `resolve_unchecked` guards, both `test_ladder_e2e.py`
cases, and the checkout-sync/crash-resume/mechanical-resolver tests are genuinely non-vacuous and
well built. Ruff is clean, the five files pass 5/5 runs in three orders, and no `src/` change is
attributable to this ticket.

But this is **the epic's verification gate**, and its flagship artifact does not verify. **13 of the
32 tests in `tests/test_e2e_isolation.py` assert nothing but the CLI exit code** (C-1), and six of
those are named and docstring'd for properties they never set up — including AC-17/S-4, which the
2026-09-07 architect amendment introduced specifically *to replace* a criterion it called "too
weak", and which has come back weaker than the one it replaced (C-2). Four e2e acceptance criteria
(AC-4 partial, AC-5, AC-6, AC-7) are not delivered at any altitude (C-3). AC-1 has been redefined
rather than met (M-1), AC-19's measurement is a synthetic self-fulfilling constant (M-3), and the
gate shipped a matrix claiming ✅ on rows it does not cover while **missing a live product defect**
that a real `status.json` assertion would have caught (M-2: `tier_counts` is serialized and
displayed but never written by any code path in `src/`).

The fix is not large — most of it is strengthening assertions in tests that already build the right
fixtures — but a verification gate that passes vacuously is worse than no gate, because the matrix
in `STATUS.md` will be read as proof.

---

## Findings

### C-0 — Verified sound: the three former xfails were test bugs, not product defects

Not a finding; recorded because the brief asked for independent confirmation and the answer is
"the docstrings are right".

| Former xfail | Claim | How I confirmed |
|---|---|---|
| R-2 missing outputs not gating landing | test bug: `repo_writes` never controlled `ctx.output_paths`; needed `write_outputs=False` | Replaced `missing = [o for o in task.outputs if not st.exists(o)]` with `missing = []` at `src/agent_orchestrator/engine.py:3334`. `TestMissingOutputs` **failed** (`integrated=1`, exit 0). Restored → passes. |
| `isolation.strict` not enforced on non-git | test bug: `WorkflowSpec` has no `isolation` field; `strict` is `.ao/config.yaml`-only | Confirmed directly: `WorkflowSpec.model_fields` has no `isolation`, and `model_validate({... "isolation": {"strict": True}})` is **silently accepted and ignored** (see N-3). Then replaced `if self._isolation_strict:` with `if False:` at `engine.py:2324` → `TestDegradation` **failed**. Restored → passes. |
| resume not testable | test bug: it never crashed anything, and passed `--run`, which `resume` does not define (it is `--run-id`, `cli.py:1164`) | Confirmed `--run-id` is the only option. Injected `raise RuntimeError` at the top of `RunStateStore.prepare_resume` → both `TestCrashResume` tests **failed**, proving the resume path is genuinely entered. Restored → passes. |

All three tests are now non-vacuous. Good work; the docstrings explaining *why* the earlier
diagnosis was wrong are exactly the right artifact to leave behind.

### Blocking

**C-1 — 13 of the 32 e2e tests assert only `result.exit_code`, and the matrix reports them as
covered.**

- Location: `tests/test_e2e_isolation.py`. Measured by AST over the file (script in Evidence):

  | Test | Line | Asserts | Non-exit-code asserts |
  |---|---|---|---|
  | `TestHappyPath::test_isolation_precedence_matrix` | 289 | 1 | **0** |
  | `TestShouldSkipSecondBranch::test_skip_if_outputs_exist_does_not_skip_pending_conflict_task` | 556 | 1 | **0** |
  | `TestDegradation::test_non_strict_mode_still_degrades_on_non_git_via_config_file` | 866 | 1 | **0** |
  | `TestFindingDrivenCases::test_r5_rank_wave_applied_to_dispatch` | 918 | 1 | **0** |
  | `TestFindingDrivenCases::test_s3_untracked_env_aborts_integration` | 1004 | 1 | **0** |
  | `TestConflictLadder::test_full_ladder_with_default_retry_policy` | 1105 | 1 | **0** |
  | `TestConflictLadder::test_ladder_without_mechanical_skips_t1` | 1133 | 1 | **0** |
  | `TestMultiRunPolicy::test_second_run_degrades_to_none_under_require` | 1360 | 1 | **0** |
  | `TestMultiRunPolicy::test_skip_sync_policy_isolates_and_lands` | 1388 | 1 | **0** |
  | `TestMultiRunPolicy::test_off_policy_no_locking` | 1415 | 1 | **0** |
  | `TestSecurityBoundary::test_task_a_cannot_read_task_b_worktree_via_input` | 1740 | 1 | **0** |
  | `TestSecurityBoundary::test_symlink_escape_from_worktree_rejected` | 1778 | 1 | **0** |
  | `TestMultiRepo::test_multi_repo_with_nested_reporef` | 1811 | 1 | **0** |

  The worst individual cases, each verified by AST/string inspection of the function body:
  - `test_second_run_degrades_to_none_under_require` makes **exactly one** `runner.invoke` call.
    There is no second run. The name, the docstring and the `STATUS.md` matrix row all describe a
    behaviour the test does not attempt.
  - `test_symlink_escape_from_worktree_rejected` never calls `os.symlink`; "symlink" appears only in
    the name and docstring.
  - `test_skip_if_outputs_exist_does_not_skip_pending_conflict_task` never parks a task in
    `conflict_resolver`; the string occurs only in the docstring. R-3's *second* branch — the whole
    point of the row — is untested here. (Its own comment admits it: *"For now, just test that the
    flag doesn't cause a spurious skip."*)
  - `test_full_ladder_with_default_retry_policy` and `test_ladder_without_mechanical_skips_t1` each
    define a **single** task, so no conflict can occur and no ladder tier is ever entered. The
    former asserts `result.exit_code in [0, 1]` — every possible outcome.
  - `test_r6_foreign_worktrees_survive_prune` (957) asserts `exit_code in [0, 2]` and its "foreign
    worktree" is a bare `mkdir`, not a registered git worktree.
  - `test_s3_untracked_env_aborts_integration` asserts `exit_code != 0 or "denylisted" in output`,
    which passes on *any* unrelated failure, and covers neither "names the path", "never committed",
    nor the row's second half ("an already-tracked file is not screened").
- Why it matters: `STATUS.md` publishes these as ✅ matrix rows. A future maintainer refactoring
  `rank_wave`, the workspace-lock policy, or the artifact path guard will see green and ship. This
  is the specific failure mode the ticket's own AC-2 warns about ("a fixture that silently stops
  conflicting would make the whole ladder suite vacuous") applied to the tests themselves.
- Next step: for each row, either (a) strengthen it to assert the named property from `state.json` /
  `run.log` events — the file already demonstrates exactly how, in
  `TestMechanicalResolver::test_union_conflict_resolved_via_union_merge` (11 non-exit assertions,
  structured-event based) — or (b) delete it and mark the matrix row as covered by the pre-existing
  test that already does the job (see W-3; most of these rows *are* covered elsewhere, per the
  coverage sweep below), rather than leaving a weaker duplicate that reads as coverage.

**C-2 — AC-17 (S-4) is delivered in exactly the form the architect amendment replaced.**

- Location: `tests/test_e2e_isolation.py:1740` `TestSecurityBoundary::test_task_a_cannot_read_task_b_worktree_via_input`.
- Observation: AC-17 is explicit — *"in a `max_parallel=2` isolated run, a task A whose spec declares
  an absolute input, an absolute output, and an `AgentSpec.working_dir` under **task B's** worktree
  must fail with a structured `ArtifactPathError` at dispatch — and task B's worktree must be
  provably unmodified afterwards. Three explicit cases (input / output / cwd). Also assert the
  sibling case for a worktree belonging to a **different run**."* The delivered test declares two
  ordinary tasks with no cross-task path of any kind, and asserts `result.exit_code == 0`. Not one
  of the four required cases is present; no `ArtifactPathError` is expected; task B's worktree is
  never hashed or compared. The TASK.md comment records that this criterion was *rewritten* on
  2026-09-07 because the previous one was "too weak".
- What *does* exist (so the property is not unprotected, only unverified at the required altitude):
  `tests/isolation/test_view.py:160` `TestSiblingTaskIsolation::test_as_input` / `test_as_output` /
  `test_as_cwd`, and `:214` `TestCrossRunIsolation::test_absolute_path_under_a_different_runs_worktree_raises`.
  AC-17 explicitly says *"`T-Wk3Nv6` already unit-tests `IsolatedArtifactView` directly … **do not
  re-implement those**. What no ticket covers is the same property through a **real dispatch**."*
  That remains true after this ticket. Note also that `test_view.py::test_as_cwd` resolves task B's
  worktree *directory* through A's view; **no test anywhere** sets `AgentSpec.working_dir` to a
  sibling worktree and observes the failure at dispatch.
- Why it matters: this is the epic's one artifact-containment security boundary, and the amendment
  was written because the unit-level proof was judged insufficient. Shipping a green test that
  proves nothing is worse than shipping the acknowledged gap.
- Next step: build the three dispatch cases. Worktree paths are deterministic
  (`isolation/paths.py::worktree_root` is keyed by run id + task id), so the spec can name task B's
  worktree without racing; gate task B behind a latch so it is still alive when A dispatches, then
  hash task B's worktree before and after. Add the cross-run case with a second run id in the same
  workspace. `test_view.py`'s fixtures give the expected error shape.

**C-3 — AC-5, AC-6 and AC-7 are not delivered at any altitude; AC-4 only partially.**

- Observation, from a sweep of every `integration.*` event emitted by `src/` against every
  assertion in `tests/`: **22 of the 34 emitted `integration.*` events are asserted by zero test
  files**, including all four the ACs name by hand:

  | Event | Test files asserting it | AC that requires it |
  |---|---|---|
  | `integration.resolver_dispatched` | **0** | AC-5 (e2e-3) |
  | `integration.merged` | **0** | AC-5 (e2e-3) |
  | `integration.rerun_dispatched` | **0** | AC-6 (e2e-4) |
  | `integration.verify_failed` / `verify_passed` / `verify_started` | **0** | AC-6 |

  - **AC-5** (e2e-3, T2 LLM resolver via CLI, `cumulative_cost_usd` including the resolver attempt,
    `compute_run_usage_totals` reflecting it): absent. `compute_run_usage_totals` appears only in
    `tests/test_engine.py` and `tests/bench/test_subjects.py`, never in an isolation context.
  - **AC-6** (e2e-4, `semantic` fixture + `verify_command` → `integration.rerun_dispatched` → success
    on the fresh base): absent. Tellingly, the `semantic` kind and `semantic_verify_argv()` that this
    ticket *added to `tests/isolation/conftest.py` for this purpose* are consumed **only** by their
    own fixture-quality test (`test_conflict_fixtures.py:126`). Same for `make_rerere_taught_repo` —
    added, self-tested, never used to drive a rerere replay through the engine.
  - **AC-7** (e2e-5, caps 0 → exit 1, task `failed`, worktree **and** branch retained → manual fix in
    the retained worktree → `ao resume` completes with exit 0): absent. `test_ladder_e2e.py::TestMaxResolverAttemptsCapEndToEnd`
    sets `max_resolver_attempts: 0` but asserts the run **succeeds** via T3 — it is the cap test, not
    the T4-operator-handoff test.
  - **AC-4** (e2e-2) is the one that landed well: `TestMechanicalResolver::test_union_conflict_resolved_via_union_merge`
    asserts the structured `integration.resolved` event with `tier=mechanical`, `resolver=union`,
    `path=f.txt`, both entries in the landed file, and no escalation. This is the standard the rest
    should meet.
- Why it matters: HLD §17.4 marks FR-15 ("log-event assertions in each ladder test; `status.json`
  keys") as ✅ and NFR-4 (crash/idempotency) as ✅ on the strength of this ticket. The T2/T3 event
  contract — the part of the ladder a consumer actually observes — has no assertion anywhere.
- Next step: three tests. AC-6 is nearly free: the `semantic` fixture and verify argv already exist,
  so wire them into a `verify_command` workflow and assert the rerun event. AC-5 can reuse
  `test_engine_conflict_escalation.py`'s `_LadderExecutor` shape at the CLI boundary. AC-7 is the
  only one needing new machinery (retained-worktree hand-fix + `ao resume`).

### Major

**M-1 — AC-1 (the blocking NFR-2 gate) has been redefined, not met.**

- Location: `tests/test_nfr2_regression_gate.py`.
- Observation: the new gate is **genuinely non-vacuous** — I proved all three of its comparison arms
  fire (see Evidence: injecting a uuid into artifact content failed the artifact compare; injecting
  a uuid into the `run.end` event failed the event-sequence compare; constructing `GitRepo` on the
  non-isolated path fired `_MustNotConstruct`). It is a real, well-engineered property and strictly
  better than the shell-out it replaced *for what it measures*. But what it measures is run A vs run
  B **within the current tree**, i.e. "the isolation subsystem is never constructed or observed on
  the non-isolated path". AC-1 asks for something different: *"a documented, reproducible check that
  the pre-epic engine suite passes **unedited** at defaults, with exact before/after pass counts …
  Any edit to a pre-existing engine test is a failure of this gate."* The previous version shelled
  out to `pytest tests/test_engine.py …`; that check was deleted and nothing replaced it. There is
  now **no automated check** that pre-existing tests are unedited — and this ticket itself edits one
  (W-8).
- Why it matters: AC-1 is the epic's one **blocking** gate. Silently narrowing a blocking gate to a
  different (easier) property, without recording the substitution in `STATUS.md`, is the kind of
  change that only surfaces after a regression ships.
- Next step: keep the golden compare (it earns its place — give it a distinct name like
  `test_isolation_subsystem_untouched_on_the_non_isolated_path`), and re-add a separate AC-1 gate:
  either the shell-out with recorded before/after counts, or — better, and cheap — an assertion that
  no file under `tests/` matching the pre-epic engine set differs from its `git show <pre-epic-sha>:`
  content.

**M-2 — the gate missed a live product defect: `tier_counts` (S-5) is serialized and displayed but
never written.**

- Location: `src/agent_orchestrator/models.py:760` (field), `src/agent_orchestrator/runstate.py:169`
  (serialized into `status.json`), `src/agent_orchestrator/cli.py:206/220/271/319` (rendered in
  `ao status`). Grep for any write site — `tier_counts[` — over all of `src/` and `tests/`: **zero
  hits**. The engine only ever sets `ti.tier_reached` (`engine.py:1718`).
- Observation: HLD §17.5's S-5 row requires *"`tier_counts` appears in `status.json` and **increments**
  for a rerere-resolved integration."* The only test showing a non-empty value
  (`tests/test_isolation_models.py:780`) **hand-sets** it on the state object before saving, so it
  proves serialization and nothing else. The rerere-resolution test
  (`tests/isolation/test_integrator.py:473`) asserts `tier_reached` and says nothing about
  `tier_counts`. Result: `ao status` will always render `tiers: -`.
- Why it matters: this is precisely the class of defect this ticket exists to catch, and it is
  observable at the surface the ticket owns (`status.json` keys, FR-15). AC-16 says a §17.5 row
  silently absent is a failure of this gate; this row is absent *and* the underlying feature is
  unimplemented.
- Next step: this ticket does not fix it (correctly — TASK.md forbids production code). Record it in
  `STATUS.md` as a defect routed to the owning ticket (`T-Ib5Qy9`/`T-Rm2Lx7` own the tier
  bookkeeping), and add the failing/xfailed S-5 increment test so it cannot be lost again.

**M-3 — AC-19 (R-12 "measure, don't assume") is a synthetic, self-fulfilling constant.**

- Location: `tests/test_e2e_isolation.py:667-755`.
- Observation: the test plants exactly 50 dirty files of which it *constructs* exactly one to
  collide, then asserts `len(colliding_paths) / 50 == pytest.approx(1/50)`. That equality is forced
  by the fixture; it cannot fail for any product reason and measures nothing. AC-19 asks for
  something concrete and different: *"against a snapshot of the consumer's real dirty-file set
  (~4106 `status --porcelain` entries), compute how often those paths would collide with a realistic
  run's integrated changes, and record the observed collision rate in `STATUS.md` as a named
  first-adoption risk."* This ticket's own `STATUS.md` "Next actions" still lists it as outstanding.
- To be clear about what *is* good here: everything around the tautology is strong — the test proves
  `_sync_checkout`'s dirty×changed intersection semantics, asserts `reason == "dirty_checkout"`,
  `colliding_paths == ["a.txt"]`, the presence of a `hint`, and that the dirty file was left
  byte-identical. Only the "measurement" is hollow.
- Next step: drop the tautological assertion (keep the rest). Do the measurement offline against a
  captured `git status --porcelain` snapshot from `ao-runner-finplan`, intersect it with the path set
  a representative run integrates, and record the single number in `STATUS.md`. It is a one-off
  script, not a test.

**M-4 — the "no subprocess git call" structural guard is defeated by trivial variants, and its
docstring overstates its scope.**

- Location: `tests/isolation/test_security_guards.py:1-30` (docstring), `:47` (`_iter_isolation_files`),
  `:74` (`_SubprocessGitCallVisitor`).
- Observation, by mutation (each: append the snippet to `isolation/escalation.py`, run, restore):

  | Injected into `isolation/escalation.py` | Guard result |
  |---|---|
  | `import subprocess` + `subprocess.run(["git", "status"])` | **caught** ✅ |
  | `import subprocess as _sp` + `_sp.run(["git", "status"])` | **passed** ❌ |
  | `from subprocess import run as _r` + `_r(["git", "status"])` | **passed** ❌ |
  | `argv = ["git","push","--force"]` + `subprocess.run(argv)` | **passed** ❌ |
  | `subprocess.run(["git","status"])` in **`engine.py`** (outside `isolation/`) | **passed** ❌ |

  The visitor requires `func.value.id == "subprocess"` and a literal `"git"` at `argv[0]`, so an
  import alias, a `from`-import, or a one-line variable defeats it. Separately, the module docstring's
  first bullet claims *"No `subprocess` GIT call anywhere under `src/agent_orchestrator/`"*, but
  `_iter_isolation_files()` sweeps only `isolation/` — the two are contradictory within the same
  file (the helper's own docstring explains the narrowing, which makes the top-level claim a
  documentation bug rather than an oversight).
- The file's own `test_the_guard_itself_is_not_vacuous` (`:105`) does not catch this: it only proves
  the visitor matches the one fixture pattern it was written against, which is a much weaker claim
  than the "a future edit cannot quietly reuse the unguarded primitive" purpose stated for it.
- For contrast, the sibling guard is sound: injecting `store.resolve_unchecked("x")` into `engine.py`
  **failed** the test with `{'engine.py': [3834]}`. That one does what it says.
- Why it matters: the guard's stated job is to stop a future edit from bypassing `GitRepo`'s
  forbidden-verb / timeout / hook-scrubbing guarantees. As written it stops only the most literal
  spelling, while reading in `STATUS.md` as a structural guarantee.
- Next step: resolve the alias problem by tracking `import subprocess as X` / `from subprocess import
  run` bindings in the visitor (a `ast.Import`/`ast.ImportFrom` pass, ~15 lines), or invert the guard
  — assert the set of modules that import `subprocess` at all under `isolation/` equals
  `{git.py, resolvers.py, integrator.py}`, which is alias-proof and simpler. Either way, fix the
  docstring to state the `isolation/`-only scope.

**M-5 — AC-3's two distinguishing requirements (concurrency proof, teardown proof) are absent.**

- Location: `tests/test_e2e_isolation.py:152` `TestHappyPath::test_three_disjoint_tasks_land_isolated`.
- Observation: AC-3 asks for *"three worktrees existed **concurrently** (via a gated executor, not
  timing) … no worktree survives; `git worktree list` shows only the main checkout."* The delivered
  test asserts the three files landed on the integration branch and exits — nothing about
  concurrency, nothing about teardown. Grep across all five reviewed files: no `threading.Barrier`,
  `threading.Event`, `Semaphore` or latch of any kind (the only `threading.` uses are the NFR-3
  thread-identity recorder); no `git worktree list` assertion anywhere in the reviewed files (the
  existing ones live in `tests/isolation/test_worktrees.py`). The ticket's own **Risks** section
  names the gated executor as the required mitigation ("use a gated executor with latches (the
  ADR-0007 T-TNleFt pattern) rather than sleeps or timing assertions").
- Related: `test_no_isolation_flag_disables_isolation` (253) omits AC-8's "no `ao/` refs" assertion
  (grep: no `for-each-ref` or `ao/` ref check in the file), and HLD e2e-6's other two halves —
  `--isolation none` leaving explicitly-`worktree` tasks isolated (fill-in, not clobber), and
  `--no-isolation --isolation worktree` exiting 1 — are not tested here.
- Next step: borrow the latch pattern from ADR-0007's T-TNleFt tests; assert `git worktree list
  --porcelain` on the main repo returns one entry post-run.

**M-6 — three §17.5 rows are uncovered epic-wide and the gate did not report them.**

A full cross-check of all 23 §17.5 rows against the whole `tests/` tree found the matrix in far
better shape than this ticket's own files suggest — R-1a, R-1b, R-2, R-3, R-4, R-6, R-7, R-8, R-9,
R-12, R-19, R-20, R-21, R-22, R-23, S-1, S-2, S-3, S-6 are all genuinely covered, mostly by other
tickets' tests (which is fine — AC-16 asks for present-and-passing, not present-here). The
exceptions:

| Row | Status | Detail |
|---|---|---|
| **S-5** | increment half uncovered **and** unimplemented | M-2 above |
| **S-7** | `worktree.retention_high` half uncovered | `grep -rn "retention" tests/` → **0 hits**, against implemented production code at `engine.py:391` (the once-per-run latch), `:2686` (`_warn_if_retention_high`), `:2704` (emit), call sites `:1714`/`:1793`. Neither the threshold nor the fires-once latch is tested anywhere. (The row's other half — verify-failure storm trips a breaker and HALTs — *is* covered, `tests/test_engine_isolation.py:1197`.) |
| **R-5** | live-dispatch half uncovered | The pure function is thoroughly covered (`tests/test_overlap_ranking.py`). Neither live-dispatch test does what the row demands ("assert the dispatched set, not the pure function"): `tests/test_engine_isolation.py:665` asserts only `state.status == "succeeded"` and `set(state.tasks) == {a,b,c}` while its docstring claims it asserts "which two of three actually ran concurrently"; this ticket's `test_r5_rank_wave_applied_to_dispatch` asserts only `exit_code == 0` and never even sets `overlap_preference: soft`. |

AC-16 is explicit that a row silently absent is a failure of this gate. These three are absent and
unreported.

- Next step: record all three in `STATUS.md` with owning tickets named (AC-16 permits deferral *with
  an owner*, not silence). S-7's retention test is cheap and belongs in this ticket.

**M-7 — AC-13/AC-18 (NFR-3) is half-delivered in this ticket, though the epic covers the rest.**

- Location: `tests/test_e2e_isolation.py:602` `TestThreadSafety::test_runstate_never_mutated_on_worker_thread`.
- Observation: the test wraps `save()` only — AC-13 says *"if `RunState` is **mutated** or `save` is
  called from any thread other than the main thread"*; the mutation half is unasserted. It uses
  `max_parallel=2`, where AC-13 and AC-18 both specify `max_parallel=3`. And it has no control
  assertion that a worker thread was ever used, so a future regression to serial dispatch would make
  it silently vacuous — I verified empirically that worker threads *are* used today (a probe under
  the identical fixture shape recorded dispatches on `ThreadPoolExecutor-0_0` and `-0_1`), so this is
  a durability gap, not a live one.
- Mitigating: AC-18's static half **is** covered, in the sibling ticket's file —
  `tests/isolation/test_integrator.py:1453` `test_integrator_holds_no_run_state` and `:1458`
  `test_module_never_imports_run_state_or_calls_save` (AST-based). And
  `tests/test_engine_isolation.py:1292` already covers the save-on-worker-thread property, making
  this e2e test a weaker duplicate at a different altitude.
- Next step: raise to `max_parallel=3`; add a control assertion (record executor dispatch thread ids,
  assert at least one is not the main thread); wrap a mutation seam as well as `save`.

### Warnings

**W-1 — test names and docstrings assert what the bodies do not.** Beyond C-1's list: the
`STATUS.md` matrix, the test names, and the docstrings form three layers of claim over bodies that
check an exit code. `test_second_run_degrades_to_none_under_require` is the sharpest example (one
`runner.invoke`). Renaming is not the fix — the tests should do what they say — but any row that is
downgraded to a smoke test must be renamed to say so (`test_..._smoke`).

**W-2 — `tests/isolation/test_ladder_e2e.py` is named `_e2e` but drives the internal
`Orchestrator` API.** CLAUDE.md is explicit that e2e means "from as outer a boundary as possible
(e.g. invoking the CLI via `CliRunner`, not calling internal Python APIs directly)". The file has no
`CliRunner` import; it constructs `Orchestrator(...)` directly. Its *content* is good integration
testing and both cases are non-vacuous (proved by mutation — see Evidence), and the ladder ACs it
partially serves are specified as e2e cases, so the altitude matters. Either move it to the CLI
boundary or rename it (`test_ladder_integration.py`) so the file name stops implying a boundary it
does not drive. For contrast, `tests/test_e2e_isolation.py` is genuinely at the boundary: 33
`runner.invoke` calls across `run` (30), `resume` (2) and `prune` (1) — item 3 of the brief is
satisfied there.

**W-3 — meaningful duplication, at lower quality than the original.**
- `TestMechanicalResolver::test_union_conflict_resolved_via_union_merge` (325) and
  `TestConflictLadder::test_t1_mechanical_union_resolves_cleanly` (1042) are near-identical union
  scenarios differing only in assertion style. Keep one.
- `test_r6_foreign_worktrees_survive_prune` (957) is a strictly weaker duplicate of the pre-existing
  `tests/test_e2e_cli_prune_worktrees.py:222 TestPruneScopedNeverTouchesForeignWorktree::test_foreign_worktree_survives_prune_and_worktrees_only`,
  which uses a real registered worktree rather than a bare `mkdir`.
- `TestHappyPath::test_three_disjoint_tasks_land_isolated` overlaps
  `tests/test_e2e_cli_isolation.py:141 TestIsolatedRunLands::test_two_disjoint_tasks_plus_a_dependent_all_land`;
  the new one earns its place only if it adds AC-3's concurrency and teardown proofs (M-5).

**W-4 — `test_conflict_fixtures.py`'s module docstring is factually wrong.** It claims *"These tests
are **deterministic unit tests** (no real git subprocess, everything pre-staged), so they're fast"*.
Every test in the file shells out to real `git` (`git checkout`, `git rebase`, `git merge-base`,
`git rev-list`). Relatedly, `TestConflictFixtureDeterminism::test_conflict_repo_fixture_is_idempotent`
(300) does not test idempotency — it counts commits on `ours`/`theirs`. Fix the docstring; rename
the test to `test_each_branch_has_exactly_one_commit`.

**W-5 — `tests/test_e2e_isolation.py` lacks the git-env isolation fixture its siblings have.**
`tests/isolation/conftest.py` and `tests/isolation/test_ladder_e2e.py:66` both clear
`GIT_CONFIG_GLOBAL`, `GIT_CONFIG_SYSTEM` and `XDG_STATE_HOME` in an autouse fixture; the e2e file
relies solely on per-invoke `_env()` (which redirects `HOME` but cannot override an ambient
`GIT_CONFIG_GLOBAL`). HLD §17.2's determinism rules require the former. I tested this rather than
assuming: all 60 tests still pass under a hostile `GIT_CONFIG_GLOBAL`
(`merge.conflictstyle=diff3`, `rerere.enabled=true`, `core.autocrlf=true`, `init.defaultBranch=trunk`),
so there is no live flake — but the guard is missing and `rerere.enabled=true` in particular is the
kind of developer setting that would change ladder outcomes once AC-6's rerere path is wired up.

**W-6 — the S-1 non-vacuity check can pass for the wrong reason.**
`tests/test_e2e_isolation.py:461-470`: the primary assertion is inside `if sentinel_dir.exists():`,
so it is skipped in the very case it guards; and the non-vacuity proof is
`assert result.returncode == 1 or sentinel_dir.exists()`, an OR whose left arm is satisfied by any
failing raw commit. Make it unconditional: assert the sentinel dir is absent (or empty) after the
run, then assert it is non-empty after the raw commit. (`tests/isolation/test_git.py:1230`
already does exactly this properly — mirror it.)

**W-7 — fragile monkeypatch idiom in the NFR-3 test.** `tests/test_e2e_isolation.py:629-633` patches
`RunStateStore.__init__` to reassign `self.__class__ = _RecordingStore` after construction. It works,
but it mutates the type of every `RunStateStore` in the process and will break silently under
`__slots__` or a subclass. Prefer `monkeypatch.setattr(RunStateStore, "save", wrapper)`.

**W-8 — the shared-fixture change forced an edit to another ticket's test file.** Adding `"lock"` to
`CONFLICT_KINDS` (`tests/isolation/conftest.py:33`) required editing
`tests/isolation/test_resolvers.py` (its `test_all_conflict_kinds_are_covered` guard and the
`test_kind_outcome` parametrisation) — a file not listed in this ticket's "Files you own". The edit
itself is minimal, correct and well-commented, and the blast radius is clean (I ran all seven
importers of `CONFLICT_KINDS`/`make_conflict_repo`: **245 passed**). But TASK.md's ownership boundary
and AC-1's "any edit to a pre-existing test" language mean it must be declared in `STATUS.md` with
the reason, not left to be discovered in review. Consider whether a separate `MECHANICAL_KINDS`
constant would have avoided the coupling.

### Nits

**N-1 — cross-module conftest import contradicts the convention the sibling file states.**
`tests/test_e2e_isolation.py:32` does `from tests.isolation.conftest import make_hooked_repo`, while
`tests/isolation/test_ladder_e2e.py`'s own docstring says *"never cross-import fixture helpers
between test modules"* and duplicates its helpers instead. Pick one convention for the epic.

**N-2 — `STATUS.md` is stale in ways that will mislead.** It reports 35 + 3 + 11 tests and "40
passed, 3 xfailed"; the actual deliverable is 32 + 4 + 15 + 2 + 7 = **60 passed, 0 xfailed**. It
reports "⚠️ 6 E501 line-too-long"; `ruff check` and `ruff format --check` are both **clean** on all
six files. It does not mention `test_ladder_e2e.py` or `test_security_guards.py` at all. (Flagged
per the brief's instruction not to edit it — but it is the ticket's evidence log and AC-1/AC-15/AC-16
all discharge through it.)

**N-3 — pre-existing, out of scope, worth an epic follow-up: `WorkflowSpec` silently accepts unknown
top-level keys.** This is the root cause of the `isolation.strict` xfail (C-0): the original test's
`workflow["isolation"] = {"strict": True}` was accepted and ignored. I confirmed
`WorkflowSpec.model_validate({..., "isolation": {"strict": True}})` succeeds. Against CLAUDE.md's
"structured specs validated against a schema", a typo'd or misplaced spec block failing open is a
real hazard — the same mistake in a consumer workflow would silently disable an intended setting.
Suggest `model_config = ConfigDict(extra="forbid")` on the spec models, routed to whichever ticket
owns `models.py`.

---

## What I verified as sound (no action needed)

- **NFR-2 golden gate is not a tautology.** All three comparison arms proved live by mutation
  (artifact bytes, event sequence, raise-on-construct stubs). The `_normalize`/`_normalize_paths`
  helpers strip only timestamps and each run's own tmp root; `run_id` is fixed-clock-derived and
  therefore genuinely compared. `test_nfr2_gate_documents_which_names_engine_binds` is a good
  anti-rot guard. Scope, not quality, is the M-1 issue.
- **`resolve_unchecked` containment guard (AC-11a) works** — proved by injecting a caller into
  `engine.py`. The `test_isolation_view_py_does_call_resolve_unchecked` staleness check is the right
  companion.
- **Both `test_ladder_e2e.py` cases are non-vacuous** — removing the cap check
  (`escalation.py:117`) failed `TestMaxResolverAttemptsCapEndToEnd`; removing the ladder-membership
  check (`escalation.py:115`) failed `TestLadderWithoutMechanicalOrLlm`. Each mutation flipped
  exactly one test, so they are independently targeted. The `_HistoryExecutor` /
  `_resolver_dispatches` helpers and the race-order-agnostic winner/loser derivation are the right
  pattern for this suite.
- **Strong tests worth keeping as the house style:** `TestMechanicalResolver` (11 non-exit
  assertions on structured events), `TestCheckoutSync`'s four cases (whole-tree hashing via
  `_hash_user_files` to prove a refused sync changed nothing; forced-off rename detection),
  `TestCrashResume`'s two cases (real crash simulation, `worktree.branch_reattached` event
  assertion), `TestMissingOutputs`, `TestDegradation`'s strict/non-strict pair (the non-strict
  counterpart is exactly the right control).
- **Conflict fixtures are real.** Every kind produces its intended git outcome, the `semantic` kind
  correctly produces *no* conflict, and the `rerere` fixture correctly proves both directions
  (explicit `-c rerere.enabled=false` for the baseline — a subtle and correct detail, since a
  populated `rr-cache` enables rerere by default). Keeping `semantic` out of `CONFLICT_KINDS` to
  protect `test_integrator.py`'s `_CONFLICTING_KINDS` assumption is a good call, well documented.
- **Item 6 (no smuggled product changes): clean.** At the time of review `git diff HEAD -- src/` was
  empty; every `src/` change on this branch belonged to T-Lr6Ka3's commit `8a3235f`. All my mutation
  experiments were restored and byte-verified against pre-mutation copies before any other agent
  began work. (`src/agent_orchestrator/engine.py` and `cli.py` acquired uncommitted edits later in
  the session from a concurrent **T-Cx4Jf1 Part B** developer agent — those are not attributable to
  T-Ee3Mn8, and the five reviewed files still pass 60/60 alongside them.)
- **Item 5 (flakiness): clean.** 5 runs, 3 orderings, all 60 passing (see Evidence). No worktree,
  state-dir or process leakage: `git worktree list` shows only the main checkout; per-file
  before/after counts of `~/.local/state/ao/worktrees` and `.../runlocks` show **delta 0** for all
  five files (the 524 pre-existing entries come from the running `ao service` daemon, not these
  tests); no stray processes.
- **Item 4 (resolver containment, S-2): present, correctly not duplicated.** The brief flagged this
  as possibly missing; it is covered at
  `tests/test_engine_conflict_escalation.py:410-415` (`t1_ctx.agent.disallowed_tools == []`,
  `"WebFetch"/"WebSearch" in t2_ctx.agent.disallowed_tools`, `t2_ctx.env["GIT_TERMINAL_PROMPT"] ==
  "0"`, `t2_ctx.env["GIT_ASKPASS"] == "/bin/false"`), with the negative case at `:698` and unit
  coverage at `tests/isolation/test_escalation.py:312`. `test_ladder_e2e.py`'s docstring explicitly
  identifies these as already-covered and declines to duplicate them — the right call, clearly
  documented. `test_security_guards.py::TestPlantedSecretPassThrough` adds real value on top by
  proving the ADR-0005 pass-through through a genuine `ClaudeCliExecutor` `Popen` call site.

---

## Evidence

All commands run from `/usr/avadhoot/mounted/agent-orchestrator` with `./.venv/bin/…`.

**Baseline / flakiness** — 5 runs of the five files:
`-q -p no:cacheprovider` (random order): **60 passed** (4.55 s, 6.86 s);
`-p no:randomly`: **60 passed** (6.51 s); per-file in separate processes: 32 / 4 / 15 / 2 / 7 = 60;
under a hostile `GIT_CONFIG_GLOBAL`: **60 passed**. No order dependence observed.

**Mutation experiments** (each: edit `src/`, run, restore from a pre-mutation copy; final
`git diff HEAD -- src/` empty):

| # | Mutation | Expectation | Result |
|---|---|---|---|
| 1 | uuid into `FakeExecutor`'s output content | NFR-2 artifact compare fails | ✅ `artifact output/a.txt diverged between A and B` |
| 2 | uuid field into the `run.end` event | NFR-2 event compare fails | ✅ `run.log event sequence diverged between A and B` |
| 3 | `GitRepo(".")` on the non-isolated path | `_MustNotConstruct` fires | ✅ 2 failed |
| 4 | `subprocess.run(["git","status"])` in `isolation/escalation.py` | git guard trips | ✅ caught |
| 5 | same, via `import subprocess as _sp` | git guard trips | ❌ **passed** (M-4) |
| 6 | same, via `from subprocess import run as _r` | git guard trips | ❌ **passed** (M-4) |
| 7 | same, with a variable argv | git guard trips | ❌ **passed** (M-4) |
| 8 | `subprocess.run(["git","status"])` in `engine.py` | git guard trips | ❌ **passed** (M-4, scope) |
| 9 | `store.resolve_unchecked("x")` in `engine.py` | view guard trips | ✅ `{'engine.py': [3834]}` |
| 10 | R-2 gate → `missing = []` (`engine.py:3334`) | `TestMissingOutputs` fails | ✅ |
| 11 | strict branch → `if False:` (`engine.py:2324`) | `TestDegradation` fails | ✅ |
| 12 | `prepare_resume` → `raise` | `TestCrashResume` fails | ✅ (proves the resume path is entered) |
| 13 | cap check removed (`escalation.py:117`) | `TestMaxResolverAttemptsCapEndToEnd` fails | ✅ (1 failed, 1 passed) |
| 14 | `TIER_LLM in spec.ladder` removed (`escalation.py:115`) | `TestLadderWithoutMechanicalOrLlm` fails | ✅ (1 failed, 1 passed) |

**Static analysis:**
- AST assertion census over `tests/test_e2e_isolation.py` → 32 tests, **13 with zero non-exit-code
  assertions** (C-1 table).
- AST `runner.invoke` count per test → `test_second_run_degrades_to_none_under_require`: **1**.
- Every `integration.*` event emitted in `src/` vs. asserted in `tests/` → **22 of 34 uncovered**.
- `grep -rn "tier_counts\[" src/ tests/` → **0** (M-2). `grep -rn "retention" tests/` → **0** (M-6).
- `WorkflowSpec.model_fields` → no `isolation`; unknown key accepted silently (N-3).

**Regression / blast radius:** `tests/isolation/test_resolvers.py test_integrator.py test_worktrees.py
test_git.py` → **245 passed**. `ruff check` + `ruff format --check` on all six files → clean.
(A full-suite run was deliberately not attempted: a concurrent agent is editing `engine.py`,
`ui/runs.py` and `tests/ui/` for T-Cx4Jf1 Part B.)

**Coverage cross-check:** all 23 HLD §17.5 rows traced against the whole `tests/` tree; results in
M-6. Note that most rows are covered by *other tickets'* tests, which AC-16 permits.

---

## Testing notes

- **What to mock / what must be real:** the ladder and containment properties need *real* dispatch,
  not stubs — C-2's three cases must go through `_prepare_and_maybe_dispatch` so the failure is
  raised by the live `IsolatedArtifactView`, not by a helper. Conversely, AC-5's cost accounting can
  use the existing `_LadderExecutor` fake, and `subprocess.Popen` mocking (as
  `test_security_guards.py` already does) is the right seam for executor-env assertions.
- **Gated executor:** the epic needs one reusable latch-based executor (ADR-0007 T-TNleFt pattern)
  for AC-3's concurrency proof and C-2's "task B still alive when A dispatches". Put it in
  `tests/isolation/conftest.py` next to `make_conflict_repo` rather than growing a third private
  copy.
- **Integration-test the events, not the exit code.** The single highest-value change to this
  deliverable is mechanical: every test that currently asserts `exit_code` should also parse
  `run.log` into events and assert the one event that names its property.
  `TestMechanicalResolver` already shows the idiom; a small module-level helper
  (`_events(run_dir) -> list[dict]`) would remove the copy of that parsing loop that appears in six
  tests.
- **Coverage gaps to close, in priority order:** C-2 (S-4 at dispatch) → C-3 AC-6 (fixtures already
  exist, cheapest win) → M-6 S-7 `retention_high` → C-3 AC-5/AC-7 → M-3 the real R-12 measurement.
- **Deferred with owner (record in `STATUS.md`, do not fix here):** M-2's `tier_counts` write site.

---

## Pre-submit checklist

- [x] Review scope confirmed — the six files named in the brief, plus `tests/isolation/test_resolvers.py`
  which the brief omitted but which this ticket edits (W-8). T-Lr6Ka3's files excluded.
- [x] Three alignment levels checked: **project goals** (CLAUDE.md — the e2e-at-the-outermost-boundary
  rule is honoured in `test_e2e_isolation.py` and violated in name by `test_ladder_e2e.py` (W-2); the
  "specs validated against a schema" principle surfaced N-3); **epic/task goals** (TASK.md ACs 1-20
  and the 2026-09-07 amendments read line by line — AC-1 redefined (M-1), AC-3 partial (M-5), AC-5/6/7
  absent (C-3), AC-13/18 partial (M-7), AC-16 breached (M-6), AC-17 not met (C-2), AC-19 not met
  (M-3); HLD §17.3-17.5 matrix traced row by row); **code-level intent** (docstrings and test names
  checked against bodies by AST — the mismatch is the substance of C-1 and W-1).
- [x] All review dimensions walked. **SOLID/KISS**: helpers (`_setup_workflow`, `_patch_executor`,
  `_hash_user_files`, `_HistoryExecutor`) are well factored and single-purpose — no issue.
  **DRY**: W-3 (three duplications) and the six-fold copy of the `run.log` parsing loop, noted in
  Testing notes. **Magic literals**: checked — the suites use named constants
  (`_FIXED_DT`, `_VOLATILE_KEYS`, `_WALL_TIME_CEILING_SECONDS`, `escalation.RESOLVER_INSTRUCTION_FILENAME`)
  appropriately; the only bare literals are fixture content, which is correct. **Pluggability**: N/A
  for a test-only deliverable, except that the executor seams used (`executors_pkg.DispatchExecutor`,
  `FakeExecutor` subclassing) are the right injection points. **Spec/DAG correctness**: N-3 is the
  one finding. **Determinism/resume safety**: W-5 (missing git-env guard, tested — no live flake),
  M-7 (no worker-thread control assertion), and the fixed clocks/dates are otherwise correctly
  applied throughout. **Errors/logging**: N/A — no production error paths introduced; the tests'
  own failure messages are consistently good (they interpolate `result.output`). **Testability**:
  the deliverable *is* tests; the finding is that 13 of them do not test. **Concurrency/rollout**:
  M-5 (no gated executor), M-7 (`max_parallel=2` not 3), W-8 (shared-fixture blast radius —
  verified clean at 245 passed).
- [x] Every finding cites file:line + observation + why it matters + concrete next step.
- [x] Findings bucketed by actual merge-blocking severity. C-1/C-2/C-3 are Blocking because this
  ticket **is** the verification gate and its output will be read as proof of coverage it does not
  provide — not because of style. M-1..M-7 are real but scoped or partially mitigated elsewhere in
  the epic. W-1..W-8 are should-fix; N-1..N-3 are nits, with N-3 explicitly out of scope.
- [x] Testing notes included: what to mock, what must be real, the shared latch helper, the
  event-assertion idiom, prioritised gaps, and the one item to defer with an owner.
- [x] No source or test file edited; no commit created; `STATUS.md` not touched (per the brief).
  All mutation experiments restored and byte-verified — `git diff HEAD -- src/` is empty.
