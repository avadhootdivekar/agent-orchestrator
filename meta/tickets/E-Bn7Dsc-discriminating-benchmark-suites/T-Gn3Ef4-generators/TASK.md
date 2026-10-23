# TASK: T-Gn3Ef4-generators

- Epic: `E-Bn7Dsc-discriminating-benchmark-suites`
- Title: Generator families, generated provider, ao-bench gen, gen-services-mini
- Status: Done (pending integration pass with the blackbox track)
- Design: `docs-md/benchmark-discriminating-suites-hld.md` (§8 maps each piece), `docs-md/bench-probe-dsl.md`, as-built notes in `docs-md/bench-generators.md`

## Acceptance
- Implements the §8 rows named in the title against the committed seams (registries, `grading_dir`, `GraderConfig.model_extra`, `RunContext`).
- Unit + e2e tests fake-tier (no LLM/network); docker-dependent tests gated by `AO_E2E_DOCKER=1`.
- `ruff`, `mypy src/agent_orchestrator/bench`, and `pytest tests/bench` green.

## Deliverables (all landed on `ad/bench-gen`)
- [x] `bench/gen/`: `base`, `wordlists`, `traps`, `skeleton`, `dsl`, `validate`, `refs`, `materialize`, `crud_relational`, `job_queue`, Python-stdlib references under `reference/`.
- [x] `bench/generated_provider.py` (`source.type: generated`; skeleton + SPEC.md → `repo_dir`, instance/probes/reference → `grading_dir`).
- [x] `bench/gen/cli.py` (`ao-bench gen`), deterministic task ids, generic per-contract instructions, README; grader carries `sandbox.stack`.
- [x] `benchmarks/suites/gen-services-mini/` (crud-relational + job-queue, python, medium) validating under `ao-bench validate`.
- [x] Tests: `tests/bench/test_gen_core.py`, `test_gen_families.py`, `test_gen_provider.py`, `dsl_runner.py` (test-only DSL evaluator).
- [ ] Integration: reference passes all probes under the real `blackbox` grader (docker + process sandboxes) — owned by the integration pass.
