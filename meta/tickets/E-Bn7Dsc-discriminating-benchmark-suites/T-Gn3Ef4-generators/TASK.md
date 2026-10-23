# TASK: T-Gn3Ef4-generators

- Epic: `E-Bn7Dsc-discriminating-benchmark-suites`
- Title: Generator families, generated provider, ao-bench gen, gen-services-mini
- Status: In Progress
- Design: `docs-md/benchmark-discriminating-suites-hld.md` (§8 maps each piece), `docs-md/bench-probe-dsl.md`

## Acceptance
- Implements the §8 rows named in the title against the committed seams (registries, `grading_dir`, `GraderConfig.model_extra`, `RunContext`).
- Unit + e2e tests fake-tier (no LLM/network); docker-dependent tests gated by `AO_E2E_DOCKER=1`.
- `ruff`, `mypy src/agent_orchestrator/bench`, and `pytest tests/bench` green.
