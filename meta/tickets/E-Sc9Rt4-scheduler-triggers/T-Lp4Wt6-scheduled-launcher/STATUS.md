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
