# TASK: T-7gq3ax-project-scaffolding

## Metadata
- Task ID: `T-7gq3ax-project-scaffolding`
- Epic ID: `E-m2k9pa-orchestrator-mvp-a`
- Owner: `TODO`
- Created: `2026-06-16`
- Last Updated: `2026-06-16`
- Status: `Draft`
- Estimate: `< 1 day`

## Requirements Mapping
- Requirement IDs: enabler for all (NFR-2 packaging, CI for FR/NFR gates)

## Description
Create the Python project skeleton so all later tasks have a home. Greenfield — pin tooling now.

## Acceptance Criteria
1. `pyproject.toml` with package `agent_orchestrator` (src layout), deps: `pydantic>=2`, `jsonschema`, `croniter`, `typer`; dev: `pytest`, `ruff`, `mypy`.
2. `src/agent_orchestrator/__init__.py` (version) importable: `python -c "import agent_orchestrator"`.
3. `ruff check .`, `ruff format --check .`, `mypy src` all run clean on the skeleton.
4. `pytest -q` runs (0 tests or 1 trivial) green.
5. CI workflow skeleton runs install + ruff + mypy + pytest + JSON-schema validation step.

## Risks
- Tool version drift — pin minimal versions.

## Dependencies
- Upstream: none (first task). Downstream: every other task.

## Pseudocode / Algorithm
```text
create pyproject.toml (src layout, deps, tool configs for ruff/mypy/pytest)
create src/agent_orchestrator/__init__.py with __version__
create tests/ with test_smoke.py asserting import works
add .github/workflows/ci.yml: setup-python -> pip install -e .[dev] -> ruff -> mypy -> pytest -> schema-validate
```

## Schemas / Interface Notes
- Interface: none (packaging).
- Spec/data schema: N/A.
- Triggers/events: N/A.
- Artifacts: `pyproject.toml`, `src/agent_orchestrator/`, `tests/`, `.github/workflows/ci.yml`.

## Handoff Boundary
- Upstream: none.
- Downstream: package + CI ready for module work.

## Artifacts
- Docs/comments: this folder. Large outputs: none.
