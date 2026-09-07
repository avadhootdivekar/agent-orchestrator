# TASK: T-Ib5Qy9-integrator-core

## Metadata
- Task ID: `T-Ib5Qy9-integrator-core`
- Epic ID: `E-Wk9Tz3-task-isolation`
- Owner: unassigned (developer)
- Created: 2026-09-06
- Last Updated: 2026-09-06
- Status: Draft
- Estimate: 3 days

## Requirements Mapping
- Requirement IDs: FR-5, FR-6, FR-8, NFR-1, NFR-4 · Design: HLD §8, §11 M4

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
