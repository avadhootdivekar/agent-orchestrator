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
