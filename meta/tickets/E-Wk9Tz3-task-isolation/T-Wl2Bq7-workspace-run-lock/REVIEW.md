# REVIEW: T-Wl2Bq7-workspace-run-lock

- Reviewer: reviewer-agent
- Date: 2026-09-07
- Scope: uncommitted diff only — `src/agent_orchestrator/isolation/runlock.py` (new),
  `src/agent_orchestrator/errors.py` (`WorkspaceLockHeldError`),
  `src/agent_orchestrator/isolation/git.py` (`fast_forward_checkout`),
  `src/agent_orchestrator/engine.py` (narrow: `_activate_integration`,
  `_reconcile_integration_on_resume`, `_sync_checkout`, `_RunContext`, `run()`'s `finally`, three
  policy constants), `tests/isolation/test_runlock.py`, `tests/isolation/_runlock_hold_helper.py`,
  `tests/isolation/test_git.py::TestFastForwardCheckout`, `tests/test_engine_workspace_lock_sync.py`,
  `tests/test_engine_isolation.py` (`TestCheckoutSync`'s two updated tests), and this task's own
  ticket docs. `T-Rm2Lx7`'s concurrent `isolation/resolvers.py`/`tests/isolation/test_resolvers.py`
  work is excluded per instructions — confirmed no overlap with the files above (grepped
  `resolvers.py` for `read-tree`/`fast_forward_checkout`/`update-ref`/`merge_ff_only`: none). Nothing
  in this ticket's scope needs a `COORDINATE` tag.

## Verdict: **APPROVE WITH CHANGES**

Must-fix before merge: **C-2** (rename-collision diagnostics misclassification), **C-1**
(mis-attributed justification in `STATUS.md` — corrects the record, not the code). Should-fix soon
(next ticket or a fast-follow): **C-3**, **W-1** through **W-6**.

The mechanics are sound. Investigation A proves `fast_forward_checkout` is safe against real data
loss across every mandated case (collision refusal is atomic — index/tree/HEAD byte-identical, even
in a multi-path partial-collision scenario). Investigation B proves the lock is correct: `flock` is
the sole grant/deny authority (content inspection never overrides a contended lock, so there is no
split-brain double-acquire path), stale reclamation is dead-pid-or-boot-id-mismatch as specified, and
two *real* second-process tests (not threads, no sleep-as-sync) cover the actual race and the actual
crash-then-reclaim path. Gates are all green with the exact counts claimed. The two must-fix items are
both diagnostics/provenance problems, not safety problems — nothing here risks data loss or a leaked
lock.

---

## Investigation A — `fast_forward_checkout` safety (MANDATORY)

Ran all 7 cases against real temp repos (`/tmp/.../scratchpad/ffc_test/{repo,repo2,repo5,repo7}`,
deleted after; nothing under the project tree was touched).

1. **Tracked file modified AND changed by the FF → refuses.** `read-tree -u -m` exits 128
   (`Entry 'a.txt' not uptodate. Cannot merge.`), working tree/index/`HEAD` byte-identical after.
   Confirmed.
2. **Tracked file modified but NOT touched by the FF → succeeds, keeps the local edit.**
   `read-tree` exit 0, `update-ref` exit 0, `HEAD` moves to the new sha, the untouched file's local
   edit survives verbatim. Confirmed.
3. **Untracked file at a path the FF adds → refuses.** `error: Untracked working tree file 'd.txt'
   would be overwritten by merge.`, file preserved, `HEAD` unchanged. Confirmed — but see **W-1**:
   the code's own collision-set computation (`status_porcelain(untracked=False)`) cannot see this
   case, so it reports the vaguer `sync_anomaly`, never naming the path, even though git's own stderr
   names it.
4. **Staged-but-uncommitted change → refuses**, content preserved (`M  a.txt` unchanged), `HEAD`
   unchanged. Confirmed.
5. **Detached HEAD → succeeds**, `HEAD` moves directly (stays detached — `git symbolic-ref -q HEAD`
   still exits 1 after), reflog entry written (`HEAD@{0}`). Confirmed.
6. **Branch ref moved by the user mid-run (non-FF) → refuses.** This is caught *upstream* of
   `fast_forward_checkout` — `_sync_checkout` re-reads `current = git.rev_parse("HEAD")` fresh
   immediately before calling it and gates on `git.is_ancestor(current, target)` first
   (`engine.py` ~2507-2517), reporting `not_fast_forward` without ever invoking
   `fast_forward_checkout`. Confirmed by code reading — **and** by directly exercising `read-tree -u
   -m` with a genuinely non-ancestor `old` in isolation: it does **not** self-refuse — it silently
   applies a nonsensical two-way diff (a file that only existed on the divergent branch gets staged
   for deletion). See **C-3**: this makes the *caller's* `is_ancestor` gate the sole safety net, not
   a property of `fast_forward_checkout` itself, contradicting the docstring's "mirrors exactly what
   `git merge --ff-only` does internally" claim (`merge --ff-only` *does* self-verify ancestry;
   `read-tree -u -m` does not).
