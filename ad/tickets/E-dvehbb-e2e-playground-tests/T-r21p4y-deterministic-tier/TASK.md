# TASK: T-r21p4y-deterministic-tier

## Metadata
- Task ID: `T-r21p4y-deterministic-tier`
- Epic ID: `E-dvehbb-e2e-playground-tests`
- Owner: tester
- Created: 2026-07-01
- Last Updated: 2026-07-01
- Status: Done
- Estimate: `< 3 days`
- MVP: **yes**

## Requirements Mapping
- FR-4, FR-7 (all six areas), NFR-2, NFR-3, NFR-4. Key design facts 1–5.

## Description
Implement **Tier 2 — deterministic/reproducible e2e tests** for `sum-of-array`, driven
entirely through the `ao` CLI via `CliRunner` against `agents.fake.json`, with the
control-file fixtures **pre-seeded** into the workspace before each run. Assert ONLY
paths / names / directory structure / `run.log` structure & order / `status.json` fields
/ token-accounting math / CLI exit codes & flags. **Never** assert LLM-generated content.
This tier is always green in CI with zero token burn (FakeExecutor spawns no subprocess).

Deliver `tests/playground/test_sum_of_array_deterministic.py`, organized so every one of
the six areas has at least one explicit assertion (§ area matrix below).

### Pre-seed procedure (per test, before `ao run`)
```text
tmp = copy_example("sum-of-array", tmp_path)      # from T-7592ux
seed(tmp/"output/tasks-manifest.json", fixtures/tasks-manifest.json)   # emit_tasks source
seed(tmp/"output/final-verdict.json",  fixtures/final-verdict.json)    # loop gate verdict (iter 1)
# (2-round variant also seeds output/final-verdict-iter2.json -> output/final-verdict__iter2.json)
res = run_cli(["run","--workflow","workflow.json","--reposets","reposet.json",
               "--agents","agents.fake.json"], tmp)
```

## Inputs / Outputs
- Inputs: `playground/sum-of-array/*`, harness (`copy_example`/`run_cli`), fixture helpers
  (`load_expected`, `assert_tree`), `HeuristicTokenEstimator` formula, `logging_setup`
  event names, `runstate` status.json shape.
- Outputs: `tests/playground/test_sum_of_array_deterministic.py`.

## Acceptance Criteria (mapped to the six areas)
1. **Area 1 — Workflow/DAG/deps**: Given the run, When it completes, Then exit 0 and
   `status.json`.status == "succeeded"; the topological order recovered from `run.log`
   `task.start` events equals `fixtures/expected_events.json`.order (spine → injected chain
   → integrate → loop iterations → done); every path in `fixtures/expected_paths.json`
   exists and is non-empty. A negative variant (cyclic workflow) → nonzero exit + output
   contains "cycle"/"circular".
2. **Area 2 — Token computation**: Given `ao run ... --budget-total <big> --pessimism-buffer 1.3`,
   When it completes, Then `state.json`.budget_counters.consumed_tokens equals the sum over
   executed tasks of `ceil((instruction_bytes + input_bytes)/chars_per_token + output_allowance)*buffer`
   recomputed from fixture file sizes (deterministic); `run.log` contains `budget.charge` and
   `budget.reconcile` events for each task; because FakeExecutor reports no actuals, the
   reconcile keeps the estimate (assert reconcile actual == charged estimate).
3. **Area 3 — Dynamic inputs/deps**: Given the run, When `emit_tasks` fires, Then
   `status.json`.tasks contains `impl-t1`,`testwrite-t1`,`taskreview-t1` with `origin="injected"`;
   the loop materializes `bugfix`/`final-review` (+`__iter2` clones if the 2-round variant)
   with `origin="loop"`; `done` appears after the final iteration in the recovered order.
4. **Area 4 — Logging**: Given the run, When `run.log` is read, Then it exists under
   `.orchestrator/runs/<run_id>/run.log`, every non-empty line is valid JSON with
   `ts/level/logger/msg`, the event set ⊇ {run.start, task.start, task.end, run.end,
   task.injected, loop.iterate}, and per-task lines carry `task_id`.
