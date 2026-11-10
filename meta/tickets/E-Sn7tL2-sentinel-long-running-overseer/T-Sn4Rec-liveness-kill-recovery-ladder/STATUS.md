# STATUS

- ID: `T-Sn4Rec-liveness-kill-recovery-ladder`
- Updated At: `2026-11-09`
- State: `Done`
- Owner: `developer (ao run o-bi0rvg-sentinel-dashboard-impl, unit w06-02-kill-ladder)`

## This update
- By: claude-sonnet-5-5 · Role: developer · Date: 2026-11-09 · Comment: Implemented executor cancel_event +
  activity_path with process-group kill, liveness sampling, `killed` status, mechanical idle/stuck/cost ladder
  (retry → escalate → fail; kills never consume max_attempts; barrier alarm-only; systemic alarm), inbox `kill`.
  As-built notes: `docs-md/sentinel-hld.md` §5.4.1.

## Evidence
- Baseline full `pytest -q` (before edits, interrupted at ~28 %, no failure seen); after: **10797 passed, 32 skipped,
  0 failed** (989 s). `test_run_options` / `test_sentinel_settings` registry tests green (201 passed).
- New `tests/test_sentinel_recovery.py` (10 tests): real-subprocess process-tree kill (< 2 s, no orphan), idle ladder
  retry→escalate→fail, kills don't consume max_attempts, stuck-factor (≥3 samples + floor), systemic alarm,
  manual kill / `--no-retry`, bad-kill quarantine, resume mid-ladder (no double kill), barrier alarm-only, disabled ⇒
  no cancel hooks. `ruff check .`, `ruff format --check .` clean; `mypy src` only the pre-existing
  `overseer_tool.py` / `_version.py` errors; `ui`: `npm test` 879 passed, `npm run build` ok (bundle not committed).
- Disabled / absent sentinel: no token, no `start_new_session`, no new fields set ⇒ existing golden tests unchanged.

## Risks / Blockers
- Cost trigger only sees cost of *earlier* attempts mid-flight (live spend is known when the attempt ends).
- AC5 `ao ctl kill` CLI e2e is covered at the inbox level here; the CLI writer is T-Sn3Ctl's (parallel unit).
- Run cancel (`_halt_cancelled`) does not yet signal tokens to kill in-flight process groups (HLD §4 text): follow-up.
- A run halted by another task's failure leaves a concurrently-killed task as `killed` (resume resets it to pending).

## Next actions
1. T-Sn5Ui1: Kill button + liveness column (consume `last_activity_at`, `running_seconds`, `recovery`).
2. T-Sn9Pow: LLM-source kill gating.
