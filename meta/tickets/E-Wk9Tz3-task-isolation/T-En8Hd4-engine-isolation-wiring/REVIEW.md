# REVIEW: T-En8Hd4-engine-isolation-wiring

- Reviewer: reviewer-agent
- Date: 2026-09-07
- Scope: uncommitted diff only — `src/agent_orchestrator/engine.py`,
  `src/agent_orchestrator/executors/claude_cli.py` (env overlay),
  `src/agent_orchestrator/executors/fake.py` (`repo_writes`, `contexts`),
  `tests/test_engine_isolation.py`, `tests/test_e2e_cli_isolation.py`, and the three ticket-doc
  edits. `T-Rm2Lx7`'s concurrent `isolation/resolvers.py` work excluded per instructions. Everything
  else on the branch (paths/worktrees/view, integrator/locks, models/validation, ranking/hotspots,
  templates) is committed and out of scope.

## Verdict: **APPROVE WITH CHANGES**

Must-fix before merge: **C-1** (Investigation A). Should-fix before the epic's next ticket lands:
**C-2**, **C-3**.

The wiring itself is disciplined, additive-around-the-existing-scheduler engineering exactly as
TASK.md's Risks section demanded — R-19/R-2/R-3/R-5/R-23 are all genuinely fixed, byte-identical
default path is well evidenced (untouched pre-epic gate suite: 197/197 green), and the four
downstream tickets' hook points are real and consistent with what their own TASK.md files expect.
The one hole that matters is real and reachable by the single most natural way to use this feature:
an isolated code-writing task that declares its own source file as its output. That must be fixed
(or explicitly re-scoped by the architect) before merge — not shipped as a "documented limitation,"
because it silently HALTS the run despite the code landing successfully.

---

## Investigation A — declared output inside the isolated repo (MANDATORY)

**Verdict: BLOCKING DEFECT, not an acceptable known limitation.**

### Reproduction (evidence)

Wrote and ran a standalone repro test (`tests/test_investigation_a_tmp.py`, deleted after — `git
status` confirmed clean before/after, see Verification below) reusing this ticket's own fixtures: a
task `outputs=["repo/x.py"]`, `isolation: worktree`, `FakeExecutor(repo_writes={"a": {"core":
{"x.py": "..."}}})` (writes a real tracked file inside the isolated repo, exactly like AC-15's own
test technique). Result:

```
INFO  integration.activated
INFO  worktree.created
INFO  Task started
INFO  integrator.py: integration.started / squashed / rebased / verify_started / verify_passed / merged
ERROR engine.py:1516  Task succeeded but declared outputs missing: ['repo/x.py']
INFO  worktree.removed
INFO  engine.py:1600  integration.merged            <- the NEW settle block's own log line
WARNING engine.py:1668  Task ended with status failed
INFO  integration.sync_ok                            <- run-end sync succeeds, too late
INFO  integration.summary
```

Final state: `state.task_integration["a"].status == "integrated"` (the code genuinely landed on the
integration ref — confirmed independently via `git show <branch>:x.py`), but
`state.tasks["a"].status == "failed"` and `state.status == "failed"` — the whole run halts.

### Exact code path

1. Worker (`_run_and_integrate`, `engine.py:2850`): the R-2 gate (`engine.py:2906`,
   `st.exists(o)` through the isolated view) passes — the file exists in the worktree — so
   `Integrator.integrate()` runs and lands the squash commit on the integration ref. Correct.
2. Main thread (`_settle_completed_task`, `engine.py:1514`): the **pre-existing, untouched**
   missing-outputs check —
   `missing_outputs = [o for o in task.outputs if not self._store.exists(o)]` — resolves `"repo/x.py"`
   against `self._store`, the **shared, un-synced** checkout. No sync has happened for this task yet
   (`_sync_checkout` only runs before a *later* non-isolated dispatch or at run end). The file isn't
   there yet, so `ts.status = "failed"`.
3. The new integration-settle block (`engine.py:1556-1600`) runs next, sees
   `outcome.integration.status == "integrated"`, sets `ti.status = "integrated"`, releases the
   worktree, logs `integration.merged` — but **never resets `ts.status`** back to `"succeeded"`.
4. `engine.py:1736`: `if ts.status not in ("succeeded", "skipped"): state.status = "failed"; return
   SettleResult(signal="halt")` — the run halts.

### Why this is Blocking, not acceptable

