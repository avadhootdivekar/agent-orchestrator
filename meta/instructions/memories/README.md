# Repo-scoped memories

---
name: uv-is-the-package-manager
description: pip/pip3 are not installed; use uv run for all dev tooling
type: constraint
---

`pip` and `pip3` are not available in this environment. **Why**: uv is the sole package manager installed at `~/.local/bin/uv`. **Apply**: whenever installing packages, running pytest, ruff, mypy, or the `ao` CLI — prefix with `uv run` (e.g. `uv run pytest -q`, `uv run ao validate ...`). The CI workflow uses `pip install` for GitHub Actions runners, which is fine; locally always use `uv`.

---
name: subagent-lint-not-self-certifying
description: Subagents may claim ruff/mypy clean when errors remain; always re-run independently
type: pitfall
---

Developer subagents have reported "ruff: clean" after delivering code that had 16 lint errors. **Why**: the agent may run ruff on a subset of files or misread output. **Apply**: after any subagent delivers code, run `uv run ruff check .` and `uv run ruff format --check .` yourself before accepting the delivery as done.

---
name: workflow-id-must-be-lowercase
description: Workflow `id` field in JSON/YAML spec must be fully lowercase; uppercase letters (e.g. E- ticket prefix) fail schema validation
type: constraint
---

The `id` field in every workflow spec is validated against `^[a-z0-9][a-z0-9-_]*$`. **Why**: the JSON schema enforces this pattern and rejects anything with uppercase characters, including the standard ticket prefix `E-`. **Apply**: when instantiating any workflow (including the epic-lifecycle template), always use a lowercase id (e.g. `e-abc123-my-feature`). The `ad/tickets/` directory still uses uppercase `E-` — these are separate concerns.

---
name: cross-validate-arg-order
description: cross_validate(workflow, reposets, agents) — reposets is second, agents third; swapping silently misdirects validation
type: pitfall
---

`cross_validate` in `agent_orchestrator.spec` takes `(workflow, reposets_dict, agents_dict)`. **Why**: passing agents as the second argument causes every repo_set lookup to fail with "Unknown repo_set: X" — the error looks like a missing config entry but is actually an argument-order bug. **Apply**: any time you call `cross_validate` directly, double-check the order: workflow → reposets → agents.

---
name: uv-tool-install-noneditable-for-isolation
description: uv tool install must be non-editable for v1/v2 isolation; --editable makes the global `ao` share the working copy
type: pitfall
---

`uv tool install --editable .` makes the globally installed `ao` and the dev copy identical — any regression in v2 breaks v1. **Why**: editable mode imports live from `src/` with no separate snapshot. **Apply**: `install.sh` must use `uv tool install .` (no `--editable`) to produce a true v1 snapshot. Dual-invocation pattern: `ao` (global) = v1 stable; `uv run ao` in repo = v2 dev.

---
name: agents-json-required-fields
description: agents.json executor entries require `executor`, `command_template`, `prompt_template`; `type`/`description`/`env` are rejected
type: constraint
---

The agents JSON schema (`specs/agents.schema.json`) uses `additionalProperties: false`. Valid fields are `executor`, `command_template`, `prompt_template`, `context_window`. **Why**: `type`, `description`, and `env` look intuitive but are not in the schema and cause validation failure. **Apply**: when writing or scaffolding `agents.json`, match the `specs/examples/agents.json` field names exactly.

---
name: build-dag-infers-edges-from-paths
description: build_dag derives edges from matching input/output paths, so duplicated tasks sharing paths create spurious cycles
type: pitfall
---

`dag.build_dag` adds an edge whenever one task's output path equals another's input path — in addition to explicit `depends_on`. **Why**: any feature that clones or duplicates a task (loop iterations, fan-out, retries-as-tasks) while copying its `inputs`/`outputs` will make `build_dag` infer edges between the duplicates and their originals, producing false `CycleError`s against already-terminal tasks. **Apply**: when generating/cloning tasks at runtime, clear `inputs`/`outputs` on clones (as `engine._clone_body` does for `__iterN` loop tasks, ADR-007) or otherwise ensure duplicated tasks don't share artifact paths.

---
name: persisted-model-fields-need-defaults
description: New fields on RunState or any model nested in it must have defaults, or resuming an older run fails to deserialize state.json
type: constraint
---

`RunState` (and everything nested in it) is persisted to `state.json` and reloaded on resume. **Why**: a new field without a default makes pydantic reject any `state.json` written before the field existed, breaking resume for in-flight runs. **Apply**: when adding a field to `RunState` or a nested persisted model, always give it a default or `Field(default_factory=...)` (as the budget work did for `RunState.budget_counters` / `BudgetCounters`).

---
name: engine-api-tests-dont-cover-cli
description: Testing via Orchestrator() directly leaves `ao` CLI paths (resume, validate, flag parsing) uncovered even with thorough engine-API tests
type: pitfall
---