5. **Area 5 — Per-task output capture**: Given the run, When inspecting
   `.orchestrator/runs/<run_id>/<task_id>/`, Then `stdout.txt` and `stderr.txt` exist for
   every executed task (including injected + loop-clone ids); `state.json` records
   `output_artifact_path` for each.
6. **Area 6 — CLI + flags**: `ao validate` on the example → exit 0 / "OK"; `ao validate`
   on a malformed workflow → nonzero + "ERROR"; `ao run --rate-window invalid-window` →
   nonzero + "ERROR"; `ao status --workspace <tmp> --run-id <id>` prints the table;
   `ao resume` after a forced failure completes (fail-then-resume, reusing the existing
   `test_e2e_cli` pattern but on the playground workflow).
7. Determinism: running the full deterministic module twice yields identical asserted
   values (paths, counts, ordered event names, consumed_tokens). No content assertions.
8. `uv run pytest -q tests/playground/test_sum_of_array_deterministic.py` green offline;
   `ruff`/`mypy` clean; no `src/` change (NFR-1); zero subprocess spawned.

## Risks
- `run_id` / timestamps vary per run → assert structure/keys/ordered event NAMES and paths,
  never timestamp values; extract `run_id` from `Run:\s+(\S+)` in CLI output (as existing e2e does).
- Loop gate reads the verdict file the engine resolves per iteration — confirm the pre-seed
  path matches `engine._gate_path_for_iter` (iter 1 un-suffixed; iter N → `__iterN` before ext).
- Token math must recompute exactly; use the same `chars_per_token`/`output_allowance`/`buffer`
  the CLI passes (assert against `EstimatorConfig` defaults + the `--pessimism-buffer` flag).

## Dependencies
- `T-1vuzyi` (assets + fixtures), `T-ee8hzo` (helpers), `T-7592ux` (harness).

## Pseudocode / Algorithm
```text
def _run(tmp, extra=()):
    seed_control_files(tmp)                 # manifest + verdict(s)
    return run_cli(["run","--workflow","workflow.json","--reposets","reposet.json",
                    "--agents","agents.fake.json", *extra], tmp)

def test_area1_dag_order_and_outputs(tmp): r=_run(tmp); assert r.exit_code==0
    events = read_jsonl(runlog(tmp)); order = [e["task_id"] for e in events if e["event"]=="task.start"]
    assert order == load_expected("sum-of-array")["order"]
    assert_tree(tmp, load_expected("sum-of-array")["paths"])
def test_area2_token_math(tmp): r=_run(tmp, ["--budget-total","10000000","--pessimism-buffer","1.3"])
    st = read_state(tmp); assert st["budget_counters"]["consumed_tokens"] == recompute_estimate(tmp)
def test_area3_dynamic_injection_and_loop(tmp): ... origin fields in status.json
def test_area4_logging_structure(tmp): ... jsonl keys + event set
def test_area5_output_capture(tmp): ... stdout.txt/stderr.txt per task dir
def test_area6_cli_flags(tmp): validate ok/fail; invalid rate-window; status; resume
```

## Schemas / Interface Notes
- Interface / API: exercised via `ao` CLI only (NFR-4). No internal `Orchestrator(...)` calls
  in this module (resume setup may reuse the documented `test_e2e_cli` fail-then-resume pattern,
  still driving the second leg through `ao resume`).
- Spec / data schema: reads `state.json`/`status.json`/`run.log` structures.
- Triggers / events: manual trigger (default).
- Artifacts: pre-seeds control files under `<tmp>/output/`.

## Handoff Boundary
- Upstream: `T-1vuzyi`, `T-ee8hzo`, `T-7592ux`.
- Downstream: `T-g7rjh0` (real tier mirrors these run steps sans pre-seeding),
  `T-n477z9`/`T-94tepb` (reuse the parametrized deterministic harness for new examples).

## Artifacts
- Docs/comments: this task folder.
- Large outputs: none (runs write to `<tmp>/.orchestrator/`, discarded).