- **It is the natural use case, not an edge case.** HLD §7.2's own text says code changes for the
  real consumer "live in `./fin_plan` — inside a repo"; the only reason the consumer never hits this
  is that it happens to always declare `outputs` *outside* the repo for a different reason (manifest
  conventions). Declaring the source file you asked an isolated agent to write as its own `outputs`
  entry is the first thing most spec authors will try.
- **It produces an inconsistent, unrecoverable state**: `ti.status == "integrated"` (code safely
  landed) next to `ts.status == "failed"` and `state.status == "failed"` (run halted) is a genuine
  data-integrity problem, not just a UX rough edge — an operator sees a failed run for work that
  actually shipped, with no automatic path to reconcile it (the task is not retried as "succeeded" on
  `ao resume`: `should_skip`/`_settled_for_dependents` both require `ts.status == "succeeded"`, and
  `_integration_allows_skip` would require `ti.status == "integrated"` AND `should_skip`'s own branch
  1/2 — but branch 1 requires `ts.status == "succeeded"`, false here, and branch 2's `self._store`
  check is the same stale check that just failed).
- **The architect's own R-2 disposition claim is false for this case.** HLD §7.4/§11 M5/§24 all say
  the main-thread check "will re-run and reach the same verdict, so the existing... path is
  byte-identical" — with no caveat. That claim only holds when `task.outputs` live outside every
  isolated repo. HLD §7.4's own table ("Post-integration output check | shared/main root, **after the
  checkout sync**") implies the design *intended* this check to run against a synced state — the
  as-built code runs it immediately, before any sync, which is a deviation from the table's own
  stated ordering, not merely an accepted consequence of it.
  before any sync.
- **The gap is actively hidden from the ticket's own gate suite.** AC-16's own test
  (`TestMissingOutputsGate::test_missing_output_never_lands`) only covers the *genuinely missing*
  case. `tests/test_e2e_cli_isolation.py`'s header (lines 18-21) explicitly documents *why* every
  fixture in that file places outputs outside the repo — "an in-repo declared output would
  (correctly, per R-2's disposition) never be found there yet" — which is the bug, mislabeled as
  correct behavior and then carefully avoided by every test in the suite.

### Recommended minimal fix (does not require a redesign)

In `_settle_completed_task`, once the new integration-settle block has determined
`ti.status == "integrated"` for this dispatch, either:
- (a) skip/override the earlier main-thread missing-outputs verdict for this task (it is now known to
  be stale — the worker's own R-2 gate already proved the outputs existed in the view at the moment
  of landing), or
- (b) re-run the missing-outputs check through the task's isolated view (already available as
  `outcome.task_iso`) or against the just-updated `state.integration.heads` via a scoped sync of only
  this task's own repos, instead of trusting `self._store` before any sync has ever happened.

(a) is the smaller diff and matches the ticket's own "additive branch, don't touch the existing
verbatim body" discipline — set `ts.status = "succeeded"`/`ts.outputs_present = True` inside the
`integrated`/`empty` case of the new switch when `copy_ok` is true, after the pre-existing check has
already (possibly wrongly) marked it failed. Add a regression test mirroring my repro
(`outputs=["repo/x.py"]`, `isolation: worktree`) asserting `ts.status == "succeeded"` and
`state.status == "succeeded"`.

---

## Investigation B — the `--cov` hang (MANDATORY)

**Verdict: not reproducible in 2 full-suite attempts. Not blocking, but flagged for CI monitoring.**

- Attempt 1: `uv run pytest --cov=agent_orchestrator -q -p no:cacheprovider` (full suite, `tests/bench`
  included, `timeout 890`) → **3389 passed, 7 skipped, 0 failed in 147.36s**.
- Attempt 2: identical command, immediately after → **3389 passed, 7 skipped, 0 failed in 148.91s**.
- `tests/bench/test_workspace.py` alone under `--cov` → **16 passed in 1.31s**, no slowdown at all.

Both full-suite timings are close to — a bit above — the developer's claimed pre-branch baseline of
~105s, consistent with genuinely more test volume (600+ new git-subprocess-backed tests in this
epic), not a regression or a hang. I did not need to git-stash/bisect since nothing hung.

