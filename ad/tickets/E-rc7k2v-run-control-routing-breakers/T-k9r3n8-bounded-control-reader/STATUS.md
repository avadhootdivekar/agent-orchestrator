# STATUS

- ID: `T-k9r3n8-bounded-control-reader`
- Updated At: 2026-07-09
- State: Done
- Owner: developer

## This update
Ticket created from LLD §3. Generalises the loop-gate reader into one bounded control reader (NFR-1 single surface) + adds a size cap.

By: architect · Role: architect · Date: 2026-07-09 · Comment: Wave-1. `read_gate` stays a thin alias so loop behaviour (memory `loop-iterate-event-only-on-continue`) is untouched.

By: developer · Role: developer · Date: 2026-07-09 · Comment: All 6 acceptance criteria met.
(1) `src/agent_orchestrator/artifacts.py`: added `read_control(store, path) -> dict` — resolves
the path (ArtifactPathError on traversal, unchanged), then checks `exists()` before `size()`
(risk note honoured: `size()` returns 0 for a missing path, so "missing" must be decided first),
then parses JSON and rejects a non-dict root; every failure raises `ControlFileError` naming the
path. Added `read_bool_field(store, path, field) -> bool` and `read_routes(store, path,
field="routes") -> list[str]` as typed helpers on top of it (LLD §3.1 signatures). (2)
`src/agent_orchestrator/errors.py`: new `ControlFileError(OrchestratorError)` base; `GateError`
now subclasses it. (3) `read_gate(store, path, field="continue")` kept byte-identical in
signature/behaviour: it calls `read_bool_field` and translates any `ControlFileError` to
`GateError` so `engine.py`'s `except GateError` around the loop-gate call keeps catching it (the
LLD's literal `return read_bool_field(...)` pseudocode can't preserve the old concrete exception
type on its own since `GateError` is a *subclass* of what `read_bool_field` raises — noting this
as a deliberate, minimal deviation from the pseudocode's exact phrasing, not from its intent or
from any acceptance criterion; AC3/AC4 both require the old type to keep flowing). Message
wording for `read_control`/`read_bool_field` was chosen so it still satisfies every regex the
pre-existing `test_loop_construct.py` gate tests assert on ("not found", "missing field", "must
be bool", "JSON object", "not valid JSON") — none of those test files were touched. (4) Also fixed
a pre-existing `ruff` import-order finding in `artifacts.py` (TODO comment sitting between the
`__future__` import and the stdlib imports) since this ticket already touches every import line
in that file.
Verification: `uv run ruff check .` / `ruff format --check .` / `mypy .` on all files this ticket
touches (`artifacts.py`, `errors.py`, `tests/test_artifacts.py`) → clean; whole-repo `ruff check
.`/`mypy .` surface pre-existing issues in `test_e2e_cli.py`/`test_engine.py`/`test_engine_budget.py`/
`test_executor.py`/`test_project_config.py` — confirmed via `git status` these are untouched by
this ticket. `uv run pytest -q` full suite → 451 passed, 3 skipped (baseline before this ticket,
confirmed by stashing this ticket's files and re-running: 431 passed, 3 skipped — 20 new tests,
zero regressions). `tests/test_loop_construct.py` (32 tests) and `tests/test_engine.py` (19
tests) — the two suites most exposed to `read_gate`'s refactor — pass unchanged.

## Evidence
- Design: `docs-md/lld-run-control-routing-breakers.md` §3, ADR-RC-005.
- Code: `src/agent_orchestrator/artifacts.py` (`read_control`/`read_bool_field`/`read_routes`/
  `read_gate`), `src/agent_orchestrator/errors.py` (`ControlFileError`).
- Tests: `tests/test_artifacts.py` (`TestReadControl`, `TestReadBoolField`, `TestReadRoutes`,
  `TestControlFileErrorHierarchy`) — includes an AC5 test proving the oversized-file case is
  rejected by the `store.size()` guard alone (content is never opened: `Path.read_text` is
  patched to raise if called, and the on-disk content is deliberately invalid JSON as a second
  proof).

## Risks / Blockers
- None. Depended on T-b7q2m4 for `MAX_CONTROL_FILE_BYTES` + error placement — merged and
  confirmed present (`src/agent_orchestrator/models.py:32`).

## Next actions
1. Wave 1 complete (T-b7q2m4, T-h5b2q7, T-k9r3n8 all Done).
2. Wave 2 unblocked: T-c4w6p1 (route cone computation) can consume `build_dag`/graph types from
   T-b7q2m4; T-x8v4d3 (breaker framework registry) can proceed independently.
3. Downstream readers: T-m2h5t7 (router) should call `read_routes`; T-q5n7k2 (verdict breaker)
   should call `read_bool_field` — no router/breaker business logic was added here by design.
