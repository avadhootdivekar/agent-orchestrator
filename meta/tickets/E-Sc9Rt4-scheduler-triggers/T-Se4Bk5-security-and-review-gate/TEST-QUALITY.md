# T-Se4Bk5 — test-quality review of the scheduler-triggers tests

- By: reviewer agent (overseer unit `w21-05-a1-test-quality-review`, Opus) · Role: reviewer · Date: 2026-11-11
- Scope (about 10.8k lines):
  - `tests/schedules/*`;
  - in `tests/service/`: `test_{schedule_engine,schedule_launch,fire_store,service_schedule_integration,until,watch,capped_resume,webhook,webhook_listener,webhook_cli_wiring,governance_gate,governance_security_gate_ltw88y,security_gate_se4bk5}.py`;
  - `tests/auth/test_schedules_auth.py` and `tests/ui/test_schedule_usage_api.py`.
- Dimensions: weak assertions, mock-only coverage, missing tiers, clock/seed determinism, flaky risk and missing edge cases.
- Method: I read each test file against its source module. For each finding I named the source line I would break and checked whether the test still passed (a mental mutation). Every fix below was checked by a **real mutation** on a scratch copy of `src/` (`PYTHONPATH=<copy>/src`; the shared tree was never mutated). The old test passed the mutation; the new one fails it.
- Fix commit: **`b5fe677`** on `ad/enhancements-10nov` (test-only, 9 files, +133/-21).

## Verdict

The suite is broadly healthy:
- Clocks are injected almost everywhere (`_now`, `ScheduleEventLog(clock=)`, `Mono`, dashboard `clock=`).
- `HOME` and the service paths are redirected in every e2e fixture.
- The webhook tests run in-process through `TestClient`. The real-socket listener tests use ephemeral sockets passed in directly, so there are no port races.
- The windows/DST, security-gate and governance-boundary tests are strong.

The real problems are **tests that cannot fail**. Several security-critical and correctness-critical tests were refused by an earlier branch than the one they name, or did the engine's work themselves, so they passed with the protected code removed. The worst of these are fixed. The biggest open gap is that **cron and timezones are never driven through the engine** (FU-2).

## Fixed in `b5fe677` (mutation-checked)

