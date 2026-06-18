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
