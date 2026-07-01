# TASK: T-92o31p-perf-correctness-tier

## Metadata
- Task ID: `T-92o31p-perf-correctness-tier`
- Epic ID: `E-dvehbb-e2e-playground-tests`
- Owner: tester
- Created: 2026-07-01
- Last Updated: 2026-07-01
- Status: Draft
- Estimate: `< 2 days`
- MVP: no (Phase 2)

## Requirements Mapping
- FR-6, FR-7 (perf dimension), NFR-3, NFR-4. ADR-003.

## Description
Implement **Tier 4 — performance + correctness**, in two halves:
1. **Performance (always-green, Fake):** bound the FakeExecutor CLI run of each example by
   wall-clock and by "no excessive retries", proving the engine drives the full spine +
   fan-out + loop quickly and without redundant work. Marked `@pytest.mark.perf` but NOT
   `real_llm` (runs by default; can be deselected with `-m "not perf"`).
2. **Correctness (opt-in, real):** under the same double gate as the real-LLM tier
   (`real_llm` + `AO_E2E_REAL_LLM=1`), execute the generated code artifact and assert
   functional correctness (e.g. import/run the produced `sum` and check `sum([1,2,3])==6`).
   This is the ONLY place any generated content is functionally exercised, and it is opt-in.

## Inputs / Outputs
- Inputs: `playground/*` examples, deterministic run steps (T-r21p4y), real run + generated
  artifacts (T-g7rjh0), `time.perf_counter` pattern from `tests/test_e2e_cli.py`.
- Outputs: `tests/playground/test_performance.py` (Fake, always) and
  `tests/playground/test_correctness_real.py` (real, gated).

## Acceptance Criteria
1. **Perf**: Given a FakeExecutor CLI run of each example, When timed, Then it completes
   under a documented bound (e.g. < 5s on CI) and exit 0. Bound is a named constant, not a
   magic literal, and generous enough to avoid CI flake.
2. **Perf**: Given the run, When `state.json` is read, Then no task's `attempts` exceeds its
   configured `max_attempts` and successful tasks show `attempts == 1` (no redundant retries).
3. **Perf** determinism: perf tests assert timing bounds and attempt counts only — never
   absolute durations as expected values.
4. **Correctness (gated)**: Given `AO_E2E_REAL_LLM=1` + `claude` available, When the real
   `sum-of-array` run finishes and the generated implementation is located (per the
   example's documented output path), Then executing it yields correct results on a small
   fixed input matrix (`[] -> 0`, `[1,2,3] -> 6`, `[-1,1] -> 0`).
5. **Correctness** is skipped by default (marker + env), same as the real-LLM tier; no token
   burn in default CI.
6. `uv run pytest -q` green (perf half runs; correctness half skipped); `ruff`/`mypy` clean;
   no `src/` change (NFR-1).

## Risks
- CI timing flake → pick a generous bound + document it; keep FakeExecutor (no subprocess).
- Locating the generated code in the real tier is content-shaped → rely on the example's
  documented output path convention (e.g. `output/tasks/t1/impl.md` references or a fixed
  `output/solution.py`); if the agent's layout varies, skip-with-reason rather than fail
  (correctness is a best-effort opt-in signal).

## Dependencies
- `T-r21p4y` (deterministic run shape), `T-g7rjh0` (real run + gate).

## Pseudocode / Algorithm
```text
PERF_BUDGET_SECONDS = 5.0
@pytest.mark.perf
@pytest.mark.parametrize("example", discover_examples())
def test_fake_run_within_perf_budget(example, tmp_path):
    tmp = copy_example(example, tmp_path); seed_control_files(tmp)
    t0 = perf_counter(); r = run_cli([...,"agents.fake.json"], tmp); dt = perf_counter()-t0
    assert r.exit_code == 0 and dt < PERF_BUDGET_SECONDS
    st = read_state(tmp); assert all(t["attempts"] <= max_attempts for t in st tasks)

@pytest.mark.real_llm
def test_generated_sum_is_correct(tmp_path):
    requires_claude()
    tmp = copy_example("sum-of-array", tmp_path)
    assert run_cli([...,"agents.claude.json"], tmp).exit_code == 0
    fn = load_generated_solution(tmp)          # documented path convention
    for arr, want in [([],0),([1,2,3],6),([-1,1],0)]: assert fn(arr) == want
```

## Schemas / Interface Notes
- Interface / API: `ao` CLI + generated-artifact execution (real half only).
- Spec / data schema: reads `state.json` attempts + timing.
- Triggers / events: manual.
- Artifacts: perf writes to `<tmp>`; correctness executes generated code in `<tmp>`.

## Handoff Boundary
- Upstream: `T-r21p4y`, `T-g7rjh0`.
- Downstream: sorting/student-data examples parametrize into the perf test automatically.

## Artifacts
- Docs/comments: this task folder.
- Large outputs: none.
