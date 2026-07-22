# EPIC: E-9Qk4Zt-agent-benchmark-harness

## Metadata
- Epic ID: `E-9Qk4Zt-agent-benchmark-harness`
- Title: `ao` benchmarking framework — compare an `ao` workflow vs bare `claude -p` (Opus/Sonnet/Haiku) on dev-task suites
- Owner: architect agent (design) → developer/tester agents (delivery)
- Created: 2026-07-22
- Last Updated: 2026-07-22
- Status: In Progress (7/9 tasks delivered — MVP COMPLETE at `T-Fx6Dp0`; remaining: `T-Tst4Ln`, `T-Dcs2Rk`; Phase 2 real runs are NOT part of this epic)

## Summary
- **Goal:** a declarative, pluggable, resumable, observable, safe-by-default benchmarking framework that runs **subjects** (an `ao` multi-agent workflow · bare `claude -p --model opus|sonnet|haiku` · future CLIs/APIs) over **suites** of dev tasks, grades them, and publishes machine-readable + human-readable **results** to a git-committed directory — reusing `ao`'s own cost/usage plumbing so numbers are apples-to-apples.
- **Scope In (MVP):** suite/subject JSON schemas (validated, versioned, `additionalProperties:false`); `Subject` ABC + `AoWorkflowSubject`/`ClaudeCliSubject`/`FakeSubject`; `Grader` ABC + `PytestGrader`/`CommandGrader`/`FileAssertionGrader`/`FakeGrader`; metrics (pass-rate, wall-clock, cost USD, tokens, attempts/turns) reusing core helpers; a resumable/bounded runner; `run.json`+`summary.md`+cross-subject `comparison.{json,md}` under `benchmarks/results/`; a standalone `ao-bench` CLI + `make bench-*` recipes; a curated 4–8 task dev suite (bugfix/feature/refactor/test) runnable end-to-end for all three subjects at haiku; tests (unit/integration/CliRunner + opt-in real_llm) + docs/ADR.
- **Scope Out (Non-MVP):** LLM-judge grader; SWE-bench/terminal-bench **Docker** imports; inspect-ai bridge; Aider/EvalPlus importers; non-dev **domain** suites (biology/physics/legal — framework *supports* via `domain`+`CommandGrader`, ships none); HTTP/API subjects; multi-seed/bootstrap-CI/pass@k; dashboard; nightly cron; **and running the actual Phase-2 Sonnet-vs-Opus-vs-ao comparison** (consumes this framework later). See design §12.

Design (survey + HLD + LLD + schemas + diagrams + edge cases):
[`docs-md/benchmarking-framework-hld.md`](../../../docs-md/benchmarking-framework-hld.md) ·
survey [`docs-md/benchmark-landscape-survey.md`](../../../docs-md/benchmark-landscape-survey.md) ·
decision [`docs-md/adr/ADR-0008-benchmark-harness-approach.md`](../../../docs-md/adr/ADR-0008-benchmark-harness-approach.md).

## Requirements (MVP) with traceability