Engine-API integration tests (calling `Orchestrator(...).run(...)` directly) do not exercise the CLI layer. Dynamic injection, loop construct, and resume were all fully covered at engine level but had 0% CliRunner coverage. **Why**: CLI wiring, flag parsing, and command routing are separate code paths that only CliRunner tests exercise. **Apply**: for every significant feature, write both an engine-API integration test AND a CliRunner E2E test — treat them as different test layers, not substitutes.

---
name: loop-iterate-event-only-on-continue
description: The engine emits the `loop.iterate` log event ONLY when the gate says continue; a single-iteration loop never emits it
type: pitfall
---

A `LoopSpec` whose gate verdict is `{"continue": false}` on the first pass runs its body exactly once and **never emits a `loop.iterate` event** — the engine logs `loop.iterate` only on the branch that injects the next iteration's clones (`engine.py`, guarded by `should_cont`). Loop-body clones (ids like `bugfix__iter2`, `final-review__iter2`, `origin="loop"`) likewise only appear from iteration 2 onward. **Why**: a fixture/test that asserts `loop.iterate` (or `__iterN` clones) against a single-round happy path is unsatisfiable and will tempt someone to "fix" it by weakening the assertion. **Apply**: to observe `loop.iterate`/loop clones deterministically, pre-seed a 2-round gate sequence — iteration 1 gate `{"continue": true}` at `output/<gate>.json`, iteration 2 gate `{"continue": false}` at `output/<gate>__iter2.json` (the engine suffixes `__iterN` before the extension). Keep separate expected-event fixtures for the 1-round vs 2-round scenarios (see `playground/sum-of-array/fixtures/expected_events.json`). Related: driving specs through `CliRunner` needs absolute spec paths — `run_cli` in `tests/playground/harness.py` converts `--workflow/--reposets/--agents` values to absolute because CliRunner does not resolve them against a working dir.

---
name: real-llm-tests-not-tmp-path
description: Real ClaudeCliExecutor e2e tests must use a repo-local workspace, not pytest tmp_path (system /tmp is outside the claude sandbox)
type: pitfall
---

The `claude` subprocess spawned by `ClaudeCliExecutor` is sandboxed to the repo working directory; pytest's `tmp_path` (system `/tmp/pytest-of-…`) is **outside** that allow-list, so the agent's instruction reads and output writes there are denied and the run never reaches `succeeded` (fails with "Claude Code may only [access] the allowed working directories"). **Why**: executor paths are absolute (resolved under `workspace_root`), so the *location* of the workspace — not path relativity — is what the sandbox rejects; no `--add-dir` is needed if the workspace lives inside the repo. **Apply**: for any test that spawns the real `claude` CLI, use the `real_llm_workspace` fixture (`tests/playground/conftest.py`) which allocates a per-test, gitignored dir under `playground/.tmp/` inside the allow-list — never `tmp_path`. Fake-executor tiers can keep using `tmp_path`.

---
name: headless-claude-needs-acceptedits
description: A headless `claude -p` agent that must write files needs --permission-mode acceptEdits, or Write tool calls are silently denied
type: constraint
---

`claude -p` (headless) cannot prompt for tool approval, so in the default permission mode its Write/Edit calls are denied and a file-producing agent completes without writing anything. **Why**: no interactive approver exists in a spawned subprocess. **Apply**: give file-writing agents `--permission-mode acceptEdits` (auto-accepts file edits/writes, still gates bash) via the `command_template` in `agents.*.json` — a bounded choice preferable to `--dangerously-skip-permissions`; this is config-only, no `src/` change (see `playground/sum-of-array/agents.claude.json`).

---
name: agentspec-schema-must-stay-in-sync
description: specs/agents.schema.json uses additionalProperties:false — every new AgentSpec field must also be added to the schema or agents.*.json files fail validation
type: pitfall
---

`specs/agents.schema.json` has `"additionalProperties": false` for agent entries. **Why**: any field in `AgentSpec` (models.py) that isn't registered there will cause `ao validate` to reject any `agents.*.json` that uses it — including the playground files. **Apply**: whenever a new field is added to `AgentSpec`, add a matching entry to `$defs/agent/properties` in `specs/agents.schema.json` in the same commit. Current registered fields: `executor`, `command_template`, `prompt_template`, `context_window`, `extra_args`, `model`, `effort`.

---
name: e2e-fixtures-must-not-auto-delete
description: E2E test fixtures must NOT auto-delete workspaces on teardown; artifacts must survive for audit — use `ao prune` for manual cleanup
type: constraint
---

Auto-cleaning a real-LLM workspace in a `finally` block (`shutil.rmtree`) destroys agent outputs, logs, and control files that are essential for post-run debugging and audit. **Why**: e2e tests produce the same observable artifacts as production runs; deleting them by default violates the project's "no automatic artifact removal" policy. **Apply**: in `real_llm_workspace` (and any future e2e fixture), omit teardown cleanup — yield the path and let it persist. Run `ao prune --workspace <path>` explicitly when disk space needs reclaiming.
