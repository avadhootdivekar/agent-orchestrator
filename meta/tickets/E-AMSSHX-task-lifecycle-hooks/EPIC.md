# EPIC: E-AMSSHX-task-lifecycle-hooks

## Metadata
- Epic ID: `E-AMSSHX-task-lifecycle-hooks`
- Title: Task lifecycle hooks (engine-level pre_hook / post_hook)
- Owner: dev-epic (this agent), implementation delegated to developer/tester/reviewer subagents
- Created: 2026-09-21
- Last Updated: 2026-09-21
- Status: In Progress

## Summary
- Goal: give the orchestrator engine a config-driven, opt-in `pre_hook` / `post_hook` per task —
  an argv command the engine runs immediately before a task dispatch begins and immediately after
  it concludes — with a true no-op default (zero overhead when undeclared), documented failure
  semantics, and an interface general enough that a later epic (Epic B, cost/caching + outcome
  metrics) can plug a grading post-hook in (generalizing `bench/graders.py`) without changing the
  core hook-dispatch mechanism.
- Scope In: `TaskSpec.pre_hook`/`post_hook` fields (models.py + JSON schema), engine dispatch
  inside `Orchestrator._run_with_retries`, failure-semantics policy (`on_failure:
  ignore|fail_task`), capture-directory/context-file/result-file contract, example workflow spec
  + hook scripts, unit + integration + e2e tests, HLD design doc.
- Scope Out (explicitly, see HLD §9): workflow-level default hooks applied to every task; a
  `--no-hooks`/`AO_DISABLE_HOOKS` CLI/env kill switch; hook-level retry policy independent of the
  task's `RetryPolicy`; token/cost accounting for a hook's own execution; dashboard rendering of
  hook results; the actual Epic B grading-hook script (only the interface it will plug into).

## Design doc
- `docs-md/task-lifecycle-hooks-hld.md` — full design, forward-compat note for Epic B (§7),
  failure-semantics decision table (§6), config-precedence decision (§8), change-scope boundary
  table (§10).

## Requirements

### MVP (must-have; each maps to ≥1 task below)
- FR-1 (MVP): `TaskSpec.pre_hook`/`post_hook: HookSpec | None = None`, opt-in, validated by
  pydantic (`command` non-empty argv) and by `specs/workflow.schema.json`.
  Verification: unit tests in T-jI3P4p; schema validated by `ao validate`.
- FR-2 (MVP): engine runs `pre_hook` once per dispatch cycle before the agent executor is
  invoked; on failure with the (default) `on_failure="fail_task"` policy, the task fails without
  ever invoking the executor (zero agent spend).
  Verification: unit test asserting executor `call_count == 0` when a pre_hook fails with
  `fail_task` (T-jI3P4p).
- FR-3 (MVP): engine runs `post_hook` once per dispatch cycle after the agent executor produces a
  terminal result (succeeded, or exhausted-retries failed/timed_out) — NOT on cancelled or
  quota-exhausted early-return. Default `on_failure="ignore"` never changes the agent's own
  status; `on_failure="fail_task"` can downgrade succeeded→failed but never upgrade.
  Verification: unit tests for both policies × both underlying statuses (T-jI3P4p).
- FR-4 (MVP, locked-in hard requirement): default (no hooks declared) is a true no-op — no env
  dict built, no capture dir created, no subprocess spawned.
  Verification: unit test patching `Orchestrator._run_hook` with a `Mock`, asserting
  `call_count == 0` for a hookless task across succeeded/failed/timed_out outcomes; a timing
  micro-benchmark script recorded as evidence (T-jI3P4p / T-FCC8mT).
- FR-5 (MVP): hook verdict = process exit code (0 = passed); an optional JSON file at
  `AO_HOOK_RESULT_PATH`, bounded via the existing `artifacts.read_control`, is recorded verbatim
  as `HookOutcome.detail` but never overrides the exit-code verdict.
  Verification: unit tests with a hook script that writes a result file + exits both 0 and
  nonzero (T-jI3P4p).
- FR-6 (MVP): hook context handed to the subprocess is paths-only (NFR-1) — `context.json` +
  env vars (`AO_RUN_ID`, `AO_TASK_ID`, `AO_HOOK_KIND`, `AO_DISPATCH_CYCLE`,
  `AO_HOOK_CONTEXT_PATH`, `AO_HOOK_RESULT_PATH`), reusing the task's own resolved instruction/
  input/output/repo paths and `env_overlay` (isolation-aware for free).
  Verification: unit test asserting `context.json` shape + no file-content reads (T-jI3P4p).
- FR-7 (MVP): end-to-end demonstration — an example workflow spec under `specs/examples/`
  exercising both hooks through the real CLI (`ao validate` + `ao run`), with captured
  `pre_hook`/`post_hook` directories and a passing/failing hook scenario.
  Verification: e2e test + manual run transcript (T-FCC8mT).

### Non-MVP (deferred; see HLD §9 for how each would be validated later)
- NFR-1: workflow-level default hooks (validated the same way `general_instructions`/
  `resolve_effective_agent` fill-in patterns are tested today).
- NFR-2: `--no-hooks`/`AO_DISABLE_HOOKS` kill switch (validated the same way
  `test_cli_isolation_flags.py` validates `--no-isolation`).
- NFR-3: hook-level retry policy (validated with a dedicated `hook.retries` field + retry-loop
  test once designed).

### Stretch (nice-to-have; not required to ship)
- S-1: dashboard/UI rendering of `pre_hook_result`/`post_hook_result` (data is already persisted
  in `RunState`; only a UI change would be needed).

## Task List
- [ ] `T-AHvmYR-hook-schema-models` — models.py: HookSpec/HookOutcome/constants/resolver fn +
  TaskSpec/TaskResult/TaskRunState fields. (FR-1, FR-5)
- [ ] `T-lzQEyy-engine-hook-dispatch` — engine.py: `_run_hook`, pre/post wiring in
  `_run_with_retries`, `_settle_completed_task` mirror. (FR-2, FR-3, FR-4, FR-6)
- [ ] `T-DgheoA-spec-schema-validation` — `specs/workflow.schema.json` `$defs/hook` +
  pydantic-level validation confirmation. (FR-1)
- [ ] `T-fbQIFX-example-workflow-and-docs` — example spec + hook scripts under
  `specs/examples/`. (FR-7)
- [ ] `T-jI3P4p-unit-integration-tests` — unit + integration test suite for all of the above.
  (FR-1..FR-6)
- [ ] `T-FCC8mT-e2e-verification` — late-gate: run the example workflow end-to-end via the real
  CLI, capture evidence. (FR-7)
- [ ] `T-6gR2ya-review-and-hardening` — reviewer pass on the implementation diff + any fixes.

## Risks and Dependencies
- engine.py is a large (~4000-line), heavily-conventioned file with many existing invariants
  (NFR-1 paths-only, NFR-3 no-worker-RunState-mutation, R-21 dispatch-cycle keying) — mitigated
  by keeping the entire hook mechanism inside `_run_with_retries` (D1 in the HLD) so no other
  subsystem's call sites change.
- Ambiguity: exact env var / context.json field naming is this agent's own design choice (no
  prior "hook" precedent in the repo to match) — flagged as a design decision, not a blocker;
  documented explicitly in the HLD so Epic B doesn't have to guess.

## Links
- Design doc: `docs-md/task-lifecycle-hooks-hld.md`
- Example spec: `specs/examples/workflow-hooks.json` (added by T-fbQIFX)
- Output artifacts (if any): `output/E-AMSSHX-task-lifecycle-hooks/` (e2e run evidence, added by
  T-FCC8mT)
