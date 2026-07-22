# TASK: T-Ts0Xn5-tier-budget-parallel-tests

## Metadata
- Task ID: `T-Ts0Xn5-tier-budget-parallel-tests`
- Epic ID: `E-Bt4Xk9-complex-benchmark-tiers`
- Owner: tester agent
- Created: 2026-07-22
- Last Updated: 2026-07-22
- Status: Draft
- Estimate: 2.5 days

## Requirements Mapping
- FR-10 (deterministic, network-free tests for tier/budget/parallel/provider/campaign; swebench behind marker+extra; CI stays network-free; ≥80% coverage of new code)

## Description
Test every new capability deterministically with `FakeSubject`/`FakeGrader` (extend the fake subject to script per-task cost so budget/campaign can be exercised without real LLMs). Add a `swebench` pytest marker for the Docker/optional-dep tests (never in CI). Add a guard test that core + `ao-bench validate` on a non-swebench suite work with the `swebench` extra NOT installed. Keep the CI bench job network-free and add a coverage gate for the new modules.

## File ownership (exclusive — all under tests/)
- `tests/bench/test_tiers.py` — NEW (tier resolution, tiers.json, precedence, disabled tier).
- `tests/bench/test_budget.py` — NEW (per-run cap, skipped_budget, resume-raises-cap, fingerprint exclusion, boundary/overshoot).
- `tests/bench/test_parallel.py` — NEW (max_parallel=1 identical to serial; N-worker no-loss; budget-under-concurrency invariant; id-sorted persist).
- `tests/bench/test_workspace_provider.py` — NEW (fixture provider byte-identical; unknown provider rejected; source-without-fixture ok).
- `tests/bench/test_campaign.py` — NEW (whole-run cap, per-subject min-cap, xlarge gate, resume).
- `tests/bench/test_swebench.py` — NEW, all `@pytest.mark.swebench` (mocked-harness unit + opt-in real single-instance; import-guard when extra absent).
- `pyproject.toml` `[tool.pytest.ini_options] markers` — add `swebench`. (Coordinate: only the markers line; sequence after T-Sw5Hd9's `optional-dependencies` edit to the same file.)
- (optional) extend `FakeSubject` to honor a `fake_cost_per_task`/scripted cost list — if this needs a `subjects.py` edit, coordinate with T-Bg2Wq4's ownership (prefer driving cost via the existing `fake_cost` field + a per-task scripted map in the fake subject config; avoid a second editor of subjects.py by using a test-only fixture subclass in the test module instead).

## Inputs / Outputs
- Inputs: the shipped features.
- Outputs: a green, deterministic, network-free `pytest tests/bench -m "not real_llm and not swebench"`; a coverage report ≥80% for the new modules.

## Acceptance Criteria
1. `uv run pytest tests/bench -q -m "not real_llm and not swebench"` passes with **zero network**; the existing 232 bench tests still pass unedited (no regression).
2. Budget: a fake run with scripted per-task costs and a cap asserts the exact set of `skipped_budget` tasks, the bounded overshoot, exit code 0, and the resume-with-higher-cap re-attempt.
3. Parallel: `max_parallel=1` produces a `run.json` byte-identical to the serial path on the same fixture; `max_parallel=4` over 8 tasks records all 8 exactly once, id-sorted, over ≥5 repeated runs (no lost update).
4. Provider: a `dev-core` task through the refactored `materialize_workspace` yields a `RunContext` equal to the pre-refactor golden (regression); an unknown `source.type` is rejected at load.
5. Optional-dep guard: a test that simulates the `swebench` extra missing (e.g. `sys.modules` patch / import hook) asserts `import agent_orchestrator.bench.cli` and `ao-bench validate` on `dev-core` both succeed.
6. Coverage: `--cov=agent_orchestrator.bench --cov-fail-under=80` over the non-real/non-swebench suite; new modules (`tiers.py`, `providers_swebench.py`, `graders_swebench.py`, `campaign.py`) counted (swebench modules' Docker paths excluded via marker but their import/guard paths covered).
7. CI: the `.github/workflows/ci.yml` bench step still runs `-m "not real_llm and not swebench"` — network-free, no Docker; documented.

## Risks
- Testing parallelism deterministically: use injected fake costs + a barrier/latch or a fixed small worker count and assert *invariants* (set membership, no-loss, ordering) rather than exact interleavings. Do not assert on completion order.
- The swebench real test must clean up Docker/disk even on failure (`try/finally`), and skip cleanly when Docker/extra is absent.

## Pseudocode / Algorithm
```text
# test_budget.py (sketch)
def test_per_run_cap_skips_after_boundary(tmp_path):
    suite = fake_suite(6 tasks); subject = fake_subject(cost_per_task=2.0)
    rec = run_suite(suite, subject, cost_budget_usd=5.0, out_dir=tmp_path)
    statuses = [t.subject_status for t in rec.tasks]
    assert statuses.count("skipped_budget") == 3   # ran 3 (=$6 >= $5, one-task overshoot), skipped 3
    assert exit-equivalent == 0
```

## Schemas / Interface Notes
- New pytest marker `swebench` (alongside `real_llm`). Artifacts: none committed (tests write under tmp/gitignored).

## Handoff Boundary
- Upstream: all feature tasks (T-Tr1Km8..T-Cm9Tb4).
- Downstream: T-Dc1Yg7 (docs cite the green suite + coverage number).

## Artifacts
- Docs/comments: this folder. Large outputs: none.
