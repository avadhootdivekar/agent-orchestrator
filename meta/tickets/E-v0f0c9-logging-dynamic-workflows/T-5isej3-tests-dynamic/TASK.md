# TASK: T-5isej3-tests-dynamic

## Metadata
- Task ID: `T-5isej3-tests-dynamic`
- Epic ID: `E-v0f0c9-logging-dynamic-workflows`
- Owner: tester
- Created: 2026-06-18
- Last Updated: 2026-06-18
- Status: Done
- Estimate: `< 3 days`

## Requirements Mapping
- Requirement IDs: FR-7..FR-12, NFR-1, NFR-2, NFR-3, NFR-6

## Description
Unit + integration tests for Feature Area 2 (dynamic task injection + loop
construct, including post-dev review/audit cycles). Deterministic via fixed
clock + `__iterN` suffixing + FakeExecutor that writes the control files
(task manifests / gate verdicts) the engine reads.

## Acceptance Criteria
1. Unit: `read_task_manifest` happy path + errors (not a dict, no `tasks`, invalid TaskSpec).
2. Unit: `read_gate` happy path + errors (missing file, missing field, non-bool).
3. Unit: `clone_body` suffixes ids, rewrites intra-body `depends_on`, chains iterations, suffixes gate path.
4. Unit: cross-validation rejects `emit_tasks` without `task_manifest_path`, overlapping loop bodies, authored `__iter` ids.
5. Integration: an `emit_tasks` run injects tasks, rebuilds the DAG, and completes.
6. Integration: duplicate-id manifest → emitter fails with structured error; cyclic injection → `CycleError`.
7. Integration: loop runs N iterations then stops when gate says `continue=false`.
8. Integration: loop stops at `max_iterations` while gate still says continue (no error).
9. Integration: post-dev review/audit cycle (body = review→remediate) loops until verdict clean.
10. Integration: resume after partial injection/loop rebuilds graph, skips emitter,
    and `loop_iterations` prevents re-cloning (idempotent); identical graph across two runs (NFR-2).
11. Integration: NFR-1 — engine never reads payload artifact content (only control reads via `artifacts.read_*`); assert via spy/inspection.
12. Coverage ≥80% on injection + loop code paths. `pytest` green; `ruff`/`mypy` clean.

## Risks
- Determinism of injected graph → assert on sorted ids + edges, not dict order.
- FakeExecutor must write control files at the right paths → provide a small fixture executor that emits a fixed manifest/gate sequence.

## Dependencies
- `T-17av6o`, `T-sfdybw`.

## Pseudocode / Algorithm
```text
test_read_task_manifest_ok_and_errors()
test_read_gate_ok_and_errors()
test_clone_body_suffix_and_dep_rewrite()
test_crossvalidate_dynamic_and_loop_rules()
test_emit_tasks_injects_and_completes(fixed_clock, manifest_writing_fake)
test_duplicate_injected_id_fails()
test_cyclic_injection_raises_cycleerror()
test_loop_runs_until_gate_false(gate_seq=[True,True,False])
test_loop_stops_at_max_iterations(gate_seq=[True]*10, max=3)
test_post_dev_review_cycle()
test_resume_after_injection_is_idempotent()
test_engine_never_reads_payload_content()   # spy on file reads
```

## Schemas / Interface Notes
- Interface / API: tests only.
- Artifacts: fixture executor writes `task_manifest_path` JSON and gate JSON files the engine consumes.

## Handoff Boundary
- Upstream: Area-2 implementation tasks.
- Downstream: `T-17v8sr` (docs refresh references verified behavior).

## Artifacts
- Code: `tests/test_dynamic_injection.py`, `tests/test_loop_construct.py`, fixtures in `tests/conftest.py`.
