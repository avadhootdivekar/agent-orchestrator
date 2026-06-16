# Learning compact — agent-orchestrator

- Use `uv run <tool>` always; `pip`/`pip3` not installed in this environment.
- Re-run ruff/mypy yourself after subagent delivery; they may falsely claim "clean".
- `ruff check --fix` auto-fixes most lint issues; E501 line-length needs manual wrapping.
- Workflow `id` must be fully lowercase; uppercase `E-` ticket prefix violates the spec schema.
- `cross_validate(workflow, reposets, agents)` — reposets second; swapping gives misleading "Unknown repo_set" errors.
- Editable-install AO cannot safely develop itself; a regression breaks the CLI needed to recover.
