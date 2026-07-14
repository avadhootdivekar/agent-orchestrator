# STATUS

- ID: `T-5isej3-tests-dynamic`
- Updated At: 2026-06-18
- State: Done
- Owner: tester

## This update (2026-06-18 — Done)
- `tests/conftest.py` extended: `make_workflow` supports `emit_tasks`, `task_manifest_path`, `loops`; `make_orchestrator` fixture added.
- `tests/test_dynamic_injection.py` (27 tests): `read_task_manifest` unit, cross-validate emit_tasks, integration: injects+completes, duplicate-id, cycle-after-injection, malformed manifest, resume after injection, determinism, NFR-1 static check.
- `tests/test_loop_construct.py` (24 tests): `read_gate` unit, `clone_body` unit (suffix, dep-rewrite, chain, gate-path suffix), cross-validate LoopSpec, integration: N iterations until false, max_iterations cap, gate-false-on-iter1, missing gate file, missing gate field, depends_on loop_id, origin='loop', resume mid-loop idempotent, deterministic ids, post-dev review/audit cycle, example spec validation.

## Evidence
By: tester · Role: tester · Date: 2026-06-18
207 passed (156 pre-existing + 51 new), 0 failed; 89% total coverage.
`ruff check`, `ruff format --check` clean; `mypy` clean.

## Risks / Blockers
None — all acceptance criteria met.
