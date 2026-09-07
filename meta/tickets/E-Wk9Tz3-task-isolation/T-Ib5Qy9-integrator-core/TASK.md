# TASK: T-Ib5Qy9-integrator-core

## Metadata
- Task ID: `T-Ib5Qy9-integrator-core`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: unassigned (developer)
- Created: 2026-09-06
- Last Updated: 2026-09-07
- Status: Draft
- Estimate: 3 days

## Requirements Mapping
- Requirement IDs: FR-5, FR-6, FR-8, NFR-1, NFR-4 · Design: HLD §8, §11 M4
- Review findings folded in: **R-20** (ctor signature / NFR-3), **R-7** (multi-repo CAS-retry scope),
  **R-8** (declared outputs), **S-3** (auto-commit secret screen). Estimate unchanged at 3 days: all
  four are refinements inside work already scoped here.

## Description
The heart of the epic: land one task's work on the integration ref, or report precisely why it could
not. Squash → rebase → (resolver hook) → verify → compare-and-swap fast-forward, all under a
per-repository lock.

Files you own:
- `src/agent_orchestrator/isolation/locks.py` (new)
- `src/agent_orchestrator/isolation/integrator.py` (new)
- `tests/isolation/test_locks.py`, `tests/isolation/test_integrator.py` (new)

Do NOT touch: `git.py`, `paths.py`, `worktrees.py` (read them), `resolvers.py` (call it through an
injected hook with a stub in your tests — `T-Rm2Lx7` implements it), `engine.py`, `models.py`,
`cli.py`.

## Acceptance Criteria
1. `IntegrationLock(common_dir)` combines a process-wide `threading.Lock` (keyed by common dir) with
   an `flock` on `<common_dir>/ao-integration.lock` — a **companion** file, never a file git replaces
   via `os.replace`. `acquire(timeout)` returns `False` on timeout rather than raising or hanging.
   Tests: (a) two threads genuinely serialize (deterministic, event-synchronized, not `sleep`-based);
   (b) a **real second process** holding the lock blocks this one and `acquire(timeout=0.5)` returns
   `False`; (c) the lock is released when the holding process is killed.
2. `Integrator.integrate(task_iso, run_integration, attempt) -> IntegrationResult` implements HLD §11
   M4's step order exactly: auto-commit (outside the lock) → acquire every repo lock **in sorted repo
   key order** → squash + rebase per repo → verify once → CAS land per repo.
3. Auto-commit: `git add -A` then commit; nothing staged → no commit, no empty commit. The message is
   rendered from `integration.commit_message_template` (default in HLD §8.2) using **only** ids and
   shas. A test greps the produced commit message and asserts it contains `AO-Run-Id`, `AO-Task-Id`,
   `AO-Agent`, `AO-Base`, `AO-Attempt` and **no file contents**.
4. Squash uses `git commit-tree "<tip>^{tree}" -p <base> -m <msg>` followed by `reset --hard`, and
   writes `refs/ao/runs/<run>/<task>/squash-<n>`. After integration the task branch has **exactly one
   commit** on top of `base` (assert `rev-list --count base..branch == 1`) regardless of how many
   commits the agent made.
5. Empty task: a task that changed no repo file returns `IntegrationResult(status="empty")`, moves no
   ref, and is treated by callers as integrated. Test with a task that only wrote an artifact outside
   the repo.
6. Fast path: when the integration head still equals the task's base, no rebase is attempted and the
   CAS lands the squash directly.
7. Conflict path: with a real conflicting fixture, `integrate` calls the injected resolver hook, and —
   when the stub resolves nothing — returns a non-`integrated` status carrying the exact
   `conflicted_paths` list (paths only). It does **not** abort the rebase (the worktree is left
   mid-rebase for T2), and a test asserts `rebase_in_progress` is still `True`.
