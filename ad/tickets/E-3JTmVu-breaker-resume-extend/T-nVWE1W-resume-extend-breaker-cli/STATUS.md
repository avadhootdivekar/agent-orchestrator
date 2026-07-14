# STATUS

- ID: `T-nVWE1W-resume-extend-breaker-cli`
- Updated At: 2026-07-14
- State: **Done**
- Owner: developer

## This update (2026-07-14)
`cli.py`'s `resume` command gained `--extend-breaker <id>`, `--extend-by-seconds <float>`,
`--extend-by-same` (flag). Validation: unknown id -> `typer.echo(err=True)` + `Exit(1)`; both/
neither of the two extend flags given -> `Exit(1)` (delegated to `apply_breaker_extension`'s own
`SpecValidationError`, caught and echoed); an extend flag given without `--extend-breaker` also
rejected (defensive addition, not explicitly required but a natural companion check). The
extension is applied to the already-loaded + `prepare_resume`'d `existing` RunState BEFORE
`orch.run()` — confirmed by reading the full `resume` body: `orch.run()`'s first action is
`self._runstate.save(state)` (engine.py ~:135), and since `existing` is the SAME object instance
passed into `orch.run(..., run_state=existing)`, no extra `rs_store.save()` call was needed.
`attach_run_handler`/`get_run_logger` are invoked directly in the CLI (mirroring the exact
run-directory-path construction convention `cli.py`'s own `status` command already uses,
`Path(workspace) / ".orchestrator" / "runs" / run_id / ...` rather than reaching into
`RunStateStore`'s private `_path`) so the `breaker.extend` event lands in the SAME `run.log` the
resumed run itself writes to — `attach_run_handler` is idempotent, so `Orchestrator.run()`'s own
internal attach call (same run_id/log_path) is a safe no-op afterward. `typer.echo`s an
`Extended breaker '<id>': <old> -> <new>` confirmation line.

`tests/test_resume_extend_breaker_cli.py` (new, 6 tests, all passing): 4 validation-error tests
(unknown id, both flags, neither flag, extend-flag-without-extend-breaker) + 2 full-mechanism
CliRunner end-to-end tests. Since `ao resume` has no way to inject a fake/stepping clock (unlike
engine-API tests), both e2e tests manufacture an already-tripped `RunState` directly (same
convention as `tests/test_resume_replay.py`'s `TestInjectedTaskCountBreakerResume`) with
`started_at` 2 real hours in the past so `run_wall_clock_seconds`' condition genuinely holds
under the CLI's real, uninjectable clock: (1) `--extend-by-seconds 100000` pushes the effective
threshold (100 -> 100100) comfortably past the ~7200s real elapsed -> the run continues past
task "b"/"c" to `succeeded`, confirmation line printed, `tripped_breakers == []` after; (2)
`--extend-by-same` on a threshold of 1000 doubles it to 2000, still well under ~7200s real
elapsed -> the breaker trips AGAIN on the very next boundary (task "b" succeeds, then the
boundary check fails again before task "c" dispatches) -- proving the un-latch is real, not a
permanent bypass, exactly satisfying TASK.md AC5's "breaker CAN trip again later" requirement.

By: developer · Role: developer · Date: 2026-07-14 · Comment: T-nVWE1W DONE. All 5 acceptance
criteria verified with tests; the pre-existing `tests/test_resume_replay.py`'s
`TestE2EResumeReplayRouting` CLI suite (AC4: `--extend-breaker` omitted behaves as before) stays
green unmodified, confirming zero regression to the default (no-extend) resume path.

## Post-review fix (2026-07-14)
The `T-gzG0EI` reviewer pass (agent id `a862873eeae56a8a8`) found and reproduced a real bug: the
extension was applied to the loaded `RunState` before other CLI-only validation (e.g.
`--on-exhaustion`) that can `typer.Exit(1)` first — since the extension relied solely on
`orch.run()`'s later save, an already-logged/confirmed extension could be silently discarded if
an unrelated flag was also bad. Fixed by adding an explicit `rs_store.save(existing)`
immediately after `apply_breaker_extension` returns, so the extension is durable the moment it's
applied and confirmed, independent of anything checked afterward. New test
`test_extension_persists_even_if_a_later_unrelated_flag_fails_validation` proves the extension
(and un-latch) survives a subsequent bad `--on-exhaustion` value even though that invocation
still correctly exits 1. `uv run pytest tests/test_resume_extend_breaker_cli.py -q` -> 7 passed
(was 6 before this fix).

By: developer · Role: developer · Date: 2026-07-14 · Comment: Post-review fix applied and
pinned by a new test; full suite re-confirmed green (676 passed, 3 skipped).

## Evidence
- `src/agent_orchestrator/cli.py`: `resume` command's 3 new options + extension-application
  block.
- `tests/test_resume_extend_breaker_cli.py` (new, 6 tests, all passing).

## Risks / Blockers
- None.

## Next actions
1. None — task complete. `T-gzG0EI` documents this flag in the LLD addendum (done) and runs
   final full-suite verification + the reviewer pass.
