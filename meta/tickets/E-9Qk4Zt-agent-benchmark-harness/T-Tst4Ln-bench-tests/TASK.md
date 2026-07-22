# TASK: T-Tst4Ln-bench-tests

## Metadata
- Task ID: `T-Tst4Ln-bench-tests`
- Epic ID: `E-9Qk4Zt-agent-benchmark-harness`
- Owner: tester agent
- Created: 2026-07-22 · Last Updated: 2026-07-22
- Status: Draft · Estimate: 2.5 days (≤3)

## Requirements Mapping
- NFR-3 (tests + coverage), and verification of FR-1..FR-7 + NFR-1/NFR-2.

## Description
Build the `tests/bench/` suite (unit + integration + CliRunner e2e + opt-in `real_llm` tier) and the CI job. All non-real tests use `FakeSubject`/`FakeGrader` + tiny committed fixtures → deterministic, network-free (design §13). Provide the shared fake-subject/grader harness early (in parallel with Sprint-1 T-Run5Tz) to de-risk the runner.

Coverage:
- **Unit:** schema validate matrix (T-Sc4Hm2 AC); grader semantics + metrics edge cases (T-Grd7Vx AC); subject status mapping + workspace path-guard (T-Sbj9Ka); runner resume/force/crash-isolation/atomic-persist/fingerprint (T-Run5Tz); result/comparison shape + same-suite guard (T-Rpt3Wq).
- **Integration:** full `run_suite` over a 2-task fake suite with a real pytest fixture (fake writes real solution → real pytest passes; no-op fake → fails); resume; comparison across two fake subjects.
- **CliRunner e2e (outer boundary, CLAUDE.md):** `ao-bench validate|run|report|list` with fake subjects; assert exit codes + committed result files.
- **SI-1 import-graph test:** importing `agent_orchestrator.cli` / `.engine` must NOT import `agent_orchestrator.bench`; the existing engine/CLI suite passes unedited.
- **`real_llm` tier (marked `real_llm`, `AO_E2E_REAL_LLM=1`, never in CI):** one dev-core task × haiku × (claude_cli + ao_workflow) → asserts cost/tokens populated (reuse-of-core-helpers proof) + result dir written; workspace under `playground/.tmp/bench/`, preserved on teardown (learnings §18).
- CI: add a `bench` job (`uv run pytest tests/bench -m "not real_llm"`) + coverage gate for `bench/`.

## Acceptance Criteria
1. `uv run pytest tests/bench -m "not real_llm"` green; deterministic (fixed fixtures/fakes, no network).
2. Coverage of `src/agent_orchestrator/bench/` ≥ 80% (report the actual number).
3. The SI-1 import-graph test passes and the pre-existing engine/CLI suite passes **unedited** (regression gate; report pass counts before/after).
4. `ruff check` + `mypy src/agent_orchestrator/bench` clean (re-run yourself after any subagent delivery — learnings).
5. `[real_llm]` tier runs (when `AO_E2E_REAL_LLM=1`) and asserts populated cost/tokens; skipped by default.
6. CI job added; `make bench-validate` wired into CI or documented as a manual gate.

## Risks
- Real pytest inside a fixture during unit tests must not pollute the outer test run → run graders in the materialized `ws/repo`, never the repo root.

## Dependencies
- T-Run5Tz, T-Rpt3Wq, T-Cli8Nf (and transitively the subjects/graders). The fake harness can start alongside T-Run5Tz.

## Pseudocode / Algorithm
```text
fake_suite fixture: 2 tasks, tiny repo + a pytest test; fake-pass copies solution, fake-fail no-op
test_runner_resume: run once (all done) → run again → assert 0 re-runs; run with force → assert N re-runs
test_si1_import_graph: import cli; assert 'agent_orchestrator.bench' not in sys.modules
```

## Schemas / Interface Notes
- Interface: pytest markers (`real_llm`, `e2e`) reuse existing `pyproject` markers; add `tests/bench/conftest.py` with the fake harness + a `bench_workspace` fixture under `playground/.tmp/bench/`.
- Artifacts: test-only; no committed results here.

## Handoff Boundary
- Upstream: all implementation tasks.
- Downstream: T-Dcs2Rk cites the coverage/pass numbers in the docs-refresh.

## Artifacts
- Docs/comments: this folder. Large outputs: none.
