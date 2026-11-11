# STATUS

- ID: `T-88wSLC-governance-e2e-suite`
- Updated At: 2026-11-11
- State: Partial
- Owner: TBD (assigned at sprint start)

## This update
- 2026-11-11 — By: developer (unit `w20-08-a2-docs-adr-sync`) · Role: developer · Date: 2026-11-11 · Comment: Remains Partial; pause/resume and usage-CLI e2e depend on uncommitted work.
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

## Update 2026-11-11 (w21-03, tester)
By: tester · Role: tester · Date: 2026-11-11

Landed `tests/schedules/test_cost_cap_cli_e2e.py` (CliRunner; real `ao schedule` CLI, `ScheduleEngine`, governance gate, ledger, real
`ao run`/`ao resume` executed in-process by a scripted-supervisor stand-in with `FakeExecutor` `cost_usd` per task; injected `_now`,
temp ws/state, no sleeps). `pytest tests/schedules/test_cost_cap_cli_e2e.py` -> **6 passed, 1 xfailed** (~4 s). Wider run
`pytest tests/schedules tests/test_e2e_cli_cost_cap.py` -> 393 passed, 1 xfailed (35 s). ruff check/format clean.
- `test_per_run_cap_halts_serial_run_after_task_3_and_usage_shows_paused_overshoot` - pass (4 serial tasks @ $0.40, cap $1.00: t1-t3 done, `failed` + `builtin.cost_cap` trip, `usage` paused overshoot $0.20, `run_cost_capped` event)
- `test_resume_local_add_usd_completes_task_4_and_charges_the_same_fire` - pass (`--add-usd 1.00 --local` -> new cap $2.20, task 4 done, same fire / day=1 not re-spent, usage $1.60) - uses the `direct_launcher` workaround, see BUG
- `test_plain_ao_resume_keeps_the_sticky_cap_and_dispatches_nothing_new` - pass
- `test_on_cap_abort_is_final_and_resume_is_refused_exit_3` - pass
- `test_pause_expiry_then_resume_refused_exit_3` - pass
- `test_one_task_run_crossing_its_cap_succeeds_with_overshoot_and_no_pause` (AC9a) - pass
- `test_resume_local_with_the_real_cli_wiring_launches_the_run` - **XFAIL (strict)**: production bug B-1

**B-1 (production, owner: T-nI4DBf/T-rwj4nV; tester did not edit src):** the engine-launcher shims `_Deferred` (`schedules/cli.py`) and
`_DeferredLauncher` (`service/cli.py`) do not forward `launch_capped_resume`; `ScheduleEngine._resume_one` does
`hasattr(self._launcher, "launch_capped_resume")` -> `launcher=None` -> every real `ao schedule resume` (local or daemon-queued) ends
`ERROR: resume refused (launch_failed): no launcher configured` (exit 3). Fix: forward the method in both shims (or have the engine unwrap
`.target`). Then drop the xfail and the `direct_launcher` fixture.
**B-2 (design observation, needs owner call):** a finished capped run is only moved to `paused` (and its pause TTL started) when the engine
next evaluates a *due* schedule (`_live_runs()` runs lazily from overlap/concurrency checks; `_sweep_unfinalized` skips records still in
`active_fire_keys`). For a daily schedule `usage`/`resume` report `not_paused` until the next fire time, and the 72 h TTL starts then. Tests settle
with a second due tick (suppressed by `day=1`).

Coverage (AC8, `--cov` over tests/schedules + test_e2e_cli_cost_cap): usage_view 97 %, usage_ledger 90 %, capped_resume 89 %, governance_gate 81 % (all >= 80 %). Not measured: other new modules.

**STILL UNFINISHED:** AC6 `ao run` baseline `state.json` equality (no pre-epic baseline fixture exists; not faked); full-suite `pytest -q` delta vs epic
baseline not run; `tests/integration/test_governance_integration.py` and the remaining §18.2/§18.3 name-per-bullet mapping (AC1); API cases (T-8knqVe);
AC9b (killed attempt => `partial`) at CLI tier. State stays **Partial**.