| ID | Sev | Location | Finding | Fix / evidence |
|---|---|---|---|---|
| TQ-1 | High | `test_webhook.py::test_signature_over_raw_bytes_not_reserialised` | It sent `X-Hub-Signature-256` to the default `ao` binding, so the request was refused as `wrong_scheme` before any HMAC ran. Re-serialising the body before signing would have gone unnoticed. | Now uses the `github` binding. Asserts the reason is `bad_signature`, and the exact signed bytes get a 202 as a control. |
| TQ-2 | High | `test_webhook.py::test_missing_signature_401` | With no signature the harness also sends no timestamp, so the request was refused as `bad_timestamp`. A fail-open on `received is None` would pass. | It now sends a valid timestamp and no signature, on both the `ao` and `github` schemes. Asserts `reason == "bad_signature"` and that nothing was persisted. |
| TQ-3 | High | `test_webhook.py` replay window | Only +299 and -301 were tried. Changing `skew > REPLAY_WINDOW_SECONDS` to `>=`, or dropping `abs()`, would survive. | Added `test_replay_window_boundary_both_directions` (±300 gets 202, ±301 gets 401 `stale`). **Mutation `>=` → 2 failures.** |
| TQ-4 | High | `test_schedule_engine.py::test_f2_real_supervisor_child_exit_is_reaped_and_key_retired` | The wait loop's `Popen.poll()` reaped the child itself, so the test passed with `_reconcile_supervisors` turned into a no-op. Its comment admitted this. | It now waits with `os.waitid(..., WNOWAIT)` so the child stays a zombie, then asserts the tick removed it from `sup._children`. It also uses `time.monotonic` instead of `time.time`. **Mutation (reconcile → `pass`) → fails.** |
| TQ-5 | High | `test_e2e_schedules.py` self-trigger guard | The binding watches `inbox/*.md`, which can never match `.orchestrator/runs/inbox.md`. The test passed with `.orchestrator` removed from the ignore set. | New `test_file_watch_ignores_orchestrator_dir_even_when_the_glob_matches` uses `**/*.md`, with a positive control. **Mutation (drop `.orchestrator` from `watch.py` ignore set) → fails; the old test still passes.** |
| TQ-6 | High | `test_store.py` `mutate` locking | The re-read test is sequential, and the 8-process test only fails on a lucky interleave. Removing `fcntl.flock` is not reliably caught. | New `test_mutate_holds_exclusive_lock_while_fn_runs` uses a deterministic `LOCK_NB` probe from inside `fn`. **Mutation (flock → `pass`) → fails.** |
| TQ-7 | Medium | `test_governance_gate.py::test_l2_…under_lock_before_reading` and `test_concurrent_run_now_threads_launch_exactly_once` | The first checked only call order, not that the lock is held. The second used `join()` and `Barrier()` with no timeout, so a regression would hang CI. | Added a `LOCK_NB` probe inside `usage()`, and `timeout=10` on the joins and barrier plus an `is_alive` assertion. |
| TQ-8 | Medium | `test_governance_gate.py` | `limits()` builds only one cost period. Neither the tightest-period selection nor the `max(0, remaining)` clamp was exercised. | Added `test_cost_gate_reports_tightest_period_and_clamps_overspend`. |
| TQ-9 | Medium | `test_webhook.py` `TokenBucket` | Nothing idles long enough to overfill the bucket, so removing the `min(rate, …)` burst cap would survive. | Added `test_token_bucket_burst_capped_at_rate_after_long_idle`. |
| TQ-10 | Medium | `test_schedule_engine.py::test_catch_up_boundaries` | The cases 59/61 and 3599/3601 straddle the edges but never hit them, so `lateness > grace` or `> window` could become `>=`. | Added rows for exactly 60 and 3600. **Mutations `>= grace` → 2 failures, `>= window` → 1 failure.** |
| TQ-11 | Medium | `test_watch.py::test_m2_revert_with_preserved_mtime_fires_again_in_engine` | `reason != "already_fired"` passes for any non-fire result (`gated`, `not_due`, `overlap_skip`). | The test now ends the first run and asserts `d.fire and d.reason == "due"`. |
| TQ-12 | Medium | `test_service_schedule_integration.py::test_add_set_list_schedules_flag` | `"True" in listing` always passes because the AUTORESUME column prints `True`. | Asserts the SCHEDULES column itself. |
| TQ-13 | Medium | `test_e2e_schedules.py::test_daemon_once_unparseable_schedules_file_does_not_crash` | It only checked "no crash". Silently swallowing the bad file would pass. | Asserts that `schedule.file_invalid` is in the event log. |
| TQ-14 | Low | Several files | Tautologies and vacuous checks: a `… or len(fired) == 1` tautology and a redundant `… or Path(p1).is_file()` (`test_schedule_launch.py`); a "not an error" directory gate that never checked events, and a "same kind of bad read" that read a *missing* file (`test_until.py`); an unchecked `service run` exit code; a secret-leak scan with no positive control (it would pass if logging stopped). | Each assertion was tightened. |

The "`runs_used` only ever 0" finding against `tests/ui/test_schedule_usage_api.py` needed no change: it is already resolved at HEAD. `test_usage_reflects_fire_records_through_the_real_ledger` asserts `runs_used == 2`.

## Open follow-ups (not small, or in files other units were editing)

