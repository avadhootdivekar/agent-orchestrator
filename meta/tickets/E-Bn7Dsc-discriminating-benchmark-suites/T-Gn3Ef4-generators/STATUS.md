# STATUS

- ID: `T-Gn3Ef4-generators`
- Updated At: 2026-10-23
- State: **Done** (generator side); integration with the `blackbox` grader pending

## This update
- By: Claude · Role: developer · Date: 2026-10-23 · Comment: implemented on branch `ad/bench-gen` (worktree `.worktrees/gen`, base 9eabca7).
  Families `crud-relational` (http) and `job-queue` (jsonl) with SPEC.md, probes.json and a Python-stdlib reference each;
  `generated` provider; `ao-bench gen`; committed `gen-services-mini`. Conformed to the blackbox contract (HOST/PORT binding,
  `grader.sandbox.stack`, sibling `reference/instance.json`, `{{i}}`-addressed perf mixes). `uv run pytest -q tests/bench`:
  644 passed / 4 skipped (baseline before: 431 collected passing in that dir); ruff format/check and `mypy src/agent_orchestrator/bench` clean.
  As-built design notes: `docs-md/bench-generators.md`.

### Pre-handoff checklist
- [x] Ticket read; scope/IO contract (`docs-md/bench-probe-dsl.md`) honoured, not edited.
- [x] Smallest correct change: only `bench/gen/**`, `generated_provider.py`, the new suite, tests, doc and this ticket touched (`spec.py`/`workspace.py`/`runner.py`/`cli.py`/`blackbox.py`/`sandbox.py` untouched).
- [x] No hardcoded secrets/URLs; magic values are named constants (limits, sizes, weights, defaults).
- [x] Determinism: a single `random.Random(seed)` per generation, no clock/`set` ordering in emitted data (tested incl. `PYTHONHASHSEED`); the job-queue reference uses a virtual clock.
- [x] Errors wrapped with context at the provider boundary (`SubjectError`), CLI reports once and exits 1.
- [x] `pytest -q tests/bench`: 644 passed, 4 skipped; no regressions.
- [x] `ruff check .`, `ruff format --check`, `mypy src/agent_orchestrator/bench` clean.
- [x] Unit tests for new logic; e2e through `ao-bench gen` (CliRunner) and `run_suite` (FakeSubject); reference passes every probe via a test-only DSL evaluator, naive mutants fail their trap probes.
- [x] Contracts honoured (DSL §1–§5); interpretations of ambiguous points listed in `docs-md/bench-generators.md` §5.
- [x] Ticket STATUS/TASK updated. Epic `EPIC.md`/`STATUS.md` rollup left to the coordinator (shared with the sibling tracks).
- N/A: CI wiring (no new job needed; `tests/bench` already runs), memories/learnings.
