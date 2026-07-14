# STATUS

- ID: `T-yX1Oi5-breaker-extend-core`
- Updated At: 2026-07-14
- State: **Done**
- Owner: developer

## This update (2026-07-14)
`RunState.breaker_overrides: dict[str, float] = {}` added (defaulted for NFR-5, same pattern as
`tripped_breakers`/`route_decisions`). `apply_breaker_extension(state, spec, *,
extend_by_seconds, extend_by_same, clock, run_log) -> float` landed in `breakers.py` as a
standalone function matching `record_trip`'s exact calling convention (required `clock`/
`run_log`, caller owns persistence + logging infra) — validates exactly-one-of
extend_by_seconds/extend_by_same (raises `SpecValidationError` otherwise), validates
`spec.threshold` is set, computes `new_threshold = current_effective + delta` (delta = the
explicit seconds OR the ORIGINAL `spec.threshold` again for "by same amount"), writes
`state.breaker_overrides[spec.id]`, removes matching `TrippedBreaker` record(s) (un-latch), logs
`breaker.extend`. `evaluate_breakers` now resolves `state.breaker_overrides.get(spec.id,
spec.threshold)` ONCE per spec and evaluates a `model_copy`'d spec when an override is present —
centralized, uniform across every condition (proven against a non-time condition,
`task_failures`, not just wall-clock). Deliberate design choice: `clock`/`run_log` are REQUIRED
keyword params (not defaulted), mirroring `record_trip`'s own signature exactly, rather than
introducing a hidden module-level logger.

Regression proof (NFR-1): `uv run pytest tests/test_resume_replay.py
tests/test_stop_reframe_parity.py -q` -> **19 passed**, unmodified, zero new failures — proves
non-extended breakers' latch-forever behaviour is completely unchanged (the `.get(spec.id,
spec.threshold)` fallback means a spec with no override entry evaluates against its own
unchanged threshold, byte-identical to before this change).

New test file `tests/test_breaker_extension.py` (10 tests): isolated `apply_breaker_extension`
tests (extend_by_seconds, extend_by_same using the ORIGINAL threshold not the current effective
value across two successive extensions, un-latch-only-the-named-id, both-given error,
neither-given error, unset-threshold error, `breaker.extend` log-event shape via a `MagicMock`
run_log spy); `evaluate_breakers` override-resolution integration tests (task_failures
condition, not wall-clock, control case with no override still byte-identical); and an
end-to-end mechanism proof (trip -> extend/un-latch -> does not immediately re-trip -> trips
again once new activity exceeds the EXTENDED threshold).

By: developer · Role: developer · Date: 2026-07-14 · Comment: T-yX1Oi5 DONE. All 5 acceptance
criteria verified with tests; regression suites explicitly re-run and green (19/19, unmodified).

## Post-review fix (2026-07-14)
The `T-gzG0EI` reviewer pass (agent id `a862873eeae56a8a8`) flagged (Warning, not Critical) that
`apply_breaker_extension` had no sign check on `extend_by_seconds` — a zero/negative value would
silently shrink or no-op the effective threshold, bypassing the schema's own
`exclusiveMinimum: 0` invariant on thresholds. Fixed: `apply_breaker_extension` now raises
`SpecValidationError` for `extend_by_seconds <= 0`. New parametrized test (`bad_value in [0,
-10]`) added; confirms `state.breaker_overrides` is untouched when rejected. `uv run pytest
tests/test_breaker_extension.py -q` -> 12 passed (was 10 before this fix).

By: developer · Role: developer · Date: 2026-07-14 · Comment: Post-review fix applied and
pinned by a new test; full suite re-confirmed green (676 passed, 3 skipped).

## Evidence
- `src/agent_orchestrator/models.py`: `RunState.breaker_overrides` field.
- `src/agent_orchestrator/breakers.py`: `apply_breaker_extension`, `evaluate_breakers`'s
  centralized override resolution, module docstring addendum.
- `tests/test_breaker_extension.py` (new, 10 tests, all passing).
- Regression: `uv run pytest tests/test_resume_replay.py tests/test_stop_reframe_parity.py -q`
  -> 19 passed (unchanged from pre-epic baseline for those two files).

## Risks / Blockers
- None.

## Next actions
1. None — task complete. `T-nVWE1W` (CLI wiring) built directly on `apply_breaker_extension`.
