# STATUS

- ID: `T-Lp4Wt6-scheduled-launcher`
- Updated At: 2026-11-11
- State: Done
- Owner: developer agent

## This update
By: developer · Role: developer · Date: 2026-11-11
- Implemented `service/schedule_launch.py`: two-phase (intended -> launched/launch_failed) `ScheduledLauncher`, UTC-normalised `scheduled_run_id`, `render_instance_id` ({date}/{rand6}), root-guarded workflow/prompt resolution, template resolution by name only (F4), zero new argv surface, no reposets/agents (F10), `workspace` only for no-repo-set workflows.
- Constructor: `ScheduledLauncher(state_dir, *, process_supervisor_factory, project_config_loader, template_loader, clock, fire_store=None, state_store=None, event_log=None, rand6=...)`. `T-Sv5Hb3` must pass the engine's `state_store`/`fire_store`/`event_log` and `process_supervisor` (single-writer state cache; F2). `run-now --local` (`T-Cl6Jn9`) reuses this class.
- Additions beyond the AC text (small): an already-recorded fire key returns the existing record without relaunch; `prompt_sha256`, when set, is verified against `prompt_file` (mismatch = `launch_failed`); bookkeeping failure after a successful launch is logged but does not flip the record.

## Evidence
- `tests/service/test_schedule_launch.py`: 33 passed (coverage 99% of schedule_launch.py); `tests/schedules` + `tests/service`: 660 passed; `ruff check` / `ruff format` clean; `mypy src`: 0 errors (225 files).
- Launcher-to-engine integration test with fixed clock (`test_launcher_to_engine_tick`).

## Risks / Blockers
- AC4 zero discovery timeout is asserted on the engine's default factory (`_default_process_supervisor`), not enforced here.
- Instance ids without a token re-use the same instance per fire (documented in the module docstring).

## Next actions
1. `T-Sv5Hb3`: construct the launcher with the engine's shared stores/supervisor.

## 2026-11-11 — review fixes (w16-03, from w14-03) — By: developer · Role: developer · Date: 2026-11-11
- **H1 fixed**: `scheduled_run_id(schedule_id, scheduled_for, fire_key)` appends `fire_key[:6]`; AC3 amended (TASK.md), `T-Te3Qw8` updated. Test: `test_h1_two_fires_in_the_same_second_get_distinct_run_ids` (+ stability test); mutant "drop suffix" killed.
- **M1 fixed**: the phase-1 intent already carried the deterministic `run_id` (ab4c516); this unit wraps the `launched` update — failure is logged, the `intended` record is returned (no re-raise), reconcile resolves it to `launched` via run-dir liveness. Test `test_m1_launched_update_failure_does_not_escape_and_reconcile_resolves_launched` (plain + governed); mutants "re-raise" and "no run_id in intent" killed.
- **L2 fixed**: service wiring passes the engine's `usage_ledger` and `governance_lock_path` (asserted in `test_launcher_shares_engine_stores_and_supervisor`).
- **L3 fixed**: (a) `test_l3a_set_schedules_flips_only_the_named_workspace` (mutant killed), (b) `test_l3b_intent_created_at_uses_the_injected_clock` (mutant killed), (c) the two webhook run tests `importorskip("fastapi")`.
- **L5 doc**: AC8 amended with the `cost_cap_usd` exception. Governed-path ordering is covered in `test_governance_gate.py` (kept as is).
- **Not done, with reasons**: *exit-4 => `launch_failed` reconcile* — unreachable now that run ids are unique per fire, and the supervisor/engine reconcile code is outside this unit's paths; follow-up if wanted. *L1* (`FireIntentStore.write` exclusive create) — `fire_store.py` not owned here; mitigated by `usage.lock`/`engine.lock`. *L4* (status fallback summary) — `ao service status` path is outside the "service run" scope of this unit; still partial.