| ID | Requirement | Task(s) | Verify |
|----|-------------|---------|--------|
| FR-1 | `benchmark-suite.schema.json` + `subject.schema.json` (versioned, `additionalProperties:false`, `domain` field) + pydantic models + loader/validator; `ao-bench validate`. | T-Sc4Hm2 | unit + CliRunner: malformed/unknown-type/dup-id/uppercase-id/missing-fixture rejected; valid → OK |
| FR-2 | `Subject` ABC + `SUBJECT_REGISTRY`; `ClaudeCliSubject` (`claude -p --model … --permission-mode …`), `AoWorkflowSubject` (`uv run ao run …`), `FakeSubject`. Cost/tokens via reused core helpers. Workspaces under `playground/.tmp/bench/`. | T-Sbj9Ka | unit (fake) + real_llm (cost/tokens populated); fixture untouched |
| FR-3 | `Grader` ABC + `GRADER_REGISTRY`; `PytestGrader` (exit-code source of truth, score=pass-rate), `CommandGrader`, `FileAssertionGrader`, `FakeGrader`. | T-Grd7Vx | unit: solved/score semantics; unknown type rejected |
| FR-4 | `metrics.py`: `TaskMetric` merge + per-subject `Aggregate` (solve_rate, total_cost, cost_per_solved), handling `cost_usd is None`/`solved==0`. | T-Grd7Vx | unit |
| FR-5 | `runner.py`: deterministic order, materialize→run→grade→persist-per-task, resume (skip completed), `--force`, per-task bound (timeout), one-task-crash-doesn't-abort, path-guard. | T-Run5Tz | unit + integration (fake subject) |
| FR-6 | `results.py`: `run.json` (schema-valid) + `summary.md` written to `benchmarks/results/<date>-<suite>-<subject>/`; `ao-bench report` → `comparison.{json,md}`; refuse unlike-suite compare. | T-Rpt3Wq | integration + CliRunner |
| FR-7 | Standalone `ao-bench` console script (`validate|run|report|list`) + `make bench-validate|bench-smoke|bench-run|bench-report`; `pyproject` `[project.scripts]`. | T-Cli8Nf | CliRunner + Make dry-run |
| FR-8 | Curated 4–8 task dev suite (bugfix/feature/refactor/test) with tiny committed fixtures + instructions + graders + subject configs (opus/sonnet/haiku + ao-epic[-haiku] + fake); `make bench-smoke` runs all 3 subjects at haiku end-to-end; results committed. | T-Fx6Dp0 | real_llm smoke + committed artifacts |
| NFR-1 | **Core untouched (SI-1):** no change to engine/executors or workflow/agents/reposet schemas; `bench/` outside the engine import graph; existing engine tests pass unedited. | T-Sc4Hm2..T-Cli8Nf | regression gate + grep |
| NFR-2 | Safe/bounded/deterministic: workspaces repo-local gitignored (never `/tmp`); per-task timeout; budget/max-turns pass-through; result dir reproducible from spec (+`config_fingerprint`). | T-Run5Tz, T-Sbj9Ka | unit + audit |
| NFR-3 | Tests pass (unit+integration+CliRunner + opt-in real_llm), ≥80% coverage of `bench/`, ruff/mypy clean on `bench/`. | T-Tst4Ln | `uv run pytest`, coverage, ruff/mypy |

## Requirements (Non-MVP)
NM-1 `LlmJudgeGrader` · NM-2 `DockerSubject` + SWE-bench Verified importer · NM-3 inspect-ai bridge · NM-4 Aider/EvalPlus importers · NM-5 non-dev domain suites · NM-6 HTTP/API subjects · NM-7 statistical rigor (multi-seed/CIs/pass@k) · NM-8 dashboard/`ao-bench serve` · NM-9 nightly cron trigger. Tracked in design §12; not implemented this epic.

## HLD / LLD (condensed — full in the design doc)
`ao-bench run --suite S --subject J` → `runner.run_suite`: for each task (sorted, deterministic) → materialize `playground/.tmp/bench/<runid>/<subject>/<task>/repo` (copy fixture) → `SUBJECT_REGISTRY[type].run()` (shells to `ao`/`claude`, cost/tokens via reused core helpers) → `GRADER_REGISTRY[type].grade()` (test/command/file) → `build_task_metric` → persist `run.json` after each task (resumable) → `summary.md`. `ao-bench report` loads per-subject `run.json`s and writes `comparison.{json,md}`. `bench/` imports core helpers read-only; nothing in core imports `bench/` (SI-1). Standalone `ao-bench` console script (ADR-0008 D4). No workflow-schema change (ADR-0008 D5).