7. **Index left consistent after a refusal (atomicity).** Verified with a *multi-path* change where
   one path collides and a sibling path would otherwise cleanly fast-forward: the collision on `a.txt`
   refuses the **whole** operation — `b.txt` is left at its old content too, not partially updated.
   No half-applied `read-tree`. Confirmed.
   Also confirmed: `git update-ref HEAD <new> <old>` follows the symbolic ref to the checked-out
   branch and writes a reflog entry (both cases 2 and 5) — matches the docstring's claim, though the
   reflog message is blank (**S-1**, no `-m` passed).

**Deviation disposition (HLD §12.3's literal `git merge --ff-only` text vs. shipped
`read-tree -u -m` + `update-ref`): APPROVE the code, REJECT the stated justification.**
The mechanism itself is *safer* than the HLD's literal text for the property that matters here (no
merge commit, no `MERGE_HEAD`, verified atomic refusal, resolves the `T-En8Hd4` W-1 interface gap in
a stronger form than W-1 asked for) — recommend keeping it. But `STATUS.md` claims this was "a
deliberate, explicit requirement from this ticket's own assignment ('rebase/merge must not be used
... verify by the recording runner')." That quote is not in `TASK.md` (read in full), not in HLD
§12.3, not in ADR-0013 D5/D8 — and **`TASK.md`'s own pseudocode literally specifies
`ok = git.merge_ff_only(checkout, integration_branch)`**, i.e. the opposite of "must not be used." The
"recording runner" is this ticket's *own* new test fixture (`test_engine_workspace_lock_sync.py:471`,
`_recording_runner`), written to verify the developer's *own* design choice — not an external
requirement the developer verified against. See **C-1**.

## Investigation B — lock correctness (MANDATORY)

- **`flock` is the sole grant/deny authority.** `runlock.py:266-294`: a contended `flock` always
  returns `granted=False` without ever consulting file content; content is read only to *classify* an
  *already-uncontended* acquire as fresh vs. reclaimed. No path lets pid/boot-id content override a
  live `flock` — no split-brain double-acquire is possible. Confirmed by code reading and by
  `TestSecondHolder`/`TestStaleReclamation`.
- **Stale reclamation: dead-pid OR boot-id-mismatch, never requires both.** `_stale_reason`
  (`runlock.py:226-232`) checks pid first (dead → `"dead_pid"`), else boot-id mismatch →
  `"boot_id_mismatch"`. `test_dead_pid_checked_before_boot_id_when_both_could_apply` pins the
  ordering. One residual gap: if a stale lock's pid happens to be reused by an unrelated *live*
  process on the *same* boot (pid-reuse coincidence), `_stale_reason` returns `None` even though
  `reclaimed=True` (the lock was still genuinely reclaimed — `flock` proves the prior holder is gone
  regardless) — diagnostic-only ambiguity, not a correctness gap. See **W-5**.
- **Two real second-process tests, not threads, no sleep-as-sync.**
  `test_real_second_process_is_denied_naming_the_holder` and
  `test_real_second_process_death_lets_the_next_run_reclaim`
  (`test_runlock.py:138-190`) use a real `subprocess.Popen` of `_runlock_hold_helper.py`, synchronized
  by blocking on the child's own `ACQUIRED` stdout line (never a fixed `sleep`), and
  `proc.wait(timeout=5)` after `proc.kill()` to guarantee the OS has actually torn down the fd table
  before asserting reclaim. This is exactly what the AC asked for and is robust under load.
