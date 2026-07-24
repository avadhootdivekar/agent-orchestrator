# STATUS

- ID: `E-9Qk4Zt-agent-benchmark-harness`
- Updated At: 2026-07-22
- State: **Done** (9/9 tasks delivered 2026-07-22)
- Owner: architect agent (design) → developer/tester (delivery) → closed by manager

## This update
- By: Claude · Role: manager · Date: 2026-07-22 · Comment: **Epic closed.** `T-Dcs2Rk` complete
  (HLD 16 stale claims reconciled, ADR-0008 → Accepted, benchmarks/README.md, 5 learnings; commit
  `c1e0190`). Full-branch reviewer pass: verdict approve-with-nits, zero critical findings, SI-1
  independently confirmed (grep + import probe + real wheel build), methodology judged fair
  (identical instructions/workspaces/graders per subject) with saturation caveat. All four
  reviewer warnings fixed same-day (commit `4ec2938`): W1 typed repo-checkout error, W2
  checkout-independent fingerprints, W4 resume fingerprint-mismatch guard, W5 tie disclosure in
  winner lines (committed comparison regenerated). Deferred follow-ups (documented in
  benchmarks/README.md): runner lockfile for concurrent same-(suite,subject) runs;
  subject-set-aware comparison dir naming; a harder discriminating suite for capability claims.
  Phase-2 comparison (user ask, on top of the framework): all five real subjects 6/6 on dev-core;
  results + 5-way comparison committed (`1c60a07`, regenerated in `4ec2938`). Final verification:
  1089 passed/4 deselected, bench coverage 98% (runner 100%), ruff/mypy clean.
- By: Claude · Role: tester · Date: 2026-07-22 · Comment: `T-Tst4Ln-bench-tests` complete.
  Bench coverage 96%→98% (1120 stmts, 18 missed — all hard-to-trigger race/abstract paths,
  documented); +31 deterministic edge-case tests (221 total in tests/bench); CI job added to
  `.github/workflows/ci.yml` with `--cov-fail-under=80` gate, network-free. Zero production
  code changes. Independent re-verification: 1078 passed/4 deselected, ruff/mypy clean,
  ci.yml parses. Detail: `T-Tst4Ln-bench-tests/STATUS.md`.
- By: Claude · Role: manager · Date: 2026-07-22 · Comment: `T-Fx6Dp0-mvp-dev-suite-fixtures`
  complete — **MVP finish line**. dev-core suite: 6 tasks (2 bugfix / 2 feature / 1 refactor /
  1 test-writing), tiny stdlib-only fixtures, fail-before/pass-after verified, test-writing
  grader mutation-checked. 6 subject configs (full model ids pinned per Q2) + ao-epic 2-task
  implement→verify workflow (Q1; uniform-model, clobber-safe). Real haiku smoke: claude-haiku
  6/6 @$0.373/166s; ao-epic-haiku 6/6 @$0.662/343s; fake-pass 6/6; resume proven; comparison
  committed under `benchmarks/results/2026-07-22-dev-core-*`. One design gap worked around and
  documented (AoWorkflowSubject doesn't mirror a generic instructions/ dir into the workspace —
  workflow tasks reference `repo/INSTRUCTION.md`). 22 new deterministic tests; independent
  re-verification 1047 passed/4 deselected, ruff/mypy clean.
  Detail: `T-Fx6Dp0-mvp-dev-suite-fixtures/STATUS.md`.
- By: Claude · Role: manager · Date: 2026-07-22 · Comment: `T-Rpt3Wq` + `T-Cli8Nf` complete
  (one developer agent, sequential). `bench/results.py`: load_run/write_summary_md/
  build_comparison/write_comparison with unlike-suite + mixed-fingerprint refusal
  (`allow_mixed` escape), winner lines per axis; `ResultsError` added to errors.py.
  `bench/cli.py`: full `ao-bench validate|run|report|list` (exit codes 0/1/2; grader-unsolved
  is NOT exit 2 — only subject-level failure is); single-line `[project.scripts]` entry;
  `make bench-validate|bench-smoke|bench-run|bench-report` (guarded skip until fixtures land).
  35 net new tests incl. SI-1 subprocess check (core `ao` import never loads bench). Independent
  re-verification: 1025 passed/4 deselected, core CLI suite 81 passed, ruff/mypy clean,
  `ao-bench --help` resolves, make guards behave. Detail: both task STATUS.md files.
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