I do not dismiss the developer's report — `/proc/<pid>/wchan == do_sys_poll` with non-advancing CPU
time across multiple 5-minute polls is a specific, credible observation, not a guess — but I cannot
confirm a root cause from 2 clean runs. Two structural things are worth hardening regardless of
whether this recurs: (1) `pyproject.toml` has no `[tool.coverage]`/`.coveragerc` at all, so
`coverage.py` runs with its default (non-thread-aware) tracer even though `engine.py` is
`ThreadPoolExecutor`-heavy and this epic adds many concurrent git-subprocess call sites — recommend
`concurrency = ["thread"]` as defense-in-depth; (2) if this recurs in CI, capture a `py-spy dump` or
`faulthandler` stack of the actual blocked thread, not just `wchan`, since `do_sys_poll` alone doesn't
distinguish a real deadlock from an unrelated sandbox/CI resource hiccup. **Do not block this merge
on an unreproduced report**, but don't close the issue either — ask the developer for the exact
command/environment that hung, and re-attempt once more before considering it stale.

---

## Findings

### Blocking

- **C-1** (`engine.py:1514`, `:1556-1600`, `:1736`) — see Investigation A above. Must fix before
  merge, or the architect must explicitly re-scope AC-16/R-2's disposition with the tradeoff stated
  honestly (this makes isolation unusable for any workflow that declares in-repo outputs, which is
  the epic's own primary use case for code tasks) rather than have it ship silently as a "documented
  limitation."

### Major

- **C-2** — AC-13 (cancel/halt during integration drains, `ao resume` completes the run — ADR-0007
  D7) has **zero test coverage**. `grep -rn cancel tests/test_engine_isolation.py
  tests/test_e2e_cli_isolation.py` matches only the header docstring's own claim of coverage (line
  26), not an actual test. STATUS.md claims "All 21 ACs implemented" and the file's own docstring
  lists AC-13 as covered — it is not. By code inspection the underlying ADR-0007 "drain, don't kill"
  mechanism is unmodified (`self._cancel_fn()` is still only checked in the outer loop;
  `_drain_remaining` still awaits every in-flight future), so the invariant likely holds — but this is
  new, disk-resident, stateful machinery (worktrees, refs, per-repo locks) where "likely holds" isn't
  the bar the ticket itself set. Recommend adding the test (cancel while a worker is mid-`integrate()`,
  then resume via a fresh `Orchestrator.run()` on the same `RunState`/workspace and assert it
  completes) before this ticket is considered done, since none of the four downstream tickets own
  this AC either.
- **C-3** — AC-9/NFR-3 ("`_run_and_integrate` performs no `RunState` mutation and no `save`... a test
  asserts this by failing the run if `RunState.model_dump()` changes while a worker holds it") has no
  dedicated regression test. The test file's header conflates AC-9 with AC-16 under one bullet, which
  is not what AC-9 asks for. By direct code inspection the invariant currently holds — `_run_and_integrate`/
  `_run_with_retries` never call `self._runstate.save` or mutate `state.tasks`/`state.task_integration`;
  the one read (`ti.attempts` at `engine.py:2911`) is safely sequenced (dispatch fully precedes the
  worker; the owning task's own settle fully follows it — no cross-thread access to the same `ti`
  object while the worker holds it). But four more tickets (`T-Ac6Vd9`, `T-Wl2Bq7`, `T-Lr6Ka3`,
  `T-Cx4Jf1`) are about to extend exactly this function, and the tripwire this AC exists to install
  for their benefit isn't in place. Recommend adding it now (a `RunState`/store wrapper that asserts
  `threading.current_thread()` is the main thread on every mutation/`save`, wired into one dispatch).

### Warnings

- **W-1** (`engine.py:2306`, `_sync_checkout`) — reaches into `GitRepo._run` (private,
  `# noqa: SLF001`), with an inline comment claiming this "mirrors `isolation.integrator`'s own
  documented precedent for the identical gap (its default verify check reaching `_run` for
  `grep`/`diff --check`)." I checked `isolation/integrator.py:1099-1119` — verify actually calls
  `GitRepo.grep_conflict_markers`/`.diff_check`, proper **public** methods, not `_run` directly. There
  is no such precedent; this is the first caller to reach into `GitRepo`'s private surface. Safety is
  preserved in effect (`_run` is still the S-1 single choke point every public method routes through,
  so `SAFETY_ARGS`/hook suppression still apply), so this is not a security issue — but the
  justification comment is factually wrong and the layering violation is real, driven by the ticket's
  own "don't touch `isolation/*`" file-ownership boundary. Recommend either correcting the comment to
  state the real reason, or — matching the treatment the developer correctly gave
  `isolation_strict`/`isolation_env`'s CLI-wiring gaps — filing an explicit interface-gap note for a
  follow-up ticket to add `GitRepo.merge_ff_only()`.
