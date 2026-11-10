# STATUS

- ID: `T-PpE5lf-pre-fire-gate-and-reservation`
- Updated At: 2026-11-11
- State: In Progress (partial)
- Owner: developer agent

## This update
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
