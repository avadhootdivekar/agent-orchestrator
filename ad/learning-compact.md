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
- Real-LLM e2e tests must NOT use pytest `tmp_path`: the spawned `claude` is sandboxed to the repo dir, so system `/tmp` is denied. Use a repo-local gitignored workspace (`playground/.tmp/` via `real_llm_workspace` fixture).
- Headless `claude -p` that must write files needs `--permission-mode acceptEdits` (config in `agents.*.json`), else Write is denied; bounded, unlike `--dangerously-skip-permissions`.
- Adding a field to `AgentSpec` also requires updating `specs/agents.schema.json` (additionalProperties:false); missing this breaks `ao validate`.
- E2e test fixtures must NOT auto-delete workspaces on teardown; preserve artifacts for audit, use `ao prune` for manual cleanup.
- `effort` on `AgentSpec` maps to `--max-turns`: low=3, medium=5, high=10. Playground examples use haiku + medium effort.
