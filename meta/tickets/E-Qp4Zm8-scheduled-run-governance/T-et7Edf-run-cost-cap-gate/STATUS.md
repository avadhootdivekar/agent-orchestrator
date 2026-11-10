# STATUS

- ID: `T-et7Edf-run-cost-cap-gate`
- Updated At: 2026-11-11
- State: In Progress (partial: engine + registry done; CLI wiring pending)
- Owner: TBD (assigned at sprint start)

## This update
- 2026-11-11 — By: developer (unit `w20-08-a2-docs-adr-sync`) · Role: developer · Date: 2026-11-11 · Comment: Still PARTIAL, unchanged by this docs unit. Owed: `--cost-cap-usd` CLI wiring + `COST_CAP_OPTION` in `OPTIONS` (uncommitted in the working tree), e2e/`fake_cost_script`, and the `record_trip` docstring + trailer `×N` (AC17, `breakers.py`). Period-cost-cap polish and the cost-cap review (carry-over w18-04) are not recorded as landed.
- 2026-11-11 — By: developer (unit `w14-02-a2-run-cost-cap-gate`) · Role: developer · Date: 2026-11-11 · Comment: PARTIAL.
  **Done:** `BUILTIN_COST_CAP`, `RunState.cost_cap_usd`, `TaskRunState.actuals_available`/`attempts_without_actuals`
  (AC13, field for T-Uz0Uol; counted in `_accumulate_actuals`, carried by `prepare_resume`); engine `cost_cap_reached` (pure,
  clock-free) + `Orchestrator._trip_cost_cap` at dispatch prep (before budget gate) and at the boundary (before
  `evaluate_breakers`, only while pending/killed/unseen tasks remain, H1); `_start_llm_lane` cap = min(breakers, state cap);
  `Orchestrator(cost_cap_usd=)` seam (sets state cap when given; None keeps the persisted cap = sticky); `run_options.COST_CAP_OPTION`
  (defined, **not yet appended to `OPTIONS`**: registering it without the cli.py flag breaks `test_every_registry_flag_is_a_real_cli_flag`,
  the dashboard flag test, and `test_governance_gate` fail-closed test) + `Option.sticky_on_resume` + `SOURCE_STATE`; `validate_cost_cap_usd` / `resolve_sticky_cost_cap`
  helpers (AC1/AC2 rules); `FakeExecutor` `cost_usd` (AC15 executor half). Tests: `tests/test_engine_cost_cap.py` (14),
  `tests/test_run_options_cost_cap.py` (11).
  **Unfinished (named):** `cli.py` `run`/`resume` wiring (`--cost-cap-usd` option, exit 2 via `validate_cost_cap_usd` before
  `resolve_option`, `effective_settings_history` entry, WARNING when cap <= spend, `_effective_settings_snapshot`
  `SOURCE_STATE`, `ao status` trailer `×N` collapse) — **not edited: cli.py was owned by in-flight units this wave** (AC1, AC2,
  AC11, AC14a CLI half, AC14c warning); `tests/test_e2e_cli_cost_cap.py` and the `fake_cost_script` conftest fixture (AC15
  CLI half); `Option.resumable` docstring touch-up (AC14a); `record_trip` docstring (AC17, `breakers.py` is outside the boundary
  → follow-up for T-Hq3vXd); AC10 full-suite/state.json byte check reported in the run report; AC12 coverage % not measured.
  AC9 (launch render / schema) unfinished: needs the OPTIONS registration, done together with cli.py; then update `tests/service/test_governance_gate.py::test_real_renderer_does_not_yet_register_cost_cap_*`, `tests/test_run_options.py` snapshots.
- Ticket created by the `architect` agent as part of the E-Qp4Zm8 design package. Not started.
- Scope: General engine feature `ao run/resume --cost-cap-usd`: sticky `RunState.cost_cap_usd` + non-latching `builtin.cost_cap` gate at dispatch and boundary; sentinel cap feed.

## Evidence
- None yet. No implementation has begun. Design basis:
  `docs-md/scheduled-run-governance-hld.md` and `docs-md/adr/ADR-0025-scheduled-run-governance-quotas-and-cost-caps.md`.

## Risks / Blockers
- Not blocked. No E-Sc9Rt4 dependency; may start immediately. Coordinate engine.py edits with any concurrent engine epic.

## Next actions
1. Read the upstream tasks' **merged code** (including E-Sc9Rt4's), not only the HLD, before starting.
2. Implement against the acceptance criteria in `TASK.md`, in AC order; report the pytest delta, ruff/format, mypy, coverage.
3. Publish the option name `cost_cap_usd` and trip id `builtin.cost_cap` to `T-PpE5lf`/`T-nI4DBf`.
