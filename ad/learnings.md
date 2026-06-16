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