- **`release()` never raises**, verified by three independent `try/except` blocks
  (`flock` unlock / `close` / `unlink`, `runlock.py:296-315`) and
  `test_release_is_never_raising_*`.
- **Held for the run's entire lifetime, released only in `run()`'s `finally`.**
  `engine.py:808-813`. `test_lock_is_released_after_a_successful_run` and
  `test_lock_is_released_even_when_the_run_fails` (an executor that raises `RuntimeError`) both probe
  a fresh acquire afterward — covers the success and the exception exit path. Combined with the real
  SIGKILL test above (OS auto-release), both halves of "released, one way or another, no matter how
  the process ends" are covered.
- **`"off"` acquires nothing.** `test_acquires_nothing_and_warns_once` asserts the lock file never
  gets created at all — matches AC-3's literal wording.
- **`"require"` degrade: exactly one `integration.degraded` warning, holder named, state recorded.**
  `test_second_run_degrades_to_none_with_one_warning_naming_the_holder` /
  `test_strict_turns_the_denial_into_a_run_failure` assert `state.integration.degraded_reason ==
  "workspace_locked:holder-run"`, `workspace_lock_held is False`, and exactly one log record (WARNING
  non-strict / ERROR strict). This is run-level state (`_activate_integration` runs once, latched via
  `ctx.integration_degraded`), so "every task in this run degrades" is a structural consequence, not
  something needing a separate per-task assertion.
- **Resume re-claim.** `test_resume_reclaims_a_stale_lock` covers the successful-reclaim path
  end-to-end (fabricates a leaked lock file with a dead pid, resumes, asserts `resume: True` on the
  reclaimed-event and a clean release at the end). The *denied*-reclaim branch
  (`engine.py:2434-2444`, `state.integration.workspace_lock_held = False` when some other live run
  now holds it) has **no test** — see **W-4**.

---

## Findings

### Blocking

None. Nothing here risks data loss, a leaked lock, or non-deterministic engine behavior on the
default/happy path.

### Major

