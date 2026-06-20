# Learning compact — agent-orchestrator

- Use `uv run <tool>` always; `pip`/`pip3` not installed in this environment.
- Re-run ruff/mypy yourself after subagent delivery; they may falsely claim "clean".
- `ruff check --fix` auto-fixes most lint issues; E501 line-length needs manual wrapping.
- Workflow `id` must be fully lowercase; uppercase `E-` ticket prefix violates the spec schema.
- `cross_validate(workflow, reposets, agents)` — reposets second; swapping gives misleading "Unknown repo_set" errors.
- Editable-install AO cannot safely develop itself; a regression breaks the CLI needed to recover.
- Run tooling via `.venv/bin/python -m <tool>`; bare `python` absent, system `python3` lacks pytest.
- `build_dag` infers edges from matching input/output paths; clear inputs/outputs on cloned tasks to avoid false cycles.
- Use `mypy src` (not `mypy .`) for clean production signal; tests have pre-existing mypy errors.
- Engine-API tests (`Orchestrator()` directly) don't cover CLI; always add CliRunner tests per feature.
- `FakeExecutor` auto-writes manifest for `emit_tasks=True` tasks; no manual pre-seeding needed.
- `ao resume` CLI test: first (failing) run via Python API to get `run_id`, then CliRunner `ao resume --run-id`.
