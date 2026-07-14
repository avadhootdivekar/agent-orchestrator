# EPIC: E-3JTmVu-breaker-resume-extend

## Metadata
- Epic ID: `E-3JTmVu-breaker-resume-extend`
- Title: Circuit-breaker resume extensions — `run_active_seconds` condition + operator threshold bump/un-latch
- Owner: dev-epic
- Created: 2026-07-14
- Last Updated: 2026-07-14
- Status: **Done** — all 4 tickets complete, reviewer pass done (2 critical bugs found + fixed),
  full suite green, docs reconciled

## Summary
- Goal: Close two follow-on gaps flagged (but explicitly deferred) by `E-rc7k2v-run-control-routing-breakers`:
  1. `run_wall_clock_seconds` measures elapsed time from a run's ORIGINAL `started_at` (a documented "deadline" semantic) — a run paused for hours/days and resumed can trip immediately. Add a new `run_active_seconds` condition that sums only settled-task `(ended_at - started_at)` deltas, naturally immune to any operator pause/resume gap.
  2. `evaluate_breakers`'s per-id latch (`state.tripped_breakers`) is permanent for the run's life — `T-t4m8x1`'s STATUS.md explicitly flagged "the always-latched case is a real gap for whoever owns resume+breaker semantics next." Add a scoped, resume-time `--extend-breaker` mechanism to bump a specific tripped breaker's threshold and un-latch it, WITHOUT blanket-unlatching every other breaker (which would regress existing intentional resumability).
- Scope In (MVP): `run_active_seconds` schema+model+breaker class+tests; `RunState.breaker_overrides`; `apply_breaker_extension`; centralized override resolution in `evaluate_breakers`; `ao resume --extend-breaker/--extend-by-seconds/--extend-by-same`; docs addendum.
- Scope Out (non-MVP, explicit): live/in-process threshold mutation for an already-running invocation (no hot-reload, no stop-file-style live poll); blanket un-latch-everything-on-resume.

## Foundation / prior art
- `E-rc7k2v-run-control-routing-breakers` (Done, 2026-07-09) — NOT reopened, NOT modified. This epic only adds new, additive surface on top of `breakers.py`/`models.py`/`cli.py`'s `resume` command.
- `docs-md/lld-run-control-routing-breakers.md` — the LLD this epic adds an addendum section to (does not replace it).

## Requirements
- FR-1: `run_active_seconds` breaker condition (schema enum + `RunActiveSecondsBreaker`, registered in `BREAKER_REGISTRY`).
- FR-2a: `RunState.breaker_overrides: dict[str, float] = {}` (NFR-5 defaulted).
- FR-2b: `apply_breaker_extension(state, spec, *, extend_by_seconds, extend_by_same) -> float` standalone function in `breakers.py`.
- FR-2c: `evaluate_breakers` resolves the effective threshold centrally (`state.breaker_overrides.get(spec.id, spec.threshold)`), evaluated uniformly for every condition.
- FR-2d: `ao resume --extend-breaker <id> [--extend-by-seconds <f> | --extend-by-same]` CLI flags.
- NFR-1: Zero regression in `tests/test_resume_replay.py` / `tests/test_stop_reframe_parity.py` (non-extended breakers' latch behavior unchanged).
- NFR-2: `RunActiveSecondsBreaker` is a pure reconstruction from `state.tasks[*].started_at/ended_at` (resume-safe by construction, no new bookkeeping).
- NFR-3: Override resolution lives in exactly one place (DRY), not per-breaker-subclass.
- NFR-4: `breaker.extend` structured log event (mirrors `record_trip`'s `breaker.trip` convention).
- NFR-5: Backward-compat default for `breaker_overrides` on old `state.json`.

## Task List
- [x] `T-69MnaW-run-active-seconds-breaker` — FR-1, NFR-2
- [x] `T-yX1Oi5-breaker-extend-core` — FR-2a/b/c, NFR-1, NFR-3, NFR-4, NFR-5
- [x] `T-nVWE1W-resume-extend-breaker-cli` — FR-2d
- [x] `T-gzG0EI-docs-and-verification` — NFR-1 (final proof), docs, reviewer pass

## Risks and Dependencies
- Risk: accidentally regressing existing per-id latch semantics for untouched breakers.
  Mitigation: centralize override resolution in `evaluate_breakers` (single change point) + run
  the existing resume/stop-reframe regression suites unmodified as a gate.
- No dependency on any other in-flight epic; touches only `breakers.py`, `models.py`,
  `specs/workflow.schema.json`, `cli.py`'s `resume` command, plus new/updated tests and docs.

## Links
- Design doc: `docs-md/ai-epics/E-3JTmVu-breaker-resume-extend.md`
- Foundation epic: `ad/tickets/E-rc7k2v-run-control-routing-breakers/EPIC.md`
- LLD addendum target: `docs-md/lld-run-control-routing-breakers.md`
- Output artifacts (if any): `output/E-3JTmVu-breaker-resume-extend/`
