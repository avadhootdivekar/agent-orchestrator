# STATUS

- ID: `T-Te3Qw8-integration-e2e-suite`
- Updated At: 2026-11-11
- State: Partial — NOT Done
- Owner: TBD (assigned at sprint start)

## Sync 2026-11-11 (w36-01, tester): AC6 + AC8a closed (tests only, no src edits)
- New `tests/service/test_schedule_interleave_ac6_ac8a.py` (5 tests) and `tests/schedules/test_run_now_engine_lock_f9.py` (1 test): **6 passed**, 5/5 repeat runs stable; `ruff check` + `ruff format --check` clean on both. `tests/service` + `tests/schedules` together: **1400 passed**.
- AC6: real `Supervisor` + `ScheduleEngine` alternated (fixed `_FakeMonotonic`, engine clock/monotonic stubs). (a) child SIGKILLed inside the engine's launch (mid-evaluation) is detected by the next `Supervisor.tick()` and restarted after backoff while the engine keeps firing; (b) daemon's own `monitor_tick` with a forced monotonic budget overrun: unreached schedule `b` left unchanged (no state, `schedule.tick_budget_exceeded`), supervisor still restarts its child, `b` fires next alternation.
- AC8a: F2 -- child launched via the engine's tick->launcher->shared `ProcessSupervisor`, exit observed with WNOWAIT, next tick reaps (`os.waitpid` -> `ChildProcessError`), next due tick fires (not `overlap_skip`) and the key is retired. F7 -- `jitter=300, grace=60, catch_up=skip`: no early fire, fires at effective_due, no `schedule.catch_up`, record `intended -> launched` once with no pre-existing record. F9 -- second engine on same state dir raises `EngineLockHeldError`, lock stays held until shutdown; `run-now --local` against a REAL engine's lock queues (request file `kind: run_now`, nothing launched) and fires after release. F8 (queue drain intended->launched) was NOT added here (not in this unit's brief).
- Mypy on the two new files: only pre-existing-style test-tree noise, none from the project `mypy src` gate; `src/` untouched. No production change needed; no followups.
- **Remaining:** AC17 (clean-tree whole-suite before/after count, per-module coverage, `mypy src` count) and AC18 (explicit not-exercised list: real uvicorn child, real agent CLI, systemd, network). No independent review recorded.

## Sync 2026-11-11 (w27-02, developer; docs only; HEAD `c4e3871`)
- No change since w24-07 below. **Unfinished:** AC6, AC8a, AC17 (clean-tree whole-suite delta), AC18 not-exercised list; no independent review. Blocked on the T-Se4Bk5 clean-tree gate re-run for AC17.

## Reconciliation 2026-11-11 (By: developer · Role: developer · unit `w24-07-a1-task-status-sync` · docs only)
- Checked against branch `ad/enhancements-10nov` at HEAD `1d5c6f9`. Any older line below that says "not started", "uncommitted" or "in the working tree" is superseded by this block where they disagree.
- State: **Partial**
- Landed: per-trigger e2e tier (`test_e2e_schedules.py`, 11 tests: AC11, 13, 14, 15 + cron/disabled/no-file), AC2/AC3 shared fixtures and `make test-schedules` (w21-04, `0f7dac0`), a real fake-executor scheduled run asserting `state.json` `succeeded` (headline AC11), DST engine tests (T-Se4Bk5 FU-1). AC4-9/16 are covered by `tests/service/test_schedule_engine*.py` and `test_service_schedule_integration.py`.
- **Unmet / open**: **AC6** Supervisor/engine interleaving and **AC8a** (F2 zombie, F7, F9) not re-verified in this suite; **AC17** clean-tree whole-suite before/after delta (last gate numbers: 12155 passed / 5 failed on `4b9d6a3`, failures outside the epic); **AC18** not-exercised list stands: real uvicorn child via the schedule path, systemd, network. No independent review recorded.

## This update
- **2026-11-11 (tester, w21-04) leftovers closed:** `make_schedule_workspace(tmp)` (tests/schedules/_fixtures.py, used by the e2e `Env`); `_allow_testclient_host` hoisted to `tests/conftest.py` (removed from tests/ui + tests/auth conftests); `make test-schedules` added; `test_scheduled_fire_runs_a_real_fake_executor_run_to_succeeded` (a schedule fire runs `ao run --run-id <constructed id>` with fake agents; asserts `state.json` `succeeded` + output file). `test_hld_event_table_is_complete` already passed (table already lists `schedule.governance.*`); the "2 webhook uvicorn" tests (test_webhook_cli_wiring / test_webhook_listener) passed 72/72 here -- not reproducible, no skip added. Also fixed stale `tests/auth/test_auth_off_regression.py` openapi path set (+2 schedule routes). Still NOT exercised: real uvicorn child via the schedule path, systemd, network.
- **2026-11-11 (tester, w20-04):** `tests/schedules/test_e2e_schedules.py` (11 tests, new): per-trigger e2e through the root `CliRunner` + `ao schedule daemon --once` with a fixed clock (`schedules.cli._now`), temp workspace/state/registry (env-redirected), pinned file mtimes, `RunDirSupervisor` stub (creates the run dir; nothing spawned). Covers AC11 (interval: run dir + constructed id + fire record + explicit `run_id=` seam, 2nd `--once` = 0), AC13 (file-watch: one fire, none on no-change, `.orchestrator/` self-trigger guard, debounce deferral then fire on advanced clock), AC14 (webhook: signed delivery via `TestClient` -> fsync'd `requests/` file -> separate `--once` fires; replay 409, no second run; bad signature 401), AC15 (`max_runs: 3`: 3 fires, 4th tick none, `completed` in `list`, `enable` allows a 4th), plus cron due/not-due, disabled, and `--once` no-file/unparseable-file no-ops. AC10/AC12 already covered by `tests/schedules/test_cli_e2e.py`; AC4-9, AC16 are covered in `tests/service/test_schedule_engine.py` / `test_service_schedule_integration.py` (NFR-1 payload test, boot/orphan, isolation) -- not re-asserted here (no redundant layers).
- **Not exercised / open:** real uvicorn child (only the in-process `TestClient`), real agent CLI/`fake`-executor *run* (the supervisor is a stub that creates the run dir, so `state.json` `succeeded` is NOT asserted), systemd, network; AC2/AC3 shared fixtures (`make_schedule_workspace`, hoisted `_allow_testclient_host`) and the `Makefile test-schedules` target were not added (the existing `Env` fixture is reused); AC8a F2 zombie/F7/F9 and AC6 interleaving not re-verified here. Final numbers: see the w20-04 report.
- Ticket created by the `architect` agent as part of the E-Sc9Rt4 design package. Not started.
- Scope: the integration tier (real engine, stubbed processes, single-stepped) and the `CliRunner` e2e tier, plus the two cross-cutting proofs no single task can own: NFR-1 backward compatibility and NFR-2 daemon isolation.

## Evidence
- None yet — no implementation has begun. Design basis:
  `docs-md/scheduler-triggers-hld.md` and `docs-md/adr/ADR-0014-service-owned-scheduler-and-triggers.md`.

## Risks / Blockers
- Blocked until: every implementation task through `T-Ap1Xs3` has landed; the per-trigger e2e ACs are gated individually on `T-Fw8Gp4` / `T-Wh9Kv1` / `T-Un0Lm6`.

## Next actions
1. Read the upstream task's **merged code**, not only the HLD, before starting (the interface-drift
   rule this epic inherits from E-GIytcL).
2. Implement against the acceptance criteria in `TASK.md`, in AC order.
3. Report hard numbers (suite delta, per-module coverage, ruff/mypy) and an explicit list of what is NOT exercised; propose — never apply — any production change a test needs.
