# STATUS

- ID: `T-Ib5Qy9-integrator-core`
- Updated At: 2026-09-07
- State: Done
- Owner: developer-agent

## This update
- Ticket created by the architect as part of the `E-Wk9Tz3-task-isolation` design package. Not
  started; no code written.
- **2026-09-07 — Phase-2 review amendment applied.** Folded in: R-20 (ctor settled in favour of this ticket's locked hook-based signature; the HLD was corrected), R-7 (CAS retry scoped to the losing repo, `is_ancestor` short-circuit), R-8 (declared outputs from `TaskIsolation`; the two conflicting "Empty" definitions reconciled), S-3 (auto-commit denylist screen). Estimate unchanged at 3 days.
- Per-finding dispositions: HLD §24 "Review dispositions".

- **2026-09-07 — implementation complete: In Review.** All 18 ACs (incl. the three 2026-09-07
  amendments R-20/R-7/R-8/S-3) implemented across two new modules:
  - `src/agent_orchestrator/isolation/locks.py` — `IntegrationLock(common_dir, *, timeout=...)`:
    process-wide `threading.Lock` (keyed by normalized `common_dir`, shared by every instance in the
    process) + `flock` on a companion `<common_dir>/ao-integration.lock` file, `.acquire(timeout) ->
    bool` (bounded, never raises/hangs), `.release()` (idempotent), `__enter__`/`__exit__`
    convenience form (raises `IntegrationLockTimeoutError`, new in `errors.py`). Not reentrant on one
    instance (raises `RuntimeError`); a second instance for the same key just contends normally and
    times out like any other acquirer.
  - `src/agent_orchestrator/isolation/integrator.py` — `Integrator`, `IntegrationResult`,
    `RunIntegrationSnapshot`, `ResolverHook`/`EscalationHook` protocols. See "Hook points" below for
    exact signatures every downstream ticket consumes.
  - Errors: `IntegrationLockTimeoutError` added to `errors.py` (not off-limits — a shared, generic
    module untouched by any concurrently in-flight ticket).

  **Design decisions beyond the HLD's schematic pseudocode** (the HLD names `integrate(task_iso,
  run_integration, attempt)` illustratively, not as an exhaustive/closed signature; two additions
  were required and are documented here, not filed as interface-change requests against `T-Wk3Nv6`
  since neither touches `TaskIsolation`):
  1. **`agent_id: str` is an explicit keyword-only argument to `integrate()`.** The default commit
     message template's `AO-Agent` trailer (AC-3) needs it, and nothing in `task_iso`/`run_integration`
     carries it (R-20/R-8 deliberately keep `TaskIsolation` free of `TaskSpec`). The caller (dispatching
     this exact agent) already has it trivially.
  2. **`task_integration: TaskIntegrationState` is an explicit 3rd positional argument** (after
     `task_iso`, before `run_integration`... see the exact order in "Hook points" below). `escalate()`
     needs per-task ladder bookkeeping (`resolver_attempts`/`reruns`/etc.) that isn't derivable from
     `task_iso`/`run_integration` alone. `TaskIntegrationState` is a plain value class (not `RunState`,
     no `.save()`), so passing it does not reopen R-20/NFR-3 — the ticket's own test (below) asserts
     the module never imports `RunState` and never calls `.save(`.
  3. **`RunIntegrationSnapshot` (new, local to `integrator.py`) carries only `run_id`, `branch`,
     `run_dir`** — deliberately NOT `heads` (a snapshot's head value could be stale by lock-acquire
     time; the live `git.rev_parse(integration_ref)` read under the lock is what CAS safety actually
     depends on) and not a reuse of `models.RunIntegrationState` itself (keeps `integrator.py` from
     ever importing anything RunState-adjacent).
  4. **The default structural verify check (`git grep -l` / `git diff --check`) reaches
     `GitRepo._run`/`._raise` directly.** `git.py` (frozen, `T-Gt4Pw8`, already merged) ships no public
     `grep`/`diff --check` wrapper and is out of this ticket's ownership to extend. `_run` is still the
     actual S-1 safety choke point (hook suppression, forbidden-command guard, forced env) — this
     reaches it, not around it. Verified empirically against the installed git (not assumed): a real
     `diff --check` failure exits **2**, not 1; `grep -l`'s match output is prefixed `<ref>:<path>`
     (stripped before use). Suggested follow-up (non-blocking): a future ticket could add
     `GitRepo.grep_conflict_markers`/`.diff_check` so this reach-across can be dropped.

  **S-3 denylist matching**: `_matches_denylist` checks both the full worktree-relative path and its
  basename against each glob (`.env` also catches `config/.env`), explicitly documented as a footgun
  backstop, not a secrets scanner, per the ticket's own caution.

  **Tests**: `tests/isolation/test_locks.py` (12 — thread serialization is `threading.Event`-
  synchronized never sleep-based; a REAL second `multiprocessing` process for the blocking-acquire and
  kill-releases-the-lock cases; reentrancy; context-manager timeout; an `os.open` failure path),
  `tests/isolation/test_integrator.py` (41 — happy path single/multi/nested-repo, rebase-clean-with-
  disjoint-hunks, all 5 non-clean `make_conflict_repo` kinds parametrized, empty task (outside-repo
  write, gitignored declared output), CAS win/loss-then-retry/exceed-bound-with-partial-landing
  (multi-repo)/already-landed-short-circuit/retry-hits-a-genuine-conflict, verify structural
  pass/fail (both the grep and the diff-check branch) + `GitError` propagation from each + never-
  `open()`-a-file (AST-checked) + `verify_command` pass/fail/timeout/missing-binary, S-3 denylist
  fail/warn/allow/already-tracked-not-screened/`auto_commit:false`-never-adds, real-second-process
  lock timeout, R-20 no-`RunState` (by construction + AST import/`.save(` check), idempotency,
  `resume_integration` hand-resolve/multi-repo/lock-timeout/verify-failure/CAS-ref-race). 53 new
  tests total.

  **Gates**: `uv run ruff check .` / `ruff format --check .` clean repo-wide. `uv run mypy src`
  unchanged at exactly 4 pre-existing `_version.py` errors. `uv run pytest -q tests/isolation -p
  no:cacheprovider`: **467 passed / 0 failed**. Full suite, run twice per the brief: first run (no
  coverage) **3323 passed / 7 skipped / 0 failed**; second run (with `--cov`, which slows wall time
  and is the likely cause) **1 failed** — `tests/test_wave_scheduler.py::TestParallelDispatchProof::
  test_two_independent_tasks_overlap_at_max_parallel_two`, a timing-sensitive parallel-dispatch test
  in a file this ticket never touches; re-run alone, twice, both green. Coverage:
  `isolation/integrator.py` **95%**, `isolation/locks.py` **100%**; repo TOTAL **95%** (baseline 95%,
  no regression).

  **Hook points published for downstream tickets** (`T-En8Hd4`, `T-Rm2Lx7`, `T-Lr6Ka3`,
  `T-Ac6Vd9`) — exact, current signatures from `src/agent_orchestrator/isolation/integrator.py`:

  ```python
  # Constructor (locked, R-20) + injectable runner/hooks_dir (keyword-only, after the locked args)
  Integrator(
      spec: IntegrationSpec,
      logger: logging.LoggerAdapter,       # use logging_setup.get_run_logger — a bare
                                            # logging.LoggerAdapter's default process() DISCARDS
                                            # extra=, so events never get their event/task_id/at
                                            # fields; get_run_logger's _MergingAdapter fixes this.
      clock: Callable[[], datetime],
      resolver_hook: ResolverHook,         # T-Rm2Lx7 supplies the real one
      escalation_hook: EscalationHook,     # T-Lr6Ka3 supplies the real one
      *,
      runner: isolation.git.Runner | None = None,
      hooks_dir: Path | None = None,
  )

  # T-En8Hd4's call sites. `agent_id` and `task_integration` are additions beyond the HLD's
  # schematic pseudocode — see "Design decisions" above for why.
  integrator.integrate(
      task_iso: worktrees.TaskIsolation,
      run_integration: RunIntegrationSnapshot,   # {run_id, branch, run_dir} -- build fresh per
                                                  # dispatch from RunIntegrationState + the
                                                  # ArtifactStore-resolved run dir; NEVER pass a
                                                  # RunState/RunIntegrationState reference itself.
      task_integration: TaskIntegrationState,    # a value/snapshot, not a live RunState field ref
      attempt: int,
      *, agent_id: str,
  ) -> IntegrationResult

  integrator.resume_integration(
      task_iso: worktrees.TaskIsolation,
      run_integration: RunIntegrationSnapshot,
      task_integration: TaskIntegrationState,
      attempt: int,
  ) -> IntegrationResult
  # NOTE (scope): resume_integration's CAS step does the same is_ancestor already-landed
  # short-circuit as integrate() but does NOT retry-and-restage on a genuine CAS loss -- it
  # returns failed(reason="ref_race") directly. A caller that hits this can simply issue a
  # fresh resume_integration() call.

  @dataclass(frozen=True)
  class IntegrationResult:
      status: Literal["integrated","conflict_resolver","conflict_rerun","failed","empty"]
      tier_reached: ResolverTier | None = None       # "auto"|"mechanical"|"llm"|"rerun"|None
      heads: dict[str, str] = {}                     # repo_key -> new integration head (landed)
      squash: dict[str, str] = {}                     # repo_key -> squash sha (audit; populated
                                                        # even on a conflict/failure for repos
                                                        # already squashed this pass)
      conflicted_paths: list[str] = []                # paths only (NFR-1); also used for the
                                                        # S-3 denylist-hit path
      verify_status: Literal["not_run","passed","failed"] = "not_run"
      reason: str | None = None    # this module assigns: "lock_timeout" | "denylisted_path" |
                                    # "ref_race" | "git_error" | "verify_timeout" |
                                    # "verify_command_error" | "verify_command_failed" --
                                    # escalation_hook assigns its own vocabulary (e.g.
                                    # "verify_failed", "conflict_unresolved") for
                                    # conflict_resolver/conflict_rerun/failed outcomes it decides.
      untracked_outputs: list[str] = []   # declared outputs present, untracked+gitignored
                                           # (GitRepo.ls_files_untracked_ignored) -- COPY-BACK
                                           # to the shared path is the CALLER's job (HLD §11 M5's
                                           # copy_untracked_outputs, engine-side, T-En8Hd4); this
                                           # module only detects and reports the list.

  # T-Rm2Lx7 supplies a callable matching this shape (e.g. a thin `resolvers.resolve_conflicts`
  # wrapping its own plan_resolution + apply_plan) and T-En8Hd4 wires it in as resolver_hook=.
  class ResolverHook(Protocol):
      def __call__(self, conflicted_paths: list[str], *, worktree: str,
                   git: GitRepo, config: ResolverConfig, env: dict[str, str]) -> list[str]: ...
      # Returns the STILL-unresolved paths (empty = fully resolved). Called with the paths git
      # itself reports still conflicted AFTER rerere's own automatic replay -- rerere already ran
      # (RERERE_ARGS on every GitRepo call) by the time this hook is invoked, so it needs no
      # "already_resolved" input of its own.

  # T-Lr6Ka3 supplies a callable matching this shape (its own escalate(ti, spec, cause)) and
  # T-En8Hd4 wires it in as escalation_hook=.
  class EscalationHook(Protocol):
      def __call__(self, task_integration: TaskIntegrationState, spec: IntegrationSpec,
                   cause: Literal["conflict", "verify"]) -> IntegrationResult: ...
      # `task_integration.conflicted_paths`/`.tier_reached` are populated with THIS pass's
      # observed values before the call. Integrator merges the returned result's squash/
      # conflicted_paths/tier_reached/verify_status fallbacks via `_merge_escalation` -- the hook
      # does not need to know about squash shas or already-landed repos.
  ```

  Events emitted (`extra={"event": ..., "task_id": ..., "at": clock().isoformat(), ...}`):
  `integration.started|squashed|rebased|conflict|resolved|verify_started|verify_passed|
  verify_failed|merged|partial|failed|denylisted_path|empty`. `tier_counts`/`status.json`
  surfacing (S-5) reads `IntegrationResult.tier_reached` — this module does not itself touch
  `RunIntegrationState.tier_counts` (R-20).

  Not done / explicitly out of scope for this ticket (owned elsewhere, consumed as documented):
  the missing-declared-output pre-`integrate()` gate (R-2, `T-En8Hd4`), the untracked-output
  copy-back action itself (`T-En8Hd4`), release()-on-plain-failure (R-23, `T-En8Hd4`),
  `WorkspaceRunLock`/checkout sync (`T-Wl2Bq7`), cost/token accounting on requeue (`T-Ac6Vd9`),
  the real T1/T2/T3 mechanics behind the two injected hooks (`T-Rm2Lx7`/`T-Lr6Ka3`).

  Awaiting review; no commit made per instruction.
  — By: developer-agent · Role: developer · Date: 2026-09-07

- **2026-09-07 — review fix pass (`REVIEW.md`): APPROVE WITH CHANGES addressed, re-submitted.**
  No public signature changed (`Integrator.integrate`/`.resume_integration`, `IntegrationResult`,
  `RunIntegrationSnapshot`, `ResolverHook`, `EscalationHook` all unchanged) — `T-En8Hd4`/`T-Rm2Lx7`
  build against the same hook points published above.

  **C-1 (Blocking, fixed).** `resume_integration()` could silently drop a sibling task's
  already-landed commit in a shared repo: for a repo not mid-rebase it trusted a stale
  pre-round-trip candidate sha, and `update-ref`'s CAS performs no ancestry check, so a fresh
  `expected_old` read would let a stale candidate land over a concurrently-landed sibling commit.
  Fixed by never trusting a captured candidate: extracted `_rebase_onto_and_resolve` (rebase +
  resolver-hook + continue, given an already-known squash sha) out of `_stage_one_repo`, and added
  `_restage_or_escalate` (shared by the CAS-retry loop AND `resume_integration` — the "same logic,
  no duplicate" the review asked for). Mid-rebase repos: after `rebase --continue`, an
  `is_ancestor(head_sha, new_sha)` check detects a stale target and restages against the fresh
  head. Not-mid-rebase repos: ALWAYS restage against the freshly-read head (idempotent/harmless
  when nothing moved, since `commit_tree` is deterministic and the rebase fast path is a no-op
  when `target_head == repo.base`) rather than trusting the branch's current tip. One residual
  edge case documented: a repo the original `integrate()` call never reached at all (an earlier
  repo's conflict returned first) has no `recorded_squash`, so resume freshly re-squashes it —
  `resume_integration`'s locked signature carries no `agent_id`, so that one path's commit-message
  audit trail uses a documented placeholder (`_RESUME_FALLBACK_AGENT_ID`); the landed TREE content
  is unaffected. Tests: `test_resume_never_drops_a_sibling_landed_during_the_t2_window` (single
  repo), `test_resume_multi_repo_only_one_repo_moved_during_t2_window` (multi-repo, only one repo
  moved) — both assert the sibling's commit is a real ancestor of the final head (via
  `git merge-base --is-ancestor`) and the resumed task's own change is present.
  **C-2 (Major, fixed).** The T1 "resolver actually resolves" path had zero coverage. Added
  `TestMechanicalResolution`: `test_resolver_resolves_one_path_leaves_another_for_escalation` (a
  two-file conflict fixture — `make_conflict_repo` only ever shapes one conflicting file — where a
  resolver stub resolves `a.txt` and returns `b.txt` unresolved; asserts the resolution is really
  staged, `tier_reached == "mechanical"`, and the escalation hook receives exactly `["b.txt"]`) and
  `test_rerere_only_resolution_lands_without_ever_calling_the_resolver_hook` (S-5: teaches git
  rerere a resolution via a real rebase, then replays the identical conflict shape through
  `Integrator.integrate`; asserts the injected `resolver_hook` is NEVER called and the result still
  lands at `tier_reached == "mechanical"`).
  **C-3 (COORDINATE → authorized, fixed).** Added `GitRepo.grep_conflict_markers(cwd, ref,
  paths=None) -> list[str]` and `GitRepo.diff_check(cwd, base, head) -> list[str]` to `git.py`
  (through `_run`, `_CONFLICT_MARKER_PATTERNS`/`_DIFF_CHECK_FATAL_THRESHOLD` module constants,
  `-z`-delimited for unicode-path safety) — `integrator.py`'s `_run_verify_structural` now calls
  these public wrappers instead of reaching `GitRepo._run`/`._raise` directly; the two private
  helper methods and their `# noqa: SLF001` suppressions are gone. Added both to
  `test_git.py`'s `call_args` structural sweep (additive only — every other entry unchanged) and a
  new `TestGrepConflictMarkersAndDiffCheck` (8 tests over real git repos, incl. a unicode-named
  file for each wrapper, and a bad-ref `GitError` case for each).
  **C-4 (Major, fixed).** `TaskIntegrationState.model_copy()` (defensive, no `update=`) now runs as
  the first statement in both `integrate()` and `resume_integration()`, so the R-20/NFR-3 guarantee
  holds regardless of what the caller passes, not only by the discipline of every internal read
  already going through `.model_copy(update=...)`.
  **Nits**: fixed the three tautological/dead-code assertions
  (`test_integrator.py`'s `... or True` in two tests, `test_locks.py`'s `True in results.values()
  or all(...)`) and added the `_git_for` cwd-invariant docstring. **Deferred**: "re-derive rather
  than trust a captured value" tightening for the main pass's own verify/CAS reads (not a bug under
  the current control flow — the lock is held continuously start-to-finish there — and the fix
  would touch several call sites beyond the ≤15-line bar; no dedicated ticket filed, noted here for
  a future pass if `T-En8Hd4`'s integration surfaces a real instance).

  **New/changed files**: `isolation/integrator.py` (refactored `_stage_one_repo` into
  `_squash_repo`/`_rebase_onto_and_resolve`/`_restage_or_escalate`; C-1/C-4 fixes; C-3's `_run`
  reach-across removed), `isolation/git.py` (two new public methods + two constants),
  `tests/isolation/test_integrator.py` (+8 tests: 2 C-1, 2 C-2, 4 nit fixes touching existing
  tests), `tests/isolation/test_git.py` (+8 tests, +2 sweep entries), `tests/isolation/
  test_locks.py` (1 nit fix).

  **Gates**: `ruff check .` / `ruff format --check .` clean repo-wide. `mypy src` unchanged at
  exactly 4 pre-existing `_version.py` errors. `tests/isolation -q`, run 3×: **1 failure**, all
  three times, in `tests/isolation/test_view.py::TestResolveUncheckedStructuralGuard::
  test_resolve_unchecked_referenced_only_in_its_two_sanctioned_files` — a structural sweep that
  fails because `engine.py` currently references `resolve_unchecked` outside its sanctioned
  callers; `engine.py` is mid-edit by `T-En8Hd4` (per the coordinator's own advisory) and is not a
  file this ticket touches or owns — not investigated further. Full suite, run twice: first run
  **3335 passed / 7 skipped / 0 failed** (fully green); second run **4 failed**, all in
  `tests/test_engine_isolation.py` (same root cause — `T-En8Hd4`'s in-progress `engine.py`).
  Coverage: `isolation/integrator.py` 97% (up from 95%), `isolation/locks.py` 100%,
  `isolation/git.py` 97%; repo TOTAL 94% (one run) / 95% (another) depending on `engine.py`'s
  in-flight state at capture time — both within noise of the 95% baseline, no regression
  attributable to this ticket's own files.
  — By: developer-agent · Role: developer · Date: 2026-09-07

## Evidence
- Design: [`docs-md/task-isolation-hld.md`](../../../../docs-md/task-isolation-hld.md) (see the
  module section named in `TASK.md`) and
  [`ADR-0013`](../../../../docs-md/adr/ADR-0013-per-task-git-isolation-and-rebase-integration.md).
- New source: `src/agent_orchestrator/isolation/locks.py`,
  `src/agent_orchestrator/isolation/integrator.py`.
- Edited source: `src/agent_orchestrator/errors.py` (added `IntegrationLockTimeoutError`).
- New tests: `tests/isolation/test_locks.py`, `tests/isolation/test_integrator.py`.

## Risks / Blockers
- See `TASK.md` > Risks. Blocked only by the dependencies listed in `TASK.md` > Dependencies — both
  (`T-Gt4Pw8`, `T-Wk3Nv6`) were available in the working tree and consumed as documented (imported,
  never re-derived or edited). `T-Wk3Nv6`'s own review found two real (untested) defects in
  `worktrees.py`/`paths.py` (`sanitize_ref_component` long-id collision, `ensure()`'s reuse branch)
  unrelated to this ticket's own scope — carried as a pre-existing risk in the code this ticket
  builds on, not introduced by it.
- Cross-repo landing is explicitly non-atomic (documented limitation, `integration.partial` event +
  `reason="ref_race"`/partial `heads`), matching ADR-0013's accepted Consequences.
- S-3's denylist screen is a footgun backstop, not a security control — documented in code comments
  and here so nobody later relies on it as one.

## Next actions
1. Reviewer: check the "Design decisions beyond the HLD's schematic pseudocode" list above,
   especially #2 (`task_integration` as an explicit 3rd argument) and #4 (the `GitRepo._run`
   reach-across for the structural verify check).
2. `T-En8Hd4` wires `Integrator`/`IntegrationLock` into the worker path per the "Hook points"
   signatures above.
3. `T-Rm2Lx7`/`T-Lr6Ka3` implement `resolvers.py`/`escalation.py` against the published
   `ResolverHook`/`EscalationHook` protocol shapes — no changes to `integrator.py` needed.

---
- By: reviewer-agent · Role: reviewer · Date: 2026-09-07 · Comment: Full review filed —
  `REVIEW.md`. Verdict **APPROVE WITH CHANGES**. Gates independently re-verified (not trusted from
  this doc): ruff/format clean, mypy unchanged at 4 pre-existing `_version.py` errors, `tests/isolation/
  test_integrator.py`+`test_locks.py` green 3× (53 passed each run, no flakes), full `tests/isolation`
  467 passed, coverage matches (integrator.py 95%, locks.py 100%). The three design decisions beyond
  the HLD's schematic pseudocode (`agent_id`, `task_integration`, a `heads`-less `RunIntegrationSnapshot`)
  are all judged **justified, not R-20 violations** — tagged `COORDINATE` only so `T-En8Hd4`/`T-Lr6Ka3`
  build against this ticket's published hook-points block rather than the HLD's illustrative pseudocode.
  **Blocking (C-1, must-fix before merge)**: `resume_integration()` releases every repo's lock across
  the T2 (LLM-resolver) round trip, then on resume trusts the *stale* pre-round-trip candidate sha for
  any repo that isn't itself mid-rebase, re-validating only `expected_old` (read fresh) — since
  `update-ref`'s CAS is pointer-equality only (empirically confirmed, no ancestry check), a sibling
  task that lands into the same repo during that window gets silently dropped from history on resume.
  Fix is self-contained to `integrator.py` (re-stage via the already-durable `recorded_squash` when the
  current head has moved) and mirrors logic the CAS-retry path already has. **Should-fix (C-2)**: the
  T1 "resolver/rerere actually resolves the conflict" path (`_stage_one_repo:897-918`) is completely
  uncovered (confirmed via `--cov-report=term-missing`, lines 904/908-918) despite STATUS.md's test
  list implying broad conflict coverage — every conflict test uses the never-resolves stub.
  `COORDINATE` (non-blocking, follow-up ticket): C-3, the structural verify check's direct
  `git._run`/`._raise` reach-across, matches the same smell `T-Ov9Bt5`'s review flagged as a Major
  (C-1 there) — recommend adding `GitRepo.grep_conflict_markers`/`.diff_check` public wrappers in
  whichever ticket next touches `git.py`. C-4 (defensive `task_integration.model_copy()` at the top of
  both public methods, cheap and self-contained) and several nits (two vacuous `assert X or True`
  test lines, one tautological lock test assertion) round out the minor findings. Full detail,
  locations, evidence and fixes: `REVIEW.md`.

- By: coordinator · Role: manager · Date: 2026-09-07 · Comment: State -> **Done**. Review findings
  in this ticket's `REVIEW.md` were dispositioned by the implementing agent, the gates were re-run
  independently by the coordinator (ruff check/format clean, `mypy src` at exactly the 4 pre-existing
  `_version.py` errors, full suite green with no regression against the pre-epic baseline of 2008
  passed / 7 skipped / 94% coverage), and the work is committed on `ad/task-isolation` under this
  ticket's own commit. Anything still open was re-filed against a named later ticket rather than left
  in this one; see the epic `STATUS.md` rollup for that ticket's entry.