| ID | Sev | Location | Finding | Proposed change |
|---|---|---|---|---|
| FU-1 | High | `test_webhook.py::test_n1_downgrade_after_nonce_eviction_is_401` | It does not test eviction. The forged request is refused as `wrong_scheme` whatever the nonce cache holds, so it duplicates the pinned-scheme test. Its 700 setup posts are unasserted, and at **12.9 s it is the slowest test in scope**. `test_n3_…` takes 8.7 s. | Assert `reason == "wrong_scheme"` and drop the churn. Test eviction safety on a `github` binding with a small `nonce_cache_factory`. |
| FU-2 | High | Engine tier (all 7 service files) | **No `kind="cron"` and no non-UTC timezone anywhere in the engine tests.** The spring-forward gap, the fall-back fold (fire twice or once? distinct `fire_key`s?) and a restart across a DST change are uncovered. HLD says the DST double-fire is pinned by a test, but no test drives it through the engine. | Add engine tests with `kind="cron"` and `timezone="America/New_York"` that tick across 2026-03-08 and 2026-11-01 on a fixed clock. Assert the exact `scheduled_for` list and that fire keys are unique. |
| FU-3 | High | `test_service_schedule_integration.py` (real-engine opt-in test, about lines 272-283) | `build_schedule_engine` uses the real `time.monotonic`. The second tick is therefore rate-limited (`[]`), and the first is a first sighting. Both "fires nothing" assertions are vacuous; only the snapshot check guards the opt-in filter. | Add a `monotonic=` seam to `service/cli.py::build_schedule_engine` (a src change, owner T-Sv5Hb3), seed the anchor, and tick twice. |
| FU-4 | Medium | `fire_store.py::_persist` + `test_fire_store.py` (about lines 226-233) | `_persist` fsyncs the file and then calls `os.replace`, but never fsyncs the directory. After a power loss the rename can be lost, which reopens an at-most-once key. The test asserts **exactly one** fsync, so it would fail if the correct fix were made. | Add a parent-directory fsync. Make the test assert ordering (file fsync, then replace, then directory fsync), not a count. Owner: T-Fr2Nx8. |
| FU-5 | Medium | `tests/schedules/test_cli_e2e.py` (about lines 283-290, 226, 406, 592, 626) | `list --all` is untested: the second workspace has no bindings, so it is skipped either way, and the `service add` exit code is unchecked. Several error-path tests check only for a non-zero exit. The file was being edited concurrently by another unit, so I left it. | Add a bound second workspace and assert the ids `["nightly", "o1"]` with and without `--all`. Assert the specific error messages (`no such schedule 'nope'`, `mutually exclusive`, `drop --cron/--interval`, `invalid cron expression ''`) and `exit_code == 1`. |
| FU-6 | Medium | `test_cli_governance_e2e.py` (`files_mtimes`) | The "read-only" checks compare mtimes on files written moments earlier. A same-tick rewrite keeps the mtime, so the check misses it. | Set the mtimes to a fixed past time (`os.utime(p, ns=(10**9, 10**9))`) before the snapshot, or compare content hashes. |
| FU-7 | Medium | File-watch e2e (`test_e2e_schedules.py`, `test_cli_e2e.py` watch tests) | `watch.py`'s 0.25 s scan budget runs on the real clock, and a scan over budget silently means "no fire". On a loaded CI box these can see 0 fires instead of 1, and the CLI has no clock seam. | Add a monotonic or budget seam reachable from `_build_engine`, e.g. a test-only env override. |
| FU-8 | Medium | Engine / capped resume | Three untested paths: the first-sighting anchor path (`schedule_engine.py` "first sighting"); the crash window between `update_governance(cap_state="resumed")` and the spawn in capped resume; and engine drain wired to the real `ScheduledLauncher.launch_capped_resume` (each half is covered only separately). | Add a no-anchor tick, then a restart, then exactly one fire. Simulate a crash after the governance write and assert what the next sweep does. Add one wired drain test. |
| FU-9 | Medium | Webhook | Several gaps: no test that concurrent identical deliveries give exactly one 202 (removing the `accept()` lock would survive); no `secret_file` oversize, FIFO or `O_NOFOLLOW` tests; no signed POST over a real socket; `test_webhook_listener.py` "slow body not cut off" doesn't hold the body open, and its `recv` loop spins forever on early close; the `WEBHOOK_QUEUE_MAXSIZE` monkeypatch is a no-op (the default is bound when the function is defined). | Add the tests. In the loop, use `if not (chunk := s.recv(4096)): break`. Drop the no-op patch. |
| FU-10 | Medium | `schedules/models.py` cron gap check (about lines 167-175) + `test_models.py` | The check is dead code: 5-field cron can't fire more often than every 60 s, and 6-7-field crons are rejected earlier. Deleting it breaks no test, yet a test is named for it. | Delete the branch, or keep it as a documented guard, and rename the test. Owner: T-Sd1Kq7. |
| FU-11 | Low | Various | `fire_key` hashes `tmp_path`, so the jitter `off > 0` check fails about 1 time in 3601. One test counts calls to `monotonic` (`jump_at_call = calls + 3`). The cross-process key test doesn't vary `PYTHONHASHSEED`. The store test uses a `fork` multiprocessing context. The tick-budget test has a wall-clock `perf_counter` bound and a vacuous `tick_budget_exceeded == []`. The 255-socket test assumes `ulimit -n` ≥ ~600. There are misnamed tests and overclaiming docstrings (`test_windows.py` Apia claim, `test_until.py` `runs_count`, `test_fire_store.py` "clock rewind", `test_governance_model.py` "defaults alone"). | Fix opportunistically as the files are touched. |

## Test evidence

| Run | Result |
|---|---|
| Baseline, before the edits (shared tree, the 24 in-scope files) | **950 passed, 1 xfailed** in 53 s |
| Committed state verified in a clean `git worktree` (HEAD `47afab8` + this patch, nothing else) | **959 passed, 1 failed, 1 xfailed** |

- The one failure in the clean worktree is `test_capped_resume.py::test_only_the_resume_drain_calls_launch_capped_resume`. It is a source-grep test that also finds `schedules/cli.py`. It is **pre-existing at committed HEAD** and is not in a file this unit touched. Another unit's staged, uncommitted edits fix it in the shared tree.
- `ruff check` and `ruff format --check` are clean on all 9 changed files.
