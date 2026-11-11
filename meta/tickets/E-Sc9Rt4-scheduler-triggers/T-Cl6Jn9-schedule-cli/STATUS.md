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
- ~~AC15a/AC3 `observe` branch~~ DONE 2026-11-11 (unit `w22-04-a1-schedule-next-observe`): `ao schedule next` builds `WebhookSource` / `FileWatchSource` (digest store wrapped read-only so baseline seeding is dropped; no `commit`) and lists a pending event at its observed instant (`pending event` note; `pending_event`+`kind` in `--json`); no pending event -> the unpredicted NOTE. Tests `tests/schedules/test_next_observe.py` (3, fixed clock T0: no-pending, webhook not consumed, file-watch writes nothing).
- AC19 coverage target met (see Evidence).
Deviations: AC1 reads `<state_dir>/schedules/state.json` always (the hub snapshot is derived from the same store, and F9 says liveness comes from the lock, so no hub probe); `enable` of a `completed` schedule needs the engine lock to reset state.json (single-writer cache) and refuses with a clear message while a daemon holds it.

## Evidence
- `tests/schedules/test_cli_e2e.py`: 31 tests (CliRunner on the root app, fixed clock, temp workspace/state/registry, fake supervisor); coverage of `schedules/cli.py` 90%.
- `tests/schedules` + `tests/service`: 833 passed. `ruff check .` / `ruff format --check .` clean; `mypy src`: 0 errors (229 files).
- Whole-tree `mypy .`: 1154 errors in 120 files, all in pre-existing test modules (none in `schedules/cli.py` or `test_cli_e2e.py`).

## Next actions
1. `T-Un0Lm6`/`T-Fw8Gp4`/`T-Wh9Kv1`: add their flags to `add`; wire event-source `observe` into `next`.

