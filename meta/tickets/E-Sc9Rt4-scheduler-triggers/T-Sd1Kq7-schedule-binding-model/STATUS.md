# STATUS

- ID: `T-Sd1Kq7-schedule-binding-model`
- Updated At: 2026-11-10
- State: Done
- Owner: developer agent

## This update
- Implemented all ACs: `schedules/{__init__,errors,models,store}.py`, `service/statefile.py`,
  `specs/schedules.schema.json`, `IntervalScheduler` + amended `Scheduler` docstring, additive
  `Trigger.interval_seconds`/`"interval"` and `$defs.trigger` schema change.
- See "Implementation notes" in `TASK.md` for decisions.

## Evidence
- `uv run pytest -q tests/schedules tests/service tests/test_scheduler.py`: 334 passed (new tests:
  tests/schedules/{test_models,test_store,test_interval_scheduler}.py, tests/service/test_statefile.py).
- `ruff check` / `ruff format --check` clean on owned paths; `mypy src`: 0 errors (220 files).
- Coverage: schedules/ 99-100 % (store.py 90 %), service/statefile.py 100 %.

## Risks / Blockers
- None.

## Next actions
1. Follow-up (not scheduled): refactor `service/registry.py`, `service/boot_resume.py` and the
   supervisor snapshot onto `service/statefile.py`.
2. Downstream `T-Ev3Qm5`, `T-Lp4Wt6`, `T-Cl6Jn9`, `T-Fr2Nx8` can start; landed names:
   `ScheduleFile.from_data/bindings/to_data`, `ScheduleBinding.to_trigger`, `RunArgs.as_options`,
   `ScheduleStore.{path,load,save,mutate}`, `IntervalScheduler`, `read_json_model`, `write_json_model_atomic`.
