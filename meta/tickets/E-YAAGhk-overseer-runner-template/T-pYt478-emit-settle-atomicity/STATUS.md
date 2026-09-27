# STATUS

- ID: `T-pYt478-emit-settle-atomicity`
- Updated At: 2026-09-26
- State: **Done, landed on the epic branch.** User decided against a standalone PR to `main` for
  this fix in isolation — instead, commit `3692eac` was cherry-picked from `fix/emit-settle-atomicity`
  onto `ad/overseer-runner-workflow` as commit `2387503`, the full suite was re-run on the epic
  branch post-cherry-pick (**3983 passed, 8 skipped, 0 failed**, identical to the isolated-branch
  numbers below), and `ad/overseer-runner-workflow` (fix included) was pushed to
  `origin/ad/overseer-runner-workflow`. `fix/emit-settle-atomicity` itself is left as-is, local,
  unpushed, unopened as a PR — the fix now ships as part of this epic's own eventual PR to `main`
  instead of separately. See epic STATUS.md.
- Owner: developer (implementation) → reviewer (review) → dev-epic (verification, ticket sync) →
  user (branch/ship decision)

## This update
- Implemented on a standalone branch `fix/emit-settle-atomicity`, cut from `main`@`8c13320` (NOT
  on `ad/overseer-runner-workflow`), per the epic owner's explicit sequencing instruction. Commit
  `3692eac` (local only, unpushed): "Fix emit_tasks injection lost on breaker trip at emitter
  settle (G5)".
- Root cause confirmed exactly as designed: `dev-epic` independently reproduced the bug on
  unmodified `main`@`8c13320` using the architect's repro script before any code was written —
  `run1: failed {'emit': 'succeeded'} injected= []` → `run2: succeeded {'emit': 'succeeded'}
  injected= []`.
- Fix: `_settle_completed_task`'s `emit_tasks` injection block (manifest read → isolation
  revalidate → `_inject` → `build_dag`/`_recompute_order`) moved to run before the first
  `self._runstate.save(state)` and before circuit-breaker evaluation, so success + injection land
  in one save. Every error path inside the block (manifest_error / isolation_validation_error /
  injection_error) is byte-identical to its pre-move behavior — confirmed by direct diff read by
  both `dev-epic` and the `reviewer` agent.
- All 10 acceptance criteria met (see "Evidence" below for exact command output).

By: developer · Role: developer · Date: 2026-09-26 · Comment: Implemented per TASK.md pseudocode.
Ran the full suite and inspected every named candidate file (`test_mvp_breaker_conditions.py`,
`test_resume_replay.py`, plus `test_dynamic_injection`, `test_wave_scheduler`,
`test_engine_conflict_escalation`, `test_isolation_*`, `test_engine_routing`,
`test_routing_breaker_models`, `test_loop_construct`, `test_engine_breakers`,
`test_monitoring_breaker_consult`) — none needed edits, so no `test_nfr2_regression_gate.py`
allowlist entry was added (nothing pre-existing was touched). Release note: "emit_tasks injection
now persists before breaker evaluation; `injected_task_count` trips one boundary earlier."

By: dev-epic · Role: manager · Date: 2026-09-26 · Comment: Independently re-verified — did not
trust the developer's self-report. Re-ran the exact same commands myself and got matching numbers
(see Evidence). Read the full diff and the full new test file line by line before accepting.

