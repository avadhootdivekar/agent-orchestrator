# STATUS

- ID: `T-sfdybw-loop-construct`
- Updated At: 2026-06-18
- State: Done
- Owner: developer

## This update (2026-06-18 — Done)
- `LoopSpec` model added to `models.py`; `WorkflowSpec.loops` field added.
- `workflow.schema.json` `$defs.loop` added; `loops` property wired to workflow top-level.
- `artifacts.read_gate` implemented (GateError on missing file/field/non-bool).
- `errors.LoopError`, `GateError` added.
- `spec.cross_validate` enforces all LoopSpec constraints (§3.2): body task existence, gate_task_id ∈ body, no emitter as gate, no overlapping bodies, `__iter` reserved suffix check.
- `dag.build_dag` resolves `depends_on:[<loop_id>]` to the final iteration's last task.
- Engine `_loop_for_gate`, `_gate_path_for_iter`, `_clone_body` helpers implemented.
- Loop iteration expansion integrated into re-entrant `while` loop (ADR-001: loops are injection).
- `_clone_body` clears `inputs`/`outputs` on clones to avoid spurious DAG inferred edges.
- `FakeExecutor` extended with `emit_payloads` and `gate_payloads` + `gate_invocations`.
- Example specs `specs/examples/workflow-dynamic.json` and `specs/examples/workflow-loop.json` added.

## Evidence
By: developer · Role: developer · Date: 2026-06-18
207 passed, 0 failed; 89% total coverage. All 156 pre-existing tests still pass.
`ruff check`, `ruff format --check` clean; `mypy` clean on new modules.

## Risks / Blockers
None — all acceptance criteria met.