- **W-2** — Process note, not code: STATUS.md frames the Investigation-A behavior as "the documented,
  reviewed behavior HLD §24/R-2's disposition accepts... for the outputs-outside-repo convention
  specifically." I read HLD §7.4 and §24 directly; neither R-2's writeup nor its disposition row states
  that outside-repo carve-out — the caveat appears to originate from the developer's own STATUS.md
  paraphrase of "byte-identical," not from the architect's actual text, which claims the check "will
  re-run and reach the same verdict" unconditionally. That claim is what's actually wrong (see C-1);
  it isn't a caveat the architect wrote down and the developer is faithfully honoring. Worth a quick
  architect sync so the epic's own disposition record doesn't misstate what was decided.

### Suggestions

- **S-1** — The NFR-2 "byte-identical when nothing is isolated" claim is well evidenced (untouched
  pre-epic gate suite, 197/197 green; by code inspection `_activate_integration`/`WorktreeManager`/
  `Integrator`/`GitRepo` are only ever constructed inside the `iso_mode == ISOLATION_WORKTREE` branch),
  but there's no dedicated unit test that monkeypatches `subprocess.Popen`/`GitRepo` construction to
  assert **zero** git-subprocess activity for a fully non-isolated run — which is what this review's
  brief specifically asked to confirm structurally, not just behaviorally. Cheap to add (e.g. patch
  `GitRepo.__init__` to raise, run a non-isolated workflow, assert success); recommend for whichever
  ticket next touches this area if not this one.

### Checked, no issue found

