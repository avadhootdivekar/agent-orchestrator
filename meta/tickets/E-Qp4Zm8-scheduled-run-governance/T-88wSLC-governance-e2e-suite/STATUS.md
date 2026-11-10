# STATUS

- ID: `T-88wSLC-governance-e2e-suite`
- Updated At: 2026-11-11
- State: Partial
- Owner: TBD (assigned at sprint start)

## This update
- Ticket created by the `architect` agent as part of the E-Qp4Zm8 design package. Not started.
- Scope: Integration + CliRunner e2e tier covering every HLD §18.2/§18.3 scenario under fixed clocks with a scripted cost executor.

## Evidence
- None yet. No implementation has begun. Design basis:
  `docs-md/scheduled-run-governance-hld.md` and `docs-md/adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md`.

## Risks / Blockers
- Blocked until `T-PpE5lf`, `T-nI4DBf`, `T-rwj4nV` have merged (`T-8knqVe` for API cases).

## Next actions
1. Read the upstream tasks' **merged code** (including E-Sc9Rt4's), not only the HLD, before starting.
2. Implement against the acceptance criteria in `TASK.md`, in AC order; report the pytest delta, ruff/format, mypy, coverage.
3. Hand the test list + coverage to `T-lTW88y`.

## Update 2026-11-11 (w20-05, tester)
By: tester · Role: tester · Date: 2026-11-11

Landed `tests/schedules/test_governance_e2e.py` (CliRunner, real engine/gate/ledger, injected `_now`, temp dirs, stub
supervisor, no sleeps). 9 tests, all passing (`pytest tests/schedules/test_governance_e2e.py` -> 9 passed, 2.3 s):
- `test_add_with_no_limits_is_refused_and_writes_nothing` - pass
- `test_add_persists_quota_cost_and_prompt_pin` - pass
- `test_headline_quota_two_runs_then_third_tick_suppressed_quota_exhausted` - pass (2 launches, prompt verbatim, skip in `history`)
- `test_day_quota_boundaries_at_cli_tier[x4]` - pass (Europe/Berlin midnight 22:59Z/23:00Z; America/New_York DST fall-back double fire contained, next local day admitted)
- `test_no_schedules_file_emits_no_governance_events` - pass
- `test_each_tick_rebuilds_from_disk_so_quota_count_survives_restart` - pass
Existing lower-tier coverage (committed): week_start sunday + month rollover (`test_governance_gate.py::test_m3_*`), usage ledger
(`test_usage_ledger.py`), restart equality (AC6), crash orphaning, overlap-queue burn, H1/ln2 charge rules.

**UNFINISHED (not covered here, code not committed at time of writing):**
- `ao schedule usage --json` / `ao schedule resume ... --add-usd --local` CLI (absent from committed `schedules/cli.py`; `service/capped_resume.py` is untracked) - headline spend `$1.60`, mid-run cap pause/resume e2e, plain `ao resume` re-cap.
- `--per-run-usd` governed *fires* at CLI tier: real renderer does not register `--cost-cap-usd` yet (fails closed by design, see `test_real_renderer_does_not_yet_register_cost_cap...`); `ao run --cost-cap-usd` e2e is in untracked `tests/test_e2e_cli_cost_cap.py` (other unit).
- AC6 baseline `state.json` equality, AC8 coverage report, full-suite delta, `tests/integration/` file named in TASK.md (brief scoped to tests/schedules + tests/service).
Environment note: in the dirty tree, `pytest tests/schedules tests/service tests/test_engine_cost_cap.py` = 1176 passed / 3 failed
(`test_fire_store::test_hld_event_table_is_complete`, two `test_service_schedule_integration::test_run_webhook_*`) and `mypy src` reports 1 error
(`ui/service.py:1294 _clock`): all in carry-over uncommitted webhook/fire_store/ui edits, not in this unit's paths.