8. Verify: (a) with `verify_command` unset, the built-in structural check runs — a fixture whose
   resolved file still contains `<<<<<<<` fails it, a clean one passes; the check uses
   `git grep -l` / `git diff --check` so **no file contents enter Python** (assert by code review note
   + a test that the checker never opens a repo file with `open()`); (b) with `verify_command` set, it
   runs in the task worktree with the configured timeout and env, and stdout/stderr/exit are captured
   to `<run_dir>/<task>/integration/attempt-<n>/verify.*`; (c) a missing/non-executable command yields
   `verify_status="failed"`, `reason="verify_command_error"` — **never** a silent pass; (d) a
   verify timeout is a failure, not a hang.
9. CAS landing: (a) success moves the ref and returns the new head; (b) a lost CAS triggers exactly
   one full retry against the new head, and a second loss returns `failed(reason="ref_race")`;
   (c) `already_landed` — when the candidate is already an ancestor of the integration head (a crashed
   previous process landed it), `integrate` returns `integrated` **without** moving the ref. All three
   have dedicated tests.
10. Multi-repo: with two real repos, all rebases and the single verify happen before any CAS; a
    resolver-stub failure in repo B lands **nothing** in repo A. A partial-land I/O failure between
    two CAS calls emits `integration.partial` naming both refs and returns `failed`.
11. `resume_integration(task_iso, ...)` continues a worktree left mid-rebase: `add -A`,
    `rebase --continue`, verify, CAS. Test by hand-resolving the conflict in the worktree between the
    two calls.
12. Idempotency: calling `integrate` twice for the same task in a row is safe — the second call
    detects `already_landed` and is a no-op. Test it.
13. `lock_timeout` returns `failed(reason="lock_timeout")` — never blocks the caller past the
    configured timeout. Test with the real second-process lock holder.
14. `uv run pytest -q tests/isolation/` green; full suite green with recorded counts; `ruff` clean;
    `uv run mypy src` zero new errors.

### Amendments from the 2026-09-07 review gates

15. **R-20 — the constructor, settled.** `Integrator(spec, logger, clock, resolver_hook,
    escalation_hook)`. The HLD previously showed a `run_state_ref` parameter; that is **wrong** and has
    been corrected in §11 M4. `integrate()` runs on a worker thread and ADR-0007 D3 / NFR-3 restrict
    every `RunState` mutation to the main thread, so a live `RunState` reference reaching this object
    is precisely the bug that invariant exists to prevent. Run-scoped data arrives as an **immutable
    snapshot** (`RunIntegrationSnapshot`). Test: assert `Integrator` holds no `RunState` (inspect its
    instance attributes) and that nothing in `isolation/integrator.py` imports `RunState` or calls
    `.save(`.
16. **R-8 — declared outputs come from `TaskIsolation`.** `integrate()` reads
    `task_iso.declared_outputs`; it never sees a `TaskSpec`. Reconcile the two disagreeing "Empty"
    definitions the HLD previously carried: the authoritative condition is
    **`tip == base` AND the worktree is clean AND there is no untracked declared output**. Test: a task
    that leaves the tree at `base` but produced a git-ignored declared output is **not** `Empty`, and
    the output is copied back.
17. **R-7 — the CAS retry is scoped to the losing repo.** On a lost CAS: first check
    `is_ancestor(candidate, head_now)` — if true, a previous process already landed it, so return
    `integrated` without moving the ref (this is also the crash-recovery path). Otherwise retry
    **only that repository**, once. **Never** re-run squash/rebase for a repo already in `landed`:
    re-squashing mints a new attempt-numbered message, hence a new sha, hence a spurious duplicate
    commit for content that is already present. Tests: (a) two repos, A lands, B's CAS loses → assert
    A gains **no** second commit and its ref is unchanged after the retry; (b) `already_landed`
    short-circuit; (c) second loss on the same repo → `failed(reason="ref_race")`, and if anything had
    landed, `integration.partial` names both refs.
