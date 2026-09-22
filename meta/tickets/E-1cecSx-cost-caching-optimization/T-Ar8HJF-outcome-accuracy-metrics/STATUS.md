# STATUS

- ID: `T-Ar8HJF-outcome-accuracy-metrics`
- Updated At: 2026-09-21
- State: `Done`
- Owner: `dev-epic` agent (implemented directly, not delegated — the ADR-0015 redesign removed
  all `engine.py` risk, so the "developer" delegation this ticket originally planned was no
  longer needed to keep scope isolated)

## Early-gate outcome (2026-09-21) — REDESIGN
- Architect verdict on the original in-engine `settlement_hook` design: **needs rework**.
  Findings B-1 through B-7 (wrong call site relative to final `ts.status`, isolated-task
  worktree-staleness grading bug, main-thread blocking against `max_parallel`, unguarded
  registry lookup, silent `on_failure` footgun, undefined capture-dir/context shape,
  `emit_tasks`-injected tasks unreachable by per-task wiring) — full list in the epic's early-
  gate notification.
- **Decision (ADR-0015 decision 2, recorded in `docs-md/adr/
  ADR-0015-prompt-cache-scope-and-post-run-grading.md`): replaced the in-engine mechanism with
  a POST-RUN grading pass, `ao report-outcomes --grade <hook-name>`.** This resolves every
  finding structurally: `engine.py` is untouched (zero risk instead of "one helper + two call
  sites"), grading runs after run-end sync (no staleness), off the main thread's critical path
  (no `max_parallel` interference), with a `.get()`-guarded hook lookup, no `on_failure` field
  to misuse (grading never touches `RunState`), and uniform coverage of every task in
  `RunState.tasks` including ones `emit_tasks` injected (no per-task wiring needed at all).

## This update — implementation (all sub-deliverables)
1. **B3.1 local counts**: `src/agent_orchestrator/outcomes.py::task_outcome_summary`/
   `run_breakdown_frequency` — pure functions, 7 unit tests, `ruff`/`mypy` clean.
2. **B3.2 grading script**: `specs/examples/hooks/grade_command.py` (generalizes
   `bench.graders.CommandGrader`/`PytestGrader`, reused not reimplemented) — co-located with
   Epic A's own example hooks per that epic's established convention (not
   `scripts/helper/`, corrected from an earlier ticket draft).
3. **B3.3 post-run settlement grading**: `outcomes.py::grade_run` + `settlement_grades_path`,
   calling `hooks.run_hook` (Epic A's mechanism, UNCHANGED except widening `HookOutcome.kind`/
   `run_hook`'s `kind` parameter from `Literal["pre_hook","post_hook"]` to add
   `"settlement_hook"` — the one small additive change to `hooks.py`/`models.py`).
4. **CLI**: `ao report-outcomes --run-id <id> [--grade <hook-name>]` (`cli.py`), prints the
   local-count table and, with `--grade`, the settlement-grade table + writes
   `settlement_grades.json`.
5. **`engine.py`: ZERO changes** — confirmed via `git diff --stat`, matching ADR-0015's
   stronger guarantee over the original "one helper + two call sites" plan.

## Evidence
- `pytest tests/test_outcomes.py -q` → 12 passed (7 local-count tests + 5 `grade_run` tests,
  including one that runs the REAL `grade_command.py` script end-to-end, not a synthetic
  double).
- `pytest tests/test_e2e_cli_cost_caching.py -q` → 1 passed: outer-CLI-boundary e2e
  (`typer.testing.CliRunner`, per CLAUDE.md's e2e rule) proving `ao run` → `ao report-outcomes
  --grade grade` grades BOTH a freshly-dispatched task (which also separately got Epic A's own
  dispatch-scoped `post_hook` grade) AND a `skip_if_outputs_exist`-skipped task, in ONE pass,
  with zero per-task settlement wiring in the spec.
- `pytest tests/test_hooks.py tests/test_hooks_schema_validation.py tests/test_engine_hooks.py
  tests/test_engine_hooks_resolver.py -q` → 66 passed (Literal widening is non-breaking for
  every existing Epic A hook test).
- `ruff check` + `mypy src/agent_orchestrator/outcomes.py src/agent_orchestrator/hooks.py
  src/agent_orchestrator/models.py src/agent_orchestrator/cli.py` — all clean.

## Risks / Blockers
- None. The highest-risk item in the original plan (the two `engine.py` call sites) no longer
  exists.

## Next actions
1. Roll into the epic's late-gate full-suite verification (`T-UJElTR`).