- **C-1 — `STATUS.md` misattributes the `fast_forward_checkout` design deviation to a non-existent
  requirement.**
  `meta/tickets/.../T-Wl2Bq7-workspace-run-lock/STATUS.md:70` ("a deliberate, explicit requirement
  from this ticket's own assignment ('rebase/merge must not be used ... verify by the recording
  runner')"). Verified against `TASK.md` (full read), HLD §12.3, ADR-0013 D5/D8: no such requirement
  exists anywhere, and `TASK.md`'s own pseudocode literally says `git.merge_ff_only(...)`. The
  "recording runner" is the developer's own new test (`test_engine_workspace_lock_sync.py:471`), used
  to verify their *own* choice, not a citation of an external mandate. **Why it matters**: citing a
  fabricated source to justify an architecture deviation — even a good one — undermines every other
  provenance claim in the same document and sets a bad precedent for how deviations get recorded.
  **Fix**: edit `STATUS.md` (not code) to correctly attribute this as a developer-initiated
  strengthening of the `T-En8Hd4` review's W-1 finding, verified empirically (Investigation A above
  independently confirms it's sound) — not as compliance with an assignment requirement that doesn't
  exist. Recommend `T-Dr5Yq6` also update HLD §12.3's literal `git merge --ff-only` text to describe
  the actual shipped mechanism.

- **C-2 — Rename collisions are misclassified as `sync_anomaly` instead of `dirty_checkout`, never
  naming the actual colliding path.**
  `engine.py:2524-2526` (`incoming = set(git.diff_names(...))`, `dirty = {e.path for e in
  git.status_porcelain(..., untracked=False)}`). `diff_names` runs `git diff --name-only <old>
  <new>` with git's *default* rename detection: for a pure rename `a.txt` → `a2.txt`, this reports
  **only the new path** (`a2.txt`), never the old one. If the operator has a local, uncommitted edit
  at the **old** path (`a.txt`) — a completely ordinary case, since the old path is exactly what they
  were editing before the rename landed upstream — `dirty = {"a.txt"}` and `incoming = {"a2.txt"}`,
  so `colliding = incoming & dirty = {}`. `fast_forward_checkout` still correctly *refuses* (no data
  loss — reproduced empirically: `error: Entry 'a.txt' not uptodate. Cannot merge.`), but the code
  reports the generic `reason="sync_anomaly"` / "the checkout moved during sync ... retry" hint
  instead of `reason="dirty_checkout"` with `colliding_paths=["a.txt"]` and the actual remedy. This
  directly violates AC-9's explicit requirement ("a checkout dirtied on a path the run will integrate
  produces a `sync_failed` naming exactly that path") for a realistic, common case — and it is
  **untested**: `TestSyncCheckoutFastForward` and `TestFastForwardCheckout` have no rename case
  (confirmed by grep — zero hits for "rename" outside the pre-existing `status_porcelain` unit test).
  **Fix**: compute `incoming` in a way that also surfaces the pre-rename path — e.g. call
  `git diff --name-only --no-renames <old> <new>` for *this* purpose specifically (the collision-set
  computation, not the fast-forward itself, which is unaffected either way), so a rename shows up as
  a delete-of-old + add-of-new pair and can be intersected against `dirty` correctly. Add a test
  mirroring the empirical repro above.

- **C-3 — `fast_forward_checkout` does not verify `old` is an ancestor of `new`, contradicting its
  own docstring's parity claim with `git merge --ff-only`.**
  `isolation/git.py`, new `fast_forward_checkout` (docstring: "mirroring exactly what `git merge
  --ff-only`/`git pull --ff-only` do internally for a genuine fast-forward"). This is not accurate:
  `git merge --ff-only` self-verifies ancestry and refuses a non-fast-forward on its own; `git
  read-tree -u -m <old> <new>` does not — it blindly applies a two-way diff between whatever two
  trees it's given. Verified empirically: called with a genuinely divergent `old` (not an ancestor of
  `new`), `read-tree -u -m` **succeeds** (exit 0) and stages a nonsensical deletion of a file that
  only existed on the divergent commit. The only reason this is safe today is that the sole caller,
  `_sync_checkout` (`engine.py:2507`), always re-reads `current` fresh and gates with
  `git.is_ancestor(current, target)` immediately before calling it. **Why it matters**: this is a
  *public* `GitRepo` method; a future caller (this codebase's own precedent is that `GitRepo` methods
  get reused — see `merge_tree_probe`, `diff_check`, etc.) that skips the ancestry check will silently
  corrupt state, and the docstring's false parity claim actively invites that mistake. **Fix**: either
  add an internal `is_ancestor(old, new)` check inside `fast_forward_checkout` itself (one more cheap
  `merge-base` call, makes the primitive safe by construction) or, at minimum, correct the docstring
  to state the precondition as a hard caller obligation instead of implying it's handled internally.

### Warnings

- **W-1 — Untracked-file collisions are never named, only reported as a generic anomaly.**
  `engine.py:2524-2525` builds `dirty` from `status_porcelain(..., untracked=False)`, which by
  construction cannot see an untracked-file collision (Investigation A case 3). The code's own
  comment acknowledges this ("an untracked file the dirty-tracked-only collision set does not
  cover"), so this is a known, accepted gap rather than an oversight — but it does mean R-12's
  "naming the colliding paths" promise is only kept for tracked collisions. **Fix (optional)**: a
  second `status_porcelain(..., untracked=True)` call (or reuse the pre-flight one already computed
  in `_activate_integration`) would let an untracked collision be named too, at the cost of one more
  cheap subprocess call per sync.

- **W-2 — `_pid_alive`/`_read_os_boot_id` are now triplicated.**
  `runlock.py:87-108` duplicates the same pair already duplicated once from `ui/processes.py` into
  `service/supervisor.py:140-166`. The developer's disposition (read `supervisor.py` first, found it
  "not reusable as-is" because it never classifies staleness, only gates on `flock`) is accurate and
  well-evidenced, and `supervisor.py::_pid_alive`'s own docstring does establish local-duplication (not
  cross-module import) as this codebase's precedent for this exact pair — so this is not a
  process violation. But CLAUDE.md's "extract if reused 2+ times" is now at 3 copies. **Recommend**
  extracting both helpers to a new leaf module (e.g. `src/agent_orchestrator/procutil.py`, zero
  internal imports, so no circular-dependency risk from `isolation/` → `service/`/`ui/`) in a
  fast-follow ticket — not in this one, since `ui/processes.py` and `service/supervisor.py` are both
  outside this ticket's declared file-ownership list and touching them now would widen scope beyond
  the "narrow" edit TASK.md asked for.

- **W-3 — The workspace lock can be claimed and then held for the run's entire remaining lifetime
  even when the run never actually activates isolation.**
  `_activate_integration` claims the lock (`engine.py` ~2297-2337) *before* the `unborn_branch` probe
  (`engine.py:2350-2351`, inside the `for repo in repos:` loop that runs right after). If any repo has
  no commits yet, the function degrades via `_degrade("unborn_branch")` — but the lock, if just
  granted, is never released until `run()`'s `finally` at the very end of the run, needlessly denying
  a concurrent second run's *real* isolation attempt for the whole time, even though this run isn't
  using isolation or syncing anything. This mirrors `TASK.md`'s own pseudocode ordering (claim lock,
  *then* "existing git probe + ref creation"), so it's a design-level gap inherited from the ticket,
  not an implementation slip — every other degrade check (`git_unavailable_or_old`, `no_git_repos`,
  `unsafe_state_dir`) already runs *before* the lock claim, so only the rare unborn-HEAD case is
  affected. **Recommend**: flag for `T-Dr5Yq6`/an HLD note — move the unborn-branch probe before the
  lock claim, or release on this specific degrade path.

- **W-4 — The resume-denied-reclaim branch is untested.**
  `engine.py:2434-2444` (`else: state.integration.workspace_lock_held = False`, `resume: True`) has no
  covering test — `TestResumeTakeover` only exercises the successful-reclaim path. **Fix**: add a test
  that holds the lock with a second live `WorkspaceRunLock` at resume time and asserts the resumed run
  proceeds with `workspace_lock_held is False` and `_sync_checkout` no-ops for the rest of that run.

- **W-5 — `stale_reason` can be `None` on a `reclaimed=True` claim.**
  `runlock.py:226-232`: if a stale lock's payload happens to have a live-but-reused pid on the same
  boot id (rare pid-reuse coincidence), `_stale_reason` returns `None` even though the acquire is
  provably a reclaim (an uncontended `flock` proves the prior holder is gone). Diagnostic-only — the
  grant/deny decision is unaffected — but worth a one-line docstring note acknowledging the gap so a
  future reader doesn't treat `stale_reason: None` + `reclaimed: True` as a contradiction to debug.

- **W-6 — Shipped `WorkspaceRunLock` interface silently differs from `TASK.md`'s "locked" interface,
  undisclosed.**
  `TASK.md`'s "Schemas / Interface Notes" locks `WorkspaceRunLock(workspace_root)` +
  `.try_acquire(run_id, policy) -> Claim`. Shipped: `WorkspaceRunLock(workspace_root, run_id, ...)` +
  `.acquire()` (no args; policy interpretation moved entirely into `engine.py`, keeping the lock class
  policy-agnostic). This is arguably a *better* separation of concerns than the pseudocode's proposal,
  and no downstream ticket depends on the literal signature (the Handoff Boundary only publishes the
  lock file path and the degrade-reason string). Unlike the `fast_forward_checkout` deviation, this
  one isn't called out anywhere in `STATUS.md`. **Fix**: add one sentence to `STATUS.md` noting the
  rename/signature change and why, for the record.

### Suggestions

- **S-1** — `git update-ref HEAD <new> <old>` (`git.py`, `fast_forward_checkout`) never passes `-m`,
  so every sync's reflog entry is blank (confirmed: `HEAD@{0}: ` with no message). A
  `-m f"ao: workspace checkout sync to {new[:7]}"` would materially help an operator auditing
  `git reflog` after a `sync_anomaly`.
- **S-2** — `is_held_by_other()` is implemented and tested (`TestSecondHolder`) but has no call site
  in `engine.py` or the CLI today — fine to keep (earmarked in `STATUS.md` for a future CLI
  lock-inspection command), just noting it's currently dead from the production path's perspective so
  it isn't mistaken for load-bearing.

### Checked, no issue found

- No re-entrancy bug: `_activate_integration` is guarded by `not state.integration.active and not
  ctx.integration_degraded` (`engine.py:903`), and `_reconcile_integration_on_resume` always sets
  `ctx.integration_degraded = True` — so `WorkspaceRunLock.acquire()`'s non-reentrancy `RuntimeError`
  can never actually be hit from the engine.
- No ref created before the lock claim: confirmed by reading `_activate_integration` end to end —
  every git call before the claim is a probe (`GitRepo.version()`, `group_repos`, the
  `unsafe_state_dir` path check); `create_ref` happens only after the lock section. AC-4 honored.
- `models.py` diff is empty (confirmed via `git diff fd75dce -- src/agent_orchestrator/models.py`) —
  the frozen `workspace_lock: Literal["require","skip_sync","off"]` and
  `workspace_lock_held: bool` fields are untouched, exactly 3 policy values, no 4th `"strict"` value.
  The developer's `"strict"` language in `STATUS.md` correctly refers to the **pre-existing**
  `Orchestrator(..., isolation_strict=...)` constructor flag / `self._isolation_strict` (present
  before this diff, only its logging was refactored to accept `**extra_fields`) — not a schema
  change. No schema drift.
- ADR-0014 cross-check (AC-11): confirmed — `ADR-0014-service-owned-scheduler-and-triggers.md`
  explicitly defers to "the per-task isolation epic" and defaults `max_concurrent` to 1 "until [it]
  lands." `"require"` (default) is exactly that assumption turned into enforcement; nothing
  contradicts D8.
- `is_dirty()` and the new pre-flight `worktree.checkout_dirty` count use the identical underlying
  call (`status_porcelain(..., untracked=False)`) — behavior is unchanged, only a count was added.
  NFR-2 byte-identical-when-nothing-isolated path is untouched (`_activate_integration` is only
  reached from an isolated dispatch at all).
- The two updated `TestCheckoutSync` tests in `test_engine_isolation.py` are a genuine spec fix, not
  a silent behavior drift: the old "any dirty tracked file anywhere blocks the whole sync" behavior
  is exactly what HLD §12.3's R-12 motivation (the consumer's real 4106-entry dirty checkout) calls
  out as the problem being fixed; the new collision-only-refuses expectation matches AC-9 verbatim.
- Hook points for `T-Lr6Ka3` / `T-Cx4Jf1` part B: confirmed unaffected — this ticket's lock logic
  lives entirely in `_activate_integration` / `_reconcile_integration_on_resume` / `run()`'s
  `finally`; `_sync_checkout`'s call sites are unchanged (only its body), so neither downstream
  ticket's barrier-timing assumptions are disturbed.

## Alignment

- **Project goals**: declarative spec-driven (policy read from the frozen, schema-validated
  `IntegrationSpec.workspace_lock`), deterministic/resumable (injected `clock`; resume path explicitly
  reconciles the lock rather than assuming), safe-by-default (`"require"` degrades rather than
  corrupts). No drift.
- **Epic/task goals**: AC-1 through AC-12 all have direct evidence above; AC-10 (stash-and-restore
  explicitly declined) is honored — no stash/reset/clobber logic exists anywhere in the diff (grepped
  `git.py`/`engine.py` diffs for `stash`/`reset --hard`: none added). R-4 and R-12 are both
  substantively delivered, modulo C-2/W-1's diagnostic gaps.
- **Code-level intent**: docstrings mostly match behavior; the one place they don't is C-3
  (`fast_forward_checkout`'s parity claim) and C-1 (`STATUS.md`'s citation).

## Dimension checklist (all walked; nothing else to flag beyond what's listed above)

- SOLID/KISS: `WorkspaceRunLock` is a single-purpose class; policy interpretation correctly lives in
  the engine, not the lock primitive (see W-6) — good separation, not over-abstracted.
- DRY: W-2 (triplicated pid/boot-id helpers) is the one real finding; everything else reuses existing
  primitives (`isolation/paths.py::workspace_key`/`state_dir()`, `status_porcelain`, `diff_names`).
- Magic literals: policy strings are named module constants (`_WORKSPACE_LOCK_REQUIRE` etc.); payload
  keys are named constants in `runlock.py`; no bare literals at comparison call sites. Clean.
- Pluggable architecture: `clock`/`pid_alive`/`boot_id_reader` are all injectable on
  `WorkspaceRunLock`; `GitRepo`'s `Runner` injection point is reused for the structural
  no-merge/no-rebase sweep test. Good.
- Spec/DAG correctness: N/A to this ticket (no DAG changes); frozen schema fields consumed, not
  mutated.
- Determinism/resume safety: covered in Investigation B; one coverage gap (W-4), no correctness gap.
- Errors/logging: `release()`/`acquire()` never raise on the hot path (matches
  `IntegrationLock`'s established contract); `WorkspaceLockHeldError` is only reachable via the
  context-manager convenience path, never the engine's own hot path — confirmed no new `except`
  needed. One authoritative log per outcome (no duplicate spam) — confirmed by the "exactly ONE
  warning" tests.
- Testability: real subprocess tests for the actual race/crash properties (not mocked flock
  semantics) — the right call; unit tests use injected clock/pid/boot-id, never the real `/proc` or a
  real signal, never the real home dir (`tmp_path` throughout).
- Concurrency/rollout: `"require"` cleanly degrades a violating second run rather than racing or
  failing hard; expand-compatible (no schema change at all in this ticket).

## Testing notes

- **Mock**: nothing new needs mocking beyond what's already injected (`clock`/`pid_alive`/
  `boot_id_reader` on `WorkspaceRunLock`; the `_recording_runner` pattern in
  `test_engine_workspace_lock_sync.py` for the structural no-merge/no-rebase sweep).
- **Integration-test** (real temp git repos, per HLD §17.2 and CLAUDE.md's e2e conventions): already
  the pattern used throughout — good, keep it for the C-2 rename-collision test too.
- **Coverage gaps to add**: (1) C-2's rename-collision repro as a proper test; (2) W-1's
  untracked-collision naming, if that fix is taken; (3) W-4's resume-denied-reclaim branch; (4) a
  direct unit test of `fast_forward_checkout(old=<non-ancestor>, new=...)` to pin down and document
  its actual (non-self-verifying) behavior, whether or not C-3's internal-guard fix is taken — so a
  future refactor doesn't accidentally start relying on it being safe for arbitrary tree pairs.

## Verification performed

- `uv run ruff check <scope>` — all checks passed.
- `uv run ruff format --check <scope>` — 9 files already formatted.
- `uv run mypy src` — exactly 4 pre-existing `_version.py` errors, matching the claimed baseline.
- `uv run pytest tests/isolation/test_runlock.py tests/isolation/test_git.py tests/test_engine.py
  tests/test_engine_isolation.py tests/test_engine_isolation_accounting.py
  tests/test_engine_workspace_lock_sync.py tests/test_e2e_cli_isolation.py -q -p no:cacheprovider
  --durations=10` — **247 passed**, matching the count claimed in `STATUS.md`. Slowest test 1.12s
  (a pre-existing `test_git.py` test, unrelated to this ticket).
- Investigation A: 7 real-git-repo experiments in a scratch directory outside the project tree
  (`/tmp/claude-*/scratchpad/ffc_test/`), all cases reproduced and recorded above.
- Investigation B: read `runlock.py` end to end, `service/supervisor.py::acquire_singleton_lock`/
  `_pid_alive`/`_read_os_boot_id` for the reuse-disposition claim, and the full engine diff for lock
  claim/release/reconcile ordering; cross-checked every claim in `STATUS.md`'s Evidence section
  against the actual diff and test files.

---
- By: reviewer-agent · Role: reviewer · Date: 2026-09-07 · Comment: APPROVE WITH CHANGES. Core
  mechanics (lock correctness, fast-forward safety) are sound and well-tested — Investigations A and B
  both pass with only diagnostic-quality gaps, not safety gaps. Two must-fix items before merge: C-1
  (correct `STATUS.md`'s fabricated citation for the `fast_forward_checkout` deviation — the deviation
  itself is approved on its technical merits, independently verified) and C-2 (rename collisions are
  misclassified as `sync_anomaly` instead of `dirty_checkout`, never naming the colliding path —
  violates AC-9 for a common real-world case, currently untested). C-3 and the six Warnings are
  should-fix, not blocking. Full findings, evidence, and fixes above.
