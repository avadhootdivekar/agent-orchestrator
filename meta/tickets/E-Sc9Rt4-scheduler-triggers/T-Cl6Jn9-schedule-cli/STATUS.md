# STATUS

- ID: `T-Cl6Jn9-schedule-cli`
- Updated At: 2026-11-11
- State: Partially Done (MVP surface landed; dependent ACs unfinished, listed below)
- Owner: developer agent

## This update
By: developer · Role: developer · Date: 2026-11-11
- Implemented `src/agent_orchestrator/schedules/cli.py`: `ao schedule list | next | history | validate | add | remove | enable | disable | run-now | daemon`.
- `cli.py` diff (AC18): `schedule_app` import (two lines after ruff's isort split) + `app.add_typer(schedule_app, name="schedule")` + the one-line `validate_workspace_schedules(workflow, wf)` hook in `ao validate`.
- Liveness is the `engine.lock` probe (`probe_engine_lock`), never the hub. `run-now --local` and `daemon` take the same lock; `run-now` builds `ScheduleEngine` + `ScheduledLauncher` and routes through `engine.evaluate(run_now=...)` (overlap/concurrency/governance identical to the daemon); module has no `Popen` (asserted by a test).
- `add` also takes `--quota PERIOD=N` / `--per-run-usd` because D-2 (T-tPlK1T) makes a limit mandatory (the ticket's flag list predates it).
- `daemon --once`: decisions go to stderr one per line; stdout is exactly the fire count; exit 0.

## Acceptance criteria
Done: 1 (see deviation), 2, 3 (cron/interval), 4, 5, 6 (cron/interval + common flags), 7, 8, 9, 10, 11, 12, 13, 14, 15, 15a (daemon + run-now lock cases, `next` read-only), 16, 17, 18.
Unfinished (depend on unlanded/other tasks):
- AC15a "`next` never mutates a file-watch digest" and AC3's `TriggerSource.observe` branch: event kinds are listed as "no predicted time" and no source is constructed; wire `observe` when `T-Fw8Gp4`/`T-Wh9Kv1` register their sources (they must also add `--watch` / `--webhook` / `--until-*` flags to `add`).
- AC19 coverage target met (see Evidence).
Deviations: AC1 reads `<state_dir>/schedules/state.json` always (the hub snapshot is derived from the same store, and F9 says liveness comes from the lock, so no hub probe); `enable` of a `completed` schedule needs the engine lock to reset state.json (single-writer cache) and refuses with a clear message while a daemon holds it.

## Evidence
- `tests/schedules/test_cli_e2e.py`: 31 tests (CliRunner on the root app, fixed clock, temp workspace/state/registry, fake supervisor); coverage of `schedules/cli.py` 90%.
- `tests/schedules` + `tests/service`: 833 passed. `ruff check .` / `ruff format --check .` clean; `mypy src`: 0 errors (229 files).
- Whole-tree `mypy .`: 1154 errors in 120 files, all in pre-existing test modules (none in `schedules/cli.py` or `test_cli_e2e.py`).

## Next actions
1. `T-Un0Lm6`/`T-Fw8Gp4`/`T-Wh9Kv1`: add their flags to `add`; wire event-source `observe` into `next`.
