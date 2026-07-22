# TASK: T-Fx6Dp0-mvp-dev-suite-fixtures

## Metadata
- Task ID: `T-Fx6Dp0-mvp-dev-suite-fixtures`
- Epic ID: `E-9Qk4Zt-agent-benchmark-harness`
- Owner: developer agent (with tester review)
- Created: 2026-07-22 · Last Updated: 2026-07-22 (implementation complete — see STATUS.md)
- Status: Done · Estimate: 3.0 days (≤3)
- **This is the MVP finish line** (design G6): a small curated dev suite runnable end-to-end for all three subjects, results committed, make recipe working.

## Requirements Mapping
- FR-8, plus exercises FR-2/FR-3/FR-6/FR-7 end-to-end.

## Description
Author the curated MVP dev suite `benchmarks/suites/dev-core/` (4–8 tiny fixture tasks) + the subject configs, then run `make bench-smoke` at haiku for all three subjects and commit the results.

Suite tasks (tiny, self-contained fixture repos — each ≤ a few files):
1. **bugfix** — an off-by-one/edge bug; `PytestGrader` on the fixture's own failing test that must go green.
2. **feature** — add a small function to satisfy a provided (currently failing) test.
3. **refactor** — restructure a function while keeping its passing tests green (grader = tests still pass + a `FileAssertionGrader`/`CommandGrader` invariant, e.g. a lint or a "function X removed" check).
4. **test-writing** — write a test for given code; `CommandGrader` runs the new test AND a mutation/negative check (test must fail on a seeded bug) so an empty test can't pass.
   (Optionally 2–4 more within the 4–8 band: a second bugfix, a docstring/typing task.)

Subject configs under `benchmarks/subjects/`: `claude-opus.json`, `claude-sonnet.json`, `claude-haiku.json` (`claude_cli`, models `claude-opus-4-8`/`claude-sonnet-5`/`claude-haiku-4-5`, `permission_mode=bypassPermissions`); `ao-epic.json` + `ao-epic-haiku.json` (`ao_workflow`, minimal 2-task implement→test template — resolves Q1); `fake-pass.json`/`fake-fail.json`.
Also: the `ao-epic` subject's committed `workflow.json`/`reposet.json` template + `agents.json` (single-repo, instruction read from `INSTRUCTION.md`), all ids lowercase.

## Acceptance Criteria
1. `ao-bench validate --suite benchmarks/suites/dev-core/suite.json` and each subject config → exit 0. Suite has 4–8 tasks spanning bugfix/feature/refactor/test.
2. Each fixture is self-contained and tiny; a task's grader correctly returns solved when the reference solution is applied and not-solved on the unmodified fixture (proven with `fake-pass`/`fake-fail` — no LLM needed). The test-writing task's grader rejects an empty/no-op test (negative check).
3. `make bench-smoke` runs all THREE subject kinds (claude_cli haiku ×1 model + ao_workflow haiku) over the suite end-to-end and writes committed `benchmarks/results/<date>-dev-core-<subject>/` dirs. `[real_llm]`, gated by `AO_E2E_REAL_LLM=1`; workspaces under `playground/.tmp/bench/`.
4. `ao-bench report --suite dev-core` over the smoke results writes a committed `comparison.{json,md}`.
5. Committed results contain `run.json`+`summary.md` (compact); no transcripts committed (pointers only). Fixtures never mutated by a run.
6. Q1 (ao-epic template) and Q2 (model ids) resolved and recorded in the subject configs + design doc open-questions.

## Risks
- Real-LLM cost/flakiness (R4) → keep at haiku + tiny fixtures; the deterministic gate is AC 1–2 (fake subjects); the committed real results (AC 3) may be a single cheap pass. Non-deterministic scores are expected/documented (A5).
- ao-epic template scope (Q1) → start minimal (implement→test); escalate if a richer epic is wanted.

## Dependencies
- T-Cli8Nf (CLI + make), transitively T-Sbj9Ka/T-Grd7Vx/T-Run5Tz/T-Rpt3Wq.

## Pseudocode / Algorithm
```text
per task: fixture/ (broken or incomplete code + a test) + instruction.md + reference-solution (for fake-pass) + grader in suite.json
smoke: for subject in {claude-haiku, ao-epic-haiku}: ao-bench run --suite dev-core --subject subject --max-turns 20
       ao-bench report --suite dev-core
```

## Schemas / Interface Notes
- Data: `suite.json` + subject configs (design §5); ao-epic template = a lowercase-id `workflow.json`/`reposet.json`/`agents.json`.
- Artifacts: committed `benchmarks/suites/dev-core/**`, `benchmarks/subjects/*.json`, `benchmarks/results/<date>-dev-core-*/**`.

## Handoff Boundary
- Upstream: full harness (T-Cli8Nf and its deps).
- Downstream: T-Tst4Ln adds a `[real_llm]` smoke test wrapping this; T-Dcs2Rk documents the suite in `benchmarks/README.md`. Phase-2 (actual Opus-vs-Sonnet-vs-ao comparison) consumes this suite AFTER the epic.

## Artifacts
- Docs/comments: this folder. Large outputs: committed compact results under `benchmarks/results/`.