18. **S-3 — the auto-commit screens before it sweeps.** Before staging, list **untracked** entries via
    `GitRepo.status_porcelain(...)` and match them against `spec.commit_denylist`. At the default
    `on_denylisted_path: "fail"`, return `failed(reason="denylisted_path", paths=[...])` **without
    staging anything**; at `"warn"`, log `integration.denylisted_path` and proceed; at `"allow"`,
    proceed silently. Honour `spec.auto_commit: false` by skipping engine-side committing entirely and
    integrating whatever the branch already holds.
    Tests: (a) an untracked `.env` aborts integration naming the path, and `git log` shows no commit;
    (b) an already-**tracked** file matching the denylist is **not** screened (that is the repo
    author's decision, not the engine's); (c) `"warn"` proceeds and logs; (d) `auto_commit: false`
    integrates a branch the agent committed itself and never runs `git add`.

## Risks
- The single hardest task in the epic. Mitigation: the resolver and escalation policies are **injected
  hooks** stubbed here, so this ticket is only "squash/rebase/verify/land"; `T-Rm2Lx7` and `T-Lr6Ka3`
  fill the hooks.
- Deadlock across repos. Mitigation: locks are always acquired in sorted repo-key order and released
  by an `ExitStack`; a test acquires in both orders from two threads and asserts no deadlock.
- Cross-repo landing is **not** atomic. Do not attempt to make it so; report `integration.partial` and
  document. It is an accepted, recorded limitation (ADR-0013 Consequences).
- NFR-1: it is easy to "just read the conflicted file" while debugging. Everything must flow through
  git subprocesses.
- R-20 is a five-minute fix that becomes expensive if the tests are written first against the wrong
  signature. Settle it before writing any test in this ticket.
- S-3's screen must not become a security **claim**. It is a backstop for a common footgun, not a
  secret scanner: say so in the code comment and in the handoff, so nobody later relies on it.

## Dependencies
- Upstream: `T-Gt4Pw8` (git API), `T-Wk3Nv6` (`TaskIsolation`).
- Downstream: `T-En8Hd4` (calls it from the worker), `T-Rm2Lx7`/`T-Lr6Ka3` (fill the hooks).

## Pseudocode / Algorithm
```text
HLD §8.1 (happy-path sequence), §8.2 (exact squash mechanics), §8.3 (verify),
§11 M4 (integrate / resume_integration step order and IntegrationResult).
```

## Schemas / Interface Notes
- Interface / API (locked): `IntegrationLock`, `Integrator(spec, logger, clock, resolver_hook,
  escalation_hook)`, `Integrator.integrate(...) -> IntegrationResult`,
  `Integrator.resume_integration(...) -> IntegrationResult`, and the `IntegrationResult` dataclass
  fields from HLD §11 M4.
- Spec / data schema: consumes `IntegrationSpec` from `T-Sc7Rm2`.
- Triggers / events: `integration.started|squashed|rebased|conflict|verify_started|verify_passed|
  verify_failed|merged|partial|failed`.
- Artifacts: `<run_dir>/<task_id>/integration/attempt-<n>/verify.{stdout,stderr,exit}`.

## Handoff Boundary
- Upstream: read the merged `git.py` and `worktrees.py`.
- Downstream: publish `IntegrationResult`'s exact fields and the hook signatures in `STATUS.md`;
  `T-En8Hd4` and `T-Lr6Ka3` are written against them.

## Artifacts
- Docs/comments: `meta/tickets/E-Wk9Tz3-task-isolation/T-Ib5Qy9-integrator-core/`
- Large outputs: none

---
- By: architect · Role: architect · Date: 2026-09-07 · Comment: Phase-2 amendment. R-20 resolved in
  favour of this ticket's already-locked hook-based signature (the HLD was wrong and has been
  corrected, not this ticket); R-7's CAS retry scoped to the losing repo with an `is_ancestor`
  short-circuit; R-8's declared outputs sourced from `TaskIsolation` so the Integrator stays
  `RunState`-free, and the two conflicting "Empty" definitions reconciled; S-3's auto-commit denylist
  screen specified with the deliberate carve-out that already-tracked files are not screened. Estimate
  unchanged at 3 days.