## Sprint plan & capacity
Team profile: developers with <4 yrs experience; 2-week sprints (5-day weeks); 40% overhead. Assumed `team_size = 2` (one developer + one tester, matching this repo's epic staffing).
```
GrossHoursPerSprint      = 2 * 10 * 8        = 160
NetFocusHoursPerSprint   = 160 * 0.60        = 96      (12 focus-days)
CommitmentHoursPerSprint = 96 * (0.70..0.85) = 67.2 .. 81.6
```
Task sizing (person-days → hours at 8h/d): T-Sc4Hm2 2.0(16) · T-Sbj9Ka 3.0(24) · T-Grd7Vx 2.0(16) · T-Run5Tz 3.0(24) · T-Rpt3Wq 2.0(16) · T-Cli8Nf 1.5(12) · T-Fx6Dp0 3.0(24) · T-Tst4Ln 2.5(20) · T-Dcs2Rk 1.0(8) = **20.0 person-days ≈ 160 gross hours**.

**Recommendation: two 2-week sprints**, justified — 160h of task work ÷ ~75h commitment/sprint ≈ 2.1 sprints, so two sprints (the last lightly loaded, leaving slack for real-LLM flakiness on T-Fx6Dp0). One sprint (67–82h) cannot hold 160h.
- **Sprint 1 (foundation, ~10.0 d):** T-Sc4Hm2 → T-Sbj9Ka → T-Grd7Vx → T-Run5Tz — the harness engine + a fake-subject end-to-end. Critical path is largely serial (schemas → subjects → runner); tester builds the fake-subject/grader harness in parallel to de-risk T-Run5Tz.
- **Sprint 2 (MVP-complete, ~10.0 d):** T-Rpt3Wq → T-Cli8Nf → T-Fx6Dp0 (fixtures + haiku smoke, results committed, make recipe working = the MVP finish line) → T-Tst4Ln → T-Dcs2Rk. Contingency: if T-Fx6Dp0's real-LLM smoke is flaky/expensive, its blocking part is "suite validates + fake subject runs it green"; the real haiku run can trail into a short buffer.

## Task List (ordered; each ≤3 days)
- [x] `T-Sc4Hm2-suite-subject-schemas` (2.0d) — suite/subject JSON schemas + models + loader/validator + `ao-bench validate`. Deps: none. **Done 2026-07-22.**
- [x] `T-Sbj9Ka-subject-adapters` (3.0d) — `Subject` ABC + registry; AoWorkflow/ClaudeCli/Fake subjects; workspace materialization; cost/tokens via reused core helpers. Deps: T-Sc4Hm2. **Done 2026-07-22.**
- [x] `T-Grd7Vx-grader-registry` (2.0d) — `Grader` ABC + registry (pytest/command/file/fake) + `metrics.py`. Deps: T-Sc4Hm2. **Done 2026-07-22.**
- [x] `T-Run5Tz-runner-metrics` (3.0d) — runner orchestration: deterministic/resumable/bounded, path-guard, per-task persist. Deps: T-Sbj9Ka, T-Grd7Vx. **Done 2026-07-22.**
- [x] `T-Rpt3Wq-results-comparison-report` (2.0d) — `run.json`+`summary.md` writers + cross-subject `comparison.{json,md}`. Deps: T-Run5Tz. **Done 2026-07-22.**
- [x] `T-Cli8Nf-bench-cli-make-entrypoints` (1.5d) — standalone `ao-bench` console script + `pyproject` entry + `make bench-*`; core untouched. Deps: T-Rpt3Wq. **Done 2026-07-22.**
- [x] `T-Fx6Dp0-mvp-dev-suite-fixtures` (3.0d) — curated 6-task dev-core suite + 6 subject configs + ao-epic workflow assets; `make bench-smoke` (fake + claude-haiku + ao-epic-haiku) ran end-to-end 6/6 solved each (≈$1.04 real spend); results committed. **MVP finish line reached 2026-07-22.** Deps: T-Cli8Nf.
- [ ] `T-Tst4Ln-bench-tests` (2.5d) — unit+integration+CliRunner + opt-in real_llm tier; CI job (fake, no network); ≥80% coverage of `bench/`. Deps: T-Run5Tz, T-Rpt3Wq, T-Cli8Nf.
- [ ] `T-Dcs2Rk-docs-adr-reconcile` (1.0d) — reconcile HLD/LLD + ADR-0008 to as-built, `benchmarks/README.md`, HLD-index pointer, learnings (post-implementation docs-refresh). Deps: all.

All nine tasks are **MVP**. Non-MVP items (NM-1..NM-9) are listed above without task folders (anti-overbuild; created only when scheduled).

## Risks and Dependencies
- **R1 `--permission-mode` for bare `claude`** (must edit + run tests): default subjects to `bypassPermissions` in committed configs; haiku smoke surfaces mismatches early (learnings §21).
- **R2 ao-subject cost attribution** needs exactly one run_id per workspace: fresh workspace per (subject,task); assert single run dir (A4).
- **R3 pytest-summary brittleness:** exit-code is the source of truth; summary parse only enriches `score` (A3).
- **R4 real-LLM cost/flakiness on T-Fx6Dp0:** keep smoke at haiku + tiny fixtures; the deterministic part (fake subject green) is the gate, real run trails.
- **Reuses (import-only, does not modify):** `executors.claude_cli.parse_usage_and_429`/`extract_result_event`, `models.compute_run_usage_totals`, `runstate`, `logging_setup`. **No** engine/schema edit (SI-1/NFR-1).

## Links
- Design doc (HLD+LLD): [`docs-md/benchmarking-framework-hld.md`](../../../docs-md/benchmarking-framework-hld.md)
- Landscape survey: [`docs-md/benchmark-landscape-survey.md`](../../../docs-md/benchmark-landscape-survey.md)
- ADR: [`docs-md/adr/ADR-0008-benchmark-harness-approach.md`](../../../docs-md/adr/ADR-0008-benchmark-harness-approach.md)
- Output artifacts: committed results under `benchmarks/results/` (produced by T-Fx6Dp0; Phase-2 comparison runs are out of scope for this epic)
