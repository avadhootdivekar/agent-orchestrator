# TASK: T-r4t8wd-spec-loader-validation

## Metadata
- Task ID: `T-r4t8wd-spec-loader-validation`
- Epic ID: `E-m2k9pa-orchestrator-mvp-a`
- Owner: `TODO`
- Created: `2026-06-16`
- Last Updated: `2026-06-16`
- Status: `Draft`
- Estimate: `1–2 days`

## Requirements Mapping
- Requirement IDs: FR-4, FR-7, FR-8

## Description
Implement `config.py`, `spec.py`, `models.py`: load JSON/YAML specs, validate against JSON Schema, parse
into typed pydantic models, and cross-validate references. (LLD §1, §2.)

## Acceptance Criteria
1. `load_workflow(path)` reads `.json`/`.yaml`, jsonschema-validates, returns `WorkflowSpec`.
2. `load_reposets(path)` and `load_agents(path)` return `dict[str, RepoSet]` / `dict[str, AgentSpec]`.
3. Cross-validation: unknown `task.agent`, unknown `repo_set`, or unknown `depends_on` id → `SpecValidationError` with the offending id.
4. Config file locations come from CLI flags or env (`AO_REPOSETS`, `AO_AGENTS`, `AO_WORKSPACE_ROOT`); no hardcoded paths.
5. Unit tests: valid specs parse; malformed (missing field, bad cron, dangling ref) raise the right typed error.

## Risks
- YAML vs JSON divergence — choose loader by extension; one validation path.

## Dependencies
- Upstream: T-k29mvp. Downstream: T-9xc2bk, T-h7k3qm.

## Pseudocode / Algorithm
```text
load(path): data = json/yaml by ext; jsonschema.validate(data, schema); Model(**data)
cross_validate(wf, reposets, agents): assert refs exist else SpecValidationError
```

## Schemas / Interface Notes
- Interface: `load_workflow/load_reposets/load_agents`, models in LLD §1.
- Spec/data schema: consumes `specs/*.schema.json`.
- Artifacts: reads spec files by path only.

## Handoff Boundary
- Upstream: schemas. Downstream: typed `WorkflowSpec`/registries for DAG + engine.

## Artifacts
- Docs/comments: this folder.
