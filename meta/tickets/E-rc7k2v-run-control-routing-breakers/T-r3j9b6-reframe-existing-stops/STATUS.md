# STATUS

- ID: `T-r3j9b6-reframe-existing-stops`
- Updated At: 2026-07-09
- State: Done
- Owner: developer

## This update
Ticket created from LLD §8. Re-frame budget/quota/estimate stops additively onto trip→record→act (R3).

By: architect · Role: architect · Date: 2026-07-09 · Comment: Wave-4. Characterization pins committed FIRST; status/exit/existing-events must stay byte-identical (do not weaken a pin).

## Evidence
- Design: `docs-md/lld-run-control-routing-breakers.md` §8, ADR-RC-003, §0-R3.

## Risks / Blockers
- Depends on T-x8v4d3 (record/emit path).

## Next actions
1. Write the three parity pins (engine-API + CliRunner).
2. Add `trip_builtin` at the three terminal sites; re-run pins.

---

## Completion (2026-07-09)

Characterization pins committed first, in `tests/test_stop_reframe_parity.py`: ran the pin
assertions (status/exit-code/pre-existing event) against unmodified engine.py and confirmed
green *before* touching engine.py — proving they are real pins, not tautologies (evidence:
first `uv run pytest tests/test_stop_reframe_parity.py -q` with the post-refactor
`tripped_breakers`/`breaker.trip` assertions temporarily failing — only those lines failed,
all pin lines passed).

Re-framed all **four** current terminal-stop sites in `engine.py` (the LLD's three named
pins plus the provider-429 stop, which LLD §8.1 groups under the same builtin id as site 2)
additively onto `record_trip(...)` (breakers.py's `trip_builtin`, built by T-x8v4d3):
imported `record_trip` alongside the already-imported `evaluate_breakers`, and imported the
three named `BUILTIN_*` id constants from `models.py` (no magic string literals for ids).
At each site, `record_trip(...)` is called immediately before the existing
`state.status = "failed"` line; every existing line (`run_log.*` call, `state.status`,
`failed = True`, `break`) is untouched, byte-for-byte:

1. Unsatisfiable estimate → `BUILTIN_BUDGET_UNSATISFIABLE`, condition
   `"projected_cost_exceeds"`, detail `{task_id, estimate, blocked_by}`.
2. Budget-exhaustion stop (gate path, `on_exhaustion=="stop"`) → `BUILTIN_BUDGET_EXHAUSTED`,
   condition derived from `_decision.blocked_by` (`"total_tokens"` when blocked by the total
   cap, `"rate_window"` when blocked by the rate window — descriptive, not a single
   hardcoded string per the ticket's guidance), detail `{task_id, blocked_by,
   next_available_epoch}`.
3. Quota max-wait exceeded → `BUILTIN_QUOTA_MAX_WAIT`, condition
   `"quota_exhaustion_wait_exceeded"`, detail `{task_id, elapsed_seconds,
   max_wait_seconds}`.
4. Provider-429 budget-exhaustion stop (429 branch, `else: # stop`) → same
   `BUILTIN_BUDGET_EXHAUSTED` id as site 2, condition `"provider_429"` (keeps the two sites
   distinguishable in `tripped_breakers` while sharing the rollup id per LLD §8.1), detail
   `{task_id, next_available_epoch}`.

Transient wait/retry branches (429 wait, quota poll wait, budget wait — `on_exhaustion==
"wait"`) were left completely untouched, as required; `TestTransientWaitsAreNotTrips` proves
each one drives the run to eventual success with `state.tripped_breakers == []` and no
`breaker.trip` event logged.

Both engine-API (`Orchestrator.run` directly) and CliRunner (`ao run`) levels are covered
per memory `engine-api-tests-dont-cover-cli`. The CliRunner quota-max-wait test needed a
deterministic trick since the CLI's `DispatchExecutor` always builds a bare `FakeExecutor()`
with no executor-behavior config and no injectable clock/sleeper: `monkeypatch.setattr`
replaces the `FakeExecutor` name inside `agent_orchestrator.executors` (looked up at
`DispatchExecutor.__init__` call time) with one preconfigured for
`quota_exhausted_tasks`, and `--quota-max-wait -1` expires the max-wait on the very first
occurrence (elapsed is exactly 0 the instant it's first observed, so `remaining = -1 - 0 <=
0` immediately, no real sleep needed). Note `--quota-max-wait 0` would NOT work: `cli.py`'s
three-layer settings merge uses `x or default`, so a falsy `0` silently falls through to the
21600s built-in default — a separate, out-of-scope defect (see `E-st5p3q`-adjacent
`project_model_override_clobbers_agents`-style pattern); `-1` is truthy and avoids it
without touching `cli.py`.

Tests added (all in the new `tests/test_stop_reframe_parity.py`, 10 cases): one engine-API +
one CliRunner test per named site (unsatisfiable, gate-path exhaustion, quota max-wait) with
both the pin and the new tripped_breakers/breaker.trip assertions; one bonus engine-API-only
pin for the provider-429 site; three "transient wait is not a trip" tests (429 wait, quota
poll wait, budget-window wait). All engine-API tests use a fixed clock/injected sleeper (or a
deterministic stepping clock for the quota case) and read both `run.log` (JSON lines,
existing `read_jsonl` fixture) and `state.json` (`tripped_breakers`) — no real sleeps
anywhere (CLAUDE.md determinism rule).

Verification: `uv run ruff check .` / `uv run ruff format --check .` / `uv run mypy .` clean
on every file this ticket touched (`src/agent_orchestrator/engine.py`,
`tests/test_stop_reframe_parity.py`); pre-existing findings in untouched files (owned by
other in-flight tickets in this epic/session) predate this change and are out of scope.
`uv run pytest -q` → 570 passed, 3 skipped in the working tree at completion time (551
ticket-baseline + 10 new from this ticket + 9 from a sibling ticket's concurrently-landed
`tests/test_resume_replay.py`); re-running just this ticket's own test file in isolation:
`uv run pytest tests/test_stop_reframe_parity.py -q` → 10 passed.

All 5 TASK.md acceptance criteria verified: (1) three named characterization pins committed
and confirmed green against the pre-refactor code first; (2) all three (plus the bonus
site-4) pins still pass byte-identical on status/exit/existing-event AND now additionally
record the mapped `builtin.*` breaker + a `breaker.trip` event; (3) transient waits proven to
emit no `breaker.trip` and append nothing to `tripped_breakers`; (4) all four builtin ids
used are the named `models.py` constants, no magic literals; (5) ruff/mypy/pytest all clean,
no regressions, no unrelated behaviour changes in `engine.py` (only the four additive
`record_trip` calls + the accompanying import extension).

By: developer · Role: developer · Date: 2026-07-09 · Comment: T-r3j9b6
reframe-existing-stops DONE. Re-framed all four current stop sites (the LLD's three named
sites plus the provider-429 stop sharing site 2's builtin id) additively onto
`record_trip`/`trip_builtin`; characterization pins written and proven green first, then
extended post-refactor; transient waits proven untouched. Zero change to
`spec.py`/`cli.py`/`breakers.py`'s registry/`runstate.py` (out of scope, respected). Unblocks
T-n9k3r5 (status trailer) and T-d8w4v2 (docs reconcile).
