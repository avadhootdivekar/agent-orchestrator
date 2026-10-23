# TASK: T-Dk6Lm7-docs-skill-smoke

- Epic: `E-Bn7Dsc-discriminating-benchmark-suites`
- Title: Docs/README reconcile, bench skill, memories, real haiku smoke
- Status: Todo
- Design: `docs-md/benchmark-discriminating-suites-hld.md` (§8 maps each piece), `docs-md/bench-probe-dsl.md`

## Acceptance
- Implements the §8 rows named in the title against the committed seams (registries, `grading_dir`, `GraderConfig.model_extra`, `RunContext`).
- Unit + e2e tests fake-tier (no LLM/network); docker-dependent tests gated by `AO_E2E_DOCKER=1`.
- `ruff`, `mypy src/agent_orchestrator/bench`, and `pytest tests/bench` green.
