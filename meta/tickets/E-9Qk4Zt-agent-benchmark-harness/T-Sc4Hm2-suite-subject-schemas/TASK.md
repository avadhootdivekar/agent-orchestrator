# TASK: T-Sc4Hm2-suite-subject-schemas

## Metadata
- Task ID: `T-Sc4Hm2-suite-subject-schemas`
- Epic ID: `E-9Qk4Zt-agent-benchmark-harness`
- Owner: developer agent
- Created: 2026-07-22 · Last Updated: 2026-07-22
- Status: Draft · Estimate: 2.0 days (≤3)

## Requirements Mapping
- FR-1, NFR-1 (core untouched).

## Description
Create the versioned, `additionalProperties:false` JSON schemas + pydantic models + loader/validator for benchmark **suites** and **subjects**, plus the empty registries and `errors.py` the rest of `bench/` builds on, and the `ao-bench validate` command skeleton (validate only). Design §4.1, §5, §6.

Deliverables:
- `benchmarks/schemas/benchmark-suite.schema.json`, `benchmarks/schemas/subject.schema.json` (design §5).
- `src/agent_orchestrator/bench/__init__.py`, `errors.py` (`BenchError`, `SubjectError`, `GraderError`; reuse core `SpecValidationError`), `registries.py` (empty `SUBJECT_REGISTRY`/`GRADER_REGISTRY` dicts + register helpers), `models.py` (`BenchSuite`,`BenchTask`,`SubjectSpec`,`GraderConfig`,`Assertion`), `spec.py` (`load_suite`,`load_subject`).
- `ao-bench validate` (in `cli.py`) — validate a `--suite` and/or `--subject`; probe `claude --version` when a `claude_cli` subject is validated (A1). (Full CLI is T-Cli8Nf; here just the `validate` command + a minimal Typer app.)

## Acceptance Criteria
1. Given a schema-valid `suite.json` with unique lowercase task ids and existing `instruction`/`fixture` paths, When `ao-bench validate --suite s.json`, Then exit 0 + "OK".
2. Given a suite with a duplicate task id / uppercase id / missing fixture dir / unknown `grader.type` / missing `version`, When validated, Then exit non-zero with a structured error naming the offending id/path/type (one distinct message per case).
3. Given a subject with an unknown `type`, When `ao-bench validate --subject j.json`, Then exit non-zero listing known subject types.
4. Both schemas have `"additionalProperties": false` at every object level and a required `version`; an unknown key → validation error.
5. `mypy src/agent_orchestrator/bench` and `ruff check` clean; no edit to any file outside `src/agent_orchestrator/bench/`, `benchmarks/`, `pyproject.toml` (grep-verified for NFR-1).

## Risks
- Over-strict schema blocking legitimate future fields → mitigate by versioning + keeping enums (`grader.type`,`subject.type`,`category`) as the only closed lists.

## Dependencies
- None (foundation task).

## Pseudocode / Algorithm
```text
load_suite(path): read → jsonschema.validate(BENCH_SUITE_SCHEMA) → BenchSuite(**data)
  → assert lowercase+unique task ids; resolve+exist-check instruction/fixture; validate grader vs GRADER_REGISTRY[type].config_schema (or a static per-type schema map here since graders land in T-Grd7Vx — use a schema map, not the class)
load_subject(path): read → jsonschema.validate(SUBJECT_SCHEMA) → assert type in KNOWN_SUBJECT_TYPES → SubjectSpec(**data)
```
Note: graders/subjects classes don't exist yet — validate `grader.type`/`subject.type` against a static `KNOWN_*` set defined here; the registries wire real classes in T-Grd7Vx/T-Sbj9Ka.

## Schemas / Interface Notes
- Spec/data schema (JSON): design §5 (suite + subject). Models: design §6.
- Interface: `load_suite(path)->BenchSuite`, `load_subject(path)->SubjectSpec`; `errors.BenchError`.
- Triggers/events: N/A.
- Artifacts: reads `benchmarks/**`; writes nothing at runtime.

## Handoff Boundary
- Upstream: design doc §4.1/§5/§6.
- Downstream: T-Sbj9Ka (subjects) + T-Grd7Vx (graders) register classes into the registries and extend the `KNOWN_*` sets; T-Cli8Nf fleshes out the CLI.

## Artifacts
- Docs/comments: this folder.
- Large outputs: none.