- **`_sync_checkout` ownership (review-brief challenge #4).** This is correctly AC-12 of *this*
  ticket, not scope creep from `T-Wl2Bq7`: TASK.md's own AC-12 names checkout sync explicitly, and
  `T-Wl2Bq7`'s TASK.md lists `engine.py`'s owned scope there as "the `_activate_integration` lock
  claim, the `_sync_checkout` **diagnostics**" — i.e. it extends the sync this ticket creates, it
  doesn't create it. It is correctly gated off by default (`state.integration.active` is only ever
  True once a task actually isolates). The one real residual risk — two concurrent runs' syncs racing
  on the same physical checkout — is the already-identified, already-ticketed R-4 gap explicitly
  deferred to `T-Wl2Bq7` (HLD §24, "Fixed — policy stated... New owning task
  `T-Wl2Bq7-workspace-run-lock`"), not something introduced silently here. Practical implication worth
  a one-line epic-STATUS note: merging this ticket alone (before `T-Wl2Bq7` lands) leaves that race
  live for any workspace that runs two isolated `ao run`s concurrently.
- **`_copy_untracked_outputs` (challenge #5).** S-3-safe: it only ever copies paths drawn from
  `integ.untracked_outputs`, which is itself derived from `task.outputs` (declared artifacts) by the
  already-shipped `Integrator`, not an arbitrary directory sweep — no `.env`/secret-sweep risk
  distinct from what the workflow author already declared. Both ends go through a proper resolver
  (`IsolatedArtifactView.resolve` / `LocalFsArtifactStore.resolve`), so the path guard isn't bypassed;
  `shutil.copy2(src, dst)` operates on `view.resolve(output)`'s already-symlink-resolved result.
- **`_settle_completed_task` switch — accounting (challenge #2).** No double-counting: `ts.attempts`
  (retry counter) and `ti.attempts` (integration attempt counter) are distinct fields on distinct
  models; the pre-existing, unconditional `ts.cumulative_*` accumulation runs once, before the new
  block, so R-1a's "accumulate before requeue" holds by placement, not by a second accumulation call
  in the new block (verified — the new block never touches `ts.cumulative_*`). `TaskIntegrationState`
  is confirmed written into `status.json` (`TestStatusJsonShape`, passing). Transcript-clobbering on a
  T2/T3 requeue (R-21, `output_dir` still keyed by task id alone) is real but currently unreachable in
  production — the default `_default_escalation_hook` always returns `status="failed"`, never
  `conflict_resolver`/`conflict_rerun` — and is explicitly, correctly deferred to `T-Ac6Vd9` per
  TASK.md's own Requirements Mapping.
- **`_is_barrier`/`_settled_for_dependents` (challenge #3).** No throughput overreach: only a
  *non-isolated* task is forced to barrier while integration is active (`engine.py:1958-1963`) —
  isolated tasks can still run concurrently with each other. `not_taken`/`skipped` predecessors settle
  unconditionally before the integration-status check is even consulted (`engine.py:918-926`),
  matching pre-epic routing/loop semantics; a `not_taken` task never reaches the isolation-setup block
  in the first place, so it can never leave a stray `task_integration` entry.
- **`claude_cli.py` env overlay (challenge #7).** `env=({**os.environ, **ctx.env} if ctx.env else
  None)` preserves NFR-2 (byte-identical `env=None` when nothing is isolated) and only ever adds
  `AO_*`/`isolation.env`-configured entries — no secret-scrubbing concern beyond what already exists
  for the parent process's own environment (which was already inherited pre-epic); S-1's git-hook/
  network hardening is a different module (`isolation/git.py`) and isn't implicated here.
  `isolation.env` is a user-authored config surface, same trust boundary as agent instructions.
- **`fake.py` `repo_writes`/`contexts` (challenge #8).** `grep -rn 'FakeExecutor(' tests/` → 160
  non-isolation call sites, all keyword-argument construction; `repo_writes` is appended at the end of
  `__init__` with a `None` default, so every existing call site is unaffected. Deterministic (plain
  dict-driven file writes, no timing/ordering dependence).
- **Constructor injection points (challenge #6).** `worktree_manager=`/`integrator=`/`resolver_hook=`/
  `escalation_hook=`/`isolation_strict=`/`isolation_env=` all default to production-safe values, are
  documented in both the class docstring pattern (mirrors `budget_manager`/`monitor`) and STATUS.md's
  hook-points section, and the two CLI-wiring gaps (`isolation_strict`/`isolation_env` have no
  `--isolation`/`.ao/config.yaml` plumbing yet) are reported as an explicit interface gap rather than
  silently guessed at — good practice, matches how `T-Cx4Jf1`'s TASK.md already expects to consume
  them.
- **Downstream hook points (four tickets).** Cross-checked STATUS.md's published hook points against
  each ticket's own TASK.md file-ownership/AC text:
  - `T-Ac6Vd9`: needs settle-time accumulation before requeue (present, verified above) and
    `ts.dispatch_cycle` incremented+persisted before dispatch (present — `engine.py`'s
    `ts_pre.dispatch_cycle += 1` runs before the pre-existing `save()` at the end of dispatch prep).
    `_run_with_retries` deliberately has no `cycle` param yet — correctly deferred per TASK.md's own
    "R-1/R-21 moved to T-Ac6Vd9" text.
  - `T-Wl2Bq7`: `_activate_integration`'s first non-degenerate statement is the `GitRepo.version()`
    probe (`engine.py:2147` region) — the lock claim slots in as literally the first line, matching
    the published hook point; `state.integration.workspace_lock_held` is untouched (schema default).
  - `T-Lr6Ka3`: `_run_and_integrate` does not branch on `task_integration[tid].mode` anywhere —
    confirmed by reading the full function body; `_integrate_task(..., resume=True)` exists, is
    plumbed to `integrator.resume_integration`, and is tested (`TestIntegrateTaskResumeHook`).
    `resolver_hook`/`escalation_hook` constructor seams exist and are used.
  - `T-Cx4Jf1`: `cli.py`/`project_config.py` confirmed untouched (`git diff` empty for both); every
    `integration.*`/`worktree.*` event referenced in STATUS.md's hook-points note is present in the
    diff under the names claimed.

---

## Alignment

- **Project goals (CLAUDE.md).** Declarative specs / DAG-first / pluggable / deterministic / resumable
  / observable / safe-by-default: no drift found beyond C-1's resumability hole. Pluggability is
  handled correctly — `worktree_manager`/`integrator`/hooks are injected, defaulting to production
  values, same pattern as `budget_manager`/`monitor`.
- **Epic/task goals.** 20 of 21 ACs are genuinely implemented and match their own text on inspection
  (not just STATUS.md's say-so) — I independently re-derived R-19/R-2/R-3/R-5/R-23's fixes from the
  diff rather than trusting the summary. AC-9 and AC-13 are claimed-done but under-tested (C-2/C-3).
  No scope creep: `models.py`/`spec.py`/`runstate.py`/`cli.py`/`isolation/*` all confirmed untouched
  via `git diff` (empty).
  Ticket estimate note: STATUS.md doesn't record actual time spent against the 3-day estimate; not
  something this review can verify, flagging as a gap for the manager rollup rather than a code issue.
- **Code-level intent.** The one place code and its own comments disagree is C-1's inline claim ("the
  main-thread check... will re-run and reach the same verdict") — demonstrably false for in-repo
  outputs. Everywhere else the code's docstrings/comments accurately describe what the code does.

## Dimension checklist (all walked; nothing else to flag beyond what's listed above)

- SOLID/KISS — checked. Additive-branch discipline held throughout; no new abstraction beyond what
  the HLD specified (`WorkerOutcome`, the settle switch). `_run_and_integrate` doing double duty
  (retries + outputs-gate + integrate) is a reasonable single seam per the ticket's own "ONE thin
  adapter" instruction, not an accidental god-function.
- DRY — checked. No meaningful duplication introduced; `_iso_repo_paths`/`_build_task_env`/
  `_integrate_task` are the correct single-owner extractions for what would otherwise be duplicated
  across call sites.
- Magic literals — checked. `_WORKTREE_RETENTION_WARN_THRESHOLD`, `_INFO_EXCLUDE_ENTRY`, event-name
  strings are all named/constant; no inline numbers/paths found in the diff.
- Pluggable architecture — checked, see Constructor injection points above.
- Spec/DAG correctness — N/A to this ticket (models/spec untouched, confirmed).
- Determinism/resume safety — C-1, C-2 are exactly this dimension's findings; otherwise clock/RNG
  injection unchanged from pre-epic (`self._clock` reused, no new `time`/`random` call sites).
- Errors/logging — checked. One authoritative log per boundary (`integration.*`/`worktree.*` events),
  no duplicate spam observed in the captured Investigation-A log. `_run_with_retries`/
  `_run_and_integrate` don't wrap `self._executor.execute`/`Integrator.integrate` in a defensive
  try/except — confirmed this matches the **pre-existing** convention (`self._executor.execute(ctx)`
  at `engine.py:3037` is likewise unwrapped, unedited by this ticket), so not a new gap.
- Testability — C-2/C-3 are the findings; otherwise dependencies are injectable and the new tests use
  real temp git repos with pinned identities and redirected `HOME`/`AO_STATE_DIR` (no real home dir
  touched).
- Concurrency/rollout — checked; see `_is_barrier`/checkout-sync notes above. Expand-contract for the
  schema was T-Sc7Rm2's concern, not this ticket's.

## Testing notes

- **Mock/inject:** `worktree_manager=`/`integrator=`/`resolver_hook=`/`escalation_hook=` are the right
  seams and are already used well by the existing 52+2 tests (e.g. `_ScriptedIntegrator` for the
  conflict switch).
- **Integration-test:** the missing scenarios are C-1 (in-repo output + real land), C-2 (cancel
  mid-`integrate()` + resume), C-3 (cross-thread mutation tripwire).
- **Coverage gaps:** as above; `engine.py` sits at 96% per both my full-suite run and STATUS.md's
  figure — the misses are pre-existing, unedited defensive branches, consistent with the claim.

## Verification performed

- `uv run ruff check` (5 changed files): all checks passed.
- `uv run ruff format --check` (5 changed files): already formatted.
- `uv run mypy src`: exactly 4 pre-existing `_version.py` errors, as claimed.
- `uv run pytest tests/test_engine.py tests/test_engine_isolation.py tests/test_e2e_cli_isolation.py
  tests/test_e2e_cli_max_parallel.py tests/test_wave_scheduler.py tests/test_dynamic_injection.py
  tests/test_engine_routing.py tests/test_engine_breakers.py tests/test_engine_budget.py
  tests/test_e2e_cli.py -q -p no:cacheprovider --durations=10`: **197 passed**, sub-4s wall time, no
  regressions.
- `uv run pytest --cov=agent_orchestrator -q -p no:cacheprovider` (full suite, `timeout 890`), run
  **twice**: 3389 passed / 7 skipped / 0 failed each time (147.36s, 148.91s) — see Investigation B.
- Investigation A repro test written to `tests/test_investigation_a_tmp.py`, run, evidence captured,
  then deleted; `git status --porcelain=v1 -uall` confirmed identical to the pre-review state (no
  stash was needed — nothing hung, so no stash/pop cycle occurred).
- Final `git status` matches the state at the start of this review exactly (3 tracked-file
  modifications under `meta/tickets/...`, 3 tracked-file modifications under `src/`, 2 new untracked
  test files) — no source/test edits made, no commits created.