## Update 2026-11-11 — review fixes (w15-03 findings; w17-01)
By: developer · Role: developer · Date: 2026-11-11
- **H1 fixed.** `_build_engine` now mirrors `service.cli.build_schedule_engine`: reads the registry policy (fails CLOSED if unreadable), refuses when `schedules.enabled: false` (`daemon` and `run-now --local` exit 1; queued `run-now` warns), passes `max_concurrent_global` and `eval_interval_seconds` (the `--interval` flag still overrides), registers the `webhook` trigger source, and shares the engine's `usage_ledger` / `governance_lock_path` with the launcher (the latter two were also missing, so governance quotas were unenforced on this path).
- **H2 fixed.** `run-now` without `--local` and without a live daemon now refuses an unregistered / not-opted-in workspace with the same inert reason the daemon path warns about; explicit `--local` is the opt-in.
- **Mediums/Lows fixed:** W1 (hook test now real, with `agents.json`), W2 (hook validates the workflow's workspace, not cwd), S1 (enable refusal text), S2 (`min=` on `-n` and `--interval`), S3 (`is not None` tests), S5 (status constants), S6 (signal handlers restored); W5 pins added for M5 (`--force` bypasses overlap only, not caps), M8 (local launch failure exits 1), M1/M1b. The existing run-now test was also repaired: run ids carry a rand6 suffix since the launcher change (pre-existing failure at HEAD).
- **Not fixed (reason / followup):**
  - W3 (CLI writes strip comments / materialize defaults): the fix is in `schedules/store.py` (`save` with `exclude_unset`), outside this unit's paths. Followup ticket.
  - W4 (`_run_local` calls `engine._launcher`, bypassing `_launch`): needs a public `ScheduleEngine.fire()` in `service/schedule_engine.py`. Followup.
  - S4 (`--workflow` vs `--from-workflow` path bases): behaviour change to settle with the owner. Followup.
  - W5 residual: M2 (lock probe branch is outcome-equivalent), M3/M4 (`next` backlog collapse / completed skip), M6, M12, M13, M15, M16 tests not added.
  - DRY: `_build_engine` is still a second copy of `build_schedule_engine`. A shared factory belongs in `service/cli.py`, which was dirty from other units, so it was not touched. Followup: extract and have both call it (the new `test_engine_wiring_matches_service_policy` pins the parity meanwhile).
- Mutation-checked (each fails when reverted): kill switch, global cap, eval interval, webhook source, usage ledger, H2 refusal, queue kill-switch warning, fail-closed registry, W2 workspace, S1/S2/S3.
- Evidence: `pytest tests/schedules` 311 passed; `ruff check`/`format` clean on own paths; `mypy src` 0 errors. `ruff check src` still reports 2 errors in `service/governance_gate.py` (another unit's file).

## Update 2026-11-11 — follow-ups (unit `w18-03-a1-schedule-followups`)
- By: developer · Role: developer · Date: 2026-11-11
- W4 FIXED: public `ScheduleEngine.fire(decision) -> FireIntentRecord | None` (launcher errors propagate, unlike the daemon's never-raise `_launch`); `schedules/cli.py::_run_local` uses it (no `engine._launcher`, no `noqa: SLF001`). Tests: `test_fire_*` in `tests/service/test_schedule_engine.py` (mutation-checked).
- W3 PARTLY FIXED: `ScheduleStore.save` now writes `to_data(exclude_unset=True)`, so a no-op load/save no longer materializes `defaults:` or per-binding default fields; assignments and `model_copy(update=...)` still persist (pydantic tracks them as set). A guard rejects a save whose unset-omitted payload no longer round-trips to the same model. NOT fixed: comments and key order in hand-edited files are still lost -- PyYAML cannot preserve them and `ruamel.yaml` is not a dependency (adding one is an owner decision, outside the T-Sd1Kq7 contract). Tests in `tests/schedules/test_store.py`.
- Kill-switch flake (d): NOT reproduced -- `tests/schedules/test_cli_e2e.py` 6/6 alone and 4x with `tests/service` + `tests/schedules`. w17-02 saw a different test fail each run while another unit's untracked `usage_ledger`/`governance_gate` work was in the tree; most likely cross-unit interference, not a defect in these tests. No change made; reopen with a failing seed/output if it recurs.
- S4 (`--workflow` vs `--from-workflow` path bases): **OWNER DECISION NEEDED**, unchanged. Options: resolve both against the workspace root, or both against cwd.

## Update 2026-11-11 — `--watch` (unit `w21-08-a1-schedule-add-watch`)
- By: developer · Role: developer · Date: 2026-11-11
- `ao schedule add --watch GLOB` (repeatable) + `--watch-on` (repeatable, created|modified), `--debounce`, `--poll-seconds`, `--max-files` now write a `kind: file_watch` binding (T-Fw8Gp4's flag set, landed here because the engine source `FileWatchSource` is already wired). Errors: `--watch` with `--cron/--interval` or `--webhook`; tuning flags without `--watch`; bad `--watch-on` value; escaping globs/non-positive numbers are refused by `WatchSpec`/option `min=` (nothing written).
- `--from-workflow`: already shipped (AC8/AC17 tests exist: `test_from_workflow_*`); no design gap. It derives cron/interval only, as the spec has no file_watch/webhook trigger type.
- Tests (`tests/schedules/test_cli_e2e.py`, CliRunner on root app, fixed clock): binding shape, defaults + list, 5 conflict cases (nothing written), tuning-flags-need-watch, escaping glob/bad numbers, `daemon --once` e2e (baseline 0 -> change 1 -> 0, one fire record).
- AC3/AC15a `next` calling `observe`: done (w22-04); `--webhook` flags exist already.
- Evidence: `tests/schedules/test_cli_e2e.py` 58 passed; `schedules/cli.py` coverage 73% from that file alone (the earlier 90% figure was over the wider schedules suite); `mypy src` 0 errors; ruff clean on own paths. `tests/schedules + tests/service` has 3-4 failures in other units' untracked files (`test_security_*se4bk5*`, `test_cost_cap_cli_e2e.py`), none in watch/cli code.
