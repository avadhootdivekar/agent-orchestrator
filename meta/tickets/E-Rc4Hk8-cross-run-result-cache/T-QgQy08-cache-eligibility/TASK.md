# TASK: T-QgQy08-cache-eligibility

## Metadata
- Task ID: `T-QgQy08-cache-eligibility`
- Epic ID: `E-Rc4Hk8-cross-run-result-cache`
- Owner: `developer` (Dev B)
- Created: `2026-10-04`
- Last Updated: `2026-10-05` (Rev 2)
- Status: `Draft`
- Estimate: `12 focus hours (1.5 days)` · Sprint 1, Wave 2

## Requirements Mapping
- Requirement IDs: FR-4, NFR-10 (M-12)
- HLD: §8.3.1 (classification), §8.3.2 (predicate), §8.3.3 (reasons owned by eligibility)
- ADR-0019: D10, D24, D25

## Description
Implement the fail-closed, **structural** allowlist in `cache/eligibility.py`. It is pure: no
I/O and no logging. The coordinator checks the author policy before calling it.

Contents:

- **Tables.** `TASK_ANY_VALUE` (a frozenset), `TASK_VALUE_RULED`
  (`dict[str, Rule(ok, reason)]`), `WORKFLOW_FIELD_COVERAGE` and `DEFAULTS_FIELD_COVERAGE`,
  exactly as in the §8.3.1 tables.
- **`Eligibility(eligible, reason=None, detail=None)`**, a frozen dataclass.
- **`check_eligibility(task, workflow, agents, *, integration_active) -> Eligibility`**, following
  §8.3.2 verbatim. In particular:
  - it iterates `type(task).model_fields`, sorted, so subclass fields are seen (developer #7);
  - runtime unknown-field rules apply to the task, the workflow and the defaults;
  - the command-basename rule: `posixpath.basename(command_template[0])` must be in
    `CACHEABLE_COMMAND_BASENAMES`;
  - `model_unresolved`: the effective model is `None` and no model flag is present;
  - the verdict-sidecar and breaker-verdict-source rules.
- **Tripwire message (U-E1).** Use the instructive wording from §8.3.1, including "an
  approval/human-gate field (E-Ag7Pw3) is ALWAYS RULED".

## File scope (exclusive)
- `src/agent_orchestrator/cache/eligibility.py` (new)
- `tests/cache/test_eligibility.py` (new)

## Inputs / Outputs
- **Inputs:**
  - from `models`: `resolve_task_isolation`, `strip_iter_suffix`, `resolve_effective_agent` and
    `ISOLATION_NONE`;
  - `usage.verdict_path_for`;
  - `constants`.
- **Outputs:** `check_eligibility` and the classification tables, consumed by T-gDNjN2. The
  tripwires guard future spec growth.

## Acceptance Criteria
1. **U-E1 (TaskSpec tripwire).**
   - `set(TaskSpec.model_fields) == TASK_ANY_VALUE | set(TASK_VALUE_RULED)`.
   - A failing run prints the §8.3.1 instruction text.
2. **U-E2 (workflow tripwire).** `WORKFLOW_FIELD_COVERAGE` covers every `WorkflowSpec` field, and
   `DEFAULTS_FIELD_COVERAGE` covers every `WorkflowDefaults` field.
3. **Runtime unknown-field rules.**
   - A `TaskSpec` subclass with an extra field set to a non-default value gives
     `unknown_task_field` with detail = the field name. At its default value, the task is
     eligible.
   - The same pattern holds for `WorkflowSpec` and `WorkflowDefaults` subclasses, giving
     `unknown_workflow_field` with details `<name>` and `defaults.<name>`.
4. **U-E3..E26 (one test per eligibility-owned reason in §8.3.3).** Each test triggers exactly one
   reason and asserts both `reason` and `detail`:
   - `run_integration_active`
   - `emit_tasks`
   - `task_manifest_path`
   - `output_manifest`
   - `pre_hook`
   - `post_hook`
   - `isolation_worktree`, from the task and separately from `defaults.isolation`
   - `router_task`
   - `loop_member`, including `dev__iter3`
   - `no_outputs`
   - `agent_unknown`
   - `executor_not_cacheable`
   - `command_not_cacheable`, for a wrapper script
   - `model_unresolved`
   - `verdict_sidecar_undeclared`
   - `breaker_verdict_source`
5. **Positive cases.** Each of these gives `Eligibility(True)`:
   - `command_template[0] == "/usr/local/bin/claude"`;
   - `--model opus` baked into `command_template` with `agent.model=None`;
   - `--model=opus` in `extra_args`;
   - `executor: fake`;
   - a review task whose `review-verdict.json` sibling is declared.
6. **Determinism.** With several violations at once, the first reported reason is stable across
   100 calls and follows the §8.3.2 order.
7. **Purity.** With `builtins.open`, `os.stat` and `subprocess.run` monkeypatched to raise,
   `check_eligibility` still returns.
8. **Hygiene.** `ruff` and `mypy` are clean. `pytest -q` has no new failures.

## Test requirements
- `tests/cache/test_eligibility.py`: AC-1..AC-7.

## Risks
- **The tripwire fails when E-Ag7Pw3 merges.** This is expected. The merge note (HLD §24.2) says
  to classify the approval field as RULED. The runtime rule keeps cached runs safe meanwhile.
- **Default-value comparison for mutable defaults.** Use
  `model_fields[f].get_default(call_default_factory=True)`.

## Dependencies
- T-28J9oR (models with `cache`, settings) and T-FJH6LI (constants).

## Pseudocode / Algorithm
```text
HLD §8.3.2 check_eligibility verbatim.
```

## Schemas / Interface Notes
- **Interface:** `check_eligibility(task, workflow, agents, *, integration_active) -> Eligibility`.
- **Reasons:** HLD §8.3.3.

## Handoff Boundary
- **Upstream:** T-28J9oR, T-FJH6LI.
- **Downstream:** T-gDNjN2. At merge time, E-Ag7Pw3 classifies its new field here.

## Artifacts
- **Docs/comments:** `meta/tickets/E-Rc4Hk8-cross-run-result-cache/T-QgQy08-cache-eligibility/`
- **Large outputs:** N/A

## Comments
- By: architect · Role: architect · Date: 2026-10-04 · Comment: Allowlist predicate with
  tripwires.
- By: architect · Role: architect · Date: 2026-10-05 · Comment: Rev 2 changes:
  - **Iteration:** iterates `type(task).model_fields` (developer #7).
  - **New runtime rules:** `unknown_workflow_field` (critic #8c), `command_not_cacheable` and
    `model_unresolved` (reviewer R7).
  - **Ownership:** the author opt-in moved out to `settings.task_cache_policy` (double opt-in);
    sensitive and control output checks moved to keys.