By: reviewer · Role: reviewer · Date: 2026-09-26 · Comment: **Verdict: approve with nits, no
blocking issues.** Traced every error path against the pre-move code (byte-identical). Empirically
reproduced the bug by stashing the fix and re-running the new test file against pre-fix code: 3 of
6 tests correctly fail (`test_breaker_trip_at_emitter_keeps_injection`,
`test_single_save_contains_success_and_injection`,
`test_injected_task_count_trips_at_emitting_boundary`) — proof the suite is not tautological. One
substantive finding: `test_plain_resume_after_trip_dispatches_injected` and
`test_monitor_extend_at_emitter_boundary_lets_injected_tasks_run` pass identically before and
after the fix for the `injected_task_count` breaker type specifically (that breaker's count is
always 0 at the emitter's own pre-fix boundary, so it only ever detects an overrun one boundary
late, never loses it outright — the true "lost forever" case needs a count-independent condition
like `stop_file`, which AC1 already covers). Non-blocking; `dev-epic` corrected both docstrings in
place to state accurately what they prove (latch semantics / consult fall-through) rather than
claim to be pre/post-fix discriminators for this bug. Re-ran after the docstring fix: still 6
passed, `ruff` clean. No other Critical or blocking Warning findings. Confirmed working tree left
exactly as found (no stash left behind, nothing committed by the reviewer itself).

## Evidence
- Bug repro (pre-fix, on unmodified `main`@`8c13320`): `run1: failed {'emit': 'succeeded'}
  injected= []` → `run2: succeeded {'emit': 'succeeded'} injected= []`.
- `pytest tests/test_emit_settle_atomicity.py -v`: **6 passed** (all AC1–6 node ids individually
  green): `test_breaker_trip_at_emitter_keeps_injection`,
  `test_single_save_contains_success_and_injection`,
  `test_injected_task_count_trips_at_emitting_boundary`, `test_manifest_error_path_unchanged`,
  `test_plain_resume_after_trip_dispatches_injected`,
  `test_monitor_extend_at_emitter_boundary_lets_injected_tasks_run`.
- Full suite, run independently by `dev-epic`: `.venv/bin/pytest -q` → **3983 passed, 8 skipped, 0
  failed** (169.78s) — zero regressions, matches the developer's reported numbers exactly.
- `.venv/bin/ruff check .` → **All checks passed!**. `.venv/bin/ruff format --check .` → **306
  files already formatted**.
- `.venv/bin/mypy src` (the exact command `.github/workflows/ci.yml` runs) → 4 errors, all in
  `src/agent_orchestrator/_version.py`; confirmed **pre-existing and unrelated** via `git stash` +
  re-run against unmodified `main`@`8c13320` (identical 4 errors), stash popped cleanly.
  `mypy src/agent_orchestrator/engine.py` alone: **Success: no issues found**.
- `git status --short` after the fix: only `src/agent_orchestrator/engine.py` (modified) and
  `tests/test_emit_settle_atomicity.py` (new) — no pre-existing test file touched, so AC7's
  "each change has a gate entry" requirement is vacuously satisfied (there was no change to gate).
- Diff: `+76/-55` in `engine.py::_settle_completed_task`; `+372` in the new test file (after the
  reviewer-prompted docstring corrections).
- Commit: `3692eac` on `fix/emit-settle-atomicity` (local, unpushed).

## Risks / Blockers
- No blockers. All risks from TASK.md's "Risks" section were checked and closed: hidden ordering
  assumptions in the wave/barrier scheduler were traced by the reviewer (the caller only branches
  on `settle.signal`, unaffected); the monitor consult path's "extend" fallthrough was confirmed
  working end to end.
- **Superseded — resolved by user decision.** The fix is cherry-picked onto
  `ad/overseer-runner-workflow` (commit `2387503`) and that branch is pushed to origin. `T-vmI0jI`
  is therefore **no longer blocked** on a separate PR/merge — the fix is live on the same branch
  the rest of the epic builds on. `fix/emit-settle-atomicity` remains an orphaned local branch
  (unpushed, no PR) and can be deleted once the epic branch's eventual PR to `main` carries this
  same commit; note this explicitly in that PR description so the fix isn't attributed only to the
  epic feature when it's a standalone engine correctness fix that also protects `routed-runner`.

## Next actions
1. None outstanding for this task — done, landed, pushed as part of `ad/overseer-runner-workflow`.
2. When the epic branch is ready for a PR to `main`, call out commit `2387503` (G5 fix) as a
   distinct, cleanly-revertable/cherry-pickable unit in the PR description, since it is logically
   independent of the rest of the epic and fixes a pre-existing bug that also affects
   `routed-runner` today.
