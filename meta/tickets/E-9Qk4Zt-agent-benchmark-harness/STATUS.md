# STATUS

- ID: `E-9Qk4Zt-agent-benchmark-harness`
- Updated At: 2026-07-22
- State: In Progress (4/9 tasks delivered — Sprint 1 complete; Sprint 2 next: `T-Rpt3Wq`, `T-Cli8Nf`)
- Owner: architect agent (design) → developer/tester (delivery)

## This update
- By: Claude · Role: manager · Date: 2026-07-22 · Comment: `T-Run5Tz-runner-metrics` complete —
  Sprint 1 (harness engine) done. `bench/runner.py`: `run_suite` with deterministic order,
  atomic per-task persist of `run.json` (crash-resumable; resume skips recorded tasks, `force`
  re-runs), per-task error isolation (SubjectError/GraderError/unexpected → recorded, run
  continues), run+task `config_fingerprint`, injected clock for determinism. 17 new tests.
  Deviation accepted by orchestrator: runner writes `run.json` itself (resume is meaningless
  otherwise); `T-Rpt3Wq` owns `summary.md` + comparison writers on top of the recorded shape.
  Independent re-verification: 990 passed/4 deselected, ruff/mypy clean.
  Detail: `T-Run5Tz-runner-metrics/STATUS.md`.
- By: Claude · Role: manager · Date: 2026-07-22 · Comment: `T-Sbj9Ka-subject-adapters` complete.
  `bench/subjects.py` (`Subject` ABC + `SubjectResult` with argv/model/permission-mode
  observability; `ClaudeCliSubject` stream-json capture + reused core usage parser;
  `AoWorkflowSubject` with exactly-one-run-dir invariant; `FakeSubject` scripted effects) and
  `bench/workspace.py` (`RunContext`, path-guarded `materialize_workspace`). 51 new tests
  (116 passed/1 skipped in tests/bench); real haiku sanity check confirmed live cost/token
  parsing (`total_cost_usd` 0.026, 1 turn). Independent re-verification: 973 passed/4 deselected,
  ruff/mypy clean. Forward notes for `T-Run5Tz`: set `RunContext.subject_base_dir` to the
  subject.json parent dir for `ao_workflow` subjects; catch `SubjectError` around `Subject.run()`.
  Detail: `T-Sbj9Ka-subject-adapters/STATUS.md`.
- By: Claude · Role: manager · Date: 2026-07-22 · Comment: `T-Grd7Vx-grader-registry` complete
  (parallel with in-flight `T-Sbj9Ka`). Delivered `bench/graders.py` (`Grader` ABC + `GradeResult`,
  `PytestGrader` exit-code-authoritative, `CommandGrader`, `FileAssertionGrader`, `FakeGrader`,
  all registered) and `bench/metrics.py` (`TaskMetric`/`Aggregate`/`build_task_metric`/`aggregate`
  with `cost_usd=None` + `solved==0` handling); 31 new tests. Orchestrator arbitration applied on
  top: `load_suite` now rewrites `Assertion.golden` to an absolute path (grade-time contexts have
  no suite base dir; happy-path test updated) and `test_registries_start_empty` replaced with
  `test_grader_registry_populated_on_import` (import-time registration is now the designed
  behavior). Verified: bench suite 79 passed, `ruff`/`mypy` clean. Detail:
  `T-Grd7Vx-grader-registry/STATUS.md`.
- By: Claude · Role: developer · Date: 2026-07-22 · Comment: `T-Sc4Hm2-suite-subject-schemas`
  complete — the epic's foundation task. Delivered both versioned `additionalProperties:false`
  JSON schemas (`benchmarks/schemas/{benchmark-suite,subject}.schema.json`), the pydantic models
  + `load_suite`/`load_subject` loader/validator (`src/agent_orchestrator/bench/spec.py`), the
  empty `SUBJECT_REGISTRY`/`GRADER_REGISTRY` + register helpers (`bench/registries.py`), the
  bench error hierarchy reusing core `SpecValidationError` (`bench/errors.py`), and a minimal
  `ao-bench validate` Typer app (`bench/cli.py`, no `[project.scripts]` entry yet — `T-Cli8Nf`).
  48 new tests (`tests/bench/`), full suite 857→905 passed/3 deselected (+48, zero regressions),
  `ruff`/`mypy` clean on every touched file, `bench/` confirmed outside the core import graph
  (SI-1) and no file outside `bench/`/`benchmarks/` touched (NFR-1). One scope deviation from
  `TASK.md` (models folded into `spec.py` per the assigning message's explicit file list, not a
  separate `models.py`) and one filled gap (the `Assertion` model's fields, underspecified in the
  design doc) — both recorded, low-risk, and non-blocking for downstream tasks. Full detail:
  `T-Sc4Hm2-suite-subject-schemas/STATUS.md`.

## Evidence
- Design: `docs-md/benchmarking-framework-hld.md` · `docs-md/benchmark-landscape-survey.md` · `docs-md/adr/ADR-0008-benchmark-harness-approach.md`
- Tickets: `meta/tickets/E-9Qk4Zt-agent-benchmark-harness/` (EPIC.md + 9 task folders)
- `T-Sc4Hm2`: `benchmarks/schemas/*.json`, `src/agent_orchestrator/bench/{__init__,errors,registries,spec,cli}.py`,
  `tests/bench/*` (new). `uv run pytest tests/bench -q` = 48 passed. `uv run pytest -q -m "not
  real_llm"` = 905 passed/3 deselected (was 857/3, +48, 0 regressions). `ruff check`/`ruff format
  --check` clean on touched files (2 pre-existing, untouched `test_e2e_cli.py` errors persist,
  out of scope). `mypy src/agent_orchestrator/bench` clean; `mypy src` clean except 4
  pre-existing, untouched `_version.py` errors. See `T-Sc4Hm2-suite-subject-schemas/STATUS.md`
  for the full AC-by-AC verification.

## Risks / Blockers
- Open questions Q1 (ao-epic workflow template shape), Q2 (exact model ids to pin), Q3 (report auto-discovery) — see design §18; proposed defaults recorded, escalate on disagreement.
- Real-LLM smoke (T-Fx6Dp0) cost/flakiness — mitigated by haiku + tiny fixtures; deterministic fake-subject run is the gate.
- Forward note from `T-Sc4Hm2` for `T-Sbj9Ka`/`T-Grd7Vx`: registering concrete Subject/Grader
  classes into `SUBJECT_REGISTRY`/`GRADER_REGISTRY` must be paired with extending
  `bench/spec.py`'s `KNOWN_SUBJECT_TYPES`/`KNOWN_GRADER_TYPES` closed lists, or the new types
  will still be rejected by `ao-bench validate`.

## Next actions
1. ~~Start Sprint 1: `T-Sc4Hm2-suite-subject-schemas` (no deps).~~ — done 2026-07-22.
2. ~~Start `T-Sbj9Ka-subject-adapters` and `T-Grd7Vx-grader-registry`.~~ — `T-Grd7Vx` done
   2026-07-22; `T-Sbj9Ka` in progress.
3. On `T-Sbj9Ka` completion: verify, commit, then start `T-Run5Tz-runner-metrics` (unblocks).
4. Resolve Q1/Q2 before `T-Fx6Dp0` (fixtures) lands.
