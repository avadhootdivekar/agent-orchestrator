# STATUS

- ID: `T-n9k3r5-observability-ao-status`
- Updated At: 2026-07-09
- State: Done
- Owner: developer

## This update
Ticket created from LLD §10. `ao status` route/not_taken column + tripped-breaker trailer; status.json fields.

By: architect · Role: architect · Date: 2026-07-09 · Comment: Wave-4. Seed `not_taken` into the `write_status` counts dict; keep table alignment.

## Evidence
- Design: `docs-md/lld-run-control-routing-breakers.md` §10.

## Risks / Blockers
- Depends on T-m2h5t7 (route fields), T-x8v4d3 + T-r3j9b6 (tripped_breakers). Both landed in the
  working tree before this ticket started; `route`/`not_taken_reason`/`route_decisions`/
  `tripped_breakers` were already present on `TaskRunState`/`RunState` per `models.py`, and the
  `"not_taken": 0` counts seed was already present in `write_status` — confirmed, not re-added.

## Next actions
1. ~~Extend `write_status` + `_print_status_snapshot`/`_print_state`.~~ Done.
2. ~~CliRunner assertions for routed + tripped runs.~~ Done.

---

## Completion (2026-07-09)

`write_status` (`src/agent_orchestrator/runstate.py`): each per-task dict entry now carries
`"route": getattr(ts, "route", None)` and `"not_taken_reason": getattr(ts, "not_taken_reason",
None)`, matching the existing `output_artifact_path`/`origin` getattr-fallback convention (kept
deliberately, per the ticket, for forward/backward compat even though the fields already exist).
Two new top-level keys added to the snapshot: `"route_decisions": state.route_decisions` (passed
through as-is — already `dict[str, list[str]]`, JSON-serializable) and `"tripped_breakers"`, a
list comprehension flattening each `TrippedBreaker` (`id`, `condition`, `action`, `at`, `detail`
— field names confirmed against `models.py:202-209` before writing, not guessed) into a plain
dict. The pre-existing `"not_taken": 0` counts seed was left untouched, confirmed present.

`cli.py`: both `_print_state` (live `RunState`, used by `run`/`resume`) and
`_print_status_snapshot` (`status.json` dict, used by `status`) gained a `Route` column between
`Status` and `Attempts` (`ts.route or ""` / `task_entry.get('route') or ""` — blank when `None`,
same `f"{x:<N}"` padding convention already used, separator width bumped from 55 to 75 to fit the
new column). After the table, each function now emits one trailer line per tripped breaker in the
exact LLD §10.2 format: `Tripped breakers: <id> (<condition>, action=<action>)` — one line per
breaker (multiple breakers each get their own line) for readability, sourced from
`state.tripped_breakers` (attribute access) in `_print_state` and `snap.get('tripped_breakers',
[])` (dict access) in `_print_status_snapshot`. No trailer at all when nothing tripped.

Tests added in `tests/test_status_artifact.py` (the existing status-focused test file — extended
rather than creating a near-duplicate `test_status_observability.py`, per the ticket's own
guidance to check first): three new classes,
`TestWriteStatusRoutingObservability` / `TestAoStatusRoutingAndBreakerObservability` /
`TestRunLogRoutingAndBreakerEvents` (6 new tests total). Built a `classify -> {bug-fix, doc-fix}`
routed workflow (mirrors the `RouterSpec`/`RouteSpec` patterns in `tests/test_engine_routing.py`)
and, for the breaker-tripped scenario, attached a `stop_file` `CircuitBreakerSpec` (deterministic:
trips on file existence, no FakeExecutor failure-injection plumbing needed through the CLI path) —
this single scenario is simultaneously routed AND breaker-tripped, satisfying AC4's "routed +
breaker-tripped run" wording in one test. Per memory `engine-api-tests-dont-cover-cli`, the
CLI-surface assertions (AC2, AC3, AC5) invoke the real `ao status` command via `CliRunner`, not
`_print_state`/`_print_status_snapshot` directly (consistent with this file's pre-existing
`TestAoStatusCommand` class, which does the same: build the run via the engine API, then assert
on the actual CLI command's output).

All 5 TASK.md acceptance criteria verified:
1. `status.json`: `not_taken` count key present (pre-existing, confirmed), `route` +
   `not_taken_reason` on every task entry, `route_decisions` + `tripped_breakers` top-level
   summaries present — asserted directly against parsed JSON in `TestWriteStatusRoutingObservability`
   (including a non-routed/non-breaker workflow asserting the new keys default to empty, for
   backward compat).
2. `ao status <run>` prints a `Route` column header and, for the tripped-breaker run, the exact
   trailer `Tripped breakers: stop-breaker (stop_file, action=fail)`.
3. The routed run's `ao status` output distinguishes `bug-fix` (`succeeded`, route
   `classify-router:bug`) from `doc-fix` (`not_taken`, route `classify-router:documentation`) in
   the same table — the new Route column doesn't erase the Status-column distinction.
4. `branch.route` and `breaker.trip` both confirmed present in `run.log` for the combined
   routed+tripped run via the `read_jsonl` fixture — no new engine/breakers code, log-delivery-only
   test as scoped.
5. CliRunner E2E tests cover both (a) a routed run and (b) a tripped-breaker run's `ao status`
   output.

Verification: `uv run ruff check .` / `uv run ruff format --check .` clean on every file this
ticket touched (`src/agent_orchestrator/runstate.py`, `src/agent_orchestrator/cli.py`,
`tests/test_status_artifact.py` — one import-sort autofix applied via `ruff check --fix` scoped
to just this file); `uv run mypy` clean on the same files. Pre-existing ruff/mypy findings in
other, untouched test files (`test_e2e_cli.py`, `test_engine.py`, `test_executor.py`,
`test_engine_budget.py`, `test_project_config.py`) predate this session (uncommitted work from
concurrently-landed sibling tickets) and are out of scope per "Do NOT touch" / smallest-correct-
change. `uv run pytest -q` → **576 passed, 3 skipped** (570-baseline + 6 new tests from this
ticket, no regressions); `uv run pytest tests/test_status_artifact.py -q` in isolation → 32
passed.

Did not touch `engine.py`, `spec.py`, `breakers.py`, or `prepare_resume` in `runstate.py`, as
scoped — only added to `write_status` and the two CLI print functions.

By: developer · Role: developer · Date: 2026-07-09 · Comment: T-n9k3r5
observability-ao-status DONE. `write_status` now surfaces `route`/`not_taken_reason` per task and
`route_decisions`/`tripped_breakers` at the top level; `ao status` (both the live-`RunState` and
`status.json` rendering paths) gained a `Route` column and a tripped-breaker trailer line in the
exact LLD §10.2 format. 6 new tests added to the existing `tests/test_status_artifact.py`
(CliRunner-level per `engine-api-tests-dont-cover-cli`). `uv run pytest -q` → 576 passed, 3
skipped; ruff/mypy clean on all touched files. This was the last ticket in Wave 4 — only
T-d8w4v2 (tests+docs refresh, depends on everything) remains to close out the epic.
