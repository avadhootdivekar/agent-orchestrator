# Learnings — agent-orchestrator

---
Learning-ID: LRN-20260616-uv-not-pip
Learning: Use `uv run <tool>` for all dev tooling; `pip`/`pip3` are not installed in this environment.
Context: Discovered when verifying the MVP implementation — `pip install` failed, `uv run pytest` worked.
By: agent
Role: agent
Date: 2026-06-16
---

---
Learning-ID: LRN-20260616-subagent-lint-verify
Learning: Always re-run ruff/mypy yourself after a subagent delivers code; subagents may claim "clean" when errors remain.
Context: Developer subagent reported ruff clean but 16 lint errors were present; caught only by an independent re-run.
By: agent
Role: agent
Date: 2026-06-16
---

---
Learning-ID: LRN-20260616-ruff-fix-limit
Learning: `uv run ruff check --fix` auto-fixes most lint errors but leaves line-length (E501) violations for manual edits.
Context: 13 of 16 ruff errors were auto-fixed; 2 E501s in test files needed manual line-wrapping.
By: agent
Role: agent
Date: 2026-06-16
---

---
Learning-ID: LRN-20260616-workflow-id-lowercase
Learning: Workflow `id` in JSON spec must be fully lowercase (`^[a-z0-9][a-z0-9-_]*$`); uppercase `E-` ticket prefix violates the schema.
Context: Using `E-test01-example` as workflow id caused SpecValidationError; ticket ids (ad/tickets/) still use uppercase E-.
By: agent
Role: agent
Date: 2026-06-16
---

---
Learning-ID: LRN-20260616-cross-validate-arg-order
Learning: `cross_validate(workflow, reposets, agents)` — reposets before agents; swapping them produces a misleading "Unknown repo_set" error.
Context: Passed args as (wf, agents, reposets); error looked like a config problem but was an API misuse.
By: agent
Role: agent
Date: 2026-06-16
---

---
Learning-ID: LRN-20260616-ao-self-host-risk
Learning: AO cannot safely orchestrate its own development with the editable install: any regression breaks the CLI used to recover.
Context: Editable install means every `uv run agent-orchestrator` imports live from src/; no stable copy exists as fallback.
By: agent
Role: agent
Date: 2026-06-16
---

---
Learning-ID: LRN-20260616-tool-install-noneditable-isolation
Learning: Use `uv tool install .` (non-editable) for v1/v2 isolation; `--editable` makes v1 and v2 the same working copy.
Context: install.sh was generated with `--editable`; v1 broke whenever v2 regressed. Solution: non-editable snapshot + dual-invocation (`ao` = v1, `uv run ao` = v2).
By: agent
Role: agent
Date: 2026-06-16
---

---
Learning-ID: LRN-20260616-agents-json-schema-fields
Learning: `agents.json` schema requires `executor`, `command_template`, `prompt_template`; fields `type`, `description`, `env` are rejected as additional properties.
Context: Self-dev agents.json used `type`/`description`/`env` (intuitive names) and failed schema validation; had to match the example format exactly.
By: agent
Role: agent
Date: 2026-06-16
---

---
Learning-ID: LRN-20260618-venv-python-invocation
Learning: Run project tooling via `.venv/bin/python -m <tool>` — bare `python` is absent from PATH and system `python3` has no pytest.
Context: Capturing a test baseline failed with `python: command not found` then `No module named pytest`; only `.venv/bin/python` had the deps.
By: agent
Role: agent
Date: 2026-06-18
---

---
Learning-ID: LRN-20260618-build-dag-inferred-edges
Learning: `build_dag` infers DAG edges from matching input/output paths, not just `depends_on`; any cloned/duplicated task that copies those paths creates spurious `CycleError`s — clear inputs/outputs on clones.
Context: Loop-iteration clones (`__iterN`) initially carried the body's inputs/outputs and produced false cycles back to terminal tasks; fixed by emptying them in `_clone_body` (ADR-007).
By: agent
Role: agent
Date: 2026-06-18
---

---
Learning-ID: LRN-20260618-mypy-scope-src
Learning: Run `mypy src` (not `mypy .`) for a clean production signal — `mypy .` surfaces pre-existing errors in `tests/test_project_config.py` unrelated to current work.
Context: Subagents reported "mypy clean on production modules" while `mypy .` still showed 3 long-standing test errors; scoping to src disambiguates real regressions.
By: agent
Role: agent
Date: 2026-06-18
---

---
Learning-ID: LRN-20260618-runstate-new-field-default
Learning: Every new field added to `RunState` (or any model nested in it) must have a default/`default_factory`, or resuming a run created before the change fails to deserialize the persisted `state.json`.
Context: Budget work added `BudgetCounters` to `RunState` via `Field(default_factory=...)` specifically so older run states still load on resume.
By: agent
Role: agent
Date: 2026-06-18
---

