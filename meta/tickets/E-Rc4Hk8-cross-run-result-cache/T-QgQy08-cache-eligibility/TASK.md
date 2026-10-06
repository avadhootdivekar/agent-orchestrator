# TASK: T-QgQy08-cache-eligibility

## Metadata
- Task ID: `T-QgQy08-cache-eligibility`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev B)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 3; implemented)
- Status: `Done`
- Estimate: `13 focus hours (1.6 days)` · Sprint 1

## Requirements Mapping
- Requirement IDs: FR-4, NFR-10 (M-12)
- HLD: §8.3.1 (classification), §8.3.2 (predicate), §8.3.3 (reasons owned by eligibility)
- ADR-0019: D10, D24, D25

## Description
Implement the fail-closed, **structural** allowlist in `cache/eligibility.py`. It is pure: no
I/O, no logging, public model helpers only. The coordinator checks the author policy before
calling it.

- **Tables:** `TASK_ANY_VALUE`, `TASK_VALUE_RULED` (`dict[str, Rule(ok, reason)]`),
  `WORKFLOW_FIELD_COVERAGE`, `DEFAULTS_FIELD_COVERAGE` (HLD §8.3.1).
- **`Eligibility(eligible, reason=None, detail=None)`**, a frozen dataclass.
- **`check_eligibility(task, workflow, agents, *, integration_active)`**, following HLD §8.3.2
  **verbatim**, including:
  - iteration over `type(task).model_fields`, sorted;
  - runtime unknown-field rules for the task, the workflow and the defaults;
  - **the `AgentSpec` runtime rule (Rev 3):** on the **effective** agent, any field outside
    `constants.AGENT_KEY_FIELDS | AGENT_NON_KEY_FIELDS` with a non-default value →
    `unknown_agent_field` (detail = field name);
  - the command-basename, `model_unresolved`, verdict-sidecar and breaker-verdict-source rules.
- **Tripwire message (U-E1):** the §8.3.1 wording, including "an approval/human-gate field
  (E-Ag7Pw3) is ALWAYS RULED".

## File scope (exclusive)
- `src/agent_orchestrator/cache/eligibility.py` (new)
- `tests/cache/test_eligibility.py` (new)

## Inputs / Outputs
- **Inputs:** `models` (`resolve_task_isolation`, `strip_iter_suffix`, `resolve_effective_agent`,
  `ISOLATION_NONE`); `usage.verdict_path_for`; `constants` (incl. the AgentSpec field sets).
- **Outputs:** `check_eligibility` and the tables, consumed by T-gDNjN2.

## Acceptance Criteria
1. **U-E1.** `set(TaskSpec.model_fields) == TASK_ANY_VALUE | set(TASK_VALUE_RULED)`; a failing run
   prints the §8.3.1 instruction text.
2. **U-E2.** `WORKFLOW_FIELD_COVERAGE` covers every `WorkflowSpec` field and
   `DEFAULTS_FIELD_COVERAGE` every `WorkflowDefaults` field.
3. **Runtime rules.** A `TaskSpec` subclass field at a non-default value → `unknown_task_field`
   (detail = name); at its default → eligible. The same pattern for `WorkflowSpec` and
   `WorkflowDefaults` subclasses → `unknown_workflow_field` (details `<name>` and
   `defaults.<name>`).
4. **U-E27 (`unknown_agent_field`).** An `AgentSpec` subclass with an extra field: at its default
   → eligible; non-default → `unknown_agent_field` with the field name. The rule reads the
   **effective** agent (a `defaults.model` override does not trip it).
5. **U-E3..E26.** One test per eligibility-owned reason row of §8.3.3, each asserting `reason` and
   `detail`: `run_integration_active`, `emit_tasks`, `task_manifest_path`, `output_manifest`,
   `pre_hook`, `post_hook`, `isolation_worktree` (from the task and from `defaults.isolation`),
   `router_task`, `loop_member` (including `dev__iter3`), `no_outputs`, `agent_unknown`,
   `executor_not_cacheable`, `command_not_cacheable` (wrapper script), `model_unresolved`,
   `verdict_sidecar_undeclared`, `breaker_verdict_source`.
6. **Positive cases.** Eligible: `command_template[0] == "/usr/local/bin/claude"`; `--model opus`
   baked into `command_template` with `model=None`; `--model=opus` in `extra_args`;
   `executor: fake`; a review task whose verdict sidecar is declared.
7. **U-E28 (consistency).** Every task for which `models._is_structural_task(task, workflow)` is
   true is ineligible (the production code does not call that private helper).
8. **Determinism and purity.** With several violations, the first reason is stable across 100
   calls and follows the §8.3.2 order; with `builtins.open`, `os.stat` and `subprocess.run`
   patched to raise, `check_eligibility` still returns.
9. **Hygiene.** ruff (≤ 100 columns) and mypy are clean; `pytest -q` has no new failures.

## Test requirements
- `tests/cache/test_eligibility.py`: AC-1..AC-8.

## Risks
- **Tripwires fail when sibling epics add fields.** Expected; the merge notes (HLD §24.2) say how
  to classify them. The runtime rules keep cached runs safe meanwhile.
- **Default comparison for mutable defaults.** Use
  `model_fields[f].get_default(call_default_factory=True)`.

## Dependencies
- T-28J9oR (models with `cache`), T-FJH6LI (constants).

## Pseudocode / Algorithm
```text
HLD §8.3.2 check_eligibility verbatim.
```

## Schemas / Interface Notes
- **Interface:** `check_eligibility(task, workflow, agents, *, integration_active) -> Eligibility`.
- **Reasons:** HLD §8.3.3.

## Handoff Boundary
- **Upstream:** T-28J9oR, T-FJH6LI.
- **Downstream:** T-gDNjN2; at merge time, E-Ag7Pw3 classifies its new fields here.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-QgQy08-cache-eligibility/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Allowlist predicate with
  tripwires.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2: `type(task)` iteration,
  workflow rules, command-basename and model-unresolved rules.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 3 (early-gate A3, D): the
  `unknown_agent_field` runtime rule (an unclassified `AgentSpec` field was silently unkeyed) and
  the U-E28 consistency test; re-estimated from 12 h to 13 h.
- By: developer · Role: developer · Date: 2026-10-05 · Comment: State -> Done. Delivered as commit
  `f9d4f70` (`cache/eligibility.py`, `tests/cache/test_eligibility.py`, 57 tests). All 9
  acceptance criteria pass; evidence in `STATUS.md`, frozen names in `HANDOFF.md`.
