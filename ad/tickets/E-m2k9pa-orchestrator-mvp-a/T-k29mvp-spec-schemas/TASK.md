# TASK: T-k29mvp-spec-schemas

## Metadata
- Task ID: `T-k29mvp-spec-schemas`
- Epic ID: `E-m2k9pa-orchestrator-mvp-a`
- Owner: `TODO`
- Created: `2026-06-16`
- Last Updated: `2026-06-16`
- Status: `Draft`
- Estimate: `< 1 day`

## Requirements Mapping
- Requirement IDs: FR-3, FR-4, FR-7, FR-8

## Description
Finalize the structured spec files (already drafted in design) and add an automated schema-validation
check. The schemas define workflow DAGs, multi-repo sets, and the agent registry.

## Acceptance Criteria
1. `specs/workflow.schema.json`, `specs/reposet.schema.json`, `specs/agents.schema.json` are valid JSON Schema (draft 2020-12) and `additionalProperties:false` everywhere.
2. `specs/examples/{workflow,reposet,agents}.json` validate against their schemas via `jsonschema`.
3. A `python -m agent_orchestrator.validate specs/` (or CLI `ao validate`) entrypoint validates all example specs and exits non-zero on any failure.
4. CI runs the schema-validation step (ties to T-7gq3ax CI).
5. Payloads remain **path-referenced only** — no instruction/artifact content inlined in any spec.

## Risks
- Schema too strict/loose — cover required fields + cron/event conditional requirements (already in draft).

## Dependencies
- Upstream: T-7gq3ax. Downstream: T-r4t8wd (loader consumes these schemas).

## Pseudocode / Algorithm
```text
lock the three schemas (draft in specs/)
write validate(specs_dir): for each example, jsonschema.validate(example, matching schema); collect errors
exit 0 if none else 1 with the failing instance path
```

## Schemas / Interface Notes
- Spec/data schema: `specs/*.schema.json` (authoritative shapes in LLD §1).
- Triggers/events: `trigger` def supports manual/cron/event (event = stub downstream).
- Artifacts: spec + example files under `specs/`.

## Handoff Boundary
- Upstream: package skeleton. Downstream: loader/models build on these shapes.

## Artifacts
- Docs/comments: this folder; design in `docs-md/lld-agent-orchestrator.md`.