---
Learning-ID: LRN-20260618-wait-needs-unsatisfiable-guard
Learning: A wait-on-exhaustion path must guard against an unsatisfiable charge (one task's estimate exceeding the entire total/rate-window cap) or the engine sleeps forever; detect and stop/fail instead of waiting.
Context: Token-budget engine added an `_is_unsatisfiable` check so a task whose estimate can never fit a window doesn't loop indefinitely under `on_exhaustion=wait`.
By: agent
Role: agent
Date: 2026-06-18
---

---
Learning-ID: LRN-20260620-api-tests-dont-cover-cli
Learning: Testing a feature via `Orchestrator(...)` directly does not cover the CLI surface; `ao resume`, `ao validate`, and dynamic/loop workflow paths had 0% CLI coverage despite thorough engine-API tests.
Context: E2E test audit found dynamic injection and loop construct fully tested at Python-API level but completely untested via CliRunner — resume and validate commands likewise skipped.
By: agent
Role: agent
Date: 2026-06-20
---

---
Learning-ID: LRN-20260620-fakeexecutor-manifest-auto
Learning: `FakeExecutor` automatically writes the task manifest for tasks with `emit_tasks=True`; no manual manifest pre-seeding is needed in CLI-level dynamic injection tests.
Context: Writing CliRunner-level dynamic injection test — expected manual setup but FakeExecutor already handles manifest writing, matching the production contract.
By: agent
Role: agent
Date: 2026-06-20
---

---
Learning-ID: LRN-20260620-resume-cli-test-pattern
Learning: For `ao resume` CLI tests, do the first (failing) run via Python API to capture `run_id`, then invoke `ao resume --run-id <id>` via CliRunner — avoids parsing run_id from CLI stdout.
Context: CliRunner-based resume test needed a reliable run_id; Python-API first run returns `RunState.run_id` directly.
By: agent
Role: agent
Date: 2026-06-20
---

---
Learning-ID: LRN-20260701-real-claude-subprocess-sandbox-tmp
Learning: The real `claude` subprocess spawned by `ClaudeCliExecutor` is sandboxed to the repo working directory; pytest's `tmp_path` (system `/tmp/pytest-of-…`) is OUTSIDE that allow-list, so instruction reads and output writes there are denied and the run never reaches `succeeded`. Fix: run real-LLM e2e tests in a repo-local, gitignored workspace (`playground/.tmp/` via a `real_llm_workspace` fixture) so it sits inside the allow-list — no `--add-dir` / unrestricted access needed. Executor paths are absolute (resolved under `workspace_root`), so only the workspace location changes.
Context: `test_sum_of_array_real_llm.py` failed/BLOCKED under `AO_E2E_REAL_LLM=1` with "Claude Code may only [access] the allowed working directories" for every spine agent. Moving off `tmp_path` fixed it; verified all 3 real-LLM tests pass against the real `claude` interface.
By: agent
Role: developer
Date: 2026-07-01
---

---
Learning-ID: LRN-20260701-headless-claude-permission-mode
Learning: A headless `claude -p` agent expected to WRITE files needs an elevated permission mode or its Write tool calls are denied (no interactive prompt to approve). Add `--permission-mode acceptEdits` (auto-accepts file edits/writes, still gates bash) — a bounded choice preferable to `--dangerously-skip-permissions`. This is config-only via `agents.*.json` `command_template`, no `src/` change.
Context: Real-LLM playground agents only read instructions/inputs and write markdown/JSON outputs; `acceptEdits` in `agents.claude.json` let them complete autonomously.
By: agent
Role: developer
Date: 2026-07-01
---

---
Learning-ID: LRN-20260702-agents-schema-must-track-agentspec
Learning: When adding new fields to `AgentSpec` in `models.py`, `specs/agents.schema.json` MUST also be updated — it uses `additionalProperties: false`, so unregistered fields in any `agents.*.json` file cause schema validation failure at `ao validate` time.
Context: Adding `model` and `effort` to `AgentSpec` without updating the schema would have made the playground `agents.claude.json` (and any user-authored agents file) fail to validate.
By: agent
Role: developer
Date: 2026-07-02
---

---
Learning-ID: LRN-20260702-no-auto-cleanup-e2e-fixtures
Learning: E2E test fixtures that auto-delete workspace directories on teardown (`shutil.rmtree` in `finally`) destroy debugging artifacts needed for post-run audit. Preserve workspaces by default; expose an explicit cleanup command (`ao prune`) instead.
Context: `real_llm_workspace` fixture auto-cleaned via `shutil.rmtree`; removed per user requirement that artifacts should survive test runs for debugging/audit.
By: agent
Role: developer
Date: 2026-07-02
---

---
Learning-ID: LRN-20260702-effort-maps-to-max-turns
Learning: The `effort` field on `AgentSpec` maps to `--max-turns` via `EFFORT_MAX_TURNS = {low:15, medium:30, high:60}` (models.py). Keep this mapping GENEROUS: `--max-turns` is only a runaway-loop breaker — the *real* cost guard is the token budget. Tight turn caps (the original 3/5/10) cause non-deterministic `error_max_turns` failures on trivial tasks that happen to need a few extra tool calls. An explicit `AgentSpec.max_turns` overrides the effort-derived value, and `ao run/resume --max-turns N` (also `MAX_TURNS`/`AO_MAX_TURNS` in the Makefile/harness) sets it on every agent.
Context: Corrects the earlier 3/5/10 convention — real-LLM `architect-design` failed `error_max_turns` (num_turns:6, permission_denials:[]) under `medium`→5; raising to 15/30/60 + adding the override/flag fixed it. Playground `agents.claude.json` still pins `claude-haiku-4-5-20251001` + `effort: medium`.
By: agent
Role: developer
Date: 2026-07-02
---

---
Learning-ID: LRN-20260702-max-turns-vs-max-attempts
Learning: "Reached maximum number of turns (N)" in a failed task error is the Claude CLI's per-invocation turn budget (from `effort`→`--max-turns`), NOT the orchestrator's `max_attempts`; `attempt X/Y` in logs is the orchestrator retry counter.
Context: `impl-t1` showed `attempt 1/1` (one orchestrator attempt) but error "max_turns (5)" — two independent limits that confused diagnosis until both were read together.
By: agent
Role: developer
Date: 2026-07-02
---

---
Learning-ID: LRN-20260702-bypasspermissions-for-bash
Learning: A headless agent's `--permission-mode` must match what its INSTRUCTION actually does. `acceptEdits` auto-accepts file writes but silently DENIES Bash; any agent whose instruction runs Bash (compile, run pytest, syntax-check) stalls and burns its turns before failing — sometimes exiting 0 while skipping a declared output. Use `--permission-mode bypassPermissions` for those. Simplest robust convention for an example: standardize ALL agents on `bypassPermissions` (the playground `agents.claude.json` does this for all 5 agents) rather than per-agent tuning that breaks whenever an instruction adds a Bash step.
Context: Two separate hits, same root cause — `impl-t1` developer wasted its turns on a denied `python -c` syntax check; later `taskreview-t1` reviewer (still on `acceptEdits`) exited 0 but skipped `review.md` because its required `python -m pytest` was denied. Both fixed by `bypassPermissions`.
By: agent
Role: developer
Date: 2026-07-02
---

---
Learning-ID: LRN-20260702-agent-cwd-workspace
Learning: The `claude` subprocess must be spawned with an explicit `cwd`, or it inherits whatever dir `ao` was invoked from (usually the repo root) and any agent that writes a RELATIVE output path (from its instruction/spec — e.g. a manifest at `output/tasks-manifest.json`) silently escapes the workspace, landing at repo root. The agent then reports success while the engine can't find the declared output. Fix is framework-level + config-driven, NOT a per-test patch: `AgentSpec.working_dir` (optional) → engine resolves it under `reposet.workspace_root` (path-traversal guarded) → `TaskContext.cwd` (absolute; "" = OS inherits) → `subprocess.run(cwd=ctx.cwd or None)`. Default cwd = workspace root, so relative agent writes resolve deterministically inside the run's workspace.
Context: Real-LLM `architect-breakdown` claimed success but the manifest was found at repo-root `./output/tasks-manifest.json` instead of the workspace; `subprocess.run` had no `cwd`. Note this deliberately adds `src/` changes (models/engine/executor/cli) — the working-directory contract belongs in the framework so `ao` commands honor configured/default cwd, not in tests. Covered by `test_cwd_passed_to_subprocess`/`test_cwd_none_when_unset` (executor) + `TestAgentCwd` (engine).
By: agent
Role: developer
Date: 2026-07-02
---

---
Learning-ID: LRN-20260702-empty-env-falsy-no-flag
Learning: `AO_MAX_ATTEMPTS=""` (Makefile default `MAX_ATTEMPTS ?=`) is falsy in Python, so harness.py's `if max_attempts:` never injects `--max-attempts` and the workflow runs with `max_attempts=1` (no retry) silently.
Context: Real-LLM test ran without retry; `MAX_ATTEMPTS` was unset, empty string was silently skipped by the `if` guard in `run_cli`.
By: agent
Role: developer
Date: 2026-07-02
---
