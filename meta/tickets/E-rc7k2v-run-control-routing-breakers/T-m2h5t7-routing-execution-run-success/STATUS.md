# STATUS

- ID: `T-m2h5t7-routing-execution-run-success`
- Updated At: 2026-07-09
- State: Done
- Owner: developer

## This update
DONE. Router-success hook (`_on_router_success`/`_route_fail`), not-taken skip, join handling
(`_apply_join` for `join="all"`/`join="any"` + the `any`-join missing-input relaxation), and injected-task
route inheritance all landed in `engine.py`, wired exactly at the LLD §5.1 hook points: not-taken skip ahead
of the existing `if tid in done: continue`; join evaluation before the missing-input check; router-success
hook inside the `ts.status == "succeeded"` branch (after `done.add(tid)`, before the T-x8v4d3 breaker
evaluation so `route_decisions`/`not_taken` are always settled first). `cones`/`membership` computed once via
`compute_cones` right after the post-injection-merge `build_dag` call and cached for the whole `run()` call
(never recomputed on injection, per LLD §4.3). Emit/loop injection sites now pass `route=ts.route` (the
emitter's route) into `_inject`, giving injected `TaskRunState`s the correct inherited route by construction —
a not-taken emitter never dispatches, so it never injects (R4 holds automatically, no extra guard needed).
`RunStateStore`'s `write_status` counts dict seeded with `"not_taken": 0` (small additive fix flagged back in
T-b7q2m4's ticket notes). All 7 acceptance criteria covered by new tests in `tests/test_engine_routing.py`
(single-route selection, multi-select, join handling incl. the `any`-relaxation, empty/unknown verdict with
and without `default_route`, injected-task route inheritance + not-taken-emitter-never-injects, determinism
across repeated runs) plus a CliRunner E2E routing test appended to `tests/test_e2e_cli.py` (memory
`engine-api-tests-dont-cover-cli`).

**Delivery note**: the implementing subagent's session hit the platform's usage/session limit after finishing
the implementation, tests, and its own `TASK.md` status flip, but before writing this `STATUS.md` or the epic
rollup entry. The orchestrating session independently verified the actual code/tests before completing this
write-up (not just trusting the crashed subagent's `Status: Done` claim): read the full `engine.py` diff
against the LLD §5 pseudocode line-by-line, read `tests/test_engine_routing.py`'s assertions directly (not
just its existence), and re-ran the full suite + lint fresh. One genuinely new, in-scope lint issue was found
and fixed directly (an over-long comment line in `tests/test_dynamic_injection.py` introduced earlier this
epic) — confirmed via `git stash`/`git stash pop` against the pre-epic commit that every other flagged lint
issue in touched test files predates this epic entirely (out of scope, matching the established pattern from
Waves 1-2).

`uv run pytest -q` → **529 passed, 3 skipped** (516 passed/3 skipped baseline after T-q5n7k2 landed in
parallel + 13 new, zero regressions). `ruff check`/`ruff format --check`/`mypy` clean on every file this
ticket touched (`engine.py`, `runstate.py`, `tests/test_engine_routing.py`; the one new issue in
`tests/test_dynamic_injection.py` is now fixed).

By: developer · Role: developer · Date: 2026-07-09 · Comment: T-m2h5t7 routing-execution-run-success
implementation DONE (router hook, not_taken skip, join+relaxation, injection route inheritance, run-success
unchanged) — session ended before self-reporting; see delivery note above.

By: developer · Role: developer · Date: 2026-07-09 · Comment: Independently verified the above (diff review
against LLD §5, fresh full-suite run, fresh lint run, one new lint nit fixed) and completed status-doc sync.

## Evidence
- Design: `docs-md/lld-run-control-routing-breakers.md` §5, §0-R1.
- Code: `src/agent_orchestrator/engine.py` (`_router_for_task`, `_on_router_success`, `_route_fail`,
  `_apply_join`, not-taken skip, `any`-join input relaxation, `_inject(..., route=...)`);
  `src/agent_orchestrator/runstate.py` (`not_taken` counts-dict seed).
- Tests: `tests/test_engine_routing.py` (new, all 6 functional ACs), `tests/test_e2e_cli.py`
  (`TestE2ERouting`, CliRunner E2E per AC7).

## Risks / Blockers
- None. Depended on T-c4w6p1, T-k9r3n8, T-b7q2m4 — all Done.

## Next actions
1. Done. `route_decisions`/`not_taken` ready for T-t4m8x1 (resume replay) and T-n9k3r5
   (status/route column) to consume.
