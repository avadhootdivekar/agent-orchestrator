# TASK: T-eGXqXH-template-scaffold

## Metadata
- Task ID: `T-eGXqXH-template-scaffold`
- Epic ID: `E-YAAGhk-overseer-runner-template`
- Owner: developer
- Created: 2026-09-26
- Last Updated: 2026-09-26
- Status: Draft
- Estimate: 2 days (16 h)

## Requirements Mapping
- Requirement IDs: FR-1, FR-10, NFR-5, NFR-7
- Design: `docs-md/overseer-runner-hld.md` §8.1, §13.1, §13.2, §13.4 (config), §16

## Description
Create the template skeleton under `src/agent_orchestrator/templates/builtin/overseer-runner/`,
following `routed-runner` conventions.

- `template.yaml`: the params, dirs, files, and assets exactly as in §13.1. `required_agents` is
  `[architect, developer, git-operator, manager, reviewer, tester]`. Add a `files` entry for
  `tools/overseer_tool.py`. Its content lands in T-ABDjSj; until then, commit a placeholder that
  compiles and contains no `{{`.
- `workflow.json.tmpl`: exactly the static part in §13.2.
  - Static tasks: `git-branch-off` and `intake`.
  - Six hooks. Each argv is `["{{ params.python_bin }}", "{{ workspace_root }}/{{ instance_dir }}/tools/overseer_tool.py", "<sub>", "--workspace-root", "{{ workspace_root }}", "--instance-dir", "{{ instance_dir }}"]`.
  - The breaker list from §13.2. `run-budget-backstop` and `runaway-fanout` are hard (mode
    omitted); the time, systemic, and task breakers are `recommend`.
- `overseer-config.json.tmpl` (schema `ao.overseer.config/v1`): the params rendered as bare JSON
  numbers or strings, plus the constants and the `kind_map` from §13.4.
- `prompt.md.tmpl`: sections for Asks (one bullet per distinct ask), "What usable means to me",
  Constraints, Budget notes, and Out of scope.

Verify, and document the result in the README (T-ltBLUY), how an existing instance's
`tools/overseer_tool.py` is re-rendered. Candidates are `ao new overseer-runner <id> --force`, or
whatever the templates module supports. This is needed for the per-instance tool fix rollout
(ADR-0016 D4 trigger 3).

- Inputs: `routed-runner/template.yaml`, `routed-runner/workflow.json.tmpl`, and `templates/__init__.py` (`_render`, `escape_json`)
- Outputs: the four files above, plus `tests/test_builtin_overseer_runner_assets.py`

## Acceptance Criteria
1. `load_template("overseer-runner", ws, None)` succeeds. Its params and defaults match §13.1
   exactly: 16 params, `repo_set` required, enums on `final_push` and `overseer_effort`.
2. With default params plus `repo_set`, `ao new overseer-runner demo` renders:
   - `workflow.json`, which parses and passes `load_workflow` + `ao validate` with no errors
   - `overseer-config.json`, which parses and matches the config schema's required keys
   - `overseer-contract.md` (a stub is OK until T-ltBLUY)
   - `prompt.md`
   - `tools/overseer_tool.py`
3. Rendered breakers:
   - `run-budget-backstop` is `run_cost_usd`, threshold 2000 (a bare number), action `stop`, with
     no `mode` or `mode == "hard"`
   - `runaway-fanout` threshold is 160, hard
   - `task-budget-cap` is 75, `recommend`
   - stop-file gates: 3 pause and 2 halt
4. The hooks map has exactly `ov-intake-prep`, `ov-intake-check`, `ov-ckpt-prep`, `ov-ckpt-check`,
   `ov-unit-gate`, and `ov-expander-check`. Every argv[1] is an absolute path ending in
   `/tools/overseer_tool.py`, and all have `on_failure: fail_task`.
5. `intake` has `emit_tasks: true`, `task_manifest_path` ending `outputs/manifests/intake.json`,
   `pre_hook.use == "ov-intake-prep"`, `post_hook == {"use": "ov-intake-check", "on_failure":
   "fail_task"}`, and `skip_if_outputs_exist: false`.
6. Param override: `--param run_budget_usd=500 --param wave_size=3` renders threshold 500 and config
   `wave_size: 3`. A non-numeric `run_budget_usd=abc` makes `ao new` fail with a clear error from
   rendered-JSON validation, and no half-written instance is left.
7. Tool render safety (developer #5): the source file has no `_VAR_RE` match. The rendered
   `tools/overseer_tool.py` is byte-identical to the source and passes `py_compile`.
8. Packaging: building a wheel (`uv build` or `python -m build`) produces an archive containing
   `agent_orchestrator/templates/builtin/overseer-runner/tools/overseer_tool.py` and the
   `instructions/` directory.
9. The `routed-runner` template's tests are unchanged and pass (NFR-7). A `reviewer`-agent review is
   recorded in STATUS.md.

## Risks
- The renderer has no conditionals, so optional fields (`overseer_model`) are handled in contract
  text and the checker, not in the spec.
- Absolute `workspace_root` in the hook argv breaks if the workspace moves. This is documented
  (A-3).

## Dependencies
- None hard. The final `tools/overseer_tool.py` content comes from T-ABDjSj/T-HPJcc6/T-tAKBBB.

## Pseudocode / Algorithm
```text
Mirror routed-runner's files; render tests via templates.instantiate() and CliRunner `ao new`.
```

## Schemas / Interface Notes
- Spec: §13.1, §13.2. Config: §13.4.

## Handoff Boundary
- Upstream: the design.
- Downstream: T-ltBLUY (contract, README), T-WruPiv (e2e).

## Artifacts
- Tests: `tests/test_builtin_overseer_runner_assets.py`
