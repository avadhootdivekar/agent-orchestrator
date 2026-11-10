# STATUS

- ID: `T-PpE5lf-pre-fire-gate-and-reservation`
- Updated At: 2026-11-11
- State: In Progress (partial; w14-04 review fixes applied, pending re-review/merge)
- Owner: developer agent

## This update
- **2026-11-11 (developer, unit `w15-04-a2-pre-fire-gate-review-fixes`):** review `w14-04` fixes applied (branch
  `ad/enhancements-10nov`; mutation-checked, each regression test fails when its fix is reverted):
  - **H1** `_finalize_governance` charges `max(observed, reserved_usd)` with `cost_confidence: unknown` for blind
    (no cost reported) and killed/cancelled runs; reported cost is kept and marked `actual`. Test: blind run retires,
    the next fire is `cost_cap_reached`.
  - **M1** the `intended` record (governed `check_and_reserve(run_id=)` and `_write_plain_intent`) carries the
    deterministic `scheduled_run_id`; `reconcile_orphans` reconciles a surviving child to `launched` with the
    reservation held.
  - **M2** finalisation is idempotent and re-driven: persist BEFORE emitting `run_cost_capped` (no duplicate event on
    retry), failed keys are retried every `tick()` (unthrottled), and `start()` sweeps `launched` governed records whose
    run is no longer running (state terminal, or pid dead) and that never got their retire hook.
  - **M3** fixed-clock tests through `gate()`, `check_and_reserve`, and the engine: Asia/Kolkata midnight, America/
    New_York DST fall-back double fire with `day=1`, `week_start: sunday` Sat->Sun, month roll in a non-UTC zone.
  - **L1** round-down pinned (`4.881 -> 0.11`); **L2** `invalidate`-before-`usage` under the lock pinned; **L3**
    unparseable trip time after a resume now fails closed (counts as fresh), trip `==` resume instant pinned as stale;
    **L6** prompt read is a single `O_NONBLOCK` open + `fstat` `S_ISREG` + bounded read (FIFO swap cannot hang the
    flock); **L4 (part)** post-spawn belt now checks the flag VALUE like the pre-spawn check (shared
    `_argv_forwards_cap`); capped-state tuple is one named frozenset `GOVERNANCE_CAPPED_STATES`.
  - **Unfixed Lows (reasons):** *L4 main* (post-spawn `launch_failed` charges 0 runs / $0): the record cannot tell a
    pre-spawn failure from a post-spawn one, and charging every `launch_failed` would burn quota for fires that never
    ran; needs the launcher to mark "child spawned" (follow-up, ideally with `T-Uz0Uol`'s run-dir probe). *L5*
    (bound- vs label-based window membership): differs only across the pre-2011 tzdata transitions in `windows.py`;
    accepted divergence now recorded in a code comment. Informational DRY items (`SHA_PREFIX_LEN` duplicate, `1e-9`
    literal, shared skip-event helper) left as suggestions.
  - **Evidence:** `pytest tests/service tests/schedules` 891 passed, 1 failed:
    `test_real_renderer_does_not_yet_register_cost_cap_...` fails ONLY because another unit's uncommitted
    `run_options.py` (T-et7Edf) now registers the option (the flip this STATUS predicts; whoever lands T-et7Edf
    replaces it with the "real renderer forwards `--cost-cap-usd`" test). `ruff check`/`ruff format --check` clean,
    `mypy src` clean (tests/service mypy has pre-existing import-untyped/unused-ignore noise; nothing new).
- **2026-11-11 (developer, unit `w11-02-a2-pre-fire-gate`):** implemented on branch `ad/enhancements-10nov`:
  `service/governance_gate.py` (new: `gate`, `check_and_reserve`, `read_prompt`, `GateResult`, `PromptSnapshot`,
  `UsageLedger` protocol, interim `FireRecordLedger`); `schedule_engine.py` (reason constants, gate step after overlap
  / before concurrency in BOTH the main path and the overlap-`allow` path, second call site on the queue drain, retire
  hook, `FireDecision.effective_limits/gate`, `usage_ledger_factory`/`governance_lock_path`); `schedule_launch.py`
  (`check_and_reserve` replaces the plain `intended` write for governed decisions, prompt snapshot, `cost_cap_usd`
  option, pre- and post-spawn argv checks); `fire_store.py` (`FireGovernance`, `PromptMeta`, `governance` field,
  `write_suppressed(governance=)`, governance event levels).
- **Done (fixed-clock tests in `tests/service/test_governance_gate.py`):** AC1, 2, 3, 4, 5 (launcher-level: two
  decisions / two threads), 6, 7 (fail-closed half + stubbed-renderer half), 8, 9, 10, 11, 12, 13, 14, 15, 16, 17.
- **UNFINISHED (dependency not landed):**
  - AC7 *real renderer*: `cost_cap_usd` is not a run-options registry Option until `T-et7Edf`. Today a governed
    fire that reserves money fails closed (`launch_failed: cost_cap_not_forwarded`, pinned by a test); quota-only
    bindings are unaffected. Once `T-et7Edf` lands, add the "real renderer forwards `--cost-cap-usd`" test.
  - Ledger: `T-Uz0Uol` (`usage_ledger.py`) not merged. The gate depends only on the `UsageLedger` protocol;
    `FireRecordLedger` is interim (run counts exact; spend = persisted `cost_observed_usd`, written by the retire hook
    from `state.json`; no live probe, no blind-executor rule H2/M1). Replace via `usage_ledger_factory` /
    `ScheduledLauncher(usage_ledger=)`.
  - AC5a is tested at the launcher (natural + run-now decisions in sequence), not through a full engine tick with the
    real launcher.
- **Evidence:** `pytest tests/service tests/schedules` 746 passed; `governance_gate.py` coverage 97 %; `mypy src` clean;
  `ruff format --check` clean; `ruff check` clean on all touched files (3 E501 in `schedules/governance.py` belong to
  another unit's uncommitted edit). AC13 measured: 200 schedules x 30 records, one tick = ~320 ms (budget 0.5 s),
  one directory rescan (test caps at 6x budget for instrumented CI).
- **Design notes:** (1) the gate also runs inside `_apply_overlap`'s `allow` branch, which returns before the main
  insertion point; (2) a gate exception burns `governance_error`, emits ERROR, then re-raises so `_collect`'s
  `bump_eval_error`/quarantine path applies (AC11); (3) `ResolvedBinding.limits` is already defaults-resolved, so the
  engine uses it directly as the effective limits.

## Evidence
- `tests/service/test_governance_gate.py` (44 tests); updated `tests/service/test_fire_store.py` event-table test.

## Risks / Blockers
- `T-et7Edf` (cost-cap option) and `T-Uz0Uol` (ledger) must land before the cost half is usable end to end.

## Next actions
1. Read the upstream tasks' **merged code** (including E-Sc9Rt4's), not only the HLD, before starting.
2. Implement against the acceptance criteria in `TASK.md`, in AC order; report the pytest delta, ruff/format, mypy, coverage.
3. Hand the retire hook + lock helper to `T-nI4DBf`.

## w19-02 re-review fixes (w16-06 findings)
- By: developer · Role: developer · Date: 2026-11-11
- **N-M1 fixed:** `start()` re-registers records reconcile moved `intended -> launched` into the schedule's
  `active_fire_keys` (`record_fire`); the start sweep now decides liveness with `_is_live` (supervisor + run status), not
  `rec.pid is None`, and re-adopts a still-live untracked record (`ScheduleStateStore.adopt_active`) instead of
  finalising it (also closes L-n4). Tests `test_nm1_*` (crash-after-Popen still running: not final, blocks overlap,
  pauses on a cap trip).
- **L-n1 fixed:** retire-hook finalisation retries with capped exponential backoff (2 s base, 300 s cap, monotonic
  clock) and gives up after 6 attempts with ONE ERROR `schedule.governance.finalize_failed` event; the record keeps
  holding its reservation and the next restart's sweep retries it.
- **L-n2 / L-n3 pinned:** tests for H1e, M2d, M2f, the sweep read-error branch, the `RunNotFoundError` / already-capped
  no-ops and the post-spawn wrong-value argv. 10 targeted mutations all killed.
- **L4 remainder: explicitly ACCEPTED.** A spawned-then-cancelled `launch_failed` child is charged 0 runs / $0; the
  belt cancels it in the same call, so exposure is milliseconds, and `launch_unsafe` (ERROR) is the signal.
- Real-renderer cost-cap test left to T-et7Edf's owner.
