# TASK: T-VcN4pt-task-title-field

## Metadata
- Task ID: `T-VcN4pt-task-title-field`
- Epic ID: `E-k3AMEr-run-graph-canvas`
- Owner: `developer` (unassigned)
- Created: `2026-09-27`
- Last Updated: `2026-09-27`
- Status: `Draft` (**Non-MVP**, sprint-2 stretch)
- Estimate: `6 focus hours (< 1 day)`

## Requirements Mapping
- Requirement IDs: U-6 (friendlier "title" than the id), HLD §3 A-1, §7.5 (`label_for` seam)

## Description
Add an optional, inert display field `TaskSpec.title: str | None = None` and use it as the node
label (falling back to the id). The change is additive to the workflow spec schema, and
`ao validate` accepts specs with and without it. `emit_tasks` manifests may set it: it is plain
text and executes nothing, so the AC-15 containment is unaffected.

## Acceptance Criteria
1. Given a spec with `title: "Implement parser"` on task `t1`, when `/api/runs/{id}/graph` is
   fetched, then node `t1` has `label == "Implement parser"`. Without `title`, `label == "t1"`.
2. `title` is capped at `TASK_TITLE_MAX_CHARS = 120` by pydantic (`max_length`), and longer
   values fail `ao validate` with a structured error naming the task. An injected manifest with an
   over-long title fails injection cleanly (the manifest-read `ValueError` path) and does not crash.
3. The frontend renders `label` as a text node only. A test with
   `title: "<img src=x onerror=alert(1)>"` asserts that the literal text is shown and no `img`
   element exists.
4. Existing specs and fixtures validate unchanged. The full pytest and vitest suites stay green.

## Risks
- Low. This is the first spec-schema change in the epic. Keep it optional forever (NFR-1).

## Dependencies
- `T-M4qboy-run-graph-builder` (the `label_for` seam), `T-OjTS8O-run-graph-canvas`.

## Pseudocode / Algorithm
```text
models.TaskSpec: title: str | None = Field(default=None, max_length=TASK_TITLE_MAX_CHARS)
ui/graph.py::label_for(spec, id): RETURN spec.title if spec and spec.title else id
```

## Schemas / Interface Notes
- Spec / data schema: `TaskSpec.title?: string (<=120)`
- Interface / API: `GraphNode.label` semantics are unchanged (it was already "display label").
- Triggers / events: N/A
- Artifacts: none

## Handoff Boundary
- Upstream: the builder's `label_for` seam.
- Downstream: `T-oroE5f-docs-refresh` documents the field in the spec reference.

## Artifacts
- Docs/comments: `meta/tickets/E-k3AMEr-run-graph-canvas/T-VcN4pt-task-title-field/`
- Large outputs: N/A
