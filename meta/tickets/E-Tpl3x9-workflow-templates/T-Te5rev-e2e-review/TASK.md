# TASK: T-Te5rev-e2e-review

## Metadata
- Task ID: `T-Te5rev-e2e-review`
- Epic ID: `E-Tpl3x9-workflow-templates`
- Owner: `claude`
- Created: `2026-07-24`
- Last Updated: `2026-07-24`
- Status: `Complete`
- Estimate: `< 3 days`

## Requirements Mapping
- HLD §2.5 (CLI e2e): Verify `ao templates` and `ao new` end-to-end via CliRunner
- HLD §2.6 (Dashboard API): Verify scaffold→discover workflow via service.create_instance()
- HLD §4 (Testing): "CLI e2e via CliRunner" + "Service/API integration"

## Description
Comprehensive e2e test suite for the shipped routed-runner builtin template: CLI scaffold/validation/injection, engine execution path (scaffold → run → dispatch), and dashboard scaffold-then-discover flow. Plus README and HLD doc updates.

## Acceptance Criteria (Completed)
1. ✓ New test file `tests/test_e2e_builtin_routed_runner.py` with **7 tests** covering:
   - Scaffolding via `ao new routed-runner` with all 10 required agents
   - Parameter injection (repo_set, type, task_budget_usd, run_budget_usd)
   - Prompt file injection via --prompt-file
   - Validation success when agents present / failure when missing / failure when required param omitted
   - Dashboard API scaffold (create_instance with start:false) → instance discovered by list_workflows()
   - **[NEW] Engine execution path**: Scaffolds, starts `ao run`, verifies workflow loads and engine begins task dispatch
2. ✓ All tests passing (**7/7** in new file, **1641/1641** full suite, no regressions)
3. ✓ Ruff lint check passed on new test file ✓
4. ✓ README.md updated: CLI reference + new "Workflow templates" section with usage/discovery/registration
5. ✓ HLD status updated: "in progress" → "Shipped"
6. ✓ Test design: CliRunner outermost boundary; 10 required agents in fixture; execution path verified
7. ✓ Empirical finding: FakeExecutor unconditionally overwrites output files; pre-seed verdict technique not viable via CliRunner; workaround (forced-type param) used

## Risks (Resolved)
- Routing execution completion: FakeExecutor limitations prevent full routing verification via CliRunner. Workaround: use `--param type=documentation` to force route at template render time. Full routing verified by test_engine_routing.py (dedicated routing tests).
- Non-determinism from random ID generation: tests use deterministic input (no random/seeding needed)

## Empirical Finding: FakeExecutor Behavior
- **Verified**: `src/agent_orchestrator/executors/fake.py:189` unconditionally truncates and writes to all declared output paths when `write_outputs=True` (default).
- **Consequence**: Pre-existing files (e.g., pre-seeded verdict.json) are overwritten, not preserved.
- **Impact**: The documented "fake executor leaves pre-existing files untouched" pattern (ao-runner-finplan) does not apply to CliRunner e2e tests; it may apply to direct engine API where FakeExecutor is configured with `write_outputs=False`.
- **Resolution**: Use template parameters (forced-type) to encode determinism at scaffold time rather than relying on runtime verdict files.

## Dependencies
- Delivered: builtin routed-runner template (src/agent_orchestrator/templates/builtin/routed-runner/)
- Delivered: CLI commands `ao templates` and `ao new` (src/agent_orchestrator/cli.py)
- Delivered: Dashboard service method create_instance() (src/agent_orchestrator/ui/service.py)

## Handoff Boundary
- This task (T-Te5rev-e2e-review, §E2E+Docs half): complete.
- Separate task (review half): runs independently, not blocking this completion.

## Artifacts
- Test file: tests/test_e2e_builtin_routed_runner.py
- Docs updates: README.md, docs-md/workflow-templates-hld.md
- Status: meta/tickets/E-Tpl3x9-workflow-templates/T-Te5rev-e2e-review/
